"""Sensor platform for WashWise.

Exposes the full set of read-only sensors described in plan section 5:

* Primary signals — score, next wash window, blocking reason, days until/since
  the last wash, last washed timestamp, 30-day wash count.
* Provider visibility — active provider label, active weather entity id, last
  successful coordinator update.
* Diagnostics (``EntityCategory.DIAGNOSTIC``) — days analysed, total
  precipitation, freeze risk, worst forecast condition, min/max temperatures,
  primary provider uptime, snooze remaining.
* Per-forecast-day score — one ``day_{i}_score`` sensor per configured horizon
  day so dashboards/automations can read individual day scores without parsing
  the ``forecast_summary`` attribute.

Every sensor is a :class:`CoordinatorEntity` bound to the
:class:`~custom_components.washwise.coordinator.WashWiseCoordinator`. Sensors
read primarily from ``coordinator.data`` (a :class:`Decision`) and fall back to
the persistent :class:`~custom_components.washwise.storage.WashWiseStore` for
wash log / snooze / provider health values, which the coordinator caches via
its update cycle.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfTemperature, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CATEGORY_PRESETS,
    CONF_CATEGORY,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_DAYS,
    CONF_RAIN_GAUGE_THRESHOLD_MM,
    CONF_WEATHER_ENTITIES,
    DEFAULT_CATEGORY,
    DEFAULT_RAIN_GAUGE_THRESHOLD_MM,
    DOMAIN,
)
from .coordinator import WashWiseCoordinator
from .device import device_info
from .models import Decision

# ----------------------------------------------------------------------
# Setup
# ----------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register all WashWise sensors for one config entry."""
    coordinator: WashWiseCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        ScoreSensor(coordinator, entry),
        ReasonSensor(coordinator, entry),
        DaysUntilWashSensor(coordinator, entry),
        DaysSinceWashSensor(coordinator, entry),
        LastWashedSensor(coordinator, entry),
        WashCount30dSensor(coordinator, entry),
        ActiveProviderSensor(coordinator, entry),
        LastUpdateSensor(coordinator, entry),
        DaysAnalyzedSensor(coordinator, entry),
        PrecipTotalMmSensor(coordinator, entry),
        WorstConditionSensor(coordinator, entry),
        MinTempSensor(coordinator, entry),
        MaxTempSensor(coordinator, entry),
        PrimaryProviderUptimeSensor(coordinator, entry),
        SnoozeRemainingSensor(coordinator, entry),
        CategorySensor(coordinator, entry),
    ]

    days = _resolve_horizon(entry)
    for i in range(days):
        entities.append(DayScoreSensor(coordinator, entry, i))

    category = entry.data.get(CONF_CATEGORY, DEFAULT_CATEGORY)
    if category == "garden_irrigation":
        entities.append(MeasuredRainMmSensor(coordinator, entry))
        entities.append(RainGaugeThresholdSensor(coordinator, entry))

    async_add_entities(entities)


def _resolve_horizon(entry: ConfigEntry) -> int:
    """Return the number of forecast days the user picked.

    Mirrors :meth:`WashWiseCoordinator._resolve_thresholds` for the
    ``days`` value so the per-day score sensor count matches what the
    coordinator actually computes. Defaults safely to the ``car``
    preset.
    """
    options = entry.options or {}
    customize = bool(
        options.get(CONF_CUSTOMIZE_THRESHOLDS, False)
        or entry.data.get(CONF_CUSTOMIZE_THRESHOLDS, False)
    )
    category = entry.data.get(CONF_CATEGORY, DEFAULT_CATEGORY)
    preset = CATEGORY_PRESETS.get(category, CATEGORY_PRESETS[DEFAULT_CATEGORY])
    if customize:
        # Mirror coordinator: options override entry.data; fall back to preset.
        for source in (options, entry.data or {}):
            if CONF_DAYS in source:
                try:
                    return max(0, int(source[CONF_DAYS]))
                except (TypeError, ValueError):
                    break
        return int(preset.get("days", 3))
    return int(preset.get("days", 3))


# ----------------------------------------------------------------------
# Base class
# ----------------------------------------------------------------------


class WashWiseSensorBase(CoordinatorEntity[WashWiseCoordinator], SensorEntity):
    """Common boilerplate: device info, unique_id, has_entity_name."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WashWiseCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Wire up the base attributes shared by every WashWise sensor."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = device_info(entry)

    # Convenience accessors -------------------------------------------------

    @property
    def _decision(self) -> Decision | None:
        """Return the latest ``Decision`` produced by the coordinator."""
        return self.coordinator.data

    @property
    def _stored(self):  # type: ignore[no-untyped-def]
        """Return the cached :class:`StoredData` if the store exposes it.

        The coordinator's persistent store loads/save asynchronously; we
        avoid awaiting from a property by reading any cached snapshot the
        store may expose. If the store doesn't cache, sensors that depend
        on persisted data return ``None`` until the next refresh.
        """
        store = getattr(self.coordinator, "_store", None)
        return getattr(store, "_data", None) if store is not None else None


# ----------------------------------------------------------------------
# Primary signals
# ----------------------------------------------------------------------


class ScoreSensor(WashWiseSensorBase):
    """Numeric 0-100 confidence that the wash window is good."""

    _attr_icon = "mdi:gauge"
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the score sensor."""
        super().__init__(coordinator, entry, "score")

    @property
    def native_value(self) -> int | None:
        """Return the current decision score (0-100)."""
        decision = self._decision
        if decision is None:
            return None
        return int(decision.score)


class ReasonSensor(WashWiseSensorBase):
    """Translated reason key explaining the current verdict."""

    _attr_icon = "mdi:information-outline"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options: ClassVar[list[str]] = [
        "clear",
        "rain",
        "freeze",
        "snow",
        "bad_condition",
        "bad_current_condition",
        "dirty_now",
        "snoozed",
        "unavailable",
    ]

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the reason sensor."""
        super().__init__(coordinator, entry, "reason")

    @property
    def native_value(self) -> str | None:
        """Return the current reason key (translated by HA)."""
        decision = self._decision
        if decision is None:
            return None
        return decision.reason or None


class DaysUntilWashSensor(WashWiseSensorBase):
    """Whole-day count until the next acceptable wash window."""

    _attr_icon = "mdi:calendar-arrow-right"
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the days-until-wash sensor."""
        super().__init__(coordinator, entry, "days_until_wash")

    @property
    def native_value(self) -> int | None:
        """Return how many whole days until the next wash window opens."""
        decision = self._decision
        if decision is None:
            return None
        return decision.days_until_wash


class DaysSinceWashSensor(WashWiseSensorBase):
    """Whole-day count since the last recorded wash."""

    _attr_icon = "mdi:calendar-arrow-left"
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the days-since-wash sensor."""
        super().__init__(coordinator, entry, "days_since_wash")

    @property
    def native_value(self) -> int | None:
        """Return days since the last entry in ``wash_log``."""
        ts = _last_wash_timestamp(self._stored)
        if ts is None:
            return None
        delta = datetime.now(UTC) - ts
        return max(0, delta.days)


class LastWashedSensor(WashWiseSensorBase):
    """Timestamp of the most recent wash log entry."""

    _attr_icon = "mdi:car-wash"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the last-washed timestamp sensor."""
        super().__init__(coordinator, entry, "last_washed")

    @property
    def native_value(self) -> datetime | None:
        """Return the timestamp of the last wash, if any."""
        return _last_wash_timestamp(self._stored)


class WashCount30dSensor(WashWiseSensorBase):
    """Number of wash log entries in the trailing 30 days."""

    _attr_icon = "mdi:counter"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the 30-day wash count sensor."""
        super().__init__(coordinator, entry, "wash_count_30d")

    @property
    def native_value(self) -> int | None:
        """Return number of wash entries in the last 30 days."""
        stored = self._stored
        if stored is None:
            return None
        cutoff = datetime.now(UTC) - timedelta(days=30)
        count = 0
        for entry in getattr(stored, "wash_log", []) or []:
            ts = _parse_iso(getattr(entry, "timestamp", None))
            if ts is not None and ts >= cutoff:
                count += 1
        return count


# ----------------------------------------------------------------------
# Provider visibility
# ----------------------------------------------------------------------


class ActiveProviderSensor(WashWiseSensorBase):
    """Friendly label of the weather provider used for the last update."""

    _attr_icon = "mdi:weather-cloudy-clock"

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the active-provider label sensor."""
        super().__init__(coordinator, entry, "active_provider")

    @property
    def native_value(self) -> str | None:
        """Return ``friendly_name`` of the active provider, falling back to its id."""
        return self.coordinator.active_provider_label

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the active weather entity id as an attribute."""
        entity_id = self.coordinator.active_weather_entity
        if entity_id is None:
            return None
        return {"weather_entity_id": entity_id}


class LastUpdateSensor(WashWiseSensorBase):
    """Timestamp of the last successful coordinator refresh."""

    _attr_icon = "mdi:update"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the last-update timestamp sensor."""
        super().__init__(coordinator, entry, "last_update")

    @property
    def native_value(self) -> datetime | None:
        """Return the coordinator's ``last_update_success`` time."""
        ts = getattr(self.coordinator, "last_update_success_time", None)
        if isinstance(ts, datetime):
            return ts
        return None


# ----------------------------------------------------------------------
# Diagnostics
# ----------------------------------------------------------------------


class _DiagnosticBase(WashWiseSensorBase):
    """Base for diagnostic sensors so they all land in the diagnostics group."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC


class DaysAnalyzedSensor(_DiagnosticBase):
    """Number of forecast days that fed the latest decision."""

    _attr_icon = "mdi:calendar-search"
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the diagnostic ``days_analyzed`` sensor."""
        super().__init__(coordinator, entry, "days_analyzed")

    @property
    def native_value(self) -> int | None:
        """Return how many days the algorithm walked."""
        decision = self._decision
        if decision is None:
            return None
        return int(decision.days_analyzed)


class PrecipTotalMmSensor(_DiagnosticBase):
    """Sum of forecast precipitation across the analysed horizon."""

    _attr_icon = "mdi:weather-pouring"
    _attr_native_unit_of_measurement = "mm"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the diagnostic precipitation total sensor."""
        super().__init__(coordinator, entry, "precip_total_mm")

    @property
    def native_value(self) -> float | None:
        """Return the cumulative forecast precipitation in millimetres."""
        decision = self._decision
        if decision is None:
            return None
        total = 0.0
        any_value = False
        for day in decision.forecast_summary or []:
            value = day.get("precipitation")
            if value is None:
                continue
            try:
                total += float(value)
                any_value = True
            except (TypeError, ValueError):
                continue
        if not any_value:
            return None
        return round(total, 2)


class WorstConditionSensor(_DiagnosticBase):
    """Worst (most-severe) condition seen across the analysed horizon."""

    _attr_icon = "mdi:weather-lightning-rainy"

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the diagnostic worst-condition sensor."""
        super().__init__(coordinator, entry, "worst_condition")

    @property
    def native_value(self) -> str | None:
        """Return the most severe forecast condition string seen."""
        from .const import BAD_CONDITION_SEVERITY

        decision = self._decision
        if decision is None:
            return None
        worst: tuple[float, str] | None = None
        for day in decision.forecast_summary or []:
            cond = day.get("condition")
            if not cond:
                continue
            severity = float(BAD_CONDITION_SEVERITY.get(cond, 0.0))
            if worst is None or severity > worst[0]:
                worst = (severity, cond)
        return worst[1] if worst is not None else None


class MinTempSensor(_DiagnosticBase):
    """Minimum forecast temperature across the analysed horizon."""

    _attr_icon = "mdi:thermometer-low"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the diagnostic minimum-temperature sensor."""
        super().__init__(coordinator, entry, "min_temp")

    @property
    def native_value(self) -> float | None:
        """Return the lowest ``temp_min`` across the forecast summary."""
        return _temp_extreme(self._decision, "temp_min", _min=True)


class MaxTempSensor(_DiagnosticBase):
    """Maximum forecast temperature across the analysed horizon."""

    _attr_icon = "mdi:thermometer-high"
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the diagnostic maximum-temperature sensor."""
        super().__init__(coordinator, entry, "max_temp")

    @property
    def native_value(self) -> float | None:
        """Return the highest ``temp_max`` across the forecast summary."""
        return _temp_extreme(self._decision, "temp_max", _min=False)


class PrimaryProviderUptimeSensor(_DiagnosticBase):
    """Success ratio for the primary configured weather entity (0-100 %)."""

    _attr_icon = "mdi:check-network-outline"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the diagnostic primary-provider uptime sensor."""
        super().__init__(coordinator, entry, "primary_provider_uptime")

    @property
    def native_value(self) -> float | None:
        """Return the success ratio of the primary provider (percent)."""
        weather_ids = self._entry.data.get(CONF_WEATHER_ENTITIES) or []
        if not weather_ids:
            return None
        primary = weather_ids[0]
        stored = self._stored
        if stored is None:
            return None
        health = (getattr(stored, "provider_health", {}) or {}).get(primary)
        if health is None:
            return None
        success = int(getattr(health, "success_count", 0))
        failure = int(getattr(health, "failure_count", 0))
        total = success + failure
        if total <= 0:
            return None
        return round(100.0 * success / total, 1)


class SnoozeRemainingSensor(_DiagnosticBase):
    """Time remaining on the active snooze, or ``None`` when not snoozed."""

    _attr_icon = "mdi:timer-sand"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the diagnostic snooze-remaining sensor."""
        super().__init__(coordinator, entry, "snooze_remaining")

    @property
    def native_value(self) -> int | None:
        """Return minutes left on the snooze, or ``None`` when inactive."""
        stored = self._stored
        if stored is None:
            return None
        snooze_until = getattr(stored, "snooze_until", None)
        if not snooze_until:
            return None
        ts = _parse_iso(snooze_until)
        if ts is None:
            return None
        remaining = (ts - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return None
        return max(1, int(remaining / 60))

    @property
    def extra_state_attributes(self) -> dict:
        """Expose snooze_until ISO timestamp so clients can compute remaining live."""
        stored = self._stored
        snooze_until = getattr(stored, "snooze_until", None) if stored else None
        return {"snooze_until": snooze_until}


class CategorySensor(_DiagnosticBase):
    """Diagnostic sensor: the category configured for this instance (car, boat, etc.)."""

    _attr_icon = "mdi:shape-outline"

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the diagnostic category sensor."""
        super().__init__(coordinator, entry, "category")

    @property
    def native_value(self) -> str | None:
        """Return the configured category key (e.g. ``car``, ``boat``)."""
        return self._entry.data.get(CONF_CATEGORY) or None


# ----------------------------------------------------------------------
# Per-day score sensors
# ----------------------------------------------------------------------


class DayScoreSensor(WashWiseSensorBase):
    """Per-forecast-day score (0-100) for index ``i`` in the horizon."""

    _attr_icon = "mdi:calendar-today"
    _attr_native_unit_of_measurement = PERCENTAGE

    def __init__(
        self,
        coordinator: WashWiseCoordinator,
        entry: ConfigEntry,
        index: int,
    ) -> None:
        """Register a per-day score sensor for forecast day ``index``.

        ``unique_id`` carries the day index so each sensor is distinct, but
        ``translation_key`` is the shared ``day_score`` slot fed with a
        ``{day}`` placeholder so HA renders names like "Day 1 score".
        Without the placeholder route the per-day sensors all fall back to
        the device name and look identical in the UI.
        """
        super().__init__(coordinator, entry, f"day_{index + 1}_score")
        self._index = index
        # Override the translation key set by the base class so all per-day
        # sensors share the single ``day_score`` translation entry.
        self._attr_translation_key = "day_score"
        self._attr_translation_placeholders = {"day": str(index + 1)}

    @property
    def native_value(self) -> int | None:
        """Return the per-day score (0-100) from the decision summary."""
        decision = self._decision
        if decision is None:
            return None
        summary = decision.forecast_summary or []
        if self._index >= len(summary):
            return None
        day = summary[self._index]
        # Fallback to binary 0/100 for Decision objects loaded from storage
        # that pre-date v0.2.0 (forecast_summary dicts without ``day_score``).
        return int(day.get("day_score", 0 if day.get("blocked") else 100))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the underlying forecast row for context."""
        decision = self._decision
        if decision is None:
            return None
        summary = decision.forecast_summary or []
        if self._index >= len(summary):
            return None
        day = summary[self._index]
        return {
            "date": _coerce_str(day.get("date")),
            "condition": day.get("condition"),
            "precipitation": day.get("precipitation"),
            "temp_min": day.get("temp_min"),
            "temp_max": day.get("temp_max"),
            "blockers": list(day.get("blockers") or []),
        }


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _last_wash_timestamp(stored: Any) -> datetime | None:
    """Return the timestamp of the most recent wash log entry, or ``None``."""
    if stored is None:
        return None
    log = list(getattr(stored, "wash_log", []) or [])
    if not log:
        return None
    last = log[-1]
    return _parse_iso(getattr(last, "timestamp", None))


def _parse_iso(value: Any) -> datetime | None:
    """Parse an ISO-8601 string into an aware UTC ``datetime``.

    Returns ``None`` for falsy/invalid input. Naive timestamps are
    promoted to UTC so downstream subtraction always yields a real
    duration.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def _temp_extreme(decision: Decision | None, key: str, *, _min: bool) -> float | None:
    """Return the min or max of ``key`` across the forecast summary."""
    if decision is None:
        return None
    values: list[float] = []
    for day in decision.forecast_summary or []:
        raw = day.get(key)
        if raw is None:
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        return None
    return round(min(values) if _min else max(values), 1)


def _coerce_str(value: Any) -> str | None:
    """Stringify dates/datetimes for safe attribute exposure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()  # type: ignore[no-any-return]
        except Exception:
            return str(value)
    return str(value)


class MeasuredRainMmSensor(WashWiseSensorBase):
    """Sensor: current rain gauge reading in mm.

    Only registered for the 'garden_irrigation' category. Reads from the
    coordinator's ``measured_rain_mm`` property which is populated each tick
    from the configured rain gauge sensor entity.
    """

    _attr_icon = "mdi:water-plus"
    _attr_native_unit_of_measurement = "mm"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the measured-rain sensor."""
        super().__init__(coordinator, entry, "measured_rain_mm")

    @property
    def native_value(self) -> float | None:
        """Return current rain gauge reading."""
        return self.coordinator.measured_rain_mm

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Expose the configured threshold for reference."""
        options = self._entry.options or {}
        data = self._entry.data or {}
        threshold = options.get(
            CONF_RAIN_GAUGE_THRESHOLD_MM,
            data.get(CONF_RAIN_GAUGE_THRESHOLD_MM, DEFAULT_RAIN_GAUGE_THRESHOLD_MM),
        )
        return {"threshold_mm": float(threshold)}


class RainGaugeThresholdSensor(WashWiseSensorBase):
    """Diagnostic sensor: configured rain gauge suppression threshold in mm."""

    _attr_icon = "mdi:water-alert"
    _attr_native_unit_of_measurement = "mm"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the rain gauge threshold sensor."""
        super().__init__(coordinator, entry, "rain_gauge_threshold_mm")

    @property
    def native_value(self) -> float | None:
        """Return the configured suppression threshold."""
        return self.coordinator.rain_gauge_threshold_mm


__all__ = ["async_setup_entry"]
