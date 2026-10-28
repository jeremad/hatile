"""Tests for the Tile ring protocol, against a simulated Tile."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import unittest
from unittest.mock import patch

from custom_components.hatile import tile_protocol as tp

AUTH_KEY = bytes(range(16))
CID = 5
TOKEN = b"\x0a\x0b\x0c\x0d"
READY = bytes([0x12, 19])
RING = bytes([0x05, 2, 1, 3])
CLOSE = bytes([0x11, 0])
TKA_ACK = bytes([0x08, 3])


def hmac32(key: bytes, data: bytes) -> bytes:
    """HMAC-SHA256 over data zero-padded to 32 bytes, as the Tile computes it."""
    return hmac.new(key, data.ljust(32, b"\0"), hashlib.sha256).digest()


class FakeTile:
    """Plays the Tile side of MEP TOA, as toa.h and node-tile describe it.

    The client does not check the MICs of responses, so this Tile sends random
    ones.
    """

    def __init__(self, auth_key: bytes = AUTH_KEY) -> None:
        self.auth_key = auth_key
        self.auth_rand_t = os.urandom(10)
        self.channel_rand_t = os.urandom(13)
        self.session_key: bytes | None = None
        self.nonce = 0
        self.commands: list[bytes] = []
        # Feature bytes 0 to 2 of TOA_RSP_READY: SONG only.
        self.features = bytes([0b1000, 0, 0])
        self.open_channel_extra = b""
        self.answers: dict[int, list[bytes] | None] = {}
        self.extra: dict[int, list[bytes]] = {}
        self.silent = False
        self.connected = True
        # Command after which the Tile drops the link, once it has answered.
        self.drop_link_after: int | None = None

    def channel(self, code: int, payload: bytes = b"") -> bytes:
        """Return a response on the open channel."""
        return bytes([CID, code]) + payload + os.urandom(4)

    def receive(self, data: bytes) -> list[bytes]:
        """Return the notifications the Tile sends in answer to a command."""
        assert len(data) <= 20, "longer than the TOA_CMD characteristic"
        if self.silent:
            return []
        if data[0] == 0:
            return self.receive_connectionless(data[1:5], data[5], data[6:])
        assert data[0] == CID
        message, mic = data[1:-4], data[-4:]
        self.nonce += 1
        expected = hmac32(
            self.session_key,
            self.nonce.to_bytes(8, "little") + bytes([1, len(message)]) + message,
        )[:4]
        if mic != expected:
            nonce = self.nonce.to_bytes(4, "little")
            return [self.channel(0x13, bytes([1]) + nonce + expected)]
        self.commands.append(message)
        code = message[0]
        if code in self.answers:
            answer = self.answers[code] or []
        elif code == 0x12:
            answer = [self.channel(0x01, bytes([14]) + self.features + bytes(4))]
        elif code == 0x05:
            # PLAY_OK answers PLAY (2), STOP_OK answers STOP (3).
            answer = [self.channel(0x07, message[1:2])]
        elif code == 0x11:
            answer = [self.channel(0x13, bytes([0]))]
        elif code == 0x08:
            answer = []
        else:
            answer = [self.channel(0x05, bytes([0x02, code]))]
        if code == self.drop_link_after:
            self.connected = False
        return answer + self.extra.get(code, [])

    def receive_connectionless(
        self, token: bytes, code: int, rand_a: bytes
    ) -> list[bytes]:
        """Answer a command on the connectionless channel."""
        assert len(rand_a) == 14
        header = b"\0" + token
        if code in self.answers:
            return self.answers[code] or []
        if code == 0x14:
            sres = hmac32(
                self.auth_key,
                rand_a.ljust(16, b"\0") + self.auth_rand_t.ljust(16, b"\0"),
            )[4:8]
            return [header + bytes([0x15]) + self.auth_rand_t + sres]
        assert code == 0x10
        self.session_key = hmac32(
            self.auth_key, rand_a + self.channel_rand_t + bytes([CID]) + token
        )[:16]
        return [
            header + bytes([0x12, CID]) + self.channel_rand_t + self.open_channel_extra
        ]


class FakeClient:
    """A connected Bleak client whose peripheral is a FakeTile."""

    def __init__(self, tile: FakeTile) -> None:
        self.tile = tile
        self.callback = None

    @property
    def is_connected(self) -> bool:
        return self.tile.connected

    async def start_notify(self, char_specifier, callback) -> None:
        assert char_specifier == tp.RESPONSE_UUID
        self.callback = callback

    async def write_gatt_char(self, char_specifier, data, response=True) -> None:
        assert char_specifier == tp.COMMAND_UUID
        assert response is False
        if not self.tile.connected:
            raise OSError("not connected")
        loop = asyncio.get_running_loop()
        for reply in self.tile.receive(bytes(data)):
            loop.call_soon(self.callback, None, bytearray(reply))


def fixed_token(n: int) -> bytes:
    """Return TOKEN for the token and random bytes for anything else."""
    return TOKEN if n == 4 else os.urandom(n)


async def ring(tile: FakeTile, **kwargs) -> None:
    kwargs.setdefault("timeout", 1)
    kwargs.setdefault("close_timeout", 0.1)
    await tp.async_ring(FakeClient(tile), AUTH_KEY, **kwargs)


class HelpersTest(unittest.TestCase):
    def test_decode_auth_key(self) -> None:
        self.assertEqual(tp.decode_auth_key("AAECAwQFBgcICQoLDA0ODw=="), AUTH_KEY)
        for value in (
            "AAECAwQFBgcICQoLDA0O",  # 15 bytes
            "AAECAwQFBgcICQoLDA0ODxA=",  # 17 bytes
            "AAECAwQFBgcICQoLDA0ODw",  # padding missing
            "AAECAwQFBgcICQoLDA0OD!==",  # not base64
            "AAECAwQFBgcICQoLDA0ODé==",  # not ASCII
            "",
        ):
            with self.subTest(value=value):
                self.assertIsNone(tp.decode_auth_key(value))

    def test_rejects_bad_parameters(self) -> None:
        with self.assertRaises(ValueError):
            tp.RingSession(b"short")
        for strength in (0, 4):
            with self.assertRaises(ValueError):
                tp.RingSession(AUTH_KEY, strength=strength)
        for duration in (0, 256):
            with self.assertRaises(ValueError):
                tp.RingSession(AUTH_KEY, duration=duration)

    def test_ignores_messages_for_others(self) -> None:
        session = tp.RingSession(AUTH_KEY, randbytes=fixed_token)
        session.start()
        # Another client's token, the broadcast token, then a broadcast.
        self.assertIsNone(session.handle(b"\0\1\2\3\4" + bytes([0x15]) + bytes(14)))
        self.assertIsNone(
            session.handle(b"\0\xff\xff\xff\xff" + bytes([0x1C, 1, 0, 0]))
        )
        self.assertIsNone(session.handle(bytes([1, 0x1C, 1, 0, 0]) + bytes(4)))
        self.assertIsNone(session.handle(b""))
        self.assertEqual(session.waiting_for, "AUTHENTICATE")


class RingTest(unittest.IsolatedAsyncioTestCase):
    async def test_rings_then_closes(self) -> None:
        tile = FakeTile()
        await ring(tile)
        self.assertEqual(tile.commands, [READY, RING, CLOSE])

    async def test_stops_then_closes(self) -> None:
        tile = FakeTile()
        await tp.async_stop(FakeClient(tile), AUTH_KEY, timeout=1, close_timeout=0.1)
        self.assertEqual(tile.commands, [READY, bytes([0x05, 3]), CLOSE])

    async def test_stop_is_not_accepted_by_play_ok(self) -> None:
        tile = FakeTile()
        tile.answers[0x05] = [tile.channel(0x07, bytes([2]))]
        with self.assertRaisesRegex(TimeoutError, "SONG"):
            await tp.async_stop(FakeClient(tile), AUTH_KEY, timeout=0.2)

    async def test_sends_strength_and_duration(self) -> None:
        tile = FakeTile()
        await ring(tile, strength=1, duration=30)
        self.assertEqual(tile.commands[1], bytes([0x05, 2, 1, 1, 30]))

    async def test_succeeds_when_tile_does_not_confirm_close(self) -> None:
        tile = FakeTile()
        tile.answers[0x11] = None
        await ring(tile)
        self.assertEqual(tile.commands, [READY, RING, CLOSE])

    async def test_succeeds_when_tile_refuses_close(self) -> None:
        tile = FakeTile()
        tile.answers[0x11] = [tile.channel(0x05, bytes([0x02, 0x11]))]
        await ring(tile)
        self.assertEqual(tile.commands, [READY, RING, CLOSE])

    async def test_succeeds_when_close_cannot_be_sent(self) -> None:
        tile = FakeTile()
        tile.drop_link_after = 0x05
        await ring(tile)
        self.assertEqual(tile.commands, [READY, RING])

    async def test_disconnect_is_reported_without_waiting(self) -> None:
        tile = FakeTile()
        tile.answers[0x12] = None
        tile.drop_link_after = 0x12
        with (
            patch.object(tp, "_LINK_CHECK_INTERVAL", 0.01),
            self.assertRaisesRegex(
                tp.TileProtocolError, "disconnected before answering READY"
            ),
        ):
            async with asyncio.timeout(1):
                await ring(tile, timeout=10)

    async def test_times_out_when_subscription_hangs(self) -> None:
        async def hang(char_specifier, callback) -> None:
            await asyncio.Event().wait()

        client = FakeClient(FakeTile())
        client.start_notify = hang
        with self.assertRaisesRegex(TimeoutError, "AUTHENTICATE within 0.2 s"):
            await tp.async_ring(client, AUTH_KEY, timeout=0.2)

    async def test_client_timeout_is_not_blamed_on_the_tile(self) -> None:
        client = FakeClient(FakeTile())
        write = client.write_gatt_char

        async def time_out_on_channel(char_specifier, data, response=True) -> None:
            if data[0] == CID:
                raise TimeoutError("write timed out")
            await write(char_specifier, data, response)

        client.write_gatt_char = time_out_on_channel
        with self.assertRaisesRegex(TimeoutError, "^write timed out$"):
            await tp.async_ring(client, AUTH_KEY, timeout=1)

    async def test_session_key_ignores_extra_open_channel_bytes(self) -> None:
        tile = FakeTile()
        tile.open_channel_extra = b"\x99\x98"
        await ring(tile)
        self.assertEqual(tile.commands, [READY, RING, CLOSE])

    async def test_ignores_broadcasts_and_unknown_responses(self) -> None:
        tile = FakeTile()
        broadcast = bytes([1, 0x1C, 1, 30, 0]) + bytes(4)
        tile.extra[0x12] = [broadcast, tile.channel(0x7F, b"\1\2")]
        await ring(tile)
        self.assertEqual(tile.commands, [READY, RING, CLOSE])

    async def test_answers_keep_alive_check(self) -> None:
        tile = FakeTile()
        tile.answers[0x05] = [
            tile.channel(0x0A, bytes([3])),
            tile.channel(0x07, bytes([2])),
        ]
        await ring(tile)
        self.assertEqual(tile.commands, [READY, RING, TKA_ACK, CLOSE])

    async def test_times_out_when_tile_does_not_answer(self) -> None:
        tile = FakeTile()
        tile.silent = True
        with self.assertRaisesRegex(TimeoutError, "AUTHENTICATE"):
            await ring(tile, timeout=0.2)

    async def test_wrong_key_raises_auth_error(self) -> None:
        tile = FakeTile(auth_key=bytes(16))
        with self.assertRaises(tp.TileAuthError):
            await ring(tile)
        self.assertEqual(tile.commands, [])

    async def test_toa_error_is_reported(self) -> None:
        tile = FakeTile()
        error = b"\0" + TOKEN + bytes([0x05, 0x05, 0x10])
        tile.answers[0x10] = [error]
        with self.assertRaisesRegex(
            tp.TileProtocolError, "OPEN_CHANNEL refused: no channel available"
        ):
            await ring(tile, randbytes=fixed_token)

    async def test_song_error_is_reported(self) -> None:
        tile = FakeTile()
        tile.answers[0x05] = [tile.channel(0x07, bytes([0x20, 0x02, 0x04]))]
        with self.assertRaisesRegex(tp.TileProtocolError, "invalid state"):
            await ring(tile)

    async def test_close_before_ringing_is_reported(self) -> None:
        tile = FakeTile()
        tile.answers[0x05] = [tile.channel(0x13, bytes([2]))]
        with self.assertRaisesRegex(tp.TileProtocolError, "keep-alive missing"):
            await ring(tile)

    async def test_tile_without_songs_is_reported(self) -> None:
        tile = FakeTile()
        tile.features = bytes(3)
        with self.assertRaisesRegex(tp.TileProtocolError, "cannot play songs"):
            await ring(tile)
        self.assertEqual(tile.commands, [READY])
