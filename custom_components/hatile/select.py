"""Ring duration of a Tile, used by its Ring button."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import HatileConfigEntry
from .device import TileDevice
from .entity import TileEntity
from .tile_protocol import SONG_DURATION_FOREVER, SONG_DURATION_ONCE

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class TileSelectDescription(SelectEntityDescription):
    """A setting of the Tile, stored in a TileDevice attribute."""

    attribute: str
    values: dict[str, int | None]
    default: str


# No volume: Tiles accept a strength, but Tile's reference firmware and the
# Tiles tried play every strength alike.
SELECTS = (
    TileSelectDescription(
        key="ring_duration",
        translation_key="ring_duration",
        attribute="duration",
        values={
            "tile_default": None,
            "once": SONG_DURATION_ONCE,
            "10_seconds": 10,
            "30_seconds": 30,
            "1_minute": 60,
            "2_minutes": 120,
            "4_minutes": 240,
            "until_stopped": SONG_DURATION_FOREVER,
        },
        default="tile_default",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HatileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the settings of a Tile."""
    async_add_entities(
        TileSelect(entry.runtime_data, description) for description in SELECTS
    )


class TileSelect(TileEntity, SelectEntity, RestoreEntity):
    """A setting of the Tile, kept across restarts."""

    _attr_entity_category = EntityCategory.CONFIG
    entity_description: TileSelectDescription

    def __init__(self, tile: TileDevice, description: TileSelectDescription) -> None:
        """Initialize the setting with its default."""
        super().__init__(tile, description.key)
        self.entity_description = description
        self._attr_options = list(description.values)
        self._set(description.default)

    async def async_added_to_hass(self) -> None:
        """Restore the last option chosen."""
        await super().async_added_to_hass()
        last_state = await self.async_get_last_state()
        if last_state is not None and last_state.state in self.options:
            self._set(last_state.state)

    async def async_select_option(self, option: str) -> None:
        """Choose an option."""
        self._set(option)
        self.async_write_ha_state()

    def _set(self, option: str) -> None:
        """Show option and apply it to the Tile."""
        self._attr_current_option = option
        description = self.entity_description
        setattr(self._tile, description.attribute, description.values[option])
