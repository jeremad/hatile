"""Config flow for the Tile (local) integration."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_ADDRESS, CONF_NAME

from .const import CONF_AUTH_KEY, DOMAIN
from .tile_protocol import decode_auth_key

MAC_RE = re.compile(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}")

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): str,
        vol.Required(CONF_ADDRESS): str,
        vol.Required(CONF_AUTH_KEY): str,
    }
)


class HatileConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add a Tile by its Bluetooth address and auth key."""

    VERSION = 1
    # 2: the volume select of earlier versions is removed.
    MINOR_VERSION = 2

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the Tile's name, address and auth key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            auth_key = user_input[CONF_AUTH_KEY].strip()
            if not MAC_RE.fullmatch(address):
                errors[CONF_ADDRESS] = "invalid_address"
            if decode_auth_key(auth_key) is None:
                errors[CONF_AUTH_KEY] = "invalid_auth_key"
            if not errors:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=user_input[CONF_NAME],
                    data={CONF_ADDRESS: address, CONF_AUTH_KEY: auth_key},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                STEP_USER_SCHEMA, user_input
            ),
            errors=errors,
        )
