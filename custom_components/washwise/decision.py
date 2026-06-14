"""Pure decision algorithm for WashWise.

This module is intentionally free of any Home Assistant imports so the
algorithm can be unit-tested with deterministic inputs and reused from
non-HA contexts (CLI, notebooks, scripts).

The algorithm is documented in ``WASHWISE_PLAN.md`` section 2. Summary:

1. If the current weather condition is "bad" (rain, snow, hail, ...), the
   verdict is immediately negative (unless the entry uses inverted logic).
2. Otherwise, walk the next ``thresholds['days']`` forecast days and check
   each for blocking precipitation, blocking conditions, and (optionally)
   freezing temperatures. The freeze check carries the previous day's
   temperature forward as ``temp_check`` (using ``temp_min`` first, then
   ``temp_max`` for the next iteration) so that a transition through 0 °C
   between two days is caught.
3. Inverted logic (solar panels): blockers become positive signals --
   forecasted rain means the panels will self-clean, so the verdict is
   positive when at least one rainy day is in the horizon.
4. Score 0..100 is derived from weighted penalties (precip / freeze /
   condition), clamped to the [0, 100] range.

The module deliberately exposes only frozen, JSON-serialisable shapes so
that tests can drive it deterministically -- no randomness, no clocks,
no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
#
# These mirror the values that will be re-exported from ``const.py`` once that
# module lands (see plan §5). They are duplicated here so that ``decision.py``
# remains a self-contained pure module: a future refactor can either keep
# them in sync or delete them in favour of importing from ``.const`` -- the
# public API of ``compute`` will not change either way.

BAD_CONDITIONS: tuple[str, ...] = (
    "lightning-rainy",
    "rainy",
    "pouring",
    "snowy",
    "snowy-rainy",
    "hail",
    "exceptional",
)

BAD_CONDITION_SEVERITY: dict[str, float] = {
    "rainy": 0.5,
    "pouring": 1.0,
    "snowy": 0.7,
    "snowy-rainy": 0.8,
    "hail": 1.0,
    "lightning-rainy": 1.0,
    "exceptional": 1.0,
}

# Default weights (sum to 100). Can be overridden via ``thresholds``.
_DEFAULT_PRECIP_WEIGHT = 40.0
_DEFAULT_FREEZE_WEIGHT = 30.0
_DEFAULT_CONDITION_WEIGHT = 30.0

# Reason i18n keys (kept as constants so callers / tests can reference them
# without hard-coding strings).
REASON_CLEAR = "clear"
REASON_RAIN = "rain"
REASON_FREEZE = "freeze"
REASON_SNOW = "snow"
REASON_BAD_CONDITION = "bad_condition"
REASON_BAD_CURRENT_CONDITION = "bad_current_condition"

_SNOW_CONDITIONS = frozenset({"snowy", "snowy-rainy"})


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------
#
# These mirror the shapes that will be exported from ``models.py`` (plan §5).
# They are defined inline here so this module has no internal dependencies.


@dataclass(frozen=True, slots=True)
class ForecastDay:
    """A single forecast day, normalised to SI / Celsius."""

    date: date
    condition: str | None
    precipitation_mm: float | None
    temp_min_c: float | None
    temp_max_c: float | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CurrentWeather:
    """Current weather snapshot."""

    condition: str | None
    temperature_c: float | None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Decision:
    """Result of running ``compute`` on a weather snapshot + forecast."""

    can_wash: bool
    score: int
    reason: str
    days_until_wash: int | None
    blocking_days: list[date]
    forecast_summary: list[dict[str, Any]]
    days_analyzed: int


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute(
    current: CurrentWeather,
    forecast: list[ForecastDay],
    thresholds: dict[str, Any],
    *,
    invert: bool,
    now: datetime,
) -> Decision:
    """Run the WashWise decision algorithm.

    Parameters
    ----------
    current:
        Current weather snapshot. ``condition`` is checked against
        ``BAD_CONDITIONS`` for the short-circuit step; ``temperature_c`` is
        used as the seed for the freeze-check carry-forward.
    forecast:
        Ordered list of forecast days. The first ``thresholds['days']``
        entries are walked.
    thresholds:
        Algorithm tuning knobs. Recognised keys:

        * ``days`` (int): horizon length. Default ``3``.
        * ``precip_threshold_mm`` (float): rain mm > this is a blocker.
          Default ``0.2``.
        * ``freeze_check`` (bool): enable the freeze blocker. Default
          ``True``.
        * ``bad_conditions`` (Iterable[str] | None): override list of bad
          conditions. ``None``/missing falls back to ``BAD_CONDITIONS``.
        * ``precip_weight`` (float): scoring weight. Default ``40``.
        * ``freeze_weight`` (float): scoring weight. Default ``30``.
        * ``condition_weight`` (float): scoring weight. Default ``30``.
    invert:
        Flip the verdict semantics (solar panels). When ``True``, blockers
        become positive: forecasted rain means "wash" verdict True.
    now:
        Reference timestamp used to compute ``days_until_wash``.
        ``days_until_wash``. Tests pass an explicit ``datetime`` so output
        is fully deterministic.

    Returns
    -------
    Decision
        Frozen result object suitable for direct attribute exposure on the
        coordinator-driven entities.
    """

    horizon: int = int(thresholds.get("days", 3))
    precip_threshold: float = float(thresholds.get("precip_threshold_mm", 0.2))
    freeze_check_enabled: bool = bool(thresholds.get("freeze_check", True))
    bad_conditions_override = thresholds.get("bad_conditions")
    bad_conditions: tuple[str, ...] = (
        tuple(bad_conditions_override) if bad_conditions_override else BAD_CONDITIONS
    )

    precip_weight = float(thresholds.get("precip_weight", _DEFAULT_PRECIP_WEIGHT))
    freeze_weight = float(thresholds.get("freeze_weight", _DEFAULT_FREEZE_WEIGHT))
    condition_weight = float(thresholds.get("condition_weight", _DEFAULT_CONDITION_WEIGHT))

    today: date = now.date()

    # ------------------------------------------------------------------
    # Step 1: short-circuit on current condition (non-inverted only).
    # ------------------------------------------------------------------
    if not invert and current.condition is not None and current.condition in bad_conditions:
        return Decision(
            can_wash=False,
            score=0,
            reason=REASON_BAD_CURRENT_CONDITION,
            days_until_wash=None,
            blocking_days=[],
            forecast_summary=[],
            days_analyzed=0,
        )

    # ------------------------------------------------------------------
    # Step 2: walk forecast days, collect blockers + per-day breakdown.
    # ------------------------------------------------------------------
    walked: list[ForecastDay] = list(forecast[:horizon]) if horizon > 0 else []
    days_analyzed = len(walked)

    forecast_summary: list[dict[str, Any]] = []
    blocking_days: list[date] = []
    score = 100.0
    first_blocker_reason: str | None = None  # tracks why "today" failed

    # Carry-forward temperature seed for freeze check. Plan §2 step 2:
    # "Carry temp_check across days using temp_min then temp_max."
    temp_check: float | None = current.temperature_c

    # For invert mode we also collect rainy days so we can pick the first
    # one as the "next_window".
    rainy_days: list[date] = []

    for day in walked:
        day_blockers: list[str] = []

        precip = day.precipitation_mm
        cond = day.condition
        tmin = day.temp_min_c
        tmax = day.temp_max_c

        # Precipitation blocker.
        if precip is not None and precip > precip_threshold:
            day_blockers.append("precip")

        # Condition blocker.
        if cond is not None and cond in bad_conditions:
            day_blockers.append("condition")

        # Freeze blocker (carry-forward across days). The wording in the
        # plan -- "temp_check < 0 <= temp_min OR < 0 <= temp_max" -- means:
        # the previous day's checkpoint temp is below freezing AND today's
        # min (or max) is at/above freezing -> we crossed 0 °C, water on
        # the surface freezes / re-thaws -> bad.
        freeze_blocker = (
            freeze_check_enabled
            and temp_check is not None
            and (
                (tmin is not None and temp_check < 0 <= tmin)
                or (tmax is not None and temp_check < 0 <= tmax)
            )
        )
        if freeze_blocker:
            day_blockers.append("freeze")

        # Update temp_check for the *next* iteration: carry the highest
        # available temperature (prefer tmax) so a day that thawed via tmax
        # does not re-trigger a freeze blocker on the next iteration.
        if tmax is not None:
            temp_check = tmax
        elif tmin is not None:
            temp_check = tmin
        # else: leave temp_check unchanged so we keep the last known value.

        is_blocked = bool(day_blockers)
        if is_blocked:
            blocking_days.append(day.date)
            if first_blocker_reason is None:
                first_blocker_reason = _reason_from_blockers(day_blockers, cond)

        # Track rainy/blocked days for invert mode (includes freeze-blocked days
        # so sub-zero conditions also suppress irrigation/solar-clean signals).
        if (
            (precip is not None and precip > precip_threshold)
            or (cond is not None and cond in bad_conditions)
            or freeze_blocker
        ):
            rainy_days.append(day.date)

        # ------------------------------------------------------------------
        # Score penalties (plan §2 step 4).
        # ------------------------------------------------------------------
        day_score_penalty = 0.0
        if precip is not None and precip > 0:
            day_score_penalty += precip_weight * min(1.0, precip / max(0.1, precip_threshold))
        if freeze_blocker:
            day_score_penalty += freeze_weight
        if cond is not None and cond in bad_conditions:
            severity = BAD_CONDITION_SEVERITY.get(cond, 0.5)
            day_score_penalty += condition_weight * severity
        score -= day_score_penalty

        forecast_summary.append(
            {
                "date": day.date.isoformat() if day.date is not None else None,
                "condition": cond,
                "precipitation": precip,
                "temp_min": tmin,
                "temp_max": tmax,
                "blocked": is_blocked,
                "blockers": day_blockers,
                "day_score": max(0, round(100 - day_score_penalty)),
            }
        )

    score = max(0.0, min(100.0, score))
    score_int = round(score)

    # ------------------------------------------------------------------
    # Step 5 + 6: next window + days_until_wash.
    # ------------------------------------------------------------------
    if invert:
        # Solar mode: rain expected = clean panels = wash verdict True.
        if rainy_days:
            days_until_wash: int | None = (rainy_days[0] - today).days
            can_wash = True
            # Inverted scoring: invert the score so "lots of rain" = high.
            score_int = 100 - score_int
            reason = REASON_RAIN if first_blocker_reason is None else first_blocker_reason
            # When inverted, "blocking_days" semantics flip: the rainy days
            # are positive signals, not blockers.
            inverted_blocking_days = list(rainy_days)
            return Decision(
                can_wash=can_wash,
                score=score_int,
                reason=reason,
                days_until_wash=days_until_wash,
                blocking_days=inverted_blocking_days,
                forecast_summary=forecast_summary,
                days_analyzed=days_analyzed,
            )
        # No rain in horizon -> panels stay dirty; invert score for consistency.
        return Decision(
            can_wash=False,
            score=100 - score_int,
            reason=REASON_CLEAR,
            days_until_wash=None,
            blocking_days=[],
            forecast_summary=forecast_summary,
            days_analyzed=days_analyzed,
        )

    # Non-inverted mode: find the first non-blocking day.
    blocking_set = set(blocking_days)
    days_until_wash: int | None = None
    for day in walked:
        if day.date not in blocking_set:
            days_until_wash = (day.date - today).days
            break

    can_wash = not blocking_days and days_analyzed > 0
    if can_wash:
        reason = REASON_CLEAR
    elif days_analyzed == 0:
        reason = REASON_CLEAR
        # No forecast data — do not grant permission; treat as unavailable.
        can_wash = False
    else:
        reason = first_blocker_reason or REASON_BAD_CONDITION

    return Decision(
        can_wash=can_wash,
        score=score_int,
        reason=reason,
        days_until_wash=days_until_wash,
        blocking_days=blocking_days,
        forecast_summary=forecast_summary,
        days_analyzed=days_analyzed,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reason_from_blockers(blockers: list[str], condition: str | None) -> str:
    """Map an ordered list of blocker tags to the canonical i18n reason key."""
    if "freeze" in blockers:
        return REASON_FREEZE
    if condition is not None and condition in _SNOW_CONDITIONS:
        return REASON_SNOW
    if "precip" in blockers or condition == "rainy" or condition == "pouring":
        return REASON_RAIN
    return REASON_BAD_CONDITION


__all__ = [
    "BAD_CONDITIONS",
    "BAD_CONDITION_SEVERITY",
    "REASON_BAD_CONDITION",
    "REASON_BAD_CURRENT_CONDITION",
    "REASON_CLEAR",
    "REASON_FREEZE",
    "REASON_RAIN",
    "REASON_SNOW",
    "CurrentWeather",
    "Decision",
    "ForecastDay",
    "compute",
]
