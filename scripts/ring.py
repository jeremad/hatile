"""Ring a Tile from this computer, with the integration's protocol code.

Usage: python scripts/ring.py AA:BB:CC:DD:EE:FF [--device ID]

The auth key is read from the TILE_AUTH_KEY environment variable, or asked for.
macOS hides MAC addresses: there Tiles are found by their advertisement (name
or service UUIDs), the nearest first, and confirmed by the Tile command
characteristic once connected. --device takes an ID printed by a previous run.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import binascii
import getpass
import importlib.util
import logging
import os
import sys
from pathlib import Path

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from bleak.exc import BleakError

# Load the module by path: importing the package would need Home Assistant.
_PATH = Path(__file__).parents[1] / "custom_components" / "hatile" / "tile_protocol.py"
_SPEC = importlib.util.spec_from_file_location("tile_protocol", _PATH)
tp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tp)

# FEED is advertised by activated Tiles, FEEC by Tiles in shipping mode.
TILE_HINTS = ("feed", "feec", "tile")
MAX_CANDIDATES = 3


class NotATileError(Exception):
    """The device has no Tile command characteristic."""


def looks_like_tile(device: BLEDevice, adv: AdvertisementData) -> bool:
    """Return whether an advertisement may come from a Tile."""
    text = " ".join(
        [device.name or "", adv.local_name or "", *adv.service_uuids, *adv.service_data]
    ).lower()
    return any(hint in text for hint in TILE_HINTS)


def describe(device: BLEDevice, adv: AdvertisementData) -> str:
    """Return a line describing what a device advertises."""
    service_data = {k: v.hex() for k, v in adv.service_data.items()}
    return (
        f"{device.address}  RSSI {adv.rssi}  name={adv.local_name or device.name!r}"
        f"  services={adv.service_uuids}  service_data={service_data}"
    )


async def find_candidates(
    address: str, device_id: str | None, scan_time: float
) -> list[BLEDevice]:
    """Return the devices to try, nearest first, after printing them."""
    if device_id:
        device = await BleakScanner.find_device_by_address(device_id, scan_time)
        if device is None:
            print(f"{device_id} not found. Is the Tile close, and free of phones?")
        return [] if device is None else [device]

    print(f"Scanning for {scan_time:g} s, keep the Tile next to the computer...")
    found = await BleakScanner.discover(timeout=scan_time, return_adv=True)
    nearest = sorted(found.values(), key=lambda pair: pair[1].rssi, reverse=True)
    for device, adv in nearest:
        if device.address.upper() == address:
            print(f"Found {address}: {describe(device, adv)}")
            return [device]
    tiles = [(device, adv) for device, adv in nearest if looks_like_tile(device, adv)]
    print(f"{len(found)} devices seen, {len(tiles)} look like a Tile")
    for device, adv in tiles:
        print("  " + describe(device, adv))
    if not tiles:
        print("Nearest devices:")
        for device, adv in nearest[:10]:
            print("  " + describe(device, adv))
    return [device for device, _ in tiles[:MAX_CANDIDATES]]


async def ring(device: BLEDevice, auth_key: bytes, args: argparse.Namespace) -> None:
    """Connect to a device and ring it, if it is a Tile."""
    async with BleakClient(device, timeout=20.0) as client:
        uuids = {c.uuid for s in client.services for c in s.characteristics}
        if tp.COMMAND_UUID not in uuids:
            raise NotATileError("no Tile command characteristic")
        if args.stop:
            print("Connected to a Tile, stopping its song...")
            await tp.async_stop(client, auth_key)
            return
        print("Connected to a Tile, ringing...")
        await tp.async_ring(
            client, auth_key, strength=args.strength, duration=args.duration
        )


def read_auth_key() -> bytes:
    """Return the auth key from TILE_AUTH_KEY or a prompt."""
    value = os.environ.get("TILE_AUTH_KEY") or getpass.getpass(
        "Auth key (base64, not shown): "
    )
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError):
        key = b""
    if len(key) != tp.AUTH_KEY_LENGTH:
        sys.exit(f"The auth key must be {tp.AUTH_KEY_LENGTH} bytes in base64.")
    return key


async def main() -> int:
    """Find the Tile, connect and ring it."""
    parser = argparse.ArgumentParser(description="Ring a Tile over Bluetooth.")
    parser.add_argument("address", help="the Tile's MAC address")
    parser.add_argument("--device", help="Bluetooth ID printed by a previous run")
    parser.add_argument(
        "--strength", type=int, default=tp.MAX_STRENGTH, help="1 (quiet) to 3"
    )
    parser.add_argument(
        "--duration", type=int, help="seconds to ring, 254 for once, 255 forever"
    )
    parser.add_argument("--scan-time", type=float, default=15.0)
    parser.add_argument("--stop", action="store_true", help="stop the song instead")
    args = parser.parse_args()

    auth_key = read_auth_key()
    logging.basicConfig(format="%(asctime)s %(name)s: %(message)s")
    logging.getLogger("tile_protocol").setLevel(logging.DEBUG)

    candidates = await find_candidates(
        args.address.upper(), args.device, args.scan_time
    )
    for device in candidates:
        print(f"\nTrying {device.address}...")
        try:
            await ring(device, auth_key, args)
        except NotATileError as err:
            print(f"  Skipped: {err}")
        except tp.TileAuthError as err:
            print(f"  Not this Tile, or not this key: {err}")
        except (BleakError, tp.TileProtocolError, TimeoutError) as err:
            print(f"  Did not ring: {type(err).__name__}: {err}")
        else:
            print("  The Tile accepted the command.")
            return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
