"""Make a Tile ring over Bluetooth LE, without the Tile servers.

This is a client for TOA (Tile Over-the-Air), the protocol Tile trackers speak
over two GATT characteristics, in its multi-end-point (MEP) variant. Codes,
message formats and the SRES check come from Tile's own header, toa.h, shipped
in Nordic's nRF5 SDK 16 (external/tile/tile_lib/src/toa/toa.h), and song
numbers from tile_song_module.h in the same SDK. That header leaves out the
session key, the MIC and the song and keep-alive sub-commands: those follow
node-tile (https://github.com/lesleyxyz/node-tile), a client reverse-engineered
from the Tile app and tested on recent Tiles.

Every message starts with a channel ID (CID):

    CID 0, connectionless:   0 | token (4) | code | payload
    CID 1, broadcast:        1 | code | payload | MIC (4)
    CID 2 and up, a channel: CID | code | payload | MIC (4)

The client picks the token and ignores connectionless messages carrying another
one. The MIC signs a message with the session key of its channel.

This module has no Home Assistant dependency so it can be tested on its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import os
from collections.abc import Callable
from typing import Any, Protocol

_LOGGER = logging.getLogger(__name__)

COMMAND_UUID = "9d410018-35d6-f4dd-ba60-e7bd8dc491c0"
RESPONSE_UUID = "9d410019-35d6-f4dd-ba60-e7bd8dc491c0"

AUTH_KEY_LENGTH = 16

# Song numbers, strengths and durations, from tile_song_module.h.
SONG_FIND = 0x01
MAX_STRENGTH = 3
SONG_DURATION_ONCE = 0xFE
SONG_DURATION_FOREVER = 0xFF

_CONNECTIONLESS_CID = 0
_TOKEN_LENGTH = 4
_RAND_A_LENGTH = 14
_AUTH_RAND_T_LENGTH = 10
_SRES_LENGTH = 4
_CHANNEL_RAND_T_LENGTH = 13
_MIC_LENGTH = 4

# TOA_CMD codes, sent by the client.
_CMD_SONG = 0x05
_CMD_TKA = 0x08
_CMD_OPEN_CHANNEL = 0x10
_CMD_CLOSE_CHANNEL = 0x11
_CMD_READY = 0x12
_CMD_AUTHENTICATE = 0x14

# TOA_RSP codes, sent by the Tile.
_RSP_READY = 0x01
_RSP_ERROR = 0x05
_RSP_SONG = 0x07
_RSP_TKA = 0x0A
_RSP_OPEN_CHANNEL = 0x12
_RSP_CLOSE_CHANNEL = 0x13
_RSP_AUTHENTICATE = 0x15

_COMMAND_NAMES = {
    _CMD_SONG: "SONG",
    _CMD_TKA: "TKA",
    _CMD_OPEN_CHANNEL: "OPEN_CHANNEL",
    _CMD_CLOSE_CHANNEL: "CLOSE_CHANNEL",
    _CMD_READY: "READY",
    _CMD_AUTHENTICATE: "AUTHENTICATE",
}

# TOA_RSP_ERROR payload: error code, offending command.
_TOA_ERRORS = {
    0x01: "security level not met",
    0x02: "unsupported command",
    0x03: "invalid parameters",
    0x04: "responses dropped",
    0x05: "no channel available",
    0x06: "authorization required",
    0x07: "service unavailable",
}

# Errors of a feature such as SONG: 0x20, offending sub-command, error code.
_FEATURE_RSP_ERROR = 0x20
_FEATURE_ERRORS = {
    0x01: "unsupported",
    0x02: "invalid parameters",
    0x03: "insufficient security",
    0x04: "invalid state",
    0x12: "resource in use",
}

_CLOSE_REASON_NO_ERROR = 0x00
_CLOSE_REASON_MIC_FAILURE = 0x01
_CLOSE_REASONS = {
    _CLOSE_REASON_NO_ERROR: "no error",
    _CLOSE_REASON_MIC_FAILURE: "MIC failure",
    0x02: "keep-alive missing",
    0x03: "broadcast MIC failure",
}

# TOA_CMD_READY carries the client's max payload size: the Tile app sends 19.
_CLIENT_MAX_PAYLOAD = 19
# Bit of the first feature byte of TOA_RSP_READY that announces songs.
_FEATURE_SONG_BIT = 3

# SONG sub-commands and the response that accepts each. node-tile names
# response 3 STOP_OK; that the stop command is 3 too is inferred.
_SONG_CMD_PLAY = 0x02
_SONG_CMD_STOP = 0x03
_SONG_ACCEPTED = {_SONG_CMD_PLAY: 0x02, _SONG_CMD_STOP: 0x03}
_TKA_RSP_CHECK = 0x03
_TKA_CMD_ACK = 0x03


class TileProtocolError(Exception):
    """The Tile reported an error or sent something unexpected."""


class TileAuthError(TileProtocolError):
    """The Tile rejected the auth key."""


def nonce_bytes(nonce: int) -> bytes:
    """Encode a MIC nonce on 8 bytes, little-endian."""
    return nonce.to_bytes(8, "little")


def tile_hmac(key: bytes, data: bytes) -> bytes:
    """Return the HMAC-SHA256 of data, zero-padded to at least 32 bytes."""
    return hmac.new(key, data.ljust(32, b"\0"), hashlib.sha256).digest()


def _name(names: dict[int, str], code: int | None) -> str:
    """Return the name of a code, for error messages."""
    if code is None:
        return "no code given"
    return names.get(code, f"code 0x{code:02x}")


def _toa_error(payload: bytes) -> TileProtocolError:
    """Return the exception for the payload of a TOA_RSP_ERROR."""
    error = payload[0] if payload else None
    command = payload[1] if len(payload) > 1 else None
    return TileProtocolError(
        f"{_name(_COMMAND_NAMES, command)} refused: {_name(_TOA_ERRORS, error)}"
    )


class SongSession:
    """State machine for one SONG command exchange, without any I/O.

    Write start() to the command characteristic, then feed every notification
    from the response characteristic to handle() and write back what it returns.
    accepted turns true once the Tile accepts the SONG command, and closed once
    the channel is closed.
    """

    def __init__(
        self,
        auth_key: bytes,
        song_command: bytes,
        *,
        randbytes: Callable[[int], bytes] = os.urandom,
    ) -> None:
        """Prepare a session that sends song_command, a SONG sub-command."""
        if len(auth_key) != AUTH_KEY_LENGTH:
            raise ValueError(f"auth key must be {AUTH_KEY_LENGTH} bytes")
        self._auth_key = auth_key
        self._song_command = song_command
        self._token = randbytes(_TOKEN_LENGTH)
        self._rand_a = randbytes(_RAND_A_LENGTH)
        self._cid: int | None = None
        self._session_key = b""
        self._nonce = 0
        self._pending: int | None = _CMD_AUTHENTICATE
        self.accepted = False
        self.closed = False

    @property
    def waiting_for(self) -> str | None:
        """Return the name of the command waiting for an answer, if any."""
        return None if self._pending is None else _COMMAND_NAMES[self._pending]

    def start(self) -> bytes:
        """Return the first message to send."""
        return self._connectionless(_CMD_AUTHENTICATE, self._rand_a)

    def handle(self, data: bytes) -> bytes | None:
        """Process a notification and return the message to send back, if any.

        Raises TileProtocolError if the Tile reports an error or closes the
        channel before accepting the command. As toa.h requires, ignores
        messages for other clients or channels, broadcasts and responses this
        client does not use.
        """
        if (
            len(data) > 1 + _TOKEN_LENGTH
            and data[0] == _CONNECTIONLESS_CID
            and data[1 : 1 + _TOKEN_LENGTH] == self._token
        ):
            return self._on_connectionless(
                data[1 + _TOKEN_LENGTH], data[2 + _TOKEN_LENGTH :]
            )
        if (
            self._cid is not None
            and len(data) > 1 + _MIC_LENGTH
            and data[0] == self._cid
        ):
            return self._on_channel(data[1], data[2:-_MIC_LENGTH])
        return None

    def _on_connectionless(self, code: int, payload: bytes) -> bytes | None:
        """Process a response on the connectionless channel."""
        if code == _RSP_ERROR:
            raise _toa_error(payload)
        if code == _RSP_AUTHENTICATE and self._pending == _CMD_AUTHENTICATE:
            if len(payload) < _AUTH_RAND_T_LENGTH + _SRES_LENGTH:
                raise TileProtocolError(
                    f"AUTHENTICATE answer too short: {payload.hex()}"
                )
            rand_t = payload[:_AUTH_RAND_T_LENGTH]
            sres = payload[_AUTH_RAND_T_LENGTH : _AUTH_RAND_T_LENGTH + _SRES_LENGTH]
            # SRES_T proves the Tile holds the key. Only logged for now: the
            # check follows toa.h but has not been tried on a real Tile yet.
            expected = tile_hmac(
                self._auth_key, self._rand_a.ljust(16, b"\0") + rand_t.ljust(16, b"\0")
            )[4:8]
            _LOGGER.debug("SRES_T %s", "valid" if sres == expected else "invalid")
            self._pending = _CMD_OPEN_CHANNEL
            return self._connectionless(_CMD_OPEN_CHANNEL, self._rand_a)
        if code == _RSP_OPEN_CHANNEL and self._pending == _CMD_OPEN_CHANNEL:
            if len(payload) < 1 + _CHANNEL_RAND_T_LENGTH:
                raise TileProtocolError(
                    f"OPEN_CHANNEL answer too short: {payload.hex()}"
                )
            self._cid = payload[0]
            rand_t = payload[1 : 1 + _CHANNEL_RAND_T_LENGTH]
            self._session_key = tile_hmac(
                self._auth_key,
                self._rand_a + rand_t + bytes([self._cid]) + self._token,
            )[:16]
            self._pending = _CMD_READY
            return self._signed(_CMD_READY, bytes([_CLIENT_MAX_PAYLOAD]))
        return None

    def _on_channel(self, code: int, payload: bytes) -> bytes | None:
        """Process a response on the open channel, MIC removed."""
        if code == _RSP_ERROR:
            raise _toa_error(payload)
        if code == _RSP_READY and self._pending == _CMD_READY:
            # Max payload size, 3 feature bytes, broadcast nonce, then more.
            if len(payload) > 1 and not payload[1] & (1 << _FEATURE_SONG_BIT):
                raise TileProtocolError("this Tile cannot play songs")
            self._pending = _CMD_SONG
            return self._signed(_CMD_SONG, self._song_command)
        if code == _RSP_SONG and self._pending == _CMD_SONG and payload:
            if payload[0] == _SONG_ACCEPTED[self._song_command[0]]:
                self.accepted = True
                self._pending = _CMD_CLOSE_CHANNEL
                return self._signed(_CMD_CLOSE_CHANNEL, bytes([_CLOSE_REASON_NO_ERROR]))
            if payload[0] == _FEATURE_RSP_ERROR:
                error = payload[2] if len(payload) > 2 else None
                raise TileProtocolError(
                    f"song refused: {_name(_FEATURE_ERRORS, error)}"
                )
            return None
        if code == _RSP_TKA and payload[:1] == bytes([_TKA_RSP_CHECK]):
            return self._signed(_CMD_TKA, bytes([_TKA_CMD_ACK]))
        if code == _RSP_CLOSE_CHANNEL:
            self.closed = True
            self._pending = None
            if not self.accepted:
                reason = payload[0] if payload else None
                if reason == _CLOSE_REASON_MIC_FAILURE:
                    raise TileAuthError(
                        "wrong auth key, the Tile reported a MIC failure"
                    )
                raise TileProtocolError(
                    "channel closed by the Tile before the song command"
                    f" ({_name(_CLOSE_REASONS, reason)})"
                )
        return None

    def _connectionless(self, code: int, payload: bytes) -> bytes:
        """Return a message on the connectionless channel."""
        return bytes([_CONNECTIONLESS_CID]) + self._token + bytes([code]) + payload

    def _signed(self, code: int, payload: bytes) -> bytes:
        """Return a message on the open channel, with its MIC."""
        assert self._cid is not None
        message = bytes([code]) + payload
        self._nonce += 1
        # The 1 before the length is a constant the Tile app puts there.
        mic = tile_hmac(
            self._session_key,
            nonce_bytes(self._nonce) + bytes([1, len(message)]) + message,
        )[:_MIC_LENGTH]
        return bytes([self._cid]) + message + mic


class RingSession(SongSession):
    """A SongSession that makes the Tile play its find song."""

    def __init__(
        self,
        auth_key: bytes,
        *,
        strength: int = MAX_STRENGTH,
        duration: int | None = None,
        randbytes: Callable[[int], bytes] = os.urandom,
    ) -> None:
        """Prepare a ring with the Tile's auth key.

        strength goes from 1 (quiet) to MAX_STRENGTH. duration is in seconds,
        or SONG_DURATION_ONCE or SONG_DURATION_FOREVER; None leaves it to the
        Tile.
        """
        if not 1 <= strength <= MAX_STRENGTH:
            raise ValueError(f"strength must be between 1 and {MAX_STRENGTH}")
        if duration is not None and not 1 <= duration <= SONG_DURATION_FOREVER:
            raise ValueError("duration must be between 1 and 255")
        command = bytes([_SONG_CMD_PLAY, SONG_FIND, strength])
        if duration is not None:
            command += bytes([duration])
        super().__init__(auth_key, command, randbytes=randbytes)


class GattClient(Protocol):
    """The part of a connected Bleak client this module uses."""

    async def start_notify(
        self, char_specifier: str, callback: Callable[[Any, bytearray], None]
    ) -> None:
        """Subscribe to notifications of a characteristic."""

    async def write_gatt_char(
        self, char_specifier: str, data: bytes, response: bool = ...
    ) -> None:
        """Write to a characteristic."""


async def async_ring(
    client: GattClient,
    auth_key: bytes,
    *,
    strength: int = MAX_STRENGTH,
    duration: int | None = None,
    timeout: float = 15.0,
    close_timeout: float = 2.0,
    randbytes: Callable[[int], bytes] = os.urandom,
) -> None:
    """Make the Tile behind a connected client play its find song.

    Returns once the Tile confirms it is ringing and the channel is closed, or
    close_timeout seconds after ringing if the Tile does not confirm the close.
    Raises TileAuthError if the Tile rejects the auth key, TileProtocolError if
    it reports another error, and TimeoutError if it does not start ringing
    within timeout seconds.
    """
    session = RingSession(
        auth_key, strength=strength, duration=duration, randbytes=randbytes
    )
    await _async_run(client, session, timeout, close_timeout)


async def async_stop(
    client: GattClient,
    auth_key: bytes,
    *,
    timeout: float = 15.0,
    close_timeout: float = 2.0,
    randbytes: Callable[[int], bytes] = os.urandom,
) -> None:
    """Make the Tile behind a connected client stop its song.

    Returns and raises like async_ring.
    """
    session = SongSession(auth_key, bytes([_SONG_CMD_STOP]), randbytes=randbytes)
    await _async_run(client, session, timeout, close_timeout)


async def _async_run(
    client: GattClient, session: SongSession, timeout: float, close_timeout: float
) -> None:
    """Run a session until the Tile accepts its command and closes the channel."""
    queue: asyncio.Queue[bytes] = asyncio.Queue()

    def on_notify(_sender: Any, data: bytearray) -> None:
        queue.put_nowait(bytes(data))

    async def send(data: bytes) -> None:
        _LOGGER.debug("TX %s", data.hex())
        await client.write_gatt_char(COMMAND_UUID, data, response=False)

    async def exchange() -> None:
        data = await queue.get()
        _LOGGER.debug("RX %s", data.hex())
        reply = session.handle(data)
        if reply is not None:
            await send(reply)

    await client.start_notify(RESPONSE_UUID, on_notify)
    await send(session.start())
    try:
        async with asyncio.timeout(timeout):
            while not session.accepted:
                await exchange()
    except TimeoutError as err:
        raise TimeoutError(
            f"no answer to {session.waiting_for} within {timeout:g} s"
        ) from err
    # The command took effect whether or not the Tile confirms the close.
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(close_timeout):
            while not session.closed:
                await exchange()
