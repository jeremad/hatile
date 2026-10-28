"""Base entities of a Tile."""

from __future__ import annotations

from homeassistant.components.bluetooth.passive_update_coordinator import (
    PassiveBluetoothCoordinatorEntity,
    PassiveBluetoothDataUpdateCoordinator,
)
from homeassistant.helpers.entity import Entity

from .device import TileDevice


class TileEntity(Entity):
    """An entity of a Tile."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, tile: TileDevice, key: str) -> None:
        """Attach the entity to its Tile."""
        self._tile = tile
        self._attr_unique_id = f"{tile.address}_{key}"
        self._attr_device_info = tile.device_info


class TileAdvertisementEntity(
    PassiveBluetoothCoordinatorEntity[PassiveBluetoothDataUpdateCoordinator]
):
    """An entity of a Tile, updated from the Tile's advertisements."""

    _attr_has_entity_name = True

    def __init__(self, tile: TileDevice, key: str) -> None:
        """Attach the entity to its Tile."""
        super().__init__(tile.coordinator)
        self._attr_unique_id = f"{tile.address}_{key}"
        self._attr_device_info = tile.device_info
