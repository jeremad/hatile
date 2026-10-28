"""Make Tile trackers ring over Bluetooth, without the Tile servers."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .device import TileDevice

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SELECT, Platform.SENSOR]

type HatileConfigEntry = ConfigEntry[TileDevice]


async def async_setup_entry(hass: HomeAssistant, entry: HatileConfigEntry) -> bool:
    """Set up a Tile from a config entry."""
    # Remove the volume select of earlier versions: Tiles ignore the volume.
    registry = er.async_get(hass)
    unique_id = f"{entry.data[CONF_ADDRESS]}_volume"
    if entity_id := registry.async_get_entity_id(Platform.SELECT, DOMAIN, unique_id):
        registry.async_remove(entity_id)
    entry.runtime_data = TileDevice(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.runtime_data.coordinator.async_start())
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HatileConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
