"""Presence of a Tile, from its Bluetooth advertisements."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HatileConfigEntry
from .device import TileDevice
from .entity import TileAdvertisementEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HatileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the presence sensor of a Tile."""
    async_add_entities([TilePresenceSensor(entry.runtime_data)])


class TilePresenceSensor(TileAdvertisementEntity, BinarySensorEntity):
    """On while Home Assistant hears the Tile."""

    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, tile: TileDevice) -> None:
        """Initialize the sensor of a Tile."""
        super().__init__(tile, "presence")

    @property
    def available(self) -> bool:
        """Return True: an unheard Tile is away, not unavailable."""
        return True

    @property
    def is_on(self) -> bool:
        """Return whether Home Assistant still hears the Tile."""
        return self.coordinator.available
