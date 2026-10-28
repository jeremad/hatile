"""Signal strength of a Tile, from its Bluetooth advertisements."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components import bluetooth
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import SIGNAL_STRENGTH_DECIBELS_MILLIWATT, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HatileConfigEntry
from .device import TileDevice
from .entity import TileAdvertisementEntity

PARALLEL_UPDATES = 0
SCAN_INTERVAL = timedelta(seconds=30)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HatileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the signal strength sensor of a Tile."""
    async_add_entities([TileSignalStrengthSensor(entry.runtime_data)])


class TileSignalStrengthSensor(TileAdvertisementEntity, SensorEntity):
    """RSSI of the last advertisement heard from the Tile."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, tile: TileDevice) -> None:
        """Initialize the sensor of a Tile."""
        super().__init__(tile, "signal_strength")

    @property
    def should_poll(self) -> bool:
        """Return True, to follow the RSSI.

        Home Assistant only reports advertisements whose data changed, and the
        data of a Tile rarely does.
        """
        return True

    @property
    def native_value(self) -> int | None:
        """Return the RSSI of the last advertisement."""
        service_info = bluetooth.async_last_service_info(
            self.hass, self.coordinator.address, connectable=False
        )
        return None if service_info is None else service_info.rssi
