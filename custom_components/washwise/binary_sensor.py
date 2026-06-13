"""Binary sensor platform for WashWise.

Exposes the primary ``can_wash`` verdict plus one ``day_{i}_ok`` sensor for
each forecast day in the configured horizon. Both classes share a single
:class:`CoordinatorEntity` base so the entities re-render when the
:class:`~custom_components.washwise.coordinator.WashWiseCoordinator` produces a
fresh :class:`~custom_components.washwise.models.Decision`.

Entity layout (per config entry):

* ``binary_sensor.<name>_can_wash`` — primary True/False verdict with rich
  diagnostic attributes (forecast summary, score, reason, …).
* ``binary_sensor.<name>_day_<i+1>_ok`` — per-day verdict, ``i`` indexes into
  ``Decision.forecast_summary``. Count == ``thresholds['days']``.

``unique_id`` is always ``"<entry_id>_<key>"`` so entities survive renames and
multi-instance installs without collisions.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CATEGORY_PRESETS,
    CONF_BAD_CONDITIONS,
    CONF_CATEGORY,
    CONF_CONDITION_WEIGHT,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_DAYS,
    CONF_FREEZE_CHECK,
    CONF_FREEZE_WEIGHT,
    CONF_PRECIP_THRESHOLD,
    CONF_PRECIP_WEIGHT,
    DEFAULT_CATEGORY,
    DEFAULT_CONDITION_WEIGHT,
    DEFAULT_FREEZE_WEIGHT,
    DEFAULT_PRECIP_WEIGHT,
    DOMAIN,
)
from .coordinator import WashWiseCoordinator
from .device import device_info


def _resolve_thresholds(entry: ConfigEntry) -> dict[str, Any]:
    """Return the effective thresholds dict for ``entry``.

    Mirrors the logic in :meth:`WashWiseCoordinator._resolve_thresholds` but
    runs at platform-setup time, before the coordinator has produced any
    data. We only need the ``days`` field here to size the per-day sensor
    list, but the full dict is returned for symmetry with the coordinator
    so future fields are available without another round of plumbing.
    """
    category = entry.data.get(CONF_CATEGORY, DEFAULT_CATEGORY)
    preset = CATEGORY_PRESETS.get(category, CATEGORY_PRESETS[DEFAULT_CATEGORY])

    options = entry.options or {}
    customize = bool(
        options.get(CONF_CUSTOMIZE_THRESHOLDS, False)
        or entry.data.get(CONF_CUSTOMIZE_THRESHOLDS, False)
    )

    if customize:
        data = entry.data or {}

        def _pick(key: str, default: Any) -> Any:
            if key in options:
                return options[key]
            if key in data:
                return data[key]
            return default

        thresholds: dict[str, Any] = {
            "days": int(_pick(CONF_DAYS, preset.get("days", 3))),
            "precip_threshold_mm": float(
                _pick(CONF_PRECIP_THRESHOLD, preset.get("precip_threshold_mm", 0.2))
            ),
            "freeze_check": bool(_pick(CONF_FREEZE_CHECK, preset.get("freeze_check", True))),
            "precip_weight": float(_pick(CONF_PRECIP_WEIGHT, DEFAULT_PRECIP_WEIGHT)),
            "freeze_weight": float(_pick(CONF_FREEZE_WEIGHT, DEFAULT_FREEZE_WEIGHT)),
            "condition_weight": float(_pick(CONF_CONDITION_WEIGHT, DEFAULT_CONDITION_WEIGHT)),
        }
        bad_override = _pick(CONF_BAD_CONDITIONS, None)
        if bad_override:
            thresholds["bad_conditions"] = list(bad_override)
        return thresholds

    return {
        "days": int(preset.get("days", 3)),
        "precip_threshold_mm": float(preset.get("precip_threshold_mm", 0.2)),
        "freeze_check": bool(preset.get("freeze_check", True)),
        "precip_weight": float(DEFAULT_PRECIP_WEIGHT),
        "freeze_weight": float(DEFAULT_FREEZE_WEIGHT),
        "condition_weight": float(DEFAULT_CONDITION_WEIGHT),
    }


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Register the ``can_wash`` and per-day binary sensors."""
    coordinator: WashWiseCoordinator = hass.data[DOMAIN][entry.entry_id]
    thresholds = _resolve_thresholds(entry)

    entities: list[BinarySensorEntity] = [
        WashWiseCanWashBinarySensor(coordinator, entry),
        WashWiseFreezeRiskBinarySensor(coordinator, entry),
    ]
    for i in range(int(thresholds.get("days", 0) or 0)):
        entities.append(WashWiseDayOkBinarySensor(coordinator, entry, i))

    category = entry.data.get(CONF_CATEGORY, DEFAULT_CATEGORY)
    if category == "garden_irrigation":
        entities.append(IrrigationSuppressedBinarySensor(coordinator, entry))
        entities.append(ForecastBlocksIrrigationBinarySensor(coordinator, entry))
        entities.append(IrrigationSwitchStateBinarySensor(coordinator, entry))

    async_add_entities(entities)


class _WashWiseBinarySensorBase(CoordinatorEntity[WashWiseCoordinator], BinarySensorEntity):
    """Common base for WashWise binary sensors."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: WashWiseCoordinator,
        entry: ConfigEntry,
        key: str,
    ) -> None:
        """Wire the coordinator and shared identity bits."""
        super().__init__(coordinator)
        self._entry = entry
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = device_info(entry)


class WashWiseCanWashBinarySensor(_WashWiseBinarySensorBase):
    """Primary verdict: ON when the surface can be washed."""

    _attr_translation_key = "can_wash"
    _attr_icon = "mdi:car-wash"

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Initialise with key ``can_wash``."""
        super().__init__(coordinator, entry, "can_wash")

    @property
    def is_on(self) -> bool | None:
        """Return the latest verdict from the coordinator."""
        decision = self.coordinator.data
        if decision is None:
            return None
        return bool(decision.can_wash)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Surface the diagnostic payload for dashboards / templates."""
        decision = self.coordinator.data
        if decision is None:
            return {}
        return {
            "forecast_summary": list(decision.forecast_summary or []),
            "decision_details": {
                "can_wash": bool(decision.can_wash),
                "score": int(decision.score),
                "reason": decision.reason,
                "days_until_wash": decision.days_until_wash,
                "blocking_days": [
                    d.isoformat() if hasattr(d, "isoformat") else d
                    for d in (decision.blocking_days or [])
                ],
            },
            "days_analyzed": int(decision.days_analyzed),
            "score": int(decision.score),
            "reason": decision.reason,
            "active_weather_entity": self.coordinator.active_weather_entity,
            "blocking_days": [
                d.isoformat() if hasattr(d, "isoformat") else d
                for d in (decision.blocking_days or [])
            ],
        }


class WashWiseDayOkBinarySensor(_WashWiseBinarySensorBase):
    """Per-day verdict: ON when forecast day ``day_index`` is not blocked."""

    _attr_translation_key = "day_ok"
    _attr_icon = "mdi:weather-partly-cloudy"

    def __init__(
        self,
        coordinator: WashWiseCoordinator,
        entry: ConfigEntry,
        day_index: int,
    ) -> None:
        """Initialise for the given zero-based ``day_index``."""
        self._day_index = int(day_index)
        super().__init__(coordinator, entry, f"day_{self._day_index + 1}_ok")
        # The ``day_ok`` translation slot uses a ``{day}`` placeholder so the
        # translated friendly_name renders e.g. "Day 1 OK".
        self._attr_translation_placeholders = {"day": str(self._day_index + 1)}

    @property
    def is_on(self) -> bool | None:
        """Return ``True`` when the forecast day is not blocked."""
        decision = self.coordinator.data
        if decision is None:
            return None
        summary = decision.forecast_summary or []
        if self._day_index >= len(summary):
            return None
        entry = summary[self._day_index]
        if not isinstance(entry, dict):
            return None
        # ``blocked`` is the canonical field set by ``decision.compute``.
        # Fallback to ``can_wash`` on dicts that pre-date the rename.
        if "blocked" in entry:
            return not bool(entry.get("blocked"))
        if "can_wash" in entry:
            return bool(entry.get("can_wash"))
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the underlying forecast row for the day."""
        decision = self.coordinator.data
        if decision is None:
            return {}
        summary = decision.forecast_summary or []
        if self._day_index >= len(summary):
            return {"day_index": self._day_index}
        row = summary[self._day_index]
        attrs: dict[str, Any] = {"day_index": self._day_index}
        if isinstance(row, dict):
            attrs.update(row)
        return attrs


__all__ = [
    "ForecastBlocksIrrigationBinarySensor",
    "IrrigationSuppressedBinarySensor",
    "IrrigationSwitchStateBinarySensor",
    "WashWiseCanWashBinarySensor",
    "WashWiseDayOkBinarySensor",
    "WashWiseFreezeRiskBinarySensor",
    "async_setup_entry",
]


class WashWiseFreezeRiskBinarySensor(_WashWiseBinarySensorBase):
    """Diagnostic binary sensor: ON when the analysed forecast crosses 0 °C.

    Uses ``BinarySensorDeviceClass.COLD`` so HA renders it with the standard
    cold/warm icon and translates the on/off state automatically.
    """

    _attr_translation_key = "freeze_risk"
    _attr_icon = "mdi:snowflake"
    _attr_device_class = BinarySensorDeviceClass.COLD
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the freeze-risk binary sensor."""
        super().__init__(coordinator, entry, "freeze_risk")

    @property
    def is_on(self) -> bool | None:
        """Return ``True`` when any analysed day would freeze."""
        decision = self.coordinator.data
        if decision is None:
            return None
        for day in decision.forecast_summary or []:
            tmin = day.get("temp_min")
            tmax = day.get("temp_max")
            if tmin is None and tmax is None:
                continue
            try:
                low = float(tmin) if tmin is not None else None
                high = float(tmax) if tmax is not None else None
            except (TypeError, ValueError):
                continue
            if low is not None and low <= 0:
                return True
            if low is None and high is not None and high <= 0:
                return True
        return False


class IrrigationSuppressedBinarySensor(_WashWiseBinarySensorBase):
    """Binary sensor: ON when irrigation should be suppressed.

    Suppression triggers when measured rain >= gauge threshold OR when the
    forecast blocks irrigation (rain expected within the horizon). Only
    registered when category == 'garden_irrigation'.
    """

    _attr_translation_key = "irrigation_suppressed"
    _attr_icon = "mdi:sprinkler-variant"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the irrigation-suppressed binary sensor."""
        super().__init__(coordinator, entry, "irrigation_suppressed")

    @property
    def is_on(self) -> bool | None:
        """Return True when irrigation is suppressed."""
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.irrigation_suppressed)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose measured rain and suppression reason."""
        measured = self.coordinator.measured_rain_mm
        decision = self.coordinator.data
        return {
            "measured_rain_mm": measured,
            "forecast_blocks": bool(decision.can_wash) if decision is not None else None,
        }


class ForecastBlocksIrrigationBinarySensor(_WashWiseBinarySensorBase):
    """Diagnostic binary sensor: ON when the forecast alone blocks irrigation.

    Separate from gauge suppression — ON means rain is expected in the forecast
    horizon, regardless of measured rainfall. Only registered for garden_irrigation.
    """

    _attr_translation_key = "forecast_blocks_irrigation"
    _attr_icon = "mdi:cloud-alert"
    _attr_device_class = BinarySensorDeviceClass.MOISTURE
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the forecast-blocks-irrigation binary sensor."""
        super().__init__(coordinator, entry, "forecast_blocks_irrigation")

    @property
    def is_on(self) -> bool | None:
        """Return True when forecast predicts rain (irrigation should be skipped)."""
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.forecast_blocks_irrigation)


class IrrigationSwitchStateBinarySensor(_WashWiseBinarySensorBase):
    """Diagnostic binary sensor: mirrors the configured irrigation switch state.

    Useful for debugging — shows whether the irrigation program switch is currently
    ON or OFF without leaving the WashWise device page. Only registered for garden_irrigation.
    """

    _attr_translation_key = "irrigation_switch_state"
    _attr_icon = "mdi:toggle-switch"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Register the irrigation switch state binary sensor."""
        super().__init__(coordinator, entry, "irrigation_switch_state")

    @property
    def is_on(self) -> bool | None:
        """Return current state of the configured irrigation switch."""
        return self.coordinator.irrigation_switch_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the switch entity id for reference."""
        options = self._entry.options or {}
        data = self._entry.data or {}
        switch_entity = options.get("irrigation_switch_entity") or data.get(
            "irrigation_switch_entity"
        )
        return {"switch_entity_id": switch_entity}
