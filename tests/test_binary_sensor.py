"""Tests for the WashWise binary_sensor platform.

Covers:

* ``can_wash`` ``is_on`` returns True / False / None for the three coordinator
  states (success, blocked, no-data).
* The diagnostic attribute payload is populated end-to-end:
  ``forecast_summary``, ``decision_details``, ``days_analyzed``, ``score``,
  ``reason`` and ``active_weather_entity``.
* The number of per-day ``day_<i>_ok`` sensors equals the configured horizon
  (e.g. ``days=3`` produces three).
* Per-day sensors honour the ``blocked``/``can_wash`` field on each forecast
  row and fall back to ``None`` when the forecast summary is shorter than the
  horizon.

These tests instantiate the entity classes directly with a stub coordinator —
no HA event loop required — so they execute fast and stay isolated from the
rest of the integration plumbing.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.washwise.binary_sensor import (
    WashWiseCanWashBinarySensor,
    WashWiseDayOkBinarySensor,
    WashWiseFreezeRiskBinarySensor,
    _resolve_thresholds,
    async_setup_entry,
)
from custom_components.washwise.const import (
    CONF_CATEGORY,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_DAYS,
    DOMAIN,
)
from custom_components.washwise.models import Decision

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_decision(
    *,
    can_wash: bool = True,
    score: int = 87,
    reason: str = "clear",
    days_analyzed: int = 3,
    forecast_summary: list[dict[str, Any]] | None = None,
) -> Decision:
    """Return a ``Decision`` populated with realistic defaults."""
    if forecast_summary is None:
        forecast_summary = [
            {
                "date": date(2026, 6, 11),
                "condition": "sunny",
                "precipitation": 0.0,
                "temp_min": 14.0,
                "temp_max": 24.0,
                "blocked": False,
                "blockers": [],
            },
            {
                "date": date(2026, 6, 12),
                "condition": "rainy",
                "precipitation": 4.5,
                "temp_min": 12.0,
                "temp_max": 19.0,
                "blocked": True,
                "blockers": ["precipitation"],
            },
            {
                "date": date(2026, 6, 13),
                "condition": "partlycloudy",
                "precipitation": 0.1,
                "temp_min": 13.0,
                "temp_max": 22.0,
                "blocked": False,
                "blockers": [],
            },
        ]
    return Decision(
        can_wash=can_wash,
        score=score,
        reason=reason,
        days_until_wash=2,
        blocking_days=[date(2026, 6, 12)],
        forecast_summary=forecast_summary,
        days_analyzed=days_analyzed,
    )


def _make_coordinator(
    decision: Decision | None,
    *,
    active_weather_entity: str | None = "weather.home",
) -> SimpleNamespace:
    """Return a stub coordinator that mimics the bits the entities read.

    ``CoordinatorEntity`` only touches a handful of attributes during init
    (mainly ``last_update_success`` and ``async_add_listener``). The
    SimpleNamespace satisfies those without needing the full HA wiring.
    """
    return SimpleNamespace(
        data=decision,
        last_update_success=decision is not None,
        active_weather_entity=active_weather_entity,
        async_add_listener=lambda update_callback, context=None: lambda: None,
    )


def _make_entry(
    *,
    days: int | None = None,
    customize: bool = False,
    category: str = "car",
    entry_id: str = "test_entry",
    title: str = "Test Wash",
) -> SimpleNamespace:
    """Return a stub :class:`ConfigEntry` carrying just what we read."""
    data: dict[str, Any] = {CONF_CATEGORY: category}
    if customize:
        data[CONF_CUSTOMIZE_THRESHOLDS] = True
    options: dict[str, Any] = {}
    if days is not None:
        options[CONF_DAYS] = days
        if customize:
            options[CONF_CUSTOMIZE_THRESHOLDS] = True
    return SimpleNamespace(
        entry_id=entry_id,
        data=data,
        options=options,
        title=title,
    )


# ----------------------------------------------------------------------
# can_wash state
# ----------------------------------------------------------------------


def test_can_wash_is_on_when_decision_true() -> None:
    """``is_on`` returns ``True`` when the decision says the surface can wash."""
    decision = _make_decision(can_wash=True)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WashWiseCanWashBinarySensor(coordinator, entry)

    assert sensor.is_on is True


def test_can_wash_is_off_when_decision_false() -> None:
    """``is_on`` returns ``False`` when the decision blocks washing."""
    decision = _make_decision(can_wash=False, score=10, reason="rain")
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WashWiseCanWashBinarySensor(coordinator, entry)

    assert sensor.is_on is False


def test_can_wash_is_unavailable_when_no_decision() -> None:
    """``is_on`` returns ``None`` (unavailable) when the coordinator has no data."""
    coordinator = _make_coordinator(None)
    entry = _make_entry()

    sensor = WashWiseCanWashBinarySensor(coordinator, entry)

    assert sensor.is_on is None
    # No decision → no extra attributes either.
    assert sensor.extra_state_attributes == {}


# ----------------------------------------------------------------------
# can_wash attributes
# ----------------------------------------------------------------------


def test_can_wash_attributes_populated() -> None:
    """All required diagnostic attributes are present and well-typed."""
    decision = _make_decision(score=72, reason="clear", days_analyzed=3)
    coordinator = _make_coordinator(decision, active_weather_entity="weather.home")
    entry = _make_entry()

    sensor = WashWiseCanWashBinarySensor(coordinator, entry)
    attrs = sensor.extra_state_attributes

    assert "forecast_summary" in attrs
    assert isinstance(attrs["forecast_summary"], list)
    assert len(attrs["forecast_summary"]) == 3

    details = attrs["decision_details"]
    assert details["can_wash"] is True
    assert details["score"] == 72
    assert details["reason"] == "clear"
    assert details["days_until_wash"] == 2
    assert "blocking_days" in details

    assert attrs["days_analyzed"] == 3
    assert attrs["score"] == 72
    assert attrs["reason"] == "clear"
    assert attrs["active_weather_entity"] == "weather.home"
    # Top-level blocking_days are ISO strings.
    assert attrs["blocking_days"] == ["2026-06-12"]


# ----------------------------------------------------------------------
# Per-day sensors aligned with horizon
# ----------------------------------------------------------------------


def test_per_day_count_matches_horizon_default_category() -> None:
    """Default ``car`` category → 3 day_ok sensors per ``CATEGORY_PRESETS``."""
    entry = _make_entry()
    thresholds = _resolve_thresholds(entry)
    assert thresholds["days"] == 3


def test_per_day_count_matches_horizon_customized() -> None:
    """``customize_thresholds=True`` with ``days=5`` → 5 day_ok sensors."""
    entry = _make_entry(days=5, customize=True)
    thresholds = _resolve_thresholds(entry)
    assert thresholds["days"] == 5


@pytest.mark.asyncio
async def test_async_setup_entry_creates_can_wash_plus_n_day_sensors(
    hass,
) -> None:
    """``async_setup_entry`` registers 2 + N entities (can_wash + freeze_risk + N day_ok)."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)

    entry = _make_entry(days=3, customize=True)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    added: list[Any] = []

    def _add(entities, update_before_add: bool = False) -> None:
        added.extend(entities)

    await async_setup_entry(hass, entry, _add)

    assert len(added) == 2 + 3
    assert isinstance(added[0], WashWiseCanWashBinarySensor)
    assert isinstance(added[1], WashWiseFreezeRiskBinarySensor)
    day_sensors = [e for e in added if isinstance(e, WashWiseDayOkBinarySensor)]
    assert len(day_sensors) == 3

    # The day sensors should map 1..N onto the unique_id key.
    keys = sorted(s.unique_id for s in day_sensors)
    assert keys == [
        f"{entry.entry_id}_day_1_ok",
        f"{entry.entry_id}_day_2_ok",
        f"{entry.entry_id}_day_3_ok",
    ]


@pytest.mark.asyncio
async def test_async_setup_entry_with_zero_horizon_only_registers_can_wash(
    hass,
) -> None:
    """Horizon 0 → only can_wash + freeze_risk, no day sensors."""
    decision = _make_decision(days_analyzed=0, forecast_summary=[])
    coordinator = _make_coordinator(decision)

    entry = _make_entry(days=0, customize=True)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    added: list[Any] = []

    def _add(entities, update_before_add: bool = False) -> None:
        added.extend(entities)

    await async_setup_entry(hass, entry, _add)

    assert len(added) == 2
    assert isinstance(added[0], WashWiseCanWashBinarySensor)
    assert isinstance(added[1], WashWiseFreezeRiskBinarySensor)


def test_day_ok_uses_blocked_field() -> None:
    """``day_<i>_ok`` is OFF when the forecast row has ``blocked=True``."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    day1 = WashWiseDayOkBinarySensor(coordinator, entry, 0)
    day2 = WashWiseDayOkBinarySensor(coordinator, entry, 1)
    day3 = WashWiseDayOkBinarySensor(coordinator, entry, 2)

    assert day1.is_on is True  # row 0 blocked=False
    assert day2.is_on is False  # row 1 blocked=True
    assert day3.is_on is True  # row 2 blocked=False


def test_day_ok_returns_none_when_index_out_of_range() -> None:
    """Day sensor for an index past the summary length returns ``None``."""
    decision = _make_decision(forecast_summary=[], days_analyzed=0)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WashWiseDayOkBinarySensor(coordinator, entry, 4)

    assert sensor.is_on is None
    # Attribute payload still exposes the day_index for debugging.
    assert sensor.extra_state_attributes == {"day_index": 4}


def test_day_ok_returns_none_when_no_decision() -> None:
    """Day sensor returns ``None`` while the coordinator has no data yet."""
    coordinator = _make_coordinator(None)
    entry = _make_entry()

    sensor = WashWiseDayOkBinarySensor(coordinator, entry, 0)

    assert sensor.is_on is None
    assert sensor.extra_state_attributes == {}


def test_day_ok_attributes_include_forecast_row() -> None:
    """The day sensor surfaces the underlying forecast row + day_index."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WashWiseDayOkBinarySensor(coordinator, entry, 1)
    attrs = sensor.extra_state_attributes

    assert attrs["day_index"] == 1
    assert attrs["condition"] == "rainy"
    assert attrs["precipitation"] == 4.5
    assert attrs["blocked"] is True


def test_day_ok_returns_none_when_row_is_not_dict() -> None:
    """Malformed forecast rows yield ``None`` instead of crashing."""
    decision = _make_decision(forecast_summary=["unexpected"], days_analyzed=1)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WashWiseDayOkBinarySensor(coordinator, entry, 0)

    assert sensor.is_on is None


def test_freeze_risk_binary_sensor_instantiates() -> None:
    """``WashWiseFreezeRiskBinarySensor`` constructs normally (covers __init__)."""
    from custom_components.washwise.binary_sensor import WashWiseFreezeRiskBinarySensor

    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WashWiseFreezeRiskBinarySensor(coordinator, entry)

    assert sensor.unique_id == f"{entry.entry_id}_freeze_risk"


def test_freeze_risk_binary_sensor_spanning_zero_branch() -> None:
    """``is_on`` True when low <= 0 — covers both line 295 and line 298."""
    from custom_components.washwise.binary_sensor import WashWiseFreezeRiskBinarySensor

    summary = [{"temp_min": -0.001, "temp_max": 1.0, "blocked": True}]
    decision = _make_decision(forecast_summary=summary, days_analyzed=1)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WashWiseFreezeRiskBinarySensor(coordinator, entry)

    assert sensor.is_on is True


def test_freeze_risk_binary_sensor_tmax_only_below_zero() -> None:
    """``is_on`` True when temp_min is absent but temp_max <= 0 (tmax-only freeze path)."""
    from custom_components.washwise.binary_sensor import WashWiseFreezeRiskBinarySensor

    summary = [{"temp_min": None, "temp_max": -1.0, "blocked": True}]
    decision = _make_decision(forecast_summary=summary, days_analyzed=1)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WashWiseFreezeRiskBinarySensor(coordinator, entry)

    assert sensor.is_on is True
