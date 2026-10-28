"""Tests for the integration inside Home Assistant, with a mocked Bluetooth stack.

Run with pytest-homeassistant-custom-component installed: pytest tests/ha
"""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    mock_restore_cache,
)

from homeassistant.components.bluetooth import (
    FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS,
    SOURCE_LOCAL,
    BluetoothServiceInfoBleak,
    async_get_advertisement_callback,
)
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.hatile.const import DOMAIN
from custom_components.hatile.tile_protocol import TileAuthError

ADDRESS = "AA:BB:CC:DD:EE:FF"
AUTH_KEY = bytes(range(16))
TILE_SERVICE = "0000feed-0000-1000-8000-00805f9b34fb"

RING = "button.wallet_ring"
STOP = "button.wallet_stop_ringing"
DURATION = "select.wallet_ring_duration"
PRESENCE = "binary_sensor.wallet_presence"
SIGNAL = "sensor.wallet_signal_strength"


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations: None, enable_bluetooth: None) -> None:
    """Load custom integrations and a mocked Bluetooth stack in every test."""


@pytest.fixture
async def entry(hass: HomeAssistant) -> MockConfigEntry:
    """Set up a Tile named Wallet."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wallet",
        unique_id=ADDRESS,
        data={"address": ADDRESS, "auth_key": "AAECAwQFBgcICQoLDA0ODw=="},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def hear_tile(hass: HomeAssistant, rssi: int = -60) -> None:
    """Make Home Assistant receive an advertisement of the Tile."""
    service_data = {TILE_SERVICE: b"\x02\x03"}
    advertisement = AdvertisementData(
        local_name=None,
        manufacturer_data={},
        service_data=service_data,
        service_uuids=[TILE_SERVICE],
        tx_power=-127,
        rssi=rssi,
        platform_data=((),),
    )
    async_get_advertisement_callback(hass)(
        BluetoothServiceInfoBleak(
            name=ADDRESS,
            address=ADDRESS,
            rssi=rssi,
            manufacturer_data={},
            service_data=service_data,
            service_uuids=[TILE_SERVICE],
            source=SOURCE_LOCAL,
            device=BLEDevice(address=ADDRESS, name=None, details={}),
            advertisement=advertisement,
            connectable=True,
            time=time.monotonic(),
            tx_power=-127,
            raw=None,
        )
    )


@contextmanager
def bluetooth_time(monotonic: float) -> Generator[None]:
    """Make the Bluetooth stack believe it is a given monotonic time."""
    with (
        patch(
            "homeassistant.components.bluetooth.MONOTONIC_TIME", return_value=monotonic
        ),
        patch("habluetooth.base_scanner.monotonic_time_coarse", return_value=monotonic),
        patch("habluetooth.manager.monotonic_time_coarse", return_value=monotonic),
        patch("habluetooth.scanner.monotonic_time_coarse", return_value=monotonic),
    ):
        yield


async def press(hass: HomeAssistant, entity_id: str) -> None:
    """Press a button."""
    await hass.services.async_call(
        "button", "press", {ATTR_ENTITY_ID: entity_id}, blocking=True
    )


async def select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    """Choose an option of a select."""
    await hass.services.async_call(
        "select",
        "select_option",
        {ATTR_ENTITY_ID: entity_id, "option": option},
        blocking=True,
    )


async def test_entities_start_with_defaults(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    for entity_id in (RING, STOP, DURATION, PRESENCE, SIGNAL):
        assert hass.states.get(entity_id) is not None, entity_id
    assert hass.states.get(PRESENCE).state == STATE_OFF
    assert hass.states.get(SIGNAL).state == STATE_UNAVAILABLE
    assert hass.states.get(DURATION).state == "tile_default"


async def test_presence_and_signal_follow_advertisements(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    hear_tile(hass, rssi=-60)
    await hass.async_block_till_done()
    assert hass.states.get(PRESENCE).state == STATE_ON
    assert hass.states.get(SIGNAL).state == "-60"

    # Same data with another RSSI: the sensor catches it on its next poll.
    hear_tile(hass, rssi=-75)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()
    assert hass.states.get(SIGNAL).state == "-75"

    stale = FALLBACK_MAXIMUM_STALE_ADVERTISEMENT_SECONDS + 1
    with bluetooth_time(time.monotonic() + stale):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=stale))
        await hass.async_block_till_done()
    assert hass.states.get(PRESENCE).state == STATE_OFF
    assert hass.states.get(SIGNAL).state == STATE_UNAVAILABLE


async def test_ring_uses_the_selected_duration(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    hear_tile(hass)
    await select(hass, DURATION, "30_seconds")
    client = AsyncMock()
    with (
        patch(
            "custom_components.hatile.device.establish_connection",
            return_value=client,
        ),
        patch("custom_components.hatile.device.async_ring") as ring,
    ):
        await press(hass, RING)
    ring.assert_awaited_once_with(client, AUTH_KEY, duration=30)
    client.disconnect.assert_awaited_once()


async def test_stop_sends_the_stop_command(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    hear_tile(hass)
    client = AsyncMock()
    with (
        patch(
            "custom_components.hatile.device.establish_connection",
            return_value=client,
        ),
        patch("custom_components.hatile.device.async_stop") as stop,
    ):
        await press(hass, STOP)
    stop.assert_awaited_once_with(client, AUTH_KEY)
    client.disconnect.assert_awaited_once()


async def test_protocol_error_is_reported(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    hear_tile(hass)
    client = AsyncMock()
    with (
        patch(
            "custom_components.hatile.device.establish_connection",
            return_value=client,
        ),
        patch(
            "custom_components.hatile.device.async_ring",
            side_effect=TileAuthError("wrong auth key"),
        ),
        pytest.raises(HomeAssistantError) as err,
    ):
        await press(hass, RING)
    assert err.value.translation_key == "ring_failed"
    assert err.value.translation_placeholders == {"error": "wrong auth key"}
    client.disconnect.assert_awaited_once()


async def test_ring_out_of_range_is_reported(
    hass: HomeAssistant, entry: MockConfigEntry
) -> None:
    with pytest.raises(HomeAssistantError) as err:
        await press(hass, RING)
    assert err.value.translation_key == "not_in_range"


async def test_settings_survive_a_restart(hass: HomeAssistant) -> None:
    mock_restore_cache(hass, [State(DURATION, "until_stopped")])
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wallet",
        unique_id=ADDRESS,
        data={"address": ADDRESS, "auth_key": "AAECAwQFBgcICQoLDA0ODw=="},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(DURATION).state == "until_stopped"
    assert entry.runtime_data.duration == 255


async def test_removes_the_old_volume_select(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Wallet",
        unique_id=ADDRESS,
        data={"address": ADDRESS, "auth_key": "AAECAwQFBgcICQoLDA0ODw=="},
    )
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "select", DOMAIN, f"{ADDRESS}_volume", config_entry=entry
    )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert registry.async_get_entity_id("select", DOMAIN, f"{ADDRESS}_volume") is None
    assert hass.states.get(DURATION) is not None


async def test_unload(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(PRESENCE).state == STATE_UNAVAILABLE
