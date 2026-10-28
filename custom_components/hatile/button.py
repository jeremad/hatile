"""Buttons that make a Tile ring and stop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import HatileConfigEntry
from .device import TileDevice
from .entity import TileEntity


@dataclass(frozen=True, kw_only=True)
class TileButtonDescription(ButtonEntityDescription):
    """A button of the Tile and what it does."""

    press: Callable[[TileDevice], Awaitable[None]]


BUTTONS = (
    TileButtonDescription(
        key="ring", translation_key="ring", press=TileDevice.async_ring
    ),
    TileButtonDescription(
        key="stop", translation_key="stop", press=TileDevice.async_stop
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HatileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the buttons of a Tile."""
    async_add_entities(
        TileButton(entry.runtime_data, description) for description in BUTTONS
    )


class TileButton(TileEntity, ButtonEntity):
    """Connect to the Tile and send it a command."""

    entity_description: TileButtonDescription

    def __init__(self, tile: TileDevice, description: TileButtonDescription) -> None:
        """Initialize the button of a Tile."""
        super().__init__(tile, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Send the command."""
        await self.entity_description.press(self._tile)
