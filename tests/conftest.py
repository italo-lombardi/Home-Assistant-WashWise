"""Shared fixtures for WashWise tests."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise.const import (
    CONF_CATEGORY,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_FORECAST_TYPE,
    CONF_NAME,
    CONF_WEATHER_ENTITIES,
    DEFAULT_CATEGORY,
    DEFAULT_FORECAST_TYPE,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable loading of custom integrations for every test."""
    yield


@pytest.fixture(autouse=True)
def _bypass_frontend_dependency(monkeypatch):
    """Stub `frontend` integration setup so washwise can load without hass_frontend.

    The washwise manifest declares a `frontend` dependency for the Lovelace
    card resource registration. In production HA the `hass_frontend` package
    is always present; in the test/dev env we may not have it. This fixture
    short-circuits the dependency resolution so config / options flow tests
    can run without pulling in the full frontend.
    """
    try:
        from homeassistant.components import frontend as ha_frontend  # noqa: F401

        async def _noop_setup(hass, config):
            return True

        monkeypatch.setattr(
            "homeassistant.components.frontend.async_setup",
            _noop_setup,
            raising=False,
        )
    except Exception:
        # If the import path changes upstream, just skip — only impacts the
        # subset of tests that exercise the full setup path.
        pass
    yield


@pytest.fixture
def mock_config_data() -> dict[str, Any]:
    """Return a standard config-entry data dict for WashWise."""
    return {
        CONF_NAME: "Test Wash",
        CONF_WEATHER_ENTITIES: ["weather.home"],
        CONF_CATEGORY: DEFAULT_CATEGORY,
        CONF_CUSTOMIZE_THRESHOLDS: False,
        CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
    }


@pytest.fixture
def mock_config_entry(mock_config_data: dict[str, Any]) -> MockConfigEntry:
    """Return a MockConfigEntry for the WashWise domain."""
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Wash",
        data=mock_config_data,
        options={},
        entry_id="test_entry_id",
        unique_id=f"{DOMAIN}_test_wash",
    )


@pytest.fixture
def mock_weather_state(hass: HomeAssistant) -> Callable[..., None]:
    """Register a fake weather entity with the supplied state and attributes.

    Usage:
        mock_weather_state("weather.home", "sunny", {"temperature": 20})
    """

    def _register(
        entity_id: str = "weather.home",
        state: str = "sunny",
        attributes: dict[str, Any] | None = None,
    ) -> None:
        hass.states.async_set(entity_id, state, attributes or {})

    return _register


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """Return a function that loads a JSON fixture from tests/fixtures/weather/."""

    def _load(name: str) -> Any:
        path = FIXTURES_DIR / "weather" / f"{name}.json"
        with path.open("r", encoding="utf-8") as handle:
            return json.loads(handle.read())

    return _load


@pytest_asyncio.fixture
async def integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
):
    """Install WashWise via the mock config entry and return the coordinator."""
    mock_config_entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()

    return hass.data[DOMAIN][mock_config_entry.entry_id]
