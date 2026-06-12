"""Generic Home Assistant ``weather`` entity adapter for WashWise.

Reads any HA weather entity (no per-provider Python class) and normalizes its
current state and forecast payload into the integration's typed models.

Robustness rules:
- ``is_available`` returns ``False`` if the state is missing,
  ``unavailable`` or ``unknown``.
- ``get_current`` / ``get_forecast`` swallow exceptions and return ``None`` /
  ``[]`` respectively. The caller is expected to fall over to the next
  configured weather entity.
- ``_normalize`` returns ``None`` on any error so the caller can filter out
  malformed forecast entries without crashing the coordinator update.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from homeassistant.const import (
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.util.unit_conversion import TemperatureConverter

from .const import PRECIP_KEYS, TIME_KEYS, TMAX_KEYS, TMIN_KEYS
from .models import CurrentWeather, ForecastDay

_LOGGER = logging.getLogger(__name__)

# Map common HA / provider unit strings to ``UnitOfTemperature`` values.
_TEMP_UNIT_MAP: dict[str, str] = {
    "°C": UnitOfTemperature.CELSIUS,
    "C": UnitOfTemperature.CELSIUS,
    "celsius": UnitOfTemperature.CELSIUS,
    UnitOfTemperature.CELSIUS: UnitOfTemperature.CELSIUS,
    "°F": UnitOfTemperature.FAHRENHEIT,
    "F": UnitOfTemperature.FAHRENHEIT,
    "fahrenheit": UnitOfTemperature.FAHRENHEIT,
    UnitOfTemperature.FAHRENHEIT: UnitOfTemperature.FAHRENHEIT,
    "K": UnitOfTemperature.KELVIN,
    "kelvin": UnitOfTemperature.KELVIN,
    UnitOfTemperature.KELVIN: UnitOfTemperature.KELVIN,
}


async def is_available(hass: HomeAssistant, entity_id: str) -> bool:
    """Return ``True`` if the weather entity exists and has a usable state."""
    state = hass.states.get(entity_id)
    if state is None:
        return False
    return state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN, None, "")


async def get_current(hass: HomeAssistant, entity_id: str) -> CurrentWeather | None:
    """Read the current weather snapshot from the entity state.

    Returns ``None`` when the entity is unavailable or the state cannot be
    read for any reason.
    """
    try:
        if not await is_available(hass, entity_id):
            return None
        state = hass.states.get(entity_id)
        if state is None:  # pragma: no cover - defensive
            return None
        attrs: dict[str, Any] = dict(state.attributes)
        unit = attrs.get("temperature_unit")
        temp_raw = attrs.get("temperature")
        temperature_c = _to_celsius(temp_raw, unit)
        return CurrentWeather(
            condition=state.state,
            temperature_c=temperature_c,
            raw=attrs,
        )
    except Exception:
        _LOGGER.exception("Failed to read current weather for %s", entity_id)
        return None


async def get_forecast(
    hass: HomeAssistant,
    entity_id: str,
    mode: str,
    days: int,
    fallback_unit: str | None = None,
) -> list[ForecastDay]:
    """Fetch ``days`` worth of forecast entries via ``weather.get_forecasts``.

    ``mode`` is forwarded as the service ``type`` parameter (``"daily"`` or
    ``"hourly"``). The first ``days`` raw entries from the response are
    normalized; entries that fail to normalize are silently skipped.

    ``fallback_unit`` lets the caller supply the temperature unit to use when
    the forecast frame itself omits ``temperature_unit`` -- many providers
    only set it on the parent state, not on each forecast row.
    """
    if days <= 0:
        return []
    try:
        response = await hass.services.async_call(
            "weather",
            "get_forecasts",
            {"entity_id": entity_id, "type": mode},
            blocking=True,
            return_response=True,
        )
    except Exception:
        _LOGGER.exception("Failed to call weather.get_forecasts for %s", entity_id)
        return []

    if not response or not isinstance(response, dict):
        return []

    payload = response.get(entity_id)
    if not isinstance(payload, dict):
        return []

    raw_forecast = payload.get("forecast")
    if not isinstance(raw_forecast, list):
        return []

    # Resolve the unit reported on the source entity once -- forecast rows
    # rarely carry their own ``temperature_unit``.
    state = hass.states.get(entity_id)
    entity_unit: str | None = None
    if state is not None:
        attr_unit = state.attributes.get("temperature_unit")
        if isinstance(attr_unit, str) and attr_unit.strip():
            entity_unit = attr_unit

    # Entity's own temperature_unit attribute wins — it knows what unit its
    # forecast frames use. The fallback_unit (from coordinator config) is only
    # used when the entity doesn't declare a unit at all.
    effective_unit = entity_unit or fallback_unit

    sliced = raw_forecast[:days]
    out: list[ForecastDay] = []
    for raw in sliced:
        if not isinstance(raw, dict):
            continue
        normalized = _normalize(raw, fallback_unit=effective_unit)
        if normalized is not None:
            out.append(normalized)
    return out


def _normalize(raw: dict[str, Any], fallback_unit: str | None = None) -> ForecastDay | None:
    """Convert a provider-specific forecast entry to a ``ForecastDay``.

    Any exception or unparseable date returns ``None`` so the caller can
    skip the entry without aborting the whole forecast. ``fallback_unit``
    is used when the entry itself does not carry ``temperature_unit``.
    """
    try:
        time_value = _resolve_key(raw, TIME_KEYS)
        fc_date = _parse_time(time_value)
        if fc_date is None:
            return None

        condition = raw.get("condition")
        if condition is not None and not isinstance(condition, str):
            condition = str(condition)

        precip_raw = _resolve_key(raw, PRECIP_KEYS)
        precip_mm: float | None
        if precip_raw is None:
            precip_mm = None
        else:
            try:
                precip_mm = float(precip_raw)
            except (TypeError, ValueError):
                precip_mm = None

        unit = raw.get("temperature_unit") or fallback_unit
        tmin_raw = _resolve_key(raw, TMIN_KEYS)
        tmax_raw = _resolve_key(raw, TMAX_KEYS)
        tmin_c = _to_celsius(tmin_raw, unit)
        tmax_c = _to_celsius(tmax_raw, unit)

        return ForecastDay(
            date=fc_date,
            condition=condition,
            precipitation_mm=precip_mm,
            temp_min_c=tmin_c,
            temp_max_c=tmax_c,
            raw=dict(raw),
        )
    except Exception:
        _LOGGER.debug("Failed to normalize forecast entry: %r", raw, exc_info=True)
        return None


def _to_celsius(value: Any, unit: Any) -> float | None:
    """Convert ``value`` from ``unit`` to °C using HA's ``TemperatureConverter``.

    Accepts °C / °F / K (and common aliases). Returns ``None`` for missing or
    unparseable values so callers can rely on a clean ``float | None`` shape.
    """
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None

    target = UnitOfTemperature.CELSIUS
    if unit is None:
        return numeric

    if isinstance(unit, str):
        mapped = _TEMP_UNIT_MAP.get(unit) or _TEMP_UNIT_MAP.get(unit.strip())
        if mapped is None:
            mapped = _TEMP_UNIT_MAP.get(unit.strip().lower())
        if mapped is None:
            # Unknown unit — fall back to the raw numeric value.
            return numeric
        source = mapped
    else:
        return numeric

    if source == target:
        return numeric
    try:
        return TemperatureConverter.convert(numeric, source, target)
    except Exception:
        _LOGGER.debug(
            "TemperatureConverter failed for value=%r unit=%r",
            value,
            unit,
            exc_info=True,
        )
        return None


def _resolve_key(d: dict[str, Any], keys: tuple[str, ...]) -> Any:
    """Return the first non-``None`` value in ``d`` for any key in ``keys``."""
    if not isinstance(d, dict):
        return None
    for key in keys:
        if key in d:
            value = d[key]
            if value is not None and value != "null":
                return value
    return None


def _parse_time(value: Any) -> date | None:
    """Parse a forecast timestamp into a ``date``.

    Accepts:
    - ISO-8601 strings (``"2026-06-11"``, ``"2026-06-11T12:00:00+00:00"``);
    - integer / float epoch milliseconds;
    - ``datetime`` instances (``date`` extracted).

    Returns ``None`` for unsupported types or parse failures.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, bool):  # bool is an int subclass; reject early
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value) / 1000).date()
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        # ``fromisoformat`` accepts trailing ``Z`` only on Python 3.11+; be safe.
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            try:
                return date.fromisoformat(text)
            except ValueError:
                return None
    return None
