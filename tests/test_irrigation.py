"""Tests for the garden_irrigation feature.

Covers:
* coordinator._async_handle_irrigation — rain gauge read, switch toggle, suppression flag
* IrrigationSuppressedBinarySensor — is_on, extra_state_attributes
* MeasuredRainMmSensor — native_value, extra_state_attributes
* config_flow async_step_irrigation (config and options)
* services.set_irrigation_switch
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise.const import (
    CONF_CATEGORY,
    CONF_IRRIGATION_SWITCH_ENTITY,
    CONF_NAME,
    CONF_RAIN_GAUGE_ENTITY,
    CONF_RAIN_GAUGE_THRESHOLD_MM,
    CONF_WEATHER_ENTITIES,
    DEFAULT_RAIN_GAUGE_THRESHOLD_MM,
    DOMAIN,
)
from custom_components.washwise.coordinator import WashWiseCoordinator
from custom_components.washwise.models import Decision
from custom_components.washwise.services import (
    SERVICE_SET_IRRIGATION_SWITCH,
    async_register_services,
    async_unregister_services,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FROZEN_NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)

# garden_irrigation uses invert=True: can_wash=True means rain expected → suppress irrigation.
# can_wash=False means no rain → irrigate.
_RAIN_EXPECTED = Decision(
    can_wash=True,
    score=80,
    reason="rain",
    days_until_wash=0,
    blocking_days=[],
    forecast_summary=[],
    days_analyzed=1,
)

_NO_RAIN = Decision(
    can_wash=False,
    score=10,
    reason="rain",
    days_until_wash=2,
    blocking_days=[],
    forecast_summary=[],
    days_analyzed=1,
)


def _make_irrigation_entry(
    *,
    gauge_entity: str | None = None,
    gauge_threshold: float = DEFAULT_RAIN_GAUGE_THRESHOLD_MM,
    switch_entity: str | None = None,
    options: dict[str, Any] | None = None,
    entry_id: str = "irr_entry",
) -> MockConfigEntry:
    data: dict[str, Any] = {
        CONF_NAME: "Garden",
        CONF_WEATHER_ENTITIES: ["weather.home"],
        CONF_CATEGORY: "garden_irrigation",
    }
    if gauge_entity:
        data[CONF_RAIN_GAUGE_ENTITY] = gauge_entity
    data[CONF_RAIN_GAUGE_THRESHOLD_MM] = gauge_threshold
    if switch_entity:
        data[CONF_IRRIGATION_SWITCH_ENTITY] = switch_entity
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Garden",
        data=data,
        options=options or {},
        entry_id=entry_id,
    )


# ---------------------------------------------------------------------------
# coordinator._async_handle_irrigation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_irrigation_no_gauge_no_suppression(hass: HomeAssistant) -> None:
    """No gauge entity configured — measured_rain_mm is None, suppressed only if forecast blocks."""
    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)

    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.measured_rain_mm is None
    assert coord.irrigation_suppressed is False

    await coord._async_handle_irrigation(_RAIN_EXPECTED)
    assert coord.irrigation_suppressed is True


@pytest.mark.asyncio
async def test_irrigation_gauge_below_threshold(hass: HomeAssistant) -> None:
    """Gauge reads below threshold — not suppressed (unless forecast blocks)."""
    entry = _make_irrigation_entry(gauge_entity="sensor.rain", gauge_threshold=5.0)
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "2.5")
    coord = WashWiseCoordinator(hass, entry)

    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.measured_rain_mm == pytest.approx(2.5)
    assert coord.irrigation_suppressed is False


@pytest.mark.asyncio
async def test_irrigation_gauge_at_threshold(hass: HomeAssistant) -> None:
    """Gauge reads at threshold — suppressed."""
    entry = _make_irrigation_entry(gauge_entity="sensor.rain", gauge_threshold=5.0)
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "5.0")
    coord = WashWiseCoordinator(hass, entry)

    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.irrigation_suppressed is True


@pytest.mark.asyncio
async def test_irrigation_gauge_above_threshold(hass: HomeAssistant) -> None:
    """Gauge reads above threshold — suppressed."""
    entry = _make_irrigation_entry(gauge_entity="sensor.rain", gauge_threshold=5.0)
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "12.3")
    coord = WashWiseCoordinator(hass, entry)

    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.measured_rain_mm == pytest.approx(12.3)
    assert coord.irrigation_suppressed is True


@pytest.mark.asyncio
async def test_irrigation_gauge_unavailable(hass: HomeAssistant) -> None:
    """Gauge entity unavailable — measured is None, fallback to forecast."""
    entry = _make_irrigation_entry(gauge_entity="sensor.rain", gauge_threshold=5.0)
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "unavailable")
    coord = WashWiseCoordinator(hass, entry)

    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.measured_rain_mm is None
    assert coord.irrigation_suppressed is False


@pytest.mark.asyncio
async def test_irrigation_gauge_non_numeric(hass: HomeAssistant) -> None:
    """Gauge entity has non-numeric state — warning logged, measured is None."""
    entry = _make_irrigation_entry(gauge_entity="sensor.rain", gauge_threshold=5.0)
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "bad_value")
    coord = WashWiseCoordinator(hass, entry)

    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.measured_rain_mm is None


@pytest.mark.asyncio
async def test_irrigation_switch_turned_off_when_suppressed(hass: HomeAssistant) -> None:
    """Switch is ON and irrigation should be suppressed — coordinator calls switch.turn_off."""
    entry = _make_irrigation_entry(
        gauge_entity="sensor.rain",
        gauge_threshold=5.0,
        switch_entity="switch.irrigation",
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "10.0")
    hass.states.async_set("switch.irrigation", "on")
    coord = WashWiseCoordinator(hass, entry)

    # Replace the coordinator's hass with a mock whose services.async_call we can inspect.
    mock_hass = MagicMock()
    mock_hass.states = hass.states
    mock_hass.services.async_call = AsyncMock()
    coord.hass = mock_hass

    await coord._async_handle_irrigation(_NO_RAIN)

    mock_hass.services.async_call.assert_awaited_once_with(
        "switch", "turn_off", {"entity_id": "switch.irrigation"}, blocking=False
    )


@pytest.mark.asyncio
async def test_irrigation_switch_turned_on_when_clear(hass: HomeAssistant) -> None:
    """Switch is OFF and no suppression — coordinator calls switch.turn_on."""
    entry = _make_irrigation_entry(
        gauge_entity="sensor.rain",
        gauge_threshold=5.0,
        switch_entity="switch.irrigation",
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "1.0")
    hass.states.async_set("switch.irrigation", "off")
    coord = WashWiseCoordinator(hass, entry)

    mock_hass = MagicMock()
    mock_hass.states = hass.states
    mock_hass.services.async_call = AsyncMock()
    coord.hass = mock_hass

    await coord._async_handle_irrigation(_NO_RAIN)

    mock_hass.services.async_call.assert_awaited_once_with(
        "switch", "turn_on", {"entity_id": "switch.irrigation"}, blocking=False
    )


@pytest.mark.asyncio
async def test_irrigation_switch_not_called_when_state_matches(hass: HomeAssistant) -> None:
    """Switch already OFF and suppression active — no service call."""
    entry = _make_irrigation_entry(
        gauge_entity="sensor.rain",
        gauge_threshold=5.0,
        switch_entity="switch.irrigation",
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "10.0")
    hass.states.async_set("switch.irrigation", "off")
    coord = WashWiseCoordinator(hass, entry)

    mock_hass = MagicMock()
    mock_hass.states = hass.states
    mock_hass.services.async_call = AsyncMock()
    coord.hass = mock_hass

    await coord._async_handle_irrigation(_NO_RAIN)

    mock_hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_irrigation_category_noop(hass: HomeAssistant) -> None:
    """For non-irrigation category, irrigation properties stay None/False."""
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Car",
        data={
            CONF_NAME: "Car",
            CONF_WEATHER_ENTITIES: ["weather.home"],
            CONF_CATEGORY: "car",
        },
        options={},
        entry_id="car_entry",
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)

    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.measured_rain_mm is None
    assert coord.irrigation_suppressed is False


@pytest.mark.asyncio
async def test_coordinator_gauge_threshold_from_options(hass: HomeAssistant) -> None:
    """Threshold in options overrides data value."""
    entry = _make_irrigation_entry(
        gauge_entity="sensor.rain",
        gauge_threshold=5.0,
        options={CONF_RAIN_GAUGE_THRESHOLD_MM: 10.0},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "7.0")
    coord = WashWiseCoordinator(hass, entry)

    # 7.0 < 10.0 threshold from options → not suppressed by gauge.
    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.irrigation_suppressed is False

    hass.states.async_set("sensor.rain", "12.0")
    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.irrigation_suppressed is True
    await coord.async_shutdown()


@pytest.mark.asyncio
async def test_coordinator_gauge_unsub_on_shutdown(hass: HomeAssistant) -> None:
    """Gauge state listener is unsubscribed on shutdown."""
    entry = _make_irrigation_entry(gauge_entity="sensor.rain")
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "0.0")
    coord = WashWiseCoordinator(hass, entry)
    assert coord._unsub_gauge is not None
    await coord.async_shutdown()
    assert coord._unsub_gauge is None


@pytest.mark.asyncio
async def test_coordinator_no_gauge_no_unsub(hass: HomeAssistant) -> None:
    """No gauge entity configured → _unsub_gauge stays None."""
    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    assert coord._unsub_gauge is None


@pytest.mark.asyncio
async def test_coordinator_gauge_state_change_triggers_refresh(hass: HomeAssistant) -> None:
    """Changing gauge entity state fires immediate coordinator refresh."""
    entry = _make_irrigation_entry(gauge_entity="sensor.rain")
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "0.0")
    coord = WashWiseCoordinator(hass, entry)

    refresh_count = 0

    async def _counting_refresh():
        nonlocal refresh_count
        refresh_count += 1

    coord.async_request_refresh = _counting_refresh

    # Simulate state change event on the gauge entity.
    hass.states.async_set("sensor.rain", "7.5")
    await hass.async_block_till_done()

    assert refresh_count >= 1
    await coord.async_shutdown()


# ---------------------------------------------------------------------------
# IrrigationSuppressedBinarySensor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_irrigation_suppressed_sensor_registered(hass: HomeAssistant) -> None:
    """IrrigationSuppressedBinarySensor created for garden_irrigation entries."""
    from custom_components.washwise.binary_sensor import (
        async_setup_entry,
    )

    entry = _make_irrigation_entry()
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.data = _NO_RAIN
    coord.irrigation_suppressed = False
    coord.measured_rain_mm = None
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added: list = []
    await async_setup_entry(hass, entry, lambda entities, **_: added.extend(entities))

    types = [type(e).__name__ for e in added]
    assert "IrrigationSuppressedBinarySensor" in types


@pytest.mark.asyncio
async def test_irrigation_suppressed_sensor_is_on(hass: HomeAssistant) -> None:
    """Sensor reflects coordinator.irrigation_suppressed."""
    from custom_components.washwise.binary_sensor import IrrigationSuppressedBinarySensor

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.irrigation_suppressed = True
    coord.measured_rain_mm = 8.5
    coord.data = _NO_RAIN
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = IrrigationSuppressedBinarySensor(coord, entry)
    assert sensor.is_on is True

    attrs = sensor.extra_state_attributes
    assert attrs["measured_rain_mm"] == pytest.approx(8.5)
    assert attrs["forecast_blocks"] is False


@pytest.mark.asyncio
async def test_irrigation_suppressed_sensor_is_off(hass: HomeAssistant) -> None:
    """Sensor is off when coordinator says not suppressed."""
    from custom_components.washwise.binary_sensor import IrrigationSuppressedBinarySensor

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.irrigation_suppressed = False
    coord.measured_rain_mm = 1.0
    coord.data = _NO_RAIN
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = IrrigationSuppressedBinarySensor(coord, entry)
    assert sensor.is_on is False


@pytest.mark.asyncio
async def test_irrigation_suppressed_sensor_none_when_no_data(hass: HomeAssistant) -> None:
    """Sensor returns None when coordinator.data is None."""
    from custom_components.washwise.binary_sensor import IrrigationSuppressedBinarySensor

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.irrigation_suppressed = False
    coord.measured_rain_mm = None
    coord.data = None
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = IrrigationSuppressedBinarySensor(coord, entry)
    assert sensor.is_on is None


# ---------------------------------------------------------------------------
# MeasuredRainMmSensor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_measured_rain_sensor_value(hass: HomeAssistant) -> None:
    """MeasuredRainMmSensor reads from coordinator.measured_rain_mm."""
    from custom_components.washwise.sensor import MeasuredRainMmSensor

    entry = _make_irrigation_entry(gauge_threshold=7.5)
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.measured_rain_mm = 4.2
    coord.data = _NO_RAIN
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = MeasuredRainMmSensor(coord, entry)
    assert sensor.native_value == pytest.approx(4.2)
    attrs = sensor.extra_state_attributes
    assert attrs["threshold_mm"] == pytest.approx(7.5)


@pytest.mark.asyncio
async def test_measured_rain_sensor_none(hass: HomeAssistant) -> None:
    """MeasuredRainMmSensor returns None when no gauge data."""
    from custom_components.washwise.sensor import MeasuredRainMmSensor

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.measured_rain_mm = None
    coord.data = None
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = MeasuredRainMmSensor(coord, entry)
    assert sensor.native_value is None


@pytest.mark.asyncio
async def test_measured_rain_sensor_registered(hass: HomeAssistant) -> None:
    """MeasuredRainMmSensor created for garden_irrigation entries."""
    from custom_components.washwise.sensor import async_setup_entry

    entry = _make_irrigation_entry()
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.data = _NO_RAIN
    coord.measured_rain_mm = None
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added: list = []
    await async_setup_entry(hass, entry, lambda entities, **_: added.extend(entities))

    types = [type(e).__name__ for e in added]
    assert "MeasuredRainMmSensor" in types


# ---------------------------------------------------------------------------
# set_irrigation_switch service
# ---------------------------------------------------------------------------


@pytest.fixture
async def irr_service_stub(hass: HomeAssistant):
    entry = _make_irrigation_entry(switch_entity="switch.irrigation")
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.entry = entry
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord
    await async_register_services(hass)
    yield hass, entry.entry_id
    async_unregister_services(hass)


async def test_set_irrigation_switch_off(irr_service_stub) -> None:
    hass, entry_id = irr_service_stub
    hass.states.async_set("switch.irrigation", "on")

    # Register a real switch domain handler so the service call succeeds.
    service_calls: list = []

    async def _switch_handler(call):
        service_calls.append(call.service)

    hass.services.async_register("switch", "turn_off", _switch_handler)
    hass.services.async_register("switch", "turn_on", _switch_handler)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_SET_IRRIGATION_SWITCH,
        {"entry_id": entry_id, "state": "off"},
        blocking=True,
    )

    assert "turn_off" in service_calls


async def test_set_irrigation_switch_no_entity_raises(hass: HomeAssistant) -> None:
    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.entry = entry
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord
    await async_register_services(hass)

    with pytest.raises(HomeAssistantError, match="No irrigation switch entity"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_IRRIGATION_SWITCH,
            {"entry_id": entry.entry_id, "state": "on"},
            blocking=True,
        )

    async_unregister_services(hass)


@pytest.mark.asyncio
async def test_set_irrigation_switch_invalid_entity_id_raises(hass: HomeAssistant) -> None:
    """Service raises HomeAssistantError when switch entity has no '.' separator."""
    from custom_components.washwise.const import CONF_IRRIGATION_SWITCH_ENTITY

    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Garden",
        data={
            CONF_NAME: "Garden",
            CONF_WEATHER_ENTITIES: ["weather.home"],
            CONF_CATEGORY: "garden_irrigation",
            CONF_IRRIGATION_SWITCH_ENTITY: "no_dot_entity",
        },
        options={},
        entry_id="irr_bad_entity",
    )
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.entry = entry
    hass.data.setdefault(DOMAIN, {})["irr_bad_entity"] = coord
    await async_register_services(hass)

    with pytest.raises(HomeAssistantError, match="Invalid irrigation switch entity ID"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_IRRIGATION_SWITCH,
            {"entry_id": "irr_bad_entity", "state": "on"},
            blocking=True,
        )

    async_unregister_services(hass)


async def test_set_irrigation_switch_service_registered(irr_service_stub) -> None:
    hass, _ = irr_service_stub
    assert hass.services.has_service(DOMAIN, SERVICE_SET_IRRIGATION_SWITCH)


# ---------------------------------------------------------------------------
# config_flow: async_step_irrigation
# ---------------------------------------------------------------------------


async def test_config_flow_irrigation_step(hass: HomeAssistant) -> None:
    """Selecting garden_irrigation routes to the irrigation step."""
    from homeassistant.data_entry_flow import FlowResultType

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == FlowResultType.FORM

    hass.states.async_set("weather.home", "sunny", {"temperature": 20, "forecast": []})
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "weather_entities": ["weather.home"],
            "name": "Garden",
            "category": "garden_irrigation",
            "customize_thresholds": False,
        },
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "irrigation"


async def test_config_flow_irrigation_step_creates_entry(hass: HomeAssistant) -> None:
    """Completing the irrigation step creates the config entry."""
    from homeassistant.data_entry_flow import FlowResultType

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    hass.states.async_set("weather.home", "sunny", {"temperature": 20, "forecast": []})
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "weather_entities": ["weather.home"],
            "name": "Garden",
            "category": "garden_irrigation",
            "customize_thresholds": False,
        },
    )
    assert result2["step_id"] == "irrigation"

    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        user_input={
            CONF_RAIN_GAUGE_THRESHOLD_MM: 5.0,
        },
    )
    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_CATEGORY] == "garden_irrigation"


async def test_config_flow_irrigation_step_customize_goes_to_thresholds(
    hass: HomeAssistant,
) -> None:
    """garden_irrigation with customize_thresholds=True goes thresholds → irrigation → entry."""
    from homeassistant.data_entry_flow import FlowResultType

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    hass.states.async_set("weather.home", "sunny", {"temperature": 20, "forecast": []})
    result2 = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        user_input={
            "weather_entities": ["weather.home"],
            "name": "Garden",
            "category": "garden_irrigation",
            "customize_thresholds": True,
        },
    )
    # customize_thresholds=True → thresholds step first.
    assert result2["step_id"] == "thresholds"

    # After thresholds → irrigation step (not create_entry).
    result3 = await hass.config_entries.flow.async_configure(
        result2["flow_id"],
        user_input={
            "days": 1,
            "precip_threshold_mm": 2.0,
            "freeze_check": False,
            "forecast_type": "daily",
            "bad_conditions": [],
            "precip_weight": 40,
            "freeze_weight": 30,
            "condition_weight": 30,
        },
    )
    assert result3["step_id"] == "irrigation"

    # Complete irrigation step → create entry.
    result4 = await hass.config_entries.flow.async_configure(
        result3["flow_id"],
        user_input={CONF_RAIN_GAUGE_THRESHOLD_MM: 5.0},
    )
    assert result4["type"] == FlowResultType.CREATE_ENTRY


# ---------------------------------------------------------------------------
# options_flow: irrigation step visible for garden_irrigation
# ---------------------------------------------------------------------------


async def test_options_flow_irrigation_menu_item(hass: HomeAssistant) -> None:
    """Options flow init menu includes 'irrigation' for garden_irrigation category."""
    from homeassistant.data_entry_flow import FlowResultType

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert "irrigation" in result["menu_options"]


async def test_options_flow_irrigation_step_saves(hass: HomeAssistant) -> None:
    """Completing the options irrigation step saves the threshold."""
    from homeassistant.data_entry_flow import FlowResultType

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "irrigation"}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "irrigation"

    result3 = await hass.config_entries.options.async_configure(
        result2["flow_id"],
        user_input={
            CONF_RAIN_GAUGE_THRESHOLD_MM: 8.0,
        },
    )
    assert result3["type"] == FlowResultType.CREATE_ENTRY
    assert result3["data"][CONF_RAIN_GAUGE_THRESHOLD_MM] == pytest.approx(8.0)


# ---------------------------------------------------------------------------
# RainGaugeThresholdSensor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rain_gauge_threshold_sensor_value(hass: HomeAssistant) -> None:
    """RainGaugeThresholdSensor exposes coordinator.rain_gauge_threshold_mm."""
    from custom_components.washwise.sensor import RainGaugeThresholdSensor

    entry = _make_irrigation_entry(gauge_threshold=7.0)
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.rain_gauge_threshold_mm = 7.0
    coord.data = _NO_RAIN
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = RainGaugeThresholdSensor(coord, entry)
    assert sensor.native_value == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_rain_gauge_threshold_sensor_none(hass: HomeAssistant) -> None:
    """RainGaugeThresholdSensor returns None when not irrigation category."""
    from custom_components.washwise.sensor import RainGaugeThresholdSensor

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.rain_gauge_threshold_mm = None
    coord.data = None
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = RainGaugeThresholdSensor(coord, entry)
    assert sensor.native_value is None


@pytest.mark.asyncio
async def test_rain_gauge_threshold_sensor_registered(hass: HomeAssistant) -> None:
    """RainGaugeThresholdSensor registered for garden_irrigation entries."""
    from custom_components.washwise.sensor import async_setup_entry

    entry = _make_irrigation_entry()
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.data = _NO_RAIN
    coord.rain_gauge_threshold_mm = 5.0
    coord.measured_rain_mm = None
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added: list = []
    await async_setup_entry(hass, entry, lambda entities, **_: added.extend(entities))

    types = [type(e).__name__ for e in added]
    assert "RainGaugeThresholdSensor" in types


# ---------------------------------------------------------------------------
# ForecastBlocksIrrigationBinarySensor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_forecast_blocks_irrigation_on(hass: HomeAssistant) -> None:
    """ForecastBlocksIrrigationBinarySensor ON when rain expected."""
    from custom_components.washwise.binary_sensor import ForecastBlocksIrrigationBinarySensor

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.forecast_blocks_irrigation = True
    coord.data = _RAIN_EXPECTED
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = ForecastBlocksIrrigationBinarySensor(coord, entry)
    assert sensor.is_on is True


@pytest.mark.asyncio
async def test_forecast_blocks_irrigation_off(hass: HomeAssistant) -> None:
    """ForecastBlocksIrrigationBinarySensor OFF when no rain forecast."""
    from custom_components.washwise.binary_sensor import ForecastBlocksIrrigationBinarySensor

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.forecast_blocks_irrigation = False
    coord.data = _NO_RAIN
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = ForecastBlocksIrrigationBinarySensor(coord, entry)
    assert sensor.is_on is False


@pytest.mark.asyncio
async def test_forecast_blocks_irrigation_none_when_no_data(hass: HomeAssistant) -> None:
    """ForecastBlocksIrrigationBinarySensor returns None when coordinator has no data."""
    from custom_components.washwise.binary_sensor import ForecastBlocksIrrigationBinarySensor

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.forecast_blocks_irrigation = False
    coord.data = None
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = ForecastBlocksIrrigationBinarySensor(coord, entry)
    assert sensor.is_on is None


# ---------------------------------------------------------------------------
# IrrigationSwitchStateBinarySensor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_irrigation_switch_state_on(hass: HomeAssistant) -> None:
    """IrrigationSwitchStateBinarySensor ON when switch is on."""
    from custom_components.washwise.binary_sensor import IrrigationSwitchStateBinarySensor

    entry = _make_irrigation_entry(switch_entity="switch.irrigation")
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.irrigation_switch_state = True
    coord.data = _NO_RAIN
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = IrrigationSwitchStateBinarySensor(coord, entry)
    assert sensor.is_on is True
    assert sensor.extra_state_attributes["switch_entity_id"] == "switch.irrigation"


@pytest.mark.asyncio
async def test_irrigation_switch_state_off(hass: HomeAssistant) -> None:
    """IrrigationSwitchStateBinarySensor OFF when switch is off."""
    from custom_components.washwise.binary_sensor import IrrigationSwitchStateBinarySensor

    entry = _make_irrigation_entry(switch_entity="switch.irrigation")
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.irrigation_switch_state = False
    coord.data = _NO_RAIN
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = IrrigationSwitchStateBinarySensor(coord, entry)
    assert sensor.is_on is False


@pytest.mark.asyncio
async def test_irrigation_switch_state_none_when_not_configured(hass: HomeAssistant) -> None:
    """IrrigationSwitchStateBinarySensor returns None when no switch configured."""
    from custom_components.washwise.binary_sensor import IrrigationSwitchStateBinarySensor

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.irrigation_switch_state = None
    coord.data = _NO_RAIN
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)

    sensor = IrrigationSwitchStateBinarySensor(coord, entry)
    assert sensor.is_on is None


@pytest.mark.asyncio
async def test_new_diagnostic_sensors_registered(hass: HomeAssistant) -> None:
    """ForecastBlocksIrrigationBinarySensor and IrrigationSwitchStateBinarySensor registered."""
    from custom_components.washwise.binary_sensor import (
        async_setup_entry,
    )

    entry = _make_irrigation_entry()
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.data = _NO_RAIN
    coord.irrigation_suppressed = False
    coord.measured_rain_mm = None
    coord.config_entry = entry
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    added: list = []
    await async_setup_entry(hass, entry, lambda entities, **_: added.extend(entities))

    types = [type(e).__name__ for e in added]
    assert "ForecastBlocksIrrigationBinarySensor" in types
    assert "IrrigationSwitchStateBinarySensor" in types


# ---------------------------------------------------------------------------
# coordinator new properties
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_coordinator_new_properties_non_irrigation(hass: HomeAssistant) -> None:
    """New coordinator properties reset for non-irrigation categories."""
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Car",
        data={CONF_NAME: "Car", CONF_WEATHER_ENTITIES: ["weather.home"], CONF_CATEGORY: "car"},
        options={},
        entry_id="car2",
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.rain_gauge_threshold_mm is None
    assert coord.forecast_blocks_irrigation is False


@pytest.mark.asyncio
async def test_coordinator_rain_gauge_threshold_exposed(hass: HomeAssistant) -> None:
    """Coordinator exposes rain_gauge_threshold_mm after irrigation handle."""
    entry = _make_irrigation_entry(gauge_threshold=9.0)
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.rain_gauge_threshold_mm == pytest.approx(9.0)


@pytest.mark.asyncio
async def test_coordinator_forecast_blocks_irrigation(hass: HomeAssistant) -> None:
    """Coordinator forecast_blocks_irrigation=True when can_wash=True (rain expected)."""
    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    await coord._async_handle_irrigation(_RAIN_EXPECTED)
    assert coord.forecast_blocks_irrigation is True
    await coord._async_handle_irrigation(_NO_RAIN)
    assert coord.forecast_blocks_irrigation is False


@pytest.mark.asyncio
async def test_coordinator_irrigation_switch_state_on(hass: HomeAssistant) -> None:
    """irrigation_switch_state returns True when switch entity is on."""
    entry = _make_irrigation_entry(switch_entity="switch.irr")
    entry.add_to_hass(hass)
    hass.states.async_set("switch.irr", "on")
    coord = WashWiseCoordinator(hass, entry)
    assert coord.irrigation_switch_state is True


@pytest.mark.asyncio
async def test_coordinator_irrigation_switch_state_off(hass: HomeAssistant) -> None:
    """irrigation_switch_state returns False when switch entity is off."""
    entry = _make_irrigation_entry(switch_entity="switch.irr")
    entry.add_to_hass(hass)
    hass.states.async_set("switch.irr", "off")
    coord = WashWiseCoordinator(hass, entry)
    assert coord.irrigation_switch_state is False


@pytest.mark.asyncio
async def test_coordinator_irrigation_switch_state_none_unavailable(hass: HomeAssistant) -> None:
    """irrigation_switch_state returns None when switch is unavailable."""
    entry = _make_irrigation_entry(switch_entity="switch.irr")
    entry.add_to_hass(hass)
    hass.states.async_set("switch.irr", "unavailable")
    coord = WashWiseCoordinator(hass, entry)
    assert coord.irrigation_switch_state is None


@pytest.mark.asyncio
async def test_coordinator_irrigation_switch_state_none_no_entity(hass: HomeAssistant) -> None:
    """irrigation_switch_state returns None when no switch entity configured."""
    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    assert coord.irrigation_switch_state is None


# ---------------------------------------------------------------------------
# Reconfigure routing — garden_irrigation must pass through irrigation step
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconfigure_garden_irrigation_routes_to_irrigation_step(
    hass: HomeAssistant,
) -> None:
    """Reconfigure with category=garden_irrigation shows the irrigation step."""
    from unittest.mock import patch

    from homeassistant.data_entry_flow import FlowResultType

    entry = _make_irrigation_entry()
    entry.add_to_hass(hass)

    with (
        patch("custom_components.washwise.async_setup_entry", return_value=True),
        patch("custom_components.washwise.async_unload_entry", return_value=True),
    ):
        result = await entry.start_reconfigure_flow(hass)
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_WEATHER_ENTITIES: ["weather.home"],
                CONF_NAME: "Garden",
                CONF_CATEGORY: "garden_irrigation",
                "customize_thresholds": False,
            },
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "irrigation"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_RAIN_GAUGE_ENTITY: "sensor.rain",
                CONF_RAIN_GAUGE_THRESHOLD_MM: 5.0,
                CONF_IRRIGATION_SWITCH_ENTITY: "input_boolean.irr",
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_RAIN_GAUGE_ENTITY] == "sensor.rain"
    assert entry.data[CONF_IRRIGATION_SWITCH_ENTITY] == "input_boolean.irr"


# ---------------------------------------------------------------------------
# Unavailable switch guard — no service call when switch is unavailable/unknown
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_irrigation_skips_switch_call_when_unavailable(
    hass: HomeAssistant,
) -> None:
    """No turn_on/turn_off call when irrigation switch state is unavailable."""
    from unittest.mock import AsyncMock, patch

    entry = _make_irrigation_entry(switch_entity="switch.irr")
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "2.0")
    hass.states.async_set("switch.irr", "unavailable")
    coord = WashWiseCoordinator(hass, entry)

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await coord._async_handle_irrigation(_NO_RAIN)
        mock_call.assert_not_called()
    await coord.async_shutdown()


# ---------------------------------------------------------------------------
# options flow irrigation step pre-populates existing entity defaults (config_flow.py:640)
# ---------------------------------------------------------------------------


async def test_options_flow_irrigation_step_shows_existing_defaults(
    hass: HomeAssistant,
) -> None:
    """Options flow irrigation step schema pre-populates entity fields from current config."""
    from homeassistant.data_entry_flow import FlowResultType

    entry = _make_irrigation_entry(
        gauge_entity="sensor.my_rain",
        gauge_threshold=3.0,
        switch_entity="switch.my_irrigation",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result2 = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={"next_step_id": "irrigation"}
    )
    assert result2["type"] == FlowResultType.FORM
    assert result2["step_id"] == "irrigation"

    schema = result2["data_schema"].schema
    defaults: dict[str, object] = {}
    for marker in schema:
        defaults[str(marker)] = marker.default() if callable(marker.default) else marker.default

    assert defaults[CONF_RAIN_GAUGE_ENTITY] == "sensor.my_rain"
    assert defaults[CONF_RAIN_GAUGE_THRESHOLD_MM] == 3.0
    assert defaults[CONF_IRRIGATION_SWITCH_ENTITY] == "switch.my_irrigation"


@pytest.mark.asyncio
async def test_irrigation_skips_switch_call_when_unknown(
    hass: HomeAssistant,
) -> None:
    """No turn_on/turn_off call when irrigation switch state is unknown."""
    from unittest.mock import AsyncMock, patch

    entry = _make_irrigation_entry(switch_entity="switch.irr")
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.rain", "2.0")
    hass.states.async_set("switch.irr", "unknown")
    coord = WashWiseCoordinator(hass, entry)

    with patch(
        "homeassistant.core.ServiceRegistry.async_call", new_callable=AsyncMock
    ) as mock_call:
        await coord._async_handle_irrigation(_NO_RAIN)
        mock_call.assert_not_called()
    await coord.async_shutdown()
