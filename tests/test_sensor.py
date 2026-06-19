"""Tests for the WashWise sensor platform.

Covers:

* Each sensor class can be instantiated with a stub coordinator + entry.
* ``None`` handling — sensors that derive from the wash log return
  ``STATE_UNKNOWN`` (i.e. ``None`` for ``native_value``) when there is no
  history.
* ``EntityCategory.DIAGNOSTIC`` is set on every diagnostic sensor.
* ``ScoreSensor`` reports an integer in the ``[0, 100]`` range.
* The number of ``DayScoreSensor`` instances created by ``async_setup_entry``
  matches the configured horizon.

These tests construct the entity classes directly with a SimpleNamespace
coordinator stub — fast, side-effect-free, and independent from the rest of
the integration plumbing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.helpers.entity import EntityCategory

from custom_components.washwise.const import (
    CONF_CATEGORY,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_DAYS,
    CONF_WEATHER_ENTITIES,
    DOMAIN,
)
from custom_components.washwise.models import (
    Decision,
    ProviderHealth,
    StoredData,
    WashEntry,
)
from custom_components.washwise.sensor import (
    ActiveProviderSensor,
    CategorySensor,
    DaysAnalyzedSensor,
    DayScoreSensor,
    DaysSinceWashSensor,
    DaysUntilWashSensor,
    LastUpdateSensor,
    LastWashedSensor,
    MaxTempSensor,
    MinTempSensor,
    PrecipTotalMmSensor,
    PrimaryProviderUptimeSensor,
    ReasonSensor,
    ScoreSensor,
    SnoozeRemainingSensor,
    WashCount30dSensor,
    WashWiseSensorBase,
    WorstConditionSensor,
    _coerce_str,
    _parse_iso,
    _resolve_horizon,
    _temp_extreme,
    async_setup_entry,
)

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _make_decision(
    *,
    score: int = 80,
    can_wash: bool = True,
    reason: str = "clear",
    days_analyzed: int = 3,
    forecast_summary: list[dict[str, Any]] | None = None,
) -> Decision:
    """Build a populated :class:`Decision` for entity tests."""
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
                "day_score": 100,
            },
            {
                "date": date(2026, 6, 12),
                "condition": "rainy",
                "precipitation": 4.5,
                "temp_min": -1.0,
                "temp_max": 5.0,
                "blocked": True,
                "blockers": ["precipitation"],
                "day_score": 0,
            },
            {
                "date": date(2026, 6, 13),
                "condition": "partlycloudy",
                "precipitation": 0.1,
                "temp_min": 12.0,
                "temp_max": 22.0,
                "blocked": False,
                "blockers": [],
                "day_score": 96,
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
    decision: Decision | None = None,
    *,
    stored: StoredData | None = None,
    active_weather_entity: str | None = "weather.home",
    active_provider_label: str | None = "Home",
    last_update_success_time: datetime | None = None,
    weather_ids: list[str] | None = None,
) -> SimpleNamespace:
    """Build a stub coordinator that satisfies CoordinatorEntity init."""
    store = SimpleNamespace(_data=stored) if stored is not None else SimpleNamespace()
    ids = list(weather_ids) if weather_ids is not None else []
    return SimpleNamespace(
        data=decision,
        last_update_success=decision is not None,
        active_weather_entity=active_weather_entity,
        active_provider_label=active_provider_label,
        last_update_success_time=last_update_success_time,
        _store=store,
        _weather_ids=lambda ids=ids: list(ids),
        async_add_listener=lambda update_callback, context=None: lambda: None,
    )


def _make_entry(
    *,
    days: int | None = None,
    customize: bool = False,
    category: str = "car",
    weather_entities: list[str] | None = None,
    entry_id: str = "test_entry",
    title: str = "Test Wash",
) -> SimpleNamespace:
    """Stub ConfigEntry with just enough surface to drive sensor classes."""
    data: dict[str, Any] = {
        CONF_CATEGORY: category,
        CONF_WEATHER_ENTITIES: list(weather_entities or ["weather.home"]),
    }
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
# Base / convenience
# ----------------------------------------------------------------------


def test_resolve_horizon_default_is_category_preset() -> None:
    """Without customize, horizon comes from the category preset (car=3)."""
    entry = _make_entry()
    assert _resolve_horizon(entry) == 3


def test_resolve_horizon_with_customize_uses_options() -> None:
    """With customize on, the explicit ``days`` option wins."""
    entry = _make_entry(days=5, customize=True)
    assert _resolve_horizon(entry) == 5


def test_resolve_horizon_invalid_days_falls_back_to_preset() -> None:
    """Non-integer ``days`` option falls back to the preset value."""
    entry = _make_entry(customize=True)
    entry.options[CONF_DAYS] = "not-a-number"
    assert _resolve_horizon(entry) == 3


# ----------------------------------------------------------------------
# Each sensor class can be instantiated
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls",
    [
        ScoreSensor,
        ReasonSensor,
        DaysUntilWashSensor,
        DaysSinceWashSensor,
        LastWashedSensor,
        WashCount30dSensor,
        ActiveProviderSensor,
        LastUpdateSensor,
        PrecipTotalMmSensor,
        WorstConditionSensor,
        MinTempSensor,
        MaxTempSensor,
        PrimaryProviderUptimeSensor,
        SnoozeRemainingSensor,
        CategorySensor,
    ],
)
def test_sensor_class_instantiates(cls: type[WashWiseSensorBase]) -> None:
    """Every primary/diagnostic sensor class instantiates without error."""
    coordinator = _make_coordinator(_make_decision())
    entry = _make_entry()

    sensor = cls(coordinator, entry)

    assert sensor.unique_id.startswith(entry.entry_id)


# ----------------------------------------------------------------------
# None / unknown handling
# ----------------------------------------------------------------------


def test_last_washed_unknown_when_log_empty() -> None:
    """``last_washed`` returns ``None`` (STATE_UNKNOWN) with no log entries."""
    coordinator = _make_coordinator(_make_decision(), stored=StoredData.empty())
    entry = _make_entry()

    sensor = LastWashedSensor(coordinator, entry)

    assert sensor.native_value is None


def test_last_washed_unknown_when_store_unloaded() -> None:
    """``last_washed`` returns ``None`` when the store hasn't loaded yet."""
    coordinator = _make_coordinator(_make_decision())  # no stored=
    entry = _make_entry()

    sensor = LastWashedSensor(coordinator, entry)

    assert sensor.native_value is None


def test_days_since_wash_unknown_when_log_empty() -> None:
    """``days_since_wash`` returns ``None`` when there is no log entry."""
    coordinator = _make_coordinator(_make_decision(), stored=StoredData.empty())
    entry = _make_entry()

    sensor = DaysSinceWashSensor(coordinator, entry)

    assert sensor.native_value is None


def test_wash_count_30d_unknown_when_store_unloaded() -> None:
    """30-day wash count returns ``None`` when the store hasn't loaded."""
    coordinator = _make_coordinator(_make_decision())  # no stored
    entry = _make_entry()

    sensor = WashCount30dSensor(coordinator, entry)

    assert sensor.native_value is None


def test_score_sensor_unknown_without_decision() -> None:
    """``ScoreSensor.native_value`` is ``None`` until a decision exists."""
    coordinator = _make_coordinator(None)
    entry = _make_entry()

    sensor = ScoreSensor(coordinator, entry)

    assert sensor.native_value is None


def test_reason_sensor_unknown_without_decision() -> None:
    """``ReasonSensor`` returns ``None`` while waiting for first refresh."""
    coordinator = _make_coordinator(None)
    entry = _make_entry()

    sensor = ReasonSensor(coordinator, entry)

    assert sensor.native_value is None


# ----------------------------------------------------------------------
# Diagnostic category
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "cls",
    [
        DaysAnalyzedSensor,
        PrecipTotalMmSensor,
        WorstConditionSensor,
        MinTempSensor,
        MaxTempSensor,
        PrimaryProviderUptimeSensor,
        SnoozeRemainingSensor,
        CategorySensor,
    ],
)
def test_diagnostic_category_set(cls: type[WashWiseSensorBase]) -> None:
    """Diagnostic sensors carry ``EntityCategory.DIAGNOSTIC``."""
    coordinator = _make_coordinator(_make_decision())
    entry = _make_entry()

    sensor = cls(coordinator, entry)

    assert sensor.entity_category is EntityCategory.DIAGNOSTIC


@pytest.mark.parametrize(
    "cls",
    [
        ScoreSensor,
        ReasonSensor,
        DaysUntilWashSensor,
        DaysSinceWashSensor,
        LastWashedSensor,
        WashCount30dSensor,
        ActiveProviderSensor,
        LastUpdateSensor,
    ],
)
def test_primary_sensors_not_diagnostic(cls: type[WashWiseSensorBase]) -> None:
    """Primary sensors should NOT be tagged diagnostic."""
    coordinator = _make_coordinator(_make_decision())
    entry = _make_entry()

    sensor = cls(coordinator, entry)

    assert sensor.entity_category is None


# ----------------------------------------------------------------------
# Score range
# ----------------------------------------------------------------------


@pytest.mark.parametrize("score", [0, 1, 50, 99, 100])
def test_score_within_range(score: int) -> None:
    """Score reports its integer value, always in the 0-100 contract."""
    decision = _make_decision(score=score)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = ScoreSensor(coordinator, entry)
    value = sensor.native_value

    assert isinstance(value, int)
    assert 0 <= value <= 100
    assert value == score


# ----------------------------------------------------------------------
# Per-day score sensors
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_setup_entry_creates_per_day_score_sensors(hass) -> None:
    """``async_setup_entry`` registers one DayScoreSensor per horizon day."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry(days=4, customize=True)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    added: list[Any] = []

    def _add(entities, update_before_add: bool = False) -> None:
        added.extend(entities)

    await async_setup_entry(hass, entry, _add)

    day_sensors = [e for e in added if isinstance(e, DayScoreSensor)]
    assert len(day_sensors) == 4
    keys = sorted(s.unique_id for s in day_sensors)
    assert keys == [
        f"{entry.entry_id}_day_1_score",
        f"{entry.entry_id}_day_2_score",
        f"{entry.entry_id}_day_3_score",
        f"{entry.entry_id}_day_4_score",
    ]


@pytest.mark.asyncio
async def test_async_setup_entry_default_horizon(hass) -> None:
    """Default horizon (car preset = 3) creates 3 per-day score sensors."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    added: list[Any] = []

    def _add(entities, update_before_add: bool = False) -> None:
        added.extend(entities)

    await async_setup_entry(hass, entry, _add)

    day_sensors = [e for e in added if isinstance(e, DayScoreSensor)]
    assert len(day_sensors) == 3


def test_day_score_value_blocked_or_unblocked() -> None:
    """Day score reads ``day_score`` from the forecast summary dict."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor_clear = DayScoreSensor(coordinator, entry, 0)
    sensor_blocked = DayScoreSensor(coordinator, entry, 1)

    assert sensor_clear.native_value == 100
    assert sensor_blocked.native_value == 0


def test_day_score_value_in_range_0_100() -> None:
    """All DayScoreSensor outputs sit in the 0..100 range or are None."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    for i in range(decision.days_analyzed):
        value = DayScoreSensor(coordinator, entry, i).native_value
        assert value is None or 0 <= value <= 100


def test_day_score_unknown_when_no_decision() -> None:
    """Day score returns ``None`` while the coordinator has no data."""
    coordinator = _make_coordinator(None)
    entry = _make_entry()

    sensor = DayScoreSensor(coordinator, entry, 0)

    assert sensor.native_value is None
    assert sensor.extra_state_attributes is None


def test_day_score_attributes_expose_forecast_row() -> None:
    """DayScoreSensor exposes condition/precip/temp on the underlying row."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = DayScoreSensor(coordinator, entry, 1)
    attrs = sensor.extra_state_attributes

    assert attrs is not None
    assert attrs["condition"] == "rainy"
    assert attrs["precipitation"] == 4.5
    assert attrs["blockers"] == ["precipitation"]


def test_score_sensor_has_measurement_state_class() -> None:
    """ScoreSensor must declare MEASUREMENT so HA tracks long-term statistics."""
    coordinator = _make_coordinator(_make_decision())
    sensor = ScoreSensor(coordinator, _make_entry())
    assert sensor.state_class == SensorStateClass.MEASUREMENT


def test_day_score_sensor_has_measurement_state_class() -> None:
    """DayScoreSensor must declare MEASUREMENT so HA tracks long-term statistics."""
    coordinator = _make_coordinator(_make_decision())
    sensor = DayScoreSensor(coordinator, _make_entry(), 0)
    assert sensor.state_class == SensorStateClass.MEASUREMENT


# ----------------------------------------------------------------------
# Specific sensor behaviours
# ----------------------------------------------------------------------


def test_days_since_wash_uses_last_log_entry() -> None:
    """``days_since_wash`` reports days between last log entry and now."""
    five_days_ago = datetime.now(UTC) - timedelta(days=5, hours=1)
    stored = StoredData(
        wash_log=[WashEntry(timestamp=five_days_ago.isoformat(), source="manual")],
        snooze_until=None,
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )
    coordinator = _make_coordinator(_make_decision(), stored=stored)
    entry = _make_entry()

    sensor = DaysSinceWashSensor(coordinator, entry)

    assert sensor.native_value == 5


def test_wash_count_30d_only_recent_entries() -> None:
    """30-day count includes recent entries and excludes old ones."""
    now = datetime.now(UTC)
    stored = StoredData(
        wash_log=[
            WashEntry(timestamp=(now - timedelta(days=2)).isoformat(), source="manual"),
            WashEntry(timestamp=(now - timedelta(days=10)).isoformat(), source="manual"),
            WashEntry(timestamp=(now - timedelta(days=45)).isoformat(), source="manual"),
        ],
        snooze_until=None,
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )
    coordinator = _make_coordinator(_make_decision(), stored=stored)
    entry = _make_entry()

    sensor = WashCount30dSensor(coordinator, entry)

    assert sensor.native_value == 2


def test_worst_condition_picks_highest_severity() -> None:
    """Worst condition picks the most severe forecast row."""
    summary = [
        {"date": date(2026, 6, 11), "condition": "rainy", "blocked": True},
        {"date": date(2026, 6, 12), "condition": "pouring", "blocked": True},
        {"date": date(2026, 6, 13), "condition": "sunny", "blocked": False},
    ]
    decision = _make_decision(forecast_summary=summary, days_analyzed=3)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WorstConditionSensor(coordinator, entry)

    assert sensor.native_value == "pouring"


def test_category_sensor_returns_configured_category() -> None:
    """``CategorySensor`` returns the category key from entry.data."""
    coordinator = _make_coordinator(_make_decision())
    entry = _make_entry(category="boat")

    sensor = CategorySensor(coordinator, entry)

    assert sensor.native_value == "boat"


def test_min_max_temp_extremes() -> None:
    """Min / max temp pull the right extremes from the forecast."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    assert MinTempSensor(coordinator, entry).native_value == -1.0
    assert MaxTempSensor(coordinator, entry).native_value == 24.0


def test_precip_total_sums_forecast_summary() -> None:
    """Precip total sums all rows where ``precipitation`` is non-None."""
    decision = _make_decision()  # 0.0 + 4.5 + 0.1
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = PrecipTotalMmSensor(coordinator, entry)

    assert sensor.native_value == pytest.approx(4.6)


def test_primary_provider_uptime_percentage() -> None:
    """Primary provider uptime computes ``success / (success + failure) * 100``."""
    health = ProviderHealth(
        entity_id="weather.home",
        success_count=8,
        failure_count=2,
        last_success_ts=None,
        last_failure_ts=None,
        last_error=None,
        last_seen_ts=datetime.now(UTC).isoformat(),
    )
    stored = StoredData(
        wash_log=[],
        snooze_until=None,
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={"weather.home": health},
    )
    coordinator = _make_coordinator(_make_decision(), stored=stored, weather_ids=["weather.home"])
    entry = _make_entry(weather_entities=["weather.home"])

    sensor = PrimaryProviderUptimeSensor(coordinator, entry)
    value = sensor.native_value

    assert value is not None
    assert value == pytest.approx(80.0)
    assert 0 <= value <= 100


def test_snooze_remaining_when_inactive_returns_none() -> None:
    """Snooze remaining is ``None`` when no snooze is active."""
    coordinator = _make_coordinator(_make_decision(), stored=StoredData.empty())
    entry = _make_entry()

    sensor = SnoozeRemainingSensor(coordinator, entry)

    assert sensor.native_value is None


def test_snooze_remaining_returns_seconds_when_active() -> None:
    """Snooze remaining returns positive minutes while the snooze is live."""
    until = datetime.now(UTC) + timedelta(hours=2)
    stored = StoredData(
        wash_log=[],
        snooze_until=until.isoformat(),
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )
    coordinator = _make_coordinator(_make_decision(), stored=stored)
    entry = _make_entry()

    sensor = SnoozeRemainingSensor(coordinator, entry)
    value = sensor.native_value

    assert isinstance(value, int)
    # 2 hours = 120 minutes; allow ±1 for clock skew.
    assert 119 <= value <= 121


def test_snooze_remaining_extra_attributes_returns_snooze_until() -> None:
    """extra_state_attributes exposes snooze_until ISO string."""
    until = datetime.now(UTC) + timedelta(hours=1)
    stored = StoredData(
        wash_log=[],
        snooze_until=until.isoformat(),
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )
    coordinator = _make_coordinator(_make_decision(), stored=stored)
    entry = _make_entry()

    sensor = SnoozeRemainingSensor(coordinator, entry)
    attrs = sensor.extra_state_attributes

    assert attrs["snooze_until"] == until.isoformat()


def test_snooze_remaining_extra_attributes_none_when_inactive() -> None:
    """extra_state_attributes returns snooze_until=None when not snoozed."""
    coordinator = _make_coordinator(_make_decision(), stored=StoredData.empty())
    entry = _make_entry()

    sensor = SnoozeRemainingSensor(coordinator, entry)
    attrs = sensor.extra_state_attributes

    assert attrs["snooze_until"] is None


# ----------------------------------------------------------------------
# native_value branches with a populated decision
# ----------------------------------------------------------------------


def test_reason_returns_decision_reason() -> None:
    """``ReasonSensor`` returns the populated reason key."""
    decision = _make_decision(reason="clear")
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = ReasonSensor(coordinator, entry)

    assert sensor.native_value == "clear"


def test_reason_empty_string_yields_none() -> None:
    """An empty reason string is normalized to ``None``."""
    decision = _make_decision(reason="")
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = ReasonSensor(coordinator, entry)

    assert sensor.native_value is None


def test_days_until_wash_returns_value_with_decision() -> None:
    """``DaysUntilWashSensor`` returns the decision's days_until_wash."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = DaysUntilWashSensor(coordinator, entry)

    assert sensor.native_value == 2


def test_days_until_wash_none_without_decision() -> None:
    """``DaysUntilWashSensor`` returns None when decision missing."""
    coordinator = _make_coordinator(None)
    entry = _make_entry()

    sensor = DaysUntilWashSensor(coordinator, entry)

    assert sensor.native_value is None


def test_active_provider_returns_label() -> None:
    """``ActiveProviderSensor`` returns the coordinator's active_provider_label."""
    coordinator = _make_coordinator(_make_decision(), active_provider_label="Home Weather")
    entry = _make_entry()

    sensor = ActiveProviderSensor(coordinator, entry)

    assert sensor.native_value == "Home Weather"


def test_active_provider_exposes_weather_entity_id_as_attribute() -> None:
    """``ActiveProviderSensor`` exposes weather_entity_id in extra_state_attributes."""
    coordinator = _make_coordinator(_make_decision(), active_weather_entity="weather.home")
    entry = _make_entry()

    sensor = ActiveProviderSensor(coordinator, entry)

    assert sensor.extra_state_attributes == {"weather_entity_id": "weather.home"}


def test_active_provider_attributes_none_when_no_entity() -> None:
    """``ActiveProviderSensor`` returns None attributes when active_weather_entity is None."""
    coordinator = _make_coordinator(_make_decision(), active_weather_entity=None)
    entry = _make_entry()

    sensor = ActiveProviderSensor(coordinator, entry)

    assert sensor.extra_state_attributes is None


def test_last_update_returns_datetime() -> None:
    """``LastUpdateSensor`` returns the coordinator's last_update_success_time."""
    ts = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    coordinator = _make_coordinator(_make_decision(), last_update_success_time=ts)
    entry = _make_entry()

    sensor = LastUpdateSensor(coordinator, entry)

    assert sensor.native_value == ts


def test_last_update_returns_none_when_not_a_datetime() -> None:
    """``LastUpdateSensor`` returns None when last_update_success_time isn't datetime."""
    coordinator = _make_coordinator(_make_decision(), last_update_success_time=None)
    entry = _make_entry()

    sensor = LastUpdateSensor(coordinator, entry)

    assert sensor.native_value is None


def test_last_update_returns_none_for_non_datetime_value() -> None:
    """``LastUpdateSensor`` returns None when ts is not a datetime instance."""
    coordinator = _make_coordinator(_make_decision())
    coordinator.last_update_success_time = "not-a-datetime"
    entry = _make_entry()

    sensor = LastUpdateSensor(coordinator, entry)

    assert sensor.native_value is None


def test_days_analyzed_returns_value() -> None:
    """``DaysAnalyzedSensor`` returns the decision's days_analyzed."""
    decision = _make_decision(days_analyzed=5)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = DaysAnalyzedSensor(coordinator, entry)

    assert sensor.native_value == 5


def test_days_analyzed_none_without_decision() -> None:
    """``DaysAnalyzedSensor`` returns None without a decision."""
    coordinator = _make_coordinator(None)
    entry = _make_entry()

    sensor = DaysAnalyzedSensor(coordinator, entry)

    assert sensor.native_value is None


# ----------------------------------------------------------------------
# PrecipTotalMmSensor edge cases
# ----------------------------------------------------------------------


def test_precip_total_none_without_decision() -> None:
    """``PrecipTotalMmSensor`` returns None when decision missing."""
    coordinator = _make_coordinator(None)
    entry = _make_entry()

    sensor = PrecipTotalMmSensor(coordinator, entry)

    assert sensor.native_value is None


def test_precip_total_skips_none_and_invalid_values() -> None:
    """``PrecipTotalMmSensor`` skips ``None`` and unparseable values."""
    summary = [
        {"date": date(2026, 6, 11), "precipitation": None, "blocked": False},
        {"date": date(2026, 6, 12), "precipitation": "oops", "blocked": False},
        {"date": date(2026, 6, 13), "precipitation": 1.25, "blocked": False},
    ]
    decision = _make_decision(forecast_summary=summary, days_analyzed=3)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = PrecipTotalMmSensor(coordinator, entry)

    assert sensor.native_value == pytest.approx(1.25)


def test_precip_total_none_when_no_valid_values() -> None:
    """When no row has valid precipitation, return ``None``."""
    summary = [
        {"date": date(2026, 6, 11), "precipitation": None, "blocked": False},
        {"date": date(2026, 6, 12), "precipitation": "bad", "blocked": False},
    ]
    decision = _make_decision(forecast_summary=summary, days_analyzed=2)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = PrecipTotalMmSensor(coordinator, entry)

    assert sensor.native_value is None


# ----------------------------------------------------------------------
# WorstConditionSensor edge cases
# ----------------------------------------------------------------------


def test_worst_condition_none_without_decision() -> None:
    """``WorstConditionSensor`` returns None when decision missing."""
    coordinator = _make_coordinator(None)
    entry = _make_entry()

    sensor = WorstConditionSensor(coordinator, entry)

    assert sensor.native_value is None


def test_worst_condition_none_when_no_conditions() -> None:
    """Returns None when every row is missing a condition string."""
    summary = [
        {"date": date(2026, 6, 11), "condition": None, "blocked": False},
        {"date": date(2026, 6, 12), "condition": "", "blocked": False},
    ]
    decision = _make_decision(forecast_summary=summary, days_analyzed=2)
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = WorstConditionSensor(coordinator, entry)

    assert sensor.native_value is None


# ----------------------------------------------------------------------
# PrimaryProviderUptimeSensor edge cases
# ----------------------------------------------------------------------


def test_primary_provider_uptime_none_when_no_weather_entities() -> None:
    """Returns None when the coordinator's _weather_ids() yields an empty list."""
    coordinator = _make_coordinator(_make_decision(), stored=StoredData.empty(), weather_ids=[])
    entry = _make_entry()

    sensor = PrimaryProviderUptimeSensor(coordinator, entry)

    assert sensor.native_value is None


def test_primary_provider_uptime_none_when_store_unloaded() -> None:
    """Returns None when stored snapshot is missing."""
    coordinator = _make_coordinator(_make_decision(), weather_ids=["weather.home"])  # no stored
    entry = _make_entry(weather_entities=["weather.home"])

    sensor = PrimaryProviderUptimeSensor(coordinator, entry)

    assert sensor.native_value is None


def test_primary_provider_uptime_none_when_no_health_entry() -> None:
    """Returns None when there is no health record for the primary entity."""
    coordinator = _make_coordinator(
        _make_decision(), stored=StoredData.empty(), weather_ids=["weather.home"]
    )
    entry = _make_entry(weather_entities=["weather.home"])

    sensor = PrimaryProviderUptimeSensor(coordinator, entry)

    assert sensor.native_value is None


def test_primary_provider_uptime_uses_options_first_provider() -> None:
    """Reorder via Options → Providers must update the uptime sensor's primary.

    Pre-fix the sensor read entry.data only, so the uptime kept reporting on
    the original config-flow primary even after a reorder. The fix routes
    through the coordinator's ``_weather_ids()`` helper, which merges options
    over data.
    """
    backup_health = ProviderHealth(
        entity_id="weather.backup",
        success_count=9,
        failure_count=1,
        last_success_ts=None,
        last_failure_ts=None,
        last_error=None,
        last_seen_ts=datetime.now(UTC).isoformat(),
    )
    primary_health = ProviderHealth(
        entity_id="weather.primary",
        success_count=1,
        failure_count=9,
        last_success_ts=None,
        last_failure_ts=None,
        last_error=None,
        last_seen_ts=datetime.now(UTC).isoformat(),
    )
    stored = StoredData(
        wash_log=[],
        snooze_until=None,
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={
            "weather.backup": backup_health,
            "weather.primary": primary_health,
        },
    )
    # Options reorder put backup first; data still has primary first.
    coordinator = _make_coordinator(
        _make_decision(),
        stored=stored,
        weather_ids=["weather.backup", "weather.primary"],
    )
    entry = _make_entry(weather_entities=["weather.primary", "weather.backup"])

    sensor = PrimaryProviderUptimeSensor(coordinator, entry)
    value = sensor.native_value

    # Should reflect backup's 9/10 = 90 %, NOT primary's 1/10 = 10 %.
    assert value == pytest.approx(90.0)


def test_primary_provider_uptime_none_when_zero_total() -> None:
    """Returns None when both success and failure counts are zero."""
    health = ProviderHealth(
        entity_id="weather.home",
        success_count=0,
        failure_count=0,
        last_success_ts=None,
        last_failure_ts=None,
        last_error=None,
        last_seen_ts="",
    )
    stored = StoredData(
        wash_log=[],
        snooze_until=None,
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={"weather.home": health},
    )
    coordinator = _make_coordinator(_make_decision(), stored=stored, weather_ids=["weather.home"])
    entry = _make_entry(weather_entities=["weather.home"])

    sensor = PrimaryProviderUptimeSensor(coordinator, entry)

    assert sensor.native_value is None


# ----------------------------------------------------------------------
# SnoozeRemainingSensor edge cases
# ----------------------------------------------------------------------


def test_snooze_remaining_none_when_store_unloaded() -> None:
    """Returns None when stored snapshot is unavailable."""
    coordinator = _make_coordinator(_make_decision())  # no stored
    entry = _make_entry()

    sensor = SnoozeRemainingSensor(coordinator, entry)

    assert sensor.native_value is None


def test_snooze_remaining_none_when_iso_unparseable() -> None:
    """Returns None when ``snooze_until`` cannot be parsed."""
    stored = StoredData(
        wash_log=[],
        snooze_until="not-an-iso-string",
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )
    coordinator = _make_coordinator(_make_decision(), stored=stored)
    entry = _make_entry()

    sensor = SnoozeRemainingSensor(coordinator, entry)

    assert sensor.native_value is None


def test_snooze_remaining_none_when_already_expired() -> None:
    """Returns None when the snooze instant is already in the past."""
    past = datetime.now(UTC) - timedelta(hours=1)
    stored = StoredData(
        wash_log=[],
        snooze_until=past.isoformat(),
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )
    coordinator = _make_coordinator(_make_decision(), stored=stored)
    entry = _make_entry()

    sensor = SnoozeRemainingSensor(coordinator, entry)

    assert sensor.native_value is None


# ----------------------------------------------------------------------
# DayScoreSensor index-out-of-range branches
# ----------------------------------------------------------------------


def test_day_score_value_none_when_index_beyond_summary() -> None:
    """``DayScoreSensor.native_value`` returns None when index >= summary len."""
    decision = _make_decision()  # 3 rows
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = DayScoreSensor(coordinator, entry, 99)

    assert sensor.native_value is None


def test_day_score_attributes_none_when_index_beyond_summary() -> None:
    """``DayScoreSensor.extra_state_attributes`` is None for out-of-range index."""
    decision = _make_decision()
    coordinator = _make_coordinator(decision)
    entry = _make_entry()

    sensor = DayScoreSensor(coordinator, entry, 99)

    assert sensor.extra_state_attributes is None


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------


def test_parse_iso_returns_none_for_falsy() -> None:
    """``_parse_iso`` returns None for falsy values."""
    assert _parse_iso(None) is None
    assert _parse_iso("") is None
    assert _parse_iso(0) is None


def test_parse_iso_passes_through_aware_datetime() -> None:
    """``_parse_iso`` passes through aware datetimes unchanged."""
    ts = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    assert _parse_iso(ts) is ts


def test_parse_iso_promotes_naive_datetime_to_utc() -> None:
    """A naive ``datetime`` gets a UTC tzinfo attached."""
    naive = datetime(2026, 6, 11, 12, 0)
    out = _parse_iso(naive)
    assert out is not None
    assert out.tzinfo is UTC


def test_parse_iso_returns_none_on_bad_string() -> None:
    """Unparseable strings yield ``None``."""
    assert _parse_iso("not-a-real-iso") is None


def test_parse_iso_promotes_naive_iso_string_to_utc() -> None:
    """A naive ISO string is promoted to UTC."""
    out = _parse_iso("2026-06-11T12:00:00")
    assert out is not None
    assert out.tzinfo is UTC


def test_parse_iso_keeps_aware_iso_string_offset() -> None:
    """An aware ISO string keeps its tzinfo."""
    out = _parse_iso("2026-06-11T12:00:00+00:00")
    assert out is not None
    assert out.tzinfo is not None


def test_temp_extreme_none_without_decision() -> None:
    """``_temp_extreme`` returns None for None decision."""
    assert _temp_extreme(None, "temp_min", _min=True) is None


def test_temp_extreme_skips_none_and_invalid() -> None:
    """``_temp_extreme`` ignores None and unparseable values."""
    summary = [
        {"date": date(2026, 6, 11), "temp_min": None, "blocked": False},
        {"date": date(2026, 6, 12), "temp_min": "bad", "blocked": False},
        {"date": date(2026, 6, 13), "temp_min": 3.5, "blocked": False},
        {"date": date(2026, 6, 14), "temp_min": -2.0, "blocked": False},
    ]
    decision = _make_decision(forecast_summary=summary, days_analyzed=4)

    assert _temp_extreme(decision, "temp_min", _min=True) == -2.0
    assert _temp_extreme(decision, "temp_min", _min=False) == 3.5


def test_temp_extreme_returns_none_when_all_invalid() -> None:
    """``_temp_extreme`` returns None when no row has a parseable value."""
    summary = [
        {"date": date(2026, 6, 11), "temp_min": None, "blocked": False},
        {"date": date(2026, 6, 12), "temp_min": "bad", "blocked": False},
    ]
    decision = _make_decision(forecast_summary=summary, days_analyzed=2)

    assert _temp_extreme(decision, "temp_min", _min=True) is None


def test_coerce_str_none() -> None:
    """``_coerce_str(None)`` returns None."""
    assert _coerce_str(None) is None


def test_coerce_str_datetime() -> None:
    """Datetime is coerced via isoformat."""
    ts = datetime(2026, 6, 11, 12, 0, tzinfo=UTC)
    assert _coerce_str(ts) == ts.isoformat()


def test_coerce_str_date_uses_isoformat() -> None:
    """A ``date`` (which has isoformat) is coerced via that method."""
    d = date(2026, 6, 11)
    assert _coerce_str(d) == "2026-06-11"


def test_coerce_str_isoformat_failure_falls_back_to_str() -> None:
    """When ``isoformat()`` raises, fall back to ``str(value)``."""

    class Boom:
        def isoformat(self) -> str:
            raise RuntimeError("nope")

        def __str__(self) -> str:
            return "boom-str"

    assert _coerce_str(Boom()) == "boom-str"


def test_coerce_str_plain_value() -> None:
    """Plain values without ``isoformat`` go through ``str``."""
    assert _coerce_str(42) == "42"
    assert _coerce_str("already") == "already"
