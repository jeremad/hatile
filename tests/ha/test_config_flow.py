"""Tests for adding a Tile from the Home Assistant UI."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.hatile.const import DOMAIN

ADDRESS = "AA:BB:CC:DD:EE:FF"
AUTH_KEY = "AAECAwQFBgcICQoLDA0ODw=="


@pytest.fixture(autouse=True)
def setup_entry() -> Generator[AsyncMock]:
    """Stop the entries the flow creates from setting up a Tile."""
    with patch(
        "custom_components.hatile.async_setup_entry", return_value=True
    ) as setup_entry:
        yield setup_entry


async def submit(hass: HomeAssistant, user_input: dict[str, Any]) -> dict[str, Any]:
    """Open the form, check it is empty, then submit user_input."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}
    return await hass.config_entries.flow.async_configure(result["flow_id"], user_input)


async def test_creates_an_entry(hass: HomeAssistant) -> None:
    result = await submit(
        hass,
        {
            "name": "Wallet",
            "address": " aa:bb:cc:dd:ee:ff ",
            "auth_key": f"{AUTH_KEY}\n",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Wallet"
    assert result["data"] == {"address": ADDRESS, "auth_key": AUTH_KEY}
    assert result["result"].unique_id == ADDRESS
    assert result["result"].minor_version == 2


async def test_reports_invalid_values(hass: HomeAssistant) -> None:
    user_input = {
        "name": "Wallet",
        "address": "AA-BB-CC-DD-EE-FF",
        "auth_key": "AAECAwQFBgcICQoLDA0O",
    }
    result = await submit(hass, user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {
        "address": "invalid_address",
        "auth_key": "invalid_auth_key",
    }
    # The form keeps what was entered.
    suggested = {
        key.schema: key.description["suggested_value"]
        for key in result["data_schema"].schema
    }
    assert suggested == user_input

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"name": "Wallet", "address": ADDRESS, "auth_key": AUTH_KEY}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_aborts_for_a_tile_already_added(hass: HomeAssistant) -> None:
    MockConfigEntry(
        domain=DOMAIN,
        title="Wallet",
        unique_id=ADDRESS,
        data={"address": ADDRESS, "auth_key": AUTH_KEY},
    ).add_to_hass(hass)
    result = await submit(
        hass, {"name": "Keys", "address": ADDRESS.lower(), "auth_key": AUTH_KEY}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
