"""One Tile, shared by its entities."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from functools import partial

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothDataUpdateCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo

from .const import CONF_AUTH_KEY, DOMAIN
from .tile_protocol import GattClient, TileProtocolError, async_ring, async_stop

_LOGGER = logging.getLogger(__name__)


class TileDevice:
    """A Tile: its advertisements, its ring settings and its connection."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Prepare the Tile of a config entry."""
        self._hass = hass
        self.address: str = entry.data[CONF_ADDRESS]
        self._auth_key = base64.b64decode(entry.data[CONF_AUTH_KEY])
        self._lock = asyncio.Lock()
        # Set by the ring duration select.
        self.duration: int | None = None
        self.device_info = DeviceInfo(
            connections={(CONNECTION_BLUETOOTH, self.address)},
            name=entry.title,
            manufacturer="Tile",
        )
        # Tiles keep a fixed address, so hearing it means the Tile is near.
        self.coordinator = PassiveBluetoothDataUpdateCoordinator(
            hass,
            _LOGGER,
            self.address,
            bluetooth.BluetoothScanningMode.PASSIVE,
            connectable=False,
        )

    async def async_ring(self) -> None:
        """Make the Tile play its find song, for the current duration."""
        await self._async_run(
            partial(async_ring, duration=self.duration), "ring_failed"
        )

    async def async_stop(self) -> None:
        """Make the Tile stop its song."""
        await self._async_run(async_stop, "stop_failed")

    async def _async_run(
        self,
        command: Callable[[GattClient, bytes], Awaitable[None]],
        failure_key: str,
    ) -> None:
        """Connect to the Tile and run a protocol command."""
        async with self._lock:
            ble_device = bluetooth.async_ble_device_from_address(
                self._hass, self.address, connectable=True
            )
            if ble_device is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="not_in_range",
                    translation_placeholders={"address": self.address},
                )
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache, ble_device, self.address
                )
            except BleakError as err:
                raise _failure(failure_key, err) from err
            try:
                await command(client, self._auth_key)
            except (BleakError, TileProtocolError, TimeoutError) as err:
                raise _failure(failure_key, err) from err
            finally:
                await client.disconnect()


def _failure(translation_key: str, err: Exception) -> HomeAssistantError:
    """Return the error to show when a command fails."""
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={"error": str(err) or repr(err)},
    )
