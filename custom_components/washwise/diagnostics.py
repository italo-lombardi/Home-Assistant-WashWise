"""Diagnostics support for WashWise.

Home Assistant auto-discovers this module (no manifest entry needed) and wires
``async_get_config_entry_diagnostics`` to the entry's ⋮ → *Download diagnostics*
button. The payload is intentionally comprehensive: it exposes the full resolved
config, the live decision + forecast the verdict was built from, every configured
weather provider's current state, per-provider health + failover history, and the
wash/snooze state — everything a user needs to attach to a bug report or debug a
confusing verdict without hand-running templates.

Nothing is redacted: WashWise stores no secrets, tokens, credentials, or
coordinates. Entity ids and weather values are not sensitive and ARE the debug
signal, so ``TO_REDACT`` stays empty (same choice as the sibling
Home-Assistant-EntityAvailability integration).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import _get_version
from .binary_sensor import _forecast_aggregates
from .const import (
    CONF_CATEGORY,
    CONF_IRRIGATION_SWITCH_ENTITY,
    CONF_RAIN_GAUGE_ENTITY,
    CONF_WEATHER_ENTITIES,
    DEFAULT_CATEGORY,
    DOMAIN,
    STORAGE_KEY_FMT,
    STORAGE_VERSION,
)
from .coordinator import WashWiseCoordinator
from .sensor import _last_wash_timestamp, _parse_iso

# WashWise persists no secrets/tokens/credentials/coordinates — see module
# docstring. Keep empty so the dump carries the full debug signal.
TO_REDACT: set[str] = set()

# How many recent wash-log entries to include (the log holds up to 365).
_WASH_LOG_TAIL = 10


def _dt_iso(value: Any) -> str | None:
    """Best-effort ISO string for a datetime-ish value."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str) and value:
        return value
    return None


def _provider_states(hass: HomeAssistant, entity_ids: list[str]) -> list[dict[str, Any]]:
    """Snapshot each configured weather provider's current live state.

    The single most useful debug block — catches a wrong-location provider, a
    stale/unavailable one, or a wind-unit mismatch at a glance.
    """
    out: list[dict[str, Any]] = []
    for eid in entity_ids:
        state = hass.states.get(eid)
        if state is None:
            out.append({"entity_id": eid, "available": False, "state": None})
            continue
        attrs = state.attributes
        out.append(
            {
                "entity_id": eid,
                "available": state.state not in ("unavailable", "unknown"),
                "state": state.state,
                "friendly_name": attrs.get("friendly_name"),
                "temperature": attrs.get("temperature"),
                "temperature_unit": attrs.get("temperature_unit"),
                "wind_speed": attrs.get("wind_speed"),
                "wind_speed_unit": attrs.get("wind_speed_unit"),
                "humidity": attrs.get("humidity"),
            }
        )
    return out


def _wash_state(coordinator: WashWiseCoordinator) -> dict[str, Any]:
    """Wash-log / snooze state from the store's cached snapshot (non-awaiting)."""
    store = getattr(coordinator, "_store", None)
    stored = getattr(store, "_data", None) if store is not None else None
    if stored is None:
        return {"store_loaded": False}

    log = list(getattr(stored, "wash_log", []) or [])
    last_ts = _last_wash_timestamp(stored)
    days_since: int | None = None
    if last_ts is not None:
        days_since = max(0, (datetime.now(UTC) - last_ts).days)
    cutoff = datetime.now(UTC) - timedelta(days=30)
    count_30d = sum(
        1
        for e in log
        if (ts := _parse_iso(getattr(e, "timestamp", None))) is not None and ts >= cutoff
    )
    return {
        "store_loaded": True,
        "wash_log_count": len(log),
        "last_washed": _dt_iso(last_ts),
        "days_since_last_wash": days_since,
        "wash_count_30d": count_30d,
        "snooze_until": getattr(stored, "snooze_until", None),
        "recent_wash_log": [
            {"timestamp": getattr(e, "timestamp", None), "source": getattr(e, "source", None)}
            for e in log[-_WASH_LOG_TAIL:]
        ],
        "failover": {
            "last_failover_ts": getattr(stored, "last_failover_ts", None),
            "last_failover_from": getattr(stored, "last_failover_from", None),
            "last_failover_to": getattr(stored, "last_failover_to", None),
        },
        "provider_health": {
            eid: {
                "success_count": h.success_count,
                "failure_count": h.failure_count,
                "last_success_ts": h.last_success_ts,
                "last_failure_ts": h.last_failure_ts,
                "last_error": h.last_error,
                "last_seen_ts": h.last_seen_ts,
            }
            for eid, h in (getattr(stored, "provider_health", {}) or {}).items()
        },
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return a comprehensive diagnostics dump for a WashWise config entry."""
    coordinator: WashWiseCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(coordinator, WashWiseCoordinator):
        return {"error": "coordinator not loaded"}

    merged = {**entry.data, **entry.options}
    category = merged.get(CONF_CATEGORY, DEFAULT_CATEGORY)
    thresholds, invert = coordinator._resolve_thresholds()
    is_irrigation = category == "garden_irrigation"

    decision = coordinator.data
    decision_block: dict[str, Any] | None = None
    if decision is not None:
        summary = list(decision.forecast_summary or [])
        decision_block = {
            "can_wash": decision.can_wash,
            "score": decision.score,
            "reason": decision.reason,
            "days_until_wash": decision.days_until_wash,
            "days_analyzed": decision.days_analyzed,
            "blocking_days": [
                d.isoformat() if hasattr(d, "isoformat") else d
                for d in (decision.blocking_days or [])
            ],
            "forecast_summary": summary,
            "aggregates": _forecast_aggregates(summary),
        }

    coordinator_block: dict[str, Any] = {
        "enabled": coordinator.enabled,
        "active_weather_entity": coordinator.active_weather_entity,
        "active_provider_label": coordinator.active_provider_label,
        "last_update_success_time": _dt_iso(getattr(coordinator, "last_update_success_time", None)),
        "update_count": getattr(coordinator, "_update_count", None),
        "update_interval": (
            coordinator.update_interval.total_seconds()
            if coordinator.update_interval is not None
            else None
        ),
    }
    if is_irrigation:
        coordinator_block["irrigation"] = {
            "measured_rain_mm": coordinator.measured_rain_mm,
            "irrigation_suppressed": coordinator.irrigation_suppressed,
            "rain_gauge_threshold_mm": coordinator.rain_gauge_threshold_mm,
            "forecast_blocks_irrigation": coordinator.forecast_blocks_irrigation,
            "irrigation_switch_state": coordinator.irrigation_switch_state,
        }

    store = getattr(coordinator, "_store", None)

    return async_redact_data(
        {
            "meta": {
                "title": entry.title,
                "entry_id": entry.entry_id,
                "category": category,
                "version": await hass.async_add_executor_job(_get_version),
                "source": entry.source,
                "disabled_by": entry.disabled_by,
                "created_at": _dt_iso(getattr(entry, "created_at", None)),
                "modified_at": _dt_iso(getattr(entry, "modified_at", None)),
            },
            "config": {
                "weather_entities": merged.get(CONF_WEATHER_ENTITIES, []),
                "resolved_thresholds": thresholds,
                "invert": invert,
                "temperature_unit": coordinator._resolve_temperature_unit(),
                "rain_gauge_entity": merged.get(CONF_RAIN_GAUGE_ENTITY),
                "irrigation_switch_entity": merged.get(CONF_IRRIGATION_SWITCH_ENTITY),
                "raw_data": dict(entry.data),
                "raw_options": dict(entry.options),
            },
            "decision": decision_block,
            "coordinator": coordinator_block,
            "providers": _provider_states(hass, coordinator._weather_ids()),
            "wash_state": _wash_state(coordinator),
            "store": {
                "key": STORAGE_KEY_FMT.format(entry_id=entry.entry_id),
                "version": STORAGE_VERSION,
                "disk_read_count": getattr(store, "disk_read_count", None),
            },
        },
        TO_REDACT,
    )
