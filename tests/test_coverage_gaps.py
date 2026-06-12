"""Targeted tests to hit residual uncovered branches.

Each test maps to one or more specific missing lines reported by
``pytest --cov-report=term-missing``. Pure unit-level — no integration
plumbing needed beyond what already exists.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise import (
    _CARD_INSTALLED_KEY,
    _async_install_card,
)
from custom_components.washwise.binary_sensor import (
    WashWiseDayOkBinarySensor,
    _resolve_thresholds,
)
from custom_components.washwise.config_flow import _category_label
from custom_components.washwise.const import (
    CONF_BAD_CONDITIONS,
    CONF_CATEGORY,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_DAYS,
    DEFAULT_CATEGORY,
    DOMAIN,
)
from custom_components.washwise.decision import (
    REASON_BAD_CONDITION,
    REASON_SNOW,
    _reason_from_blockers,
    compute,
)
from custom_components.washwise.models import (
    Decision,
    _iso_or_none,
    _parse_date,
    _parse_datetime,
)
from custom_components.washwise.storage import _parse_ts

# ---------------------------------------------------------------------------
# models.py — small helpers (lines 23, 29, 31, 33, 42)
# ---------------------------------------------------------------------------


def test_iso_or_none_returns_string_passthrough() -> None:
    """Non-date/datetime, non-None values fall through to ``str(value)`` (line 23)."""
    assert _iso_or_none("2026-06-11") == "2026-06-11"
    assert _iso_or_none(42) == "42"
    assert _iso_or_none(None) is None  # line 12: None short-circuit


def test_parse_date_handles_empty_string_and_none() -> None:
    """Empty/None inputs short-circuit to None (line 29)."""
    assert _parse_date("") is None
    assert _parse_date(None) is None


def test_parse_date_passes_through_pure_date() -> None:
    """A bare ``date`` is returned unchanged (line 31)."""
    d = date(2026, 6, 11)
    assert _parse_date(d) is d


def test_parse_date_extracts_date_from_datetime() -> None:
    """A ``datetime`` collapses to its ``.date()`` (line 33)."""
    dt = datetime(2026, 6, 11, 9, 30, tzinfo=UTC)
    assert _parse_date(dt) == date(2026, 6, 11)


def test_parse_datetime_passes_through_existing_datetime() -> None:
    """A bare ``datetime`` is returned unchanged (line 42)."""
    dt = datetime(2026, 6, 11, 9, 30, tzinfo=UTC)
    assert _parse_datetime(dt) is dt


def test_parse_datetime_handles_empty_string_and_none() -> None:
    """Empty/None inputs short-circuit to None (covers the early-return)."""
    assert _parse_datetime("") is None
    assert _parse_datetime(None) is None


def test_parse_datetime_parses_iso_string() -> None:
    """ISO string is parsed to datetime (line 33)."""
    result = _parse_datetime("2026-06-11T09:30:00")
    assert result is not None
    assert result.year == 2026


# ---------------------------------------------------------------------------
# storage.py — line 45 (``if not value: return None``)
# ---------------------------------------------------------------------------


def test_parse_ts_returns_none_for_empty_string_and_none() -> None:
    """Falsy inputs short-circuit (line 45)."""
    assert _parse_ts("") is None
    assert _parse_ts(None) is None


def test_parse_ts_returns_none_for_invalid_iso() -> None:
    """Invalid ISO strings are swallowed and return None."""
    assert _parse_ts("not-a-timestamp") is None


# ---------------------------------------------------------------------------
# config_flow.py — line 47 (``_category_label``)
# ---------------------------------------------------------------------------


def test_category_label_titlecases_and_replaces_underscores() -> None:
    """``_category_label`` replaces underscores and title-cases (line 47)."""
    assert _category_label("car_paint") == "Car Paint"
    assert _category_label("solar") == "Solar"


# ---------------------------------------------------------------------------
# services.py — snooze branches
# ---------------------------------------------------------------------------


async def test_snooze_service_calls_coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """snooze service forwards hours as timedelta to coordinator."""
    from custom_components.washwise.coordinator import WashWiseCoordinator
    from custom_components.washwise.services import (
        ATTR_ENTRY_ID,
        ATTR_HOURS,
        SERVICE_SNOOZE,
        async_register_services,
        async_unregister_services,
    )

    mock_config_entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.entry = mock_config_entry
    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coord

    await async_register_services(hass)
    try:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SNOOZE,
            {ATTR_ENTRY_ID: mock_config_entry.entry_id, ATTR_HOURS: 6},
            blocking=True,
        )
        coord.async_snooze.assert_awaited_once()
        args, _ = coord.async_snooze.call_args
        from datetime import timedelta

        assert args[0] == timedelta(hours=6)
    finally:
        async_unregister_services(hass)


async def test_clear_snooze_service_calls_coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """clear_snooze service forwards to async_clear_snooze."""
    from custom_components.washwise.coordinator import WashWiseCoordinator
    from custom_components.washwise.services import (
        ATTR_ENTRY_ID,
        SERVICE_CLEAR_SNOOZE,
        async_register_services,
        async_unregister_services,
    )

    mock_config_entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.entry = mock_config_entry
    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coord

    await async_register_services(hass)
    try:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CLEAR_SNOOZE,
            {ATTR_ENTRY_ID: mock_config_entry.entry_id},
            blocking=True,
        )
        coord.async_clear_snooze.assert_awaited_once()
    finally:
        async_unregister_services(hass)


# ---------------------------------------------------------------------------
# decision.py — _reason_from_blockers snow/default + carry-forward tmax
# ---------------------------------------------------------------------------


def test_reason_from_blockers_snowy_returns_snow() -> None:
    """A snowy condition with no recognised blocker tag → REASON_SNOW (line 391)."""
    assert _reason_from_blockers([], "snowy") == REASON_SNOW
    assert _reason_from_blockers([], "snowy-rainy") == REASON_SNOW


def test_reason_from_blockers_unknown_falls_through_to_bad_condition() -> None:
    """Unknown blockers + non-rain/snow condition → REASON_BAD_CONDITION (line 394)."""
    # No recognised tags, condition isn't snow/rain/pouring.
    assert _reason_from_blockers(["mystery"], "fog") == REASON_BAD_CONDITION
    assert _reason_from_blockers([], None) == REASON_BAD_CONDITION


def test_compute_carries_temp_check_via_tmax_when_tmin_missing() -> None:
    """Day with tmin=None but tmax present propagates tmax forward (lines 261-262)."""
    from custom_components.washwise.models import CurrentWeather, ForecastDay

    today = date.today()
    days = [
        # Day 0: tmin only, sets temp_check to a sub-zero value.
        ForecastDay(
            date=today,
            condition="sunny",
            precipitation_mm=0.0,
            temp_min_c=-5.0,
            temp_max_c=5.0,
            raw={},
        ),
        # Day 1: tmin missing → fallback to tmax via the elif branch.
        ForecastDay(
            date=date.fromordinal(today.toordinal() + 1),
            condition="sunny",
            precipitation_mm=0.0,
            temp_min_c=None,
            temp_max_c=8.0,
            raw={},
        ),
        # Day 2: triggers freeze comparison using carried temp_check==8.0,
        # so >=0 — no freeze. Just exercises the branch reachability.
        ForecastDay(
            date=date.fromordinal(today.toordinal() + 2),
            condition="sunny",
            precipitation_mm=0.0,
            temp_min_c=2.0,
            temp_max_c=10.0,
            raw={},
        ),
    ]
    decision = compute(
        current=CurrentWeather(condition="sunny", temperature_c=10.0, raw={}),
        forecast=days,
        thresholds={
            "days": 3,
            "precip_threshold_mm": 0.2,
            "freeze_check": True,
            "precip_weight": 30.0,
            "freeze_weight": 30.0,
            "condition_weight": 30.0,
        },
        invert=False,
        now=datetime.now(UTC),
    )
    assert decision.days_analyzed == 3


# ---------------------------------------------------------------------------
# binary_sensor.py — bad_override branch (line 85) + day row missing fields (233)
# ---------------------------------------------------------------------------


def test_resolve_thresholds_uses_bad_conditions_override() -> None:
    """Customised thresholds + bad-conditions override → list copied in (line 85)."""
    entry = MagicMock(spec=ConfigEntry)
    entry.data = {CONF_CATEGORY: DEFAULT_CATEGORY}
    entry.options = {
        CONF_CUSTOMIZE_THRESHOLDS: True,
        CONF_DAYS: 2,
        CONF_BAD_CONDITIONS: ["snowy", "pouring"],
    }
    thresholds = _resolve_thresholds(entry)
    assert thresholds["bad_conditions"] == ["snowy", "pouring"]


def test_day_ok_returns_none_when_row_lacks_blocked_and_can_wash() -> None:
    """Forecast row with neither ``blocked`` nor ``can_wash`` → None (line 233)."""
    entry = MagicMock(spec=ConfigEntry)
    entry.entry_id = "cov_entry"
    entry.title = "Cov"
    entry.data = {CONF_CATEGORY: DEFAULT_CATEGORY}

    coord = MagicMock()
    coord.data = Decision(
        can_wash=True,
        score=80,
        reason="clear",
        days_until_wash=0,
        # Row dict with no blocked / no can_wash keys.
        forecast_summary=[{"date": "2026-06-11", "extra": "noise"}],
        blocking_days=[],
        days_analyzed=1,
    )
    coord.config_entry = entry
    coord.last_update_success = True

    sensor = WashWiseDayOkBinarySensor.__new__(WashWiseDayOkBinarySensor)
    sensor._day_index = 0
    sensor.coordinator = coord  # bypass CoordinatorEntity init
    assert sensor.is_on is None


# ---------------------------------------------------------------------------
# __init__.py — frontend fallback paths (lines 110-115, 122-123)
# ---------------------------------------------------------------------------


def _patch_card_path(exists: bool = True):
    """Patch Path so the card source file appears to exist."""
    fake_source = MagicMock()
    fake_source.exists.return_value = exists
    fake_source.__str__ = lambda self: "/fake/path/washwise-card.js"
    chain = MagicMock()
    chain.__truediv__.return_value.__truediv__.return_value = fake_source
    chain_root = MagicMock()
    chain_root.parent = chain
    return patch("custom_components.washwise.Path", return_value=chain_root)


async def test_install_card_registers_static_path(hass: HomeAssistant) -> None:
    """_async_install_card registers the static path and Lovelace resource."""
    hass.data.setdefault(DOMAIN, {})

    fake_http = MagicMock()
    fake_http.async_register_static_paths = AsyncMock()
    hass.http = fake_http

    fake_resources = MagicMock()
    fake_resources.loaded = True
    fake_resources.async_items = MagicMock(return_value=[])
    fake_resources.async_create_item = AsyncMock()

    hass.data["lovelace"] = MagicMock(resources=fake_resources)

    with (
        _patch_card_path(exists=True),
        patch("custom_components.washwise._get_version", return_value="0.1.0"),
    ):
        await _async_install_card(hass)

    fake_http.async_register_static_paths.assert_called_once()
    fake_resources.async_create_item.assert_called_once()
    assert hass.data[DOMAIN][_CARD_INSTALLED_KEY] is True


async def test_install_card_idempotent(hass: HomeAssistant) -> None:
    """Second call to _async_install_card is a no-op (flag already set)."""
    hass.data.setdefault(DOMAIN, {})[_CARD_INSTALLED_KEY] = True

    fake_http = MagicMock()
    fake_http.async_register_static_paths = AsyncMock()
    hass.http = fake_http

    await _async_install_card(hass)

    fake_http.async_register_static_paths.assert_not_called()


async def test_install_card_warns_when_js_missing(hass: HomeAssistant, caplog) -> None:
    """Missing card JS logs a warning and does not set the flag."""
    import logging

    hass.data.setdefault(DOMAIN, {})

    with (
        _patch_card_path(exists=False),
        caplog.at_level(logging.WARNING, logger="custom_components.washwise"),
    ):
        await _async_install_card(hass)

    assert _CARD_INSTALLED_KEY not in hass.data.get(DOMAIN, {})
    assert any("not found" in r.message.lower() for r in caplog.records)


async def test_install_card_swallows_static_path_error(hass: HomeAssistant) -> None:
    """async_register_static_paths raising is swallowed (already registered)."""
    hass.data.setdefault(DOMAIN, {})

    fake_http = MagicMock()
    fake_http.async_register_static_paths = AsyncMock(side_effect=RuntimeError("already"))
    hass.http = fake_http

    fake_resources = MagicMock()
    fake_resources.loaded = True
    fake_resources.async_items = MagicMock(return_value=[])
    fake_resources.async_create_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=fake_resources)

    with (
        _patch_card_path(exists=True),
        patch("custom_components.washwise._get_version", return_value="0.1.0"),
    ):
        await _async_install_card(hass)

    assert hass.data[DOMAIN][_CARD_INSTALLED_KEY] is True

    assert hass.data[DOMAIN][_CARD_INSTALLED_KEY] is True


# ---------------------------------------------------------------------------
# Customize fields stored in entry.data (initial config-flow path)
# ---------------------------------------------------------------------------


def test_resolve_thresholds_reads_customize_fields_from_data() -> None:
    """``_resolve_thresholds`` falls back to ``entry.data`` for customize keys.

    The initial config-flow ``thresholds`` step writes the user's customizations
    into ``entry.data``, not ``entry.options``. Sensors and the coordinator must
    honour those values until the user opens the options flow.
    """
    from custom_components.washwise.const import (
        CONF_BAD_CONDITIONS,
        CONF_CONDITION_WEIGHT,
        CONF_DAYS,
        CONF_FREEZE_CHECK,
        CONF_FREEZE_WEIGHT,
        CONF_PRECIP_THRESHOLD,
        CONF_PRECIP_WEIGHT,
    )

    entry = MagicMock(spec=ConfigEntry)
    entry.data = {
        CONF_CATEGORY: "car",
        CONF_CUSTOMIZE_THRESHOLDS: True,
        CONF_DAYS: 5,
        CONF_PRECIP_THRESHOLD: 0.7,
        CONF_FREEZE_CHECK: False,
        CONF_PRECIP_WEIGHT: 50,
        CONF_FREEZE_WEIGHT: 25,
        CONF_CONDITION_WEIGHT: 25,
        CONF_BAD_CONDITIONS: ["pouring", "hail"],
    }
    entry.options = {}

    thresholds = _resolve_thresholds(entry)
    assert thresholds["days"] == 5
    assert thresholds["precip_threshold_mm"] == 0.7
    assert thresholds["freeze_check"] is False
    assert thresholds["precip_weight"] == 50.0
    assert thresholds["freeze_weight"] == 25.0
    assert thresholds["condition_weight"] == 25.0
    assert thresholds["bad_conditions"] == ["pouring", "hail"]


def test_sensor_resolve_horizon_reads_days_from_data() -> None:
    """``sensor._resolve_horizon`` reads ``CONF_DAYS`` from ``entry.data``."""
    from custom_components.washwise.const import CONF_DAYS
    from custom_components.washwise.sensor import _resolve_horizon

    entry = MagicMock(spec=ConfigEntry)
    entry.data = {
        CONF_CATEGORY: "car",
        CONF_CUSTOMIZE_THRESHOLDS: True,
        CONF_DAYS: 6,
    }
    entry.options = {}
    assert _resolve_horizon(entry) == 6


def test_sensor_resolve_horizon_invalid_days_falls_back() -> None:
    """Invalid CONF_DAYS in entry.data falls back to preset days."""
    from custom_components.washwise.const import CONF_DAYS
    from custom_components.washwise.sensor import _resolve_horizon

    entry = MagicMock(spec=ConfigEntry)
    entry.data = {
        CONF_CATEGORY: "car",
        CONF_CUSTOMIZE_THRESHOLDS: True,
        CONF_DAYS: "not-a-number",
    }
    entry.options = {}
    # Falls back to preset for car (3).
    assert _resolve_horizon(entry) == 3


async def test_coordinator_resolve_thresholds_uses_data_for_customize(hass) -> None:
    """Coordinator picks customize fields from entry.data when options absent."""
    from custom_components.washwise.const import (
        CONF_BAD_CONDITIONS,
        CONF_CONDITION_WEIGHT,
        CONF_DAYS,
        CONF_FREEZE_CHECK,
        CONF_FREEZE_WEIGHT,
        CONF_PRECIP_THRESHOLD,
        CONF_PRECIP_WEIGHT,
        CONF_WEATHER_ENTITIES,
    )
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_WEATHER_ENTITIES: ["weather.x"],
            CONF_CATEGORY: "car",
            CONF_CUSTOMIZE_THRESHOLDS: True,
            CONF_DAYS: 4,
            CONF_PRECIP_THRESHOLD: 0.9,
            CONF_FREEZE_CHECK: False,
            CONF_PRECIP_WEIGHT: 60,
            CONF_FREEZE_WEIGHT: 20,
            CONF_CONDITION_WEIGHT: 20,
            CONF_BAD_CONDITIONS: ["snowy"],
        },
        options={},
    )
    entry.add_to_hass(hass)

    coord = WashWiseCoordinator(hass, entry)
    thresholds, invert = coord._resolve_thresholds()
    assert thresholds["days"] == 4
    assert thresholds["precip_threshold_mm"] == 0.9
    assert thresholds["freeze_check"] is False
    assert thresholds["precip_weight"] == 60.0
    assert thresholds["freeze_weight"] == 20.0
    assert thresholds["condition_weight"] == 20.0
    assert thresholds["bad_conditions"] == ["snowy"]
    assert invert is False


# ---------------------------------------------------------------------------
# coordinator.py — _resolve_temperature_unit branches (lines 436,439,441,443,446,451)
# ---------------------------------------------------------------------------


async def test_resolve_temperature_unit_non_string_falls_back_to_default(hass) -> None:
    """Non-string choice → DEFAULT_TEMPERATURE_UNIT branch (line 436), then auto path."""
    from custom_components.washwise.const import (
        CONF_TEMPERATURE_UNIT,
        CONF_WEATHER_ENTITIES,
    )
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.x"], CONF_TEMPERATURE_UNIT: 42},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    # 42 is not a string → falls to DEFAULT_TEMPERATURE_UNIT ("auto") path
    # auto then reads hass.config.units.temperature_unit (returns a string or None)
    result = coord._resolve_temperature_unit()
    # Result is either the system unit string or None — just confirm it's not "42" or 42
    assert result != 42
    assert result != "42"


async def test_resolve_temperature_unit_celsius(hass) -> None:
    """Explicit 'celsius' choice returns '°C' (line 439)."""
    from custom_components.washwise.const import (
        CONF_TEMPERATURE_UNIT,
        CONF_WEATHER_ENTITIES,
    )
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.x"], CONF_TEMPERATURE_UNIT: "celsius"},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    assert coord._resolve_temperature_unit() == "°C"


async def test_resolve_temperature_unit_fahrenheit(hass) -> None:
    """Explicit 'fahrenheit' choice returns '°F' (line 441)."""
    from custom_components.washwise.const import (
        CONF_TEMPERATURE_UNIT,
        CONF_WEATHER_ENTITIES,
    )
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.x"], CONF_TEMPERATURE_UNIT: "fahrenheit"},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    assert coord._resolve_temperature_unit() == "°F"


async def test_resolve_temperature_unit_kelvin(hass) -> None:
    """Explicit 'kelvin' choice returns 'K' (line 443)."""
    from custom_components.washwise.const import (
        CONF_TEMPERATURE_UNIT,
        CONF_WEATHER_ENTITIES,
    )
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.x"], CONF_TEMPERATURE_UNIT: "kelvin"},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    assert coord._resolve_temperature_unit() == "K"


async def test_resolve_temperature_unit_unrecognised_returns_none(hass) -> None:
    """Unrecognised string that is not 'auto' returns None (line 446)."""
    from custom_components.washwise.const import (
        CONF_TEMPERATURE_UNIT,
        CONF_WEATHER_ENTITIES,
    )
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.x"], CONF_TEMPERATURE_UNIT: "rankine"},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    assert coord._resolve_temperature_unit() is None


async def test_resolve_temperature_unit_auto_uses_system_unit(hass) -> None:
    """Auto mode with a configured system unit returns it (line 451)."""
    from unittest.mock import MagicMock, patch

    from custom_components.washwise.const import CONF_WEATHER_ENTITIES
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.x"]},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    fake_units = MagicMock()
    fake_units.temperature_unit = "°C"
    with patch.object(hass.config, "units", fake_units):
        assert coord._resolve_temperature_unit() == "°C"


async def test_resolve_temperature_unit_auto_no_system_unit_returns_none(hass) -> None:
    """Auto mode with no usable system unit returns None (line 451)."""
    from unittest.mock import MagicMock, patch

    from custom_components.washwise.const import CONF_WEATHER_ENTITIES
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.x"]},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    fake_units = MagicMock()
    fake_units.temperature_unit = None
    with patch.object(hass.config, "units", fake_units):
        assert coord._resolve_temperature_unit() is None


async def test_handle_state_change_no_entity_id_returns_early(hass) -> None:
    """Event with no entity_id → early return (line 471)."""
    from custom_components.washwise.const import CONF_WEATHER_ENTITIES
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.primary"]},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)

    event = MagicMock()
    event.data = {}  # no entity_id key
    # Must not raise and must not call async_create_task
    coord._handle_state_change(event)


async def test_handle_state_change_unrelated_entity_returns_early(hass) -> None:
    """Event for entity not in weather_ids → early return (line 474)."""
    from custom_components.washwise.const import CONF_WEATHER_ENTITIES
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.primary"]},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)

    event = MagicMock()
    event.data = {"entity_id": "sensor.something_else"}
    coord._handle_state_change(event)


# ---------------------------------------------------------------------------
# coordinator.py — _handle_state_change cold-start + fallback (lines 485-499)
# ---------------------------------------------------------------------------


async def test_handle_state_change_cold_start_triggers_refresh(hass) -> None:
    """Cold start: active==None + primary entity change → refresh (line 485-487)."""
    from unittest.mock import AsyncMock, patch

    from custom_components.washwise.const import CONF_WEATHER_ENTITIES
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.primary"]},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    coord._active_weather_entity = None  # cold start

    refresh_calls = []

    async def fake_refresh():
        refresh_calls.append(1)

    with patch.object(coord, "async_request_refresh", new=AsyncMock(side_effect=fake_refresh)):
        event = MagicMock()
        event.data = {"entity_id": "weather.primary"}
        coord._handle_state_change(event)
        # Let the event loop run the created task.
        await hass.async_block_till_done()

    assert len(refresh_calls) == 1


async def test_handle_state_change_dead_primary_triggers_refresh(hass) -> None:
    """Fallback: primary dead + non-active secondary state change → refresh (lines 490-499)."""
    from unittest.mock import AsyncMock, patch

    from custom_components.washwise.const import CONF_WEATHER_ENTITIES
    from custom_components.washwise.coordinator import WashWiseCoordinator

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_WEATHER_ENTITIES: ["weather.primary", "weather.backup", "weather.third"]},
        options={},
    )
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    # active is backup; event comes from third — so neither "eid==active" nor "cold start"
    # primary is unavailable → fallback branch fires
    coord._active_weather_entity = "weather.backup"

    hass.states.async_set("weather.primary", "unavailable")

    refresh_calls = []

    async def fake_refresh():
        refresh_calls.append(1)

    with patch.object(coord, "async_request_refresh", new=AsyncMock(side_effect=fake_refresh)):
        event = MagicMock()
        event.data = {"entity_id": "weather.third"}
        coord._handle_state_change(event)
        await hass.async_block_till_done()

    assert len(refresh_calls) >= 1


# ---------------------------------------------------------------------------
# weather_source.py — entity state has temperature_unit attribute (lines 132-134)
# ---------------------------------------------------------------------------


async def test_get_forecast_reads_temperature_unit_from_entity_state(hass) -> None:
    """Entity state carries temperature_unit → it is used as entity_unit (lines 132-134)."""
    from homeassistant.core import ServiceCall
    from homeassistant.helpers.service import SupportsResponse

    from custom_components.washwise import weather_source

    entity_id = "weather.test_unit"
    raw_forecast = [
        {
            "datetime": "2026-06-13T00:00:00+00:00",
            "condition": "sunny",
            "precipitation": 0.0,
            "temperature": 72.0,
            "templow": 59.0,
        }
    ]

    async def _handler(call: ServiceCall):
        return {entity_id: {"forecast": raw_forecast}}

    hass.services.async_register(
        "weather",
        "get_forecasts",
        _handler,
        supports_response=SupportsResponse.ONLY,
    )

    # Plant a state with temperature_unit attribute so lines 132-134 are hit.
    hass.states.async_set(entity_id, "sunny", {"temperature_unit": "°F"})

    result = await weather_source.get_forecast(hass, entity_id, "daily", 1)

    assert len(result) == 1
    # 72°F → ~22.2°C
    assert result[0].temp_max_c is not None
    assert abs(result[0].temp_max_c - 22.2) < 0.5


# ---------------------------------------------------------------------------
# binary_sensor.py — WashWiseFreezeRiskBinarySensor.is_on (lines 277-299)
# ---------------------------------------------------------------------------


def test_freeze_risk_binary_sensor_is_on_below_zero() -> None:
    """``WashWiseFreezeRiskBinarySensor.is_on`` True when temp_min ≤ 0."""
    from custom_components.washwise.binary_sensor import WashWiseFreezeRiskBinarySensor
    from custom_components.washwise.models import Decision

    coord = MagicMock()
    coord.data = Decision(
        can_wash=False,
        score=60,
        reason="freeze",
        days_until_wash=1,
        forecast_summary=[
            {"temp_min": -2.0, "temp_max": 5.0, "blocked": True},
        ],
        blocking_days=[],
        days_analyzed=1,
    )
    coord.last_update_success = True

    entry = MagicMock()
    entry.entry_id = "cov_entry"
    entry.title = "Cov"
    entry.data = {}

    sensor = WashWiseFreezeRiskBinarySensor.__new__(WashWiseFreezeRiskBinarySensor)
    sensor.coordinator = coord
    assert sensor.is_on is True


def test_freeze_risk_binary_sensor_is_on_none_without_decision() -> None:
    """``WashWiseFreezeRiskBinarySensor.is_on`` None when no decision."""
    from custom_components.washwise.binary_sensor import WashWiseFreezeRiskBinarySensor

    coord = MagicMock()
    coord.data = None

    sensor = WashWiseFreezeRiskBinarySensor.__new__(WashWiseFreezeRiskBinarySensor)
    sensor.coordinator = coord
    assert sensor.is_on is None


def test_freeze_risk_binary_sensor_is_on_false_all_above_zero() -> None:
    """``WashWiseFreezeRiskBinarySensor.is_on`` False when all temps above 0."""
    from custom_components.washwise.binary_sensor import WashWiseFreezeRiskBinarySensor
    from custom_components.washwise.models import Decision

    coord = MagicMock()
    coord.data = Decision(
        can_wash=True,
        score=90,
        reason="clear",
        days_until_wash=0,
        forecast_summary=[
            {"temp_min": 5.0, "temp_max": 18.0, "blocked": False},
        ],
        blocking_days=[],
        days_analyzed=1,
    )
    coord.last_update_success = True

    sensor = WashWiseFreezeRiskBinarySensor.__new__(WashWiseFreezeRiskBinarySensor)
    sensor.coordinator = coord
    assert sensor.is_on is False


def test_freeze_risk_binary_sensor_skips_none_temps() -> None:
    """Rows with both temps None are skipped."""
    from custom_components.washwise.binary_sensor import WashWiseFreezeRiskBinarySensor
    from custom_components.washwise.models import Decision

    coord = MagicMock()
    coord.data = Decision(
        can_wash=True,
        score=90,
        reason="clear",
        days_until_wash=0,
        forecast_summary=[
            {"temp_min": None, "temp_max": None, "blocked": False},
            {"temp_min": 8.0, "temp_max": 15.0, "blocked": False},
        ],
        blocking_days=[],
        days_analyzed=2,
    )
    coord.last_update_success = True

    sensor = WashWiseFreezeRiskBinarySensor.__new__(WashWiseFreezeRiskBinarySensor)
    sensor.coordinator = coord
    assert sensor.is_on is False


def test_freeze_risk_binary_sensor_skips_invalid_temps() -> None:
    """Rows with non-numeric temps are skipped without raising."""
    from custom_components.washwise.binary_sensor import WashWiseFreezeRiskBinarySensor
    from custom_components.washwise.models import Decision

    coord = MagicMock()
    coord.data = Decision(
        can_wash=True,
        score=90,
        reason="clear",
        days_until_wash=0,
        forecast_summary=[
            {"temp_min": "bad", "temp_max": "worse", "blocked": False},
            {"temp_min": 5.0, "temp_max": 10.0, "blocked": False},
        ],
        blocking_days=[],
        days_analyzed=2,
    )
    coord.last_update_success = True

    sensor = WashWiseFreezeRiskBinarySensor.__new__(WashWiseFreezeRiskBinarySensor)
    sensor.coordinator = coord
    assert sensor.is_on is False


def test_freeze_risk_binary_sensor_spanning_zero() -> None:
    """``is_on`` True when low<=0<=high branch (line 297-298)."""
    from custom_components.washwise.binary_sensor import WashWiseFreezeRiskBinarySensor
    from custom_components.washwise.models import Decision

    coord = MagicMock()
    coord.data = Decision(
        can_wash=False,
        score=70,
        reason="freeze",
        days_until_wash=1,
        forecast_summary=[
            {"temp_min": -0.5, "temp_max": 1.0, "blocked": True},
        ],
        blocking_days=[],
        days_analyzed=1,
    )
    coord.last_update_success = True

    sensor = WashWiseFreezeRiskBinarySensor.__new__(WashWiseFreezeRiskBinarySensor)
    sensor.coordinator = coord
    assert sensor.is_on is True


# ---------------------------------------------------------------------------
# __init__.py — _get_version + _async_register_lovelace_resource branches
# ---------------------------------------------------------------------------


def test_get_version_reads_manifest() -> None:
    """``_get_version`` reads version from manifest.json (lines 31-33)."""
    from custom_components.washwise import _get_version

    version = _get_version()
    assert isinstance(version, str)
    assert len(version) > 0


async def test_register_lovelace_resource_no_lovelace_data(hass: HomeAssistant) -> None:
    """Missing lovelace data logs info and returns (lines 132-139)."""

    from custom_components.washwise import _async_register_lovelace_resource

    # Ensure "lovelace" key is absent.
    hass.data.pop("lovelace", None)

    await _async_register_lovelace_resource(hass, "0.1.0")
    # Must not raise — no lovelace data present.


async def test_register_lovelace_resource_loads_if_not_loaded(hass: HomeAssistant) -> None:
    """resources.loaded=False → async_load() called (line 142)."""
    from custom_components.washwise import _async_register_lovelace_resource

    fake_resources = MagicMock()
    fake_resources.loaded = False
    fake_resources.async_load = AsyncMock()
    fake_resources.async_items = MagicMock(return_value=[])
    fake_resources.async_create_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=fake_resources)

    await _async_register_lovelace_resource(hass, "0.1.0")

    fake_resources.async_load.assert_called_once()
    fake_resources.async_create_item.assert_called_once()


async def test_register_lovelace_resource_fallback_append(hass: HomeAssistant) -> None:
    """No async_create_item → fallback to resources.data.append (lines 152-155)."""
    from custom_components.washwise import _async_register_lovelace_resource

    fake_resources = MagicMock(spec=[])  # no async_create_item
    fake_resources.loaded = True
    fake_resources.async_items = MagicMock(return_value=[])
    fake_resources.data = MagicMock()
    fake_resources.data.append = MagicMock()
    hass.data["lovelace"] = MagicMock(resources=fake_resources)

    await _async_register_lovelace_resource(hass, "0.1.0")

    fake_resources.data.append.assert_called_once()


async def test_register_lovelace_resource_updates_existing(hass: HomeAssistant) -> None:
    """Existing resource with old URL → update_item called (lines 165-170)."""
    from homeassistant.components.lovelace.resources import ResourceStorageCollection

    from custom_components.washwise import CARD_FILENAME, _async_register_lovelace_resource

    existing = [{"id": "abc", "url": f"/washwise/{CARD_FILENAME}?old-version"}]
    fake_resources = MagicMock(spec=ResourceStorageCollection)
    fake_resources.loaded = True
    fake_resources.async_items = MagicMock(return_value=existing)
    fake_resources.async_update_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=fake_resources)

    await _async_register_lovelace_resource(hass, "0.2.0")

    fake_resources.async_update_item.assert_called_once()


async def test_register_lovelace_resource_removes_duplicates(hass: HomeAssistant) -> None:
    """Multiple existing entries → extras deleted, first updated (lines 159-162)."""
    from homeassistant.components.lovelace.resources import ResourceStorageCollection

    from custom_components.washwise import CARD_FILENAME, _async_register_lovelace_resource

    existing = [
        {"id": "first", "url": f"/washwise/{CARD_FILENAME}?old"},
        {"id": "dup1", "url": f"/washwise/{CARD_FILENAME}?dup"},
    ]
    fake_resources = MagicMock(spec=ResourceStorageCollection)
    fake_resources.loaded = True
    fake_resources.async_items = MagicMock(return_value=existing)
    fake_resources.async_delete_item = AsyncMock()
    fake_resources.async_update_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=fake_resources)

    await _async_register_lovelace_resource(hass, "0.2.0")

    fake_resources.async_delete_item.assert_called_once_with("dup1")
    fake_resources.async_update_item.assert_called_once()


async def test_register_lovelace_resource_non_storage_updates_in_place(
    hass: HomeAssistant,
) -> None:
    """Non-ResourceStorageCollection resource updated in-place (line 172)."""
    from custom_components.washwise import CARD_FILENAME, _async_register_lovelace_resource

    first = {"url": f"/washwise/{CARD_FILENAME}?old"}
    fake_resources = MagicMock()  # not a ResourceStorageCollection
    fake_resources.loaded = True
    fake_resources.async_items = MagicMock(return_value=[first])
    hass.data["lovelace"] = MagicMock(resources=fake_resources)

    await _async_register_lovelace_resource(hass, "0.2.0")

    assert "0.2.0" in first["url"]
