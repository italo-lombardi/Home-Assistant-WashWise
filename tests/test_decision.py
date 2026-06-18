"""Tests for ``custom_components.washwise.decision.compute``.

Pure deterministic tests -- no clock, no I/O, no HA imports. Every test
passes an explicit ``now`` so timestamps are reproducible.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from custom_components.washwise.decision import (
    REASON_BAD_CURRENT_CONDITION,
    REASON_CLEAR,
    REASON_DIRTY_NOW,
    REASON_FREEZE,
    REASON_RAIN,
    CurrentWeather,
    ForecastDay,
    compute,
)

NOW = datetime(2026, 6, 11, 9, 0, tzinfo=UTC)
TODAY = NOW.date()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _day(
    offset: int,
    *,
    condition: str | None = "sunny",
    precip: float | None = 0.0,
    tmin: float | None = 12.0,
    tmax: float | None = 22.0,
) -> ForecastDay:
    return ForecastDay(
        date=date.fromordinal(TODAY.toordinal() + offset),
        condition=condition,
        precipitation_mm=precip,
        temp_min_c=tmin,
        temp_max_c=tmax,
    )


def _thresholds(**overrides) -> dict:
    base: dict = {
        "days": 3,
        "precip_threshold_mm": 0.2,
        "freeze_check": True,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Sunny baseline
# ---------------------------------------------------------------------------


def test_sunny_forecast_can_wash_true_score_100() -> None:
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [_day(i) for i in range(3)]

    result = compute(cur, forecast, _thresholds(), invert=False, now=NOW)

    assert result.can_wash is True
    assert result.score == 100
    assert result.reason == REASON_CLEAR
    assert result.blocking_days == []
    assert result.days_analyzed == 3
    # next_window = today → None (sensor shows "now", days_until_wash=0 conveys the info)
    assert result.days_until_wash == 0


# ---------------------------------------------------------------------------
# Rain blocker
# ---------------------------------------------------------------------------


def test_rain_day_one_above_threshold_blocks() -> None:
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [
        _day(0, condition="rainy", precip=5.0),
        _day(1),
        _day(2),
    ]

    result = compute(cur, forecast, _thresholds(), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.reason == REASON_RAIN
    assert result.blocking_days == [forecast[0].date]


# ---------------------------------------------------------------------------
# Bad current condition short-circuit
# ---------------------------------------------------------------------------


def test_bad_current_condition_rainy_blocks_immediately() -> None:
    cur = CurrentWeather(condition="rainy", temperature_c=10.0)
    forecast = [_day(i) for i in range(3)]  # forecast itself is fine

    result = compute(cur, forecast, _thresholds(), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.reason == REASON_BAD_CURRENT_CONDITION
    assert result.score == 0
    # Verdict is negative, but the forecast horizon is still walked so the
    # diagnostic sensors (Day N OK, Day N score, forecast aggregates) keep
    # rendering values instead of "Unknown".
    assert result.days_analyzed == 3
    assert len(result.forecast_summary) == 3
    # Sunny forecast — no per-day blockers; first clear day is offset 0.
    assert result.blocking_days == []
    assert result.days_until_wash == 0


def test_bad_current_condition_still_populates_forecast_summary() -> None:
    """Forecast walk must run even when current weather short-circuits the verdict.

    Mixed forecast (rainy / sunny / rainy) -> the per-day summary still
    flags the rainy days as blocked while the verdict stays negative.
    blocking_days is kept empty for bad-current: per-day blocked state is
    visible via forecast_summary[i]["blocked"] without mixing forecast
    dates into a field whose reason is current-weather-derived.
    """
    cur = CurrentWeather(condition="rainy", temperature_c=10.0)
    forecast = [
        _day(0, condition="rainy", precip=5.0),
        _day(1, condition="sunny", precip=0.0),
        _day(2, condition="rainy", precip=4.0),
    ]

    result = compute(cur, forecast, _thresholds(), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.reason == REASON_BAD_CURRENT_CONDITION
    assert result.score == 0
    assert result.days_analyzed == 3
    assert len(result.forecast_summary) == 3
    # blocking_days is empty — reason is current-weather, not forecast-derived.
    assert result.blocking_days == []

    day0, day1, day2 = result.forecast_summary
    assert day0["blocked"] is True
    assert day0["day_score"] < 100
    assert day1["blocked"] is False
    assert day1["day_score"] == 100
    assert day2["blocked"] is True
    assert day2["day_score"] < 100
    # day1 is the first unblocked day (offset 1).
    assert result.days_until_wash == 1


def test_bad_current_condition_days_until_wash_set_when_forecast_has_clear_day() -> None:
    """days_until_wash must resolve even under bad current weather.

    When current weather is bad but the forecast has a clear window,
    days_until_wash points to the first unblocked day so the sensor
    renders a value instead of Unknown.
    """
    cur = CurrentWeather(condition="rainy", temperature_c=10.0)
    forecast = [
        _day(0, condition="rainy", precip=5.0),  # blocked
        _day(1, condition="sunny", precip=0.0),  # clear — first window
        _day(2, condition="sunny", precip=0.0),
    ]

    result = compute(cur, forecast, _thresholds(), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.reason == REASON_BAD_CURRENT_CONDITION
    # First clear day is day offset 1 → 1 day away.
    assert result.days_until_wash == 1


def test_bad_current_condition_days_until_wash_none_when_all_blocked() -> None:
    """days_until_wash is None when every forecast day is also blocked."""
    cur = CurrentWeather(condition="rainy", temperature_c=10.0)
    forecast = [
        _day(0, condition="rainy", precip=5.0),
        _day(1, condition="rainy", precip=5.0),
        _day(2, condition="rainy", precip=5.0),
    ]

    result = compute(cur, forecast, _thresholds(), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.reason == REASON_BAD_CURRENT_CONDITION
    assert result.days_until_wash is None


# ---------------------------------------------------------------------------
# Freeze cross
# ---------------------------------------------------------------------------


def test_freeze_cross_returns_freeze_reason() -> None:
    """Current temp below 0, day1 min/max at or above 0 -> freeze cross."""
    cur = CurrentWeather(condition="sunny", temperature_c=-2.0)
    forecast = [
        # day1 crosses through 0 (temp_check=-2 < 0 <= tmin=1).
        _day(0, condition="sunny", precip=0.0, tmin=1.0, tmax=5.0),
        _day(1, condition="sunny", precip=0.0, tmin=2.0, tmax=6.0),
    ]

    result = compute(cur, forecast, _thresholds(days=2), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.reason == REASON_FREEZE
    assert forecast[0].date in result.blocking_days


def test_freeze_check_disabled_does_not_block() -> None:
    cur = CurrentWeather(condition="sunny", temperature_c=-2.0)
    forecast = [
        _day(0, condition="sunny", precip=0.0, tmin=1.0, tmax=5.0),
        _day(1, condition="sunny", precip=0.0, tmin=2.0, tmax=6.0),
    ]

    result = compute(
        cur,
        forecast,
        _thresholds(days=2, freeze_check=False),
        invert=False,
        now=NOW,
    )

    assert result.can_wash is True
    assert result.reason == REASON_CLEAR


# ---------------------------------------------------------------------------
# Solar (invert) mode
# ---------------------------------------------------------------------------


def test_invert_mode_with_rain_flips_to_true() -> None:
    """Solar panels: forecasted rain == self-cleaning == verdict True."""
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [
        _day(0),  # sunny
        _day(1, condition="rainy", precip=4.0),  # rainy day
        _day(2),
    ]

    result = compute(cur, forecast, _thresholds(), invert=True, now=NOW)

    assert result.can_wash is True
    # The first rainy day is the "next window" for self-cleaning.
    assert result.days_until_wash == 1
    # blocking_days in invert mode contains the rainy days.
    assert forecast[1].date in result.blocking_days


def test_invert_mode_no_rain_returns_false() -> None:
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [_day(i) for i in range(3)]  # all sunny

    result = compute(cur, forecast, _thresholds(), invert=True, now=NOW)

    assert result.can_wash is False
    assert result.days_until_wash is None


def test_invert_mode_bad_current_condition_short_circuits_to_wash() -> None:
    """Invert mode: bad current condition short-circuits to can_wash=True (panels dirty now)."""
    cur = CurrentWeather(condition="exceptional", temperature_c=28.0)
    forecast: list = []

    result = compute(cur, forecast, _thresholds(days=0), invert=True, now=NOW)

    assert result.can_wash is True
    assert result.score == 100
    assert result.reason == REASON_DIRTY_NOW
    assert result.days_until_wash == 0


def test_invert_mode_bad_current_condition_short_circuits_with_non_zero_horizon() -> None:
    """Invert mode: bad current condition short-circuits regardless of horizon.

    days=1 covers the garden_irrigation preset (const.py: days=1, invert=True).
    """
    cur = CurrentWeather(condition="rainy", temperature_c=12.0)
    forecast = [_day(0, condition="sunny", precip=0.0)]

    result = compute(cur, forecast, _thresholds(days=1), invert=True, now=NOW)

    assert result.can_wash is True
    assert result.reason == REASON_DIRTY_NOW
    assert result.days_until_wash == 0
    assert result.forecast_summary == []


def test_invert_mode_clear_current_condition_and_forecast_returns_no_wash() -> None:
    """Invert mode: clear sky and no rain in forecast → panels stay clean → no wash needed."""
    cur = CurrentWeather(condition="sunny", temperature_c=28.0)
    forecast = [_day(0), _day(1), _day(2)]

    result = compute(cur, forecast, _thresholds(), invert=True, now=NOW)

    assert result.can_wash is False
    assert result.reason == REASON_CLEAR


# ---------------------------------------------------------------------------
# Score weights respected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "precip_weight,expected_max_score",
    [
        (40, 99),  # weight 40 -> heavy penalty -> score < 100
        (0, 100),  # weight 0 -> no precip penalty -> score stays 100
    ],
)
def test_score_respects_precip_weight(precip_weight: int, expected_max_score: int) -> None:
    """Same forecast, two precip_weights -> the high-weight score is lower."""
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    # One day of light rain that does NOT exceed the threshold (so no
    # blocker, only a score penalty).
    forecast = [
        _day(0, condition="sunny", precip=0.05),
        _day(1, condition="sunny", precip=0.0),
        _day(2, condition="sunny", precip=0.0),
    ]

    result = compute(
        cur,
        forecast,
        _thresholds(precip_weight=precip_weight),
        invert=False,
        now=NOW,
    )

    if precip_weight == 0:
        assert result.score == 100
    else:
        # weight 40 + small precip -> something less than 100 but verdict
        # still True (precip below threshold).
        assert result.can_wash is True
        assert result.score < 100


def test_score_weights_zero_keeps_100_even_with_blocker() -> None:
    """Setting all weights to zero -> score never decreases, but verdict
    still reflects the blocker."""
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [_day(0, condition="rainy", precip=10.0)]

    result = compute(
        cur,
        forecast,
        _thresholds(
            days=1,
            precip_weight=0,
            freeze_weight=0,
            condition_weight=0,
        ),
        invert=False,
        now=NOW,
    )

    assert result.can_wash is False  # blocker still applies
    assert result.score == 100  # but score untouched

    # ---------------------------------------------------------------------------
    # ---------------------------------------------------------------------------

    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [
        _day(0, condition="rainy", precip=5.0),  # blocked
        _day(1, condition="rainy", precip=5.0),  # blocked
        _day(2, condition="sunny", precip=0.0),  # clear -> next window
    ]

    result = compute(cur, forecast, _thresholds(), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.days_until_wash == 2

    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [
        _day(0, condition="rainy", precip=5.0),
        _day(1, condition="rainy", precip=5.0),
        _day(2, condition="rainy", precip=5.0),
    ]

    result = compute(cur, forecast, _thresholds(), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.days_until_wash is None


# ---------------------------------------------------------------------------
# blocking_days exactly the bad dates
# ---------------------------------------------------------------------------


def test_blocking_days_lists_exact_bad_dates() -> None:
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [
        _day(0, condition="sunny", precip=0.0),  # ok
        _day(1, condition="rainy", precip=4.0),  # blocked
        _day(2, condition="sunny", precip=0.0),  # ok
        _day(3, condition="rainy", precip=4.0),  # blocked
    ]

    result = compute(cur, forecast, _thresholds(days=4), invert=False, now=NOW)

    assert result.blocking_days == [forecast[1].date, forecast[3].date]


# ---------------------------------------------------------------------------
# days_analyzed == min(forecast_len, threshold_days)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "horizon,forecast_len,expected_analyzed",
    [
        (3, 5, 3),  # forecast longer than horizon -> horizon
        (5, 3, 3),  # horizon longer than forecast -> forecast
        (3, 3, 3),  # equal
        (1, 7, 1),  # short horizon
    ],
)
def test_days_analyzed_equals_min_of_horizon_and_forecast_len(
    horizon: int, forecast_len: int, expected_analyzed: int
) -> None:
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [_day(i) for i in range(forecast_len)]

    result = compute(cur, forecast, _thresholds(days=horizon), invert=False, now=NOW)

    assert result.days_analyzed == expected_analyzed


# ---------------------------------------------------------------------------
# Empty forecast
# ---------------------------------------------------------------------------


def test_empty_forecast_returns_can_wash_true() -> None:
    """No forecast data == no blockers == verdict False (treat as unavailable)."""
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)

    result = compute(cur, [], _thresholds(), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.reason == REASON_CLEAR
    assert result.days_analyzed == 0
    assert result.blocking_days == []


def test_zero_horizon_returns_can_wash_true() -> None:
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [_day(i) for i in range(3)]

    result = compute(cur, forecast, _thresholds(days=0), invert=False, now=NOW)

    assert result.can_wash is False
    assert result.reason == REASON_CLEAR
    assert result.days_analyzed == 0


# ---------------------------------------------------------------------------
# Threshold parametrize: precip flips verdict
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "threshold_mm,precip_mm,expected_can_wash",
    [
        (0.1, 0.3, False),  # 0.3 > 0.1 -> blocked
        (0.5, 0.3, True),  # 0.3 < 0.5 -> ok
    ],
)
def test_precip_threshold_flips_verdict(
    threshold_mm: float, precip_mm: float, expected_can_wash: bool
) -> None:
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    # Use a non-blocking condition so only precip drives the verdict.
    forecast = [_day(0, condition="cloudy", precip=precip_mm)]

    result = compute(
        cur,
        forecast,
        _thresholds(days=1, precip_threshold_mm=threshold_mm),
        invert=False,
        now=NOW,
    )

    assert result.can_wash is expected_can_wash


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_compute_is_deterministic_for_fixed_now() -> None:
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [_day(i) for i in range(3)]
    args = (cur, forecast, _thresholds())
    a = compute(*args, invert=False, now=NOW)
    b = compute(*args, invert=False, now=NOW)
    assert a == b


def test_compute_naive_now_yields_naive_window_ts() -> None:
    naive_now = datetime(2026, 6, 11, 9, 0)
    cur = CurrentWeather(condition="sunny", temperature_c=18.0)
    forecast = [
        ForecastDay(
            date=date(2026, 6, 11),
            condition="sunny",
            precipitation_mm=0.0,
            temp_min_c=12.0,
            temp_max_c=22.0,
        ),
    ]

    compute(cur, forecast, _thresholds(days=1), invert=False, now=naive_now)

    # window is today → None


# ---------------------------------------------------------------------------
# tmax carry-forward branch (decision.py lines 257-260)
# ---------------------------------------------------------------------------


def test_freeze_tmax_carry_forward_clears_next_day() -> None:
    """tmax is available: carry tmax forward so next iteration uses it (not tmin)."""
    # Start frozen (temp=-2), day0 has tmax=3 (thaw via tmax).
    # temp_check after day0 becomes tmax=3 (>0), so day1 should NOT re-trigger freeze.
    cur = CurrentWeather(condition="sunny", temperature_c=-2.0)
    forecast = [
        # day0: tmin=None, tmax=3 → temp_check=-2 < 0 <= tmax=3 → freeze blocker
        _day(0, condition="sunny", precip=0.0, tmin=None, tmax=3.0),
        # day1: tmin=None, tmax=8 → temp_check=3 (carried from day0 tmax) → no freeze
        _day(1, condition="sunny", precip=0.0, tmin=None, tmax=8.0),
    ]

    result = compute(cur, forecast, _thresholds(days=2), invert=False, now=NOW)

    assert result.reason == REASON_FREEZE
    # Only day0 blocked; day1 is clear because tmax carry-forward sets temp_check=3.
    assert forecast[0].date in result.blocking_days
    assert forecast[1].date not in result.blocking_days


def test_freeze_tmin_only_carry_forward() -> None:
    """When tmax is None but tmin is available, tmin is used for carry-forward."""
    cur = CurrentWeather(condition="sunny", temperature_c=-2.0)
    forecast = [
        # day0: tmin=1, tmax=None → temp_check=-2 < 0 <= tmin=1 → freeze blocker
        # After day0: tmax=None, tmin=1 → temp_check becomes tmin=1.
        _day(0, condition="sunny", precip=0.0, tmin=1.0, tmax=None),
        # day1: tmin=5, tmax=None → temp_check=1 (>0) → no freeze
        _day(1, condition="sunny", precip=0.0, tmin=5.0, tmax=None),
    ]

    result = compute(cur, forecast, _thresholds(days=2), invert=False, now=NOW)

    assert result.reason == REASON_FREEZE
    assert forecast[0].date in result.blocking_days
    assert forecast[1].date not in result.blocking_days
