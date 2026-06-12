"""Tests for ``custom_components.washwise.models``.

Covers:

* ``to_dict`` -> ``from_dict`` round-trip equality for every dataclass.
* Edge values: ``None`` temperatures, negative precipitation, future dates.
* ``StoredData.empty()`` defaults.
* ``ProviderHealth`` timestamp round-trip.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from custom_components.washwise.models import (
    CurrentWeather,
    Decision,
    ForecastDay,
    ProviderHealth,
    StoredData,
    WashEntry,
)

# ---------------------------------------------------------------------------
# ForecastDay
# ---------------------------------------------------------------------------


def test_forecast_day_roundtrip_basic() -> None:
    fd = ForecastDay(
        date=date(2026, 6, 11),
        condition="sunny",
        precipitation_mm=0.0,
        temp_min_c=10.0,
        temp_max_c=22.5,
        raw={"datetime": "2026-06-11T00:00:00", "extra": 1},
    )
    again = ForecastDay.from_dict(fd.to_dict())
    assert again == fd


def test_forecast_day_none_temps_roundtrip() -> None:
    fd = ForecastDay(
        date=date(2026, 6, 11),
        condition=None,
        precipitation_mm=None,
        temp_min_c=None,
        temp_max_c=None,
        raw={},
    )
    again = ForecastDay.from_dict(fd.to_dict())
    assert again == fd
    assert again.temp_min_c is None
    assert again.temp_max_c is None
    assert again.condition is None


def test_forecast_day_negative_precip_roundtrip() -> None:
    """Negative precip is nonsensical but must not be silently lost."""
    fd = ForecastDay(
        date=date(2026, 6, 11),
        condition="cloudy",
        precipitation_mm=-1.5,
        temp_min_c=5.0,
        temp_max_c=12.0,
        raw={},
    )
    again = ForecastDay.from_dict(fd.to_dict())
    assert again == fd
    assert again.precipitation_mm == -1.5


def test_forecast_day_future_date_roundtrip() -> None:
    future = date.today() + timedelta(days=365 * 5)
    fd = ForecastDay(
        date=future,
        condition="sunny",
        precipitation_mm=0.0,
        temp_min_c=20.0,
        temp_max_c=30.0,
        raw={},
    )
    again = ForecastDay.from_dict(fd.to_dict())
    assert again.date == future
    assert again == fd


def test_forecast_day_from_dict_missing_keys_defaults_safely() -> None:
    fd = ForecastDay.from_dict({"date": "2026-06-11"})
    assert fd.date == date(2026, 6, 11)
    assert fd.condition is None
    assert fd.precipitation_mm is None
    assert fd.temp_min_c is None
    assert fd.temp_max_c is None
    assert fd.raw == {}


# ---------------------------------------------------------------------------
# CurrentWeather
# ---------------------------------------------------------------------------


def test_current_weather_roundtrip_basic() -> None:
    cw = CurrentWeather(
        condition="rainy",
        temperature_c=8.0,
        raw={"humidity": 88},
    )
    again = CurrentWeather.from_dict(cw.to_dict())
    assert again == cw


def test_current_weather_none_temp_roundtrip() -> None:
    cw = CurrentWeather(condition=None, temperature_c=None, raw={})
    again = CurrentWeather.from_dict(cw.to_dict())
    assert again == cw
    assert again.temperature_c is None


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------


def test_decision_roundtrip_basic() -> None:
    d = Decision(
        can_wash=True,
        score=85,
        reason="clear",
        days_until_wash=0,
        blocking_days=[],
        forecast_summary=[{"date": "2026-06-11", "condition": "sunny"}],
        days_analyzed=3,
    )
    again = Decision.from_dict(d.to_dict())
    assert again == d


def test_decision_with_blocking_days_and_none_window_roundtrip() -> None:
    d = Decision(
        can_wash=False,
        score=20,
        reason="rain",
        days_until_wash=None,
        blocking_days=[date(2026, 6, 11), date(2026, 6, 12)],
        forecast_summary=[],
        days_analyzed=3,
    )
    again = Decision.from_dict(d.to_dict())
    assert again == d
    assert again.days_until_wash is None
    assert again.blocking_days == [date(2026, 6, 11), date(2026, 6, 12)]


def test_decision_tz_aware_next_window_roundtrip() -> None:
    d = Decision(
        can_wash=True,
        score=100,
        reason="clear",
        days_until_wash=0,
        blocking_days=[],
        forecast_summary=[],
        days_analyzed=3,
    )
    again = Decision.from_dict(d.to_dict())
    assert again == d


# ---------------------------------------------------------------------------
# WashEntry
# ---------------------------------------------------------------------------


def test_wash_entry_roundtrip() -> None:
    w = WashEntry(timestamp="2026-06-11T10:00:00+00:00", source="manual")
    assert WashEntry.from_dict(w.to_dict()) == w


def test_wash_entry_defaults() -> None:
    w = WashEntry.from_dict({})
    assert w.timestamp == ""
    assert w.source == "manual"


# ---------------------------------------------------------------------------
# ProviderHealth
# ---------------------------------------------------------------------------


def test_provider_health_roundtrip_full() -> None:
    ph = ProviderHealth(
        entity_id="weather.home",
        success_count=42,
        failure_count=3,
        last_success_ts="2026-06-11T10:00:00+00:00",
        last_failure_ts="2026-06-10T08:30:00+00:00",
        last_error="timeout",
        last_seen_ts="2026-06-11T10:05:00+00:00",
    )
    again = ProviderHealth.from_dict(ph.to_dict())
    assert again == ph


def test_provider_health_ts_roundtrip_none_fields() -> None:
    ph = ProviderHealth(
        entity_id="weather.home",
        success_count=0,
        failure_count=0,
        last_success_ts=None,
        last_failure_ts=None,
        last_error=None,
        last_seen_ts="2026-06-11T10:05:00+00:00",
    )
    again = ProviderHealth.from_dict(ph.to_dict())
    assert again == ph
    assert again.last_success_ts is None
    assert again.last_failure_ts is None
    assert again.last_error is None
    assert again.last_seen_ts == "2026-06-11T10:05:00+00:00"


def test_provider_health_defaults() -> None:
    ph = ProviderHealth.from_dict({})
    assert ph.entity_id == ""
    assert ph.success_count == 0
    assert ph.failure_count == 0
    assert ph.last_success_ts is None
    assert ph.last_failure_ts is None
    assert ph.last_error is None
    assert ph.last_seen_ts == ""


# ---------------------------------------------------------------------------
# StoredData
# ---------------------------------------------------------------------------


def test_stored_data_empty_defaults() -> None:
    sd = StoredData.empty()
    assert sd.wash_log == []
    assert sd.snooze_until is None
    assert sd.last_failover_ts is None
    assert sd.last_failover_from is None
    assert sd.last_failover_to is None
    assert sd.provider_health == {}


def test_stored_data_from_dict_none_returns_empty() -> None:
    assert StoredData.from_dict(None) == StoredData.empty()
    assert StoredData.from_dict({}) == StoredData.empty()


def test_stored_data_roundtrip_empty() -> None:
    sd = StoredData.empty()
    again = StoredData.from_dict(sd.to_dict())
    assert again == sd


def test_stored_data_roundtrip_full() -> None:
    sd = StoredData(
        wash_log=[
            WashEntry(timestamp="2026-06-01T08:00:00+00:00", source="manual"),
            WashEntry(timestamp="2026-06-08T09:30:00+00:00", source="auto"),
        ],
        snooze_until="2026-06-12T00:00:00+00:00",
        last_failover_ts="2026-06-09T11:00:00+00:00",
        last_failover_from="weather.primary",
        last_failover_to="weather.backup",
        provider_health={
            "weather.primary": ProviderHealth(
                entity_id="weather.primary",
                success_count=10,
                failure_count=1,
                last_success_ts="2026-06-11T10:00:00+00:00",
                last_failure_ts="2026-06-09T11:00:00+00:00",
                last_error="None returned",
                last_seen_ts="2026-06-11T10:05:00+00:00",
            ),
        },
    )
    again = StoredData.from_dict(sd.to_dict())
    assert again == sd


def test_stored_data_drops_non_dict_provider_health_entries() -> None:
    """Defensive: corrupt entries (non-dict values) must be silently dropped."""
    raw = {
        "wash_log": [],
        "provider_health": {
            "weather.good": {
                "entity_id": "weather.good",
                "success_count": 1,
                "failure_count": 0,
                "last_success_ts": None,
                "last_failure_ts": None,
                "last_error": None,
                "last_seen_ts": "2026-06-11T10:05:00+00:00",
            },
            "weather.broken": "not-a-dict",
        },
    }
    sd = StoredData.from_dict(raw)
    assert "weather.good" in sd.provider_health
    assert "weather.broken" not in sd.provider_health


def test_stored_data_handles_missing_override_field() -> None:
    sd = StoredData.from_dict({"wash_log": []})
    assert sd.wash_log == []


@pytest.mark.parametrize(
    "ts",
    [
        "2026-06-11T10:00:00",
        "2026-06-11T10:00:00+00:00",
        "2030-12-31T23:59:59+02:00",
    ],
)
def test_provider_health_various_iso_timestamps_roundtrip(ts: str) -> None:
    ph = ProviderHealth(
        entity_id="weather.home",
        success_count=1,
        failure_count=0,
        last_success_ts=ts,
        last_failure_ts=None,
        last_error=None,
        last_seen_ts=ts,
    )
    again = ProviderHealth.from_dict(ph.to_dict())
    assert again.last_success_ts == ts
    assert again.last_seen_ts == ts
