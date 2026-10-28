"""Fixtures of the tests inside Home Assistant."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations: None, enable_bluetooth: None) -> None:
    """Load custom integrations and a mocked Bluetooth stack in every test."""
