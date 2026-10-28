"""Ring duration of a Tile, used by its Ring button."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import HatileConfigEntry
from .device import TileDevice
from .entity import TileEntity
from .tile_protocol import SONG_DURATION_FOREVER, SONG_DURATION_ONCE

PARALLEL_UPDATES = 0

# Options of the select and the durations they send. No volume select: Tiles
# accept a strength, but Tile's reference firmware and the Tiles tried play
# every strength alike.
DURATIONS: dict[str, int | None] = {
    "tile_default": None,
    "once": SONG_DURATION_ONCE,
    "10_seconds": 10,
    "30_seconds": 30,
    "1_minute": 60,
    "2_minutes": 120,
    "4_minutes": 240,
    "until_stopped": SONG_DURATION_FOREVER,
}
DEFAULT_DURATION = "tile_default"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HatileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the ring duration setting of a Tile."""
    async_add_entities([TileDurationSelect(entry.runtime_data)])


class TileDurationSelect(TileEntity, SelectEntity, RestoreEntity):
    """How long the Ring button makes the Tile ring, kept across restarts."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "ring_duration"

    def __init__(self, tile: TileDevice) -> None:
        """Initialize the setting with its default."""
        super().__init__(tile, "ring_duration")
        self._attr_options = list(DURATIONS)
        self._set(DEFAULT_DURATION)

    async def async_added_to_hass(self) -> None:
        """Restore the last option chosen."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in DURATIONS:
            self._set(last_state.state)

    async def async_select_option(self, option: str) -> None:
        """Choose an option."""
        self._set(option)
        self.async_write_ha_state()

    def _set(self, option: str) -> None:
        """Show option and apply it to the Tile."""
        self._attr_current_option = option
        self._tile.duration = DURATIONS[option]
