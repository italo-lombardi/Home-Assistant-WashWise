"""Tests for WashWise diagnostics (Download diagnostics)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise.const import (
    CONF_CATEGORY,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_FORECAST_TYPE,
    CONF_NAME,
    CONF_RAIN_GAUGE_ENTITY,
    CONF_WEATHER_ENTITIES,
    DEFAULT_FORECAST_TYPE,
    DOMAIN,
)
from custom_components.washwise.coordinator import WashWiseCoordinator
from custom_components.washwise.diagnostics import _dt_iso, async_get_config_entry_diagnostics
from custom_components.washwise.models import Decision, ProviderHealth, StoredData, WashEntry

FROZEN_NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


def _entry(
    *,
    category: str = "car",
    weather: list[str] | None = None,
    entry_id: str = "diag_entry",
    options: dict | None = None,
) -> MockConfigEntry:
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Diag Car",
        data={
            CONF_NAME: "Diag Car",
            CONF_WEATHER_ENTITIES: weather or ["weather.primary", "weather.backup"],
            CONF_CATEGORY: category,
            CONF_CUSTOMIZE_THRESHOLDS: False,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
        },
        options=options or {},
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_{entry_id}",
    )


def _decision() -> Decision:
    return Decision(
        can_wash=True,
        score=90,
        reason="clear",
        days_until_wash=0,
        blocking_days=[],
        forecast_summary=[
            {
                "date": "2026-06-11",
                "condition": "sunny",
                "precipitation": 0.0,
                "wind_speed": 12.0,
                "temp_min": 10.0,
                "temp_max": 20.0,
                "blocked": False,
                "blockers": [],
                "day_score": 100,
            }
        ],
        days_analyzed=1,
    )


def _register(hass: HomeAssistant, entry: MockConfigEntry) -> WashWiseCoordinator:
    """Build + register a coordinator in hass.data, decision pre-seeded."""
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    coord.data = _decision()
    coord._active_weather_entity = "weather.primary"
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord
    return coord


async def _run(hass: HomeAssistant, entry: MockConfigEntry, coord: WashWiseCoordinator):
    """Call diagnostics then tear down the coordinator's listeners (no lingering timers)."""
    try:
        return await async_get_config_entry_diagnostics(hass, entry)
    finally:
        await coord.async_shutdown()


async def test_diagnostics_coordinator_not_loaded(hass: HomeAssistant) -> None:
    """Returns an error dict when the coordinator isn't in hass.data."""
    entry = _entry()
    entry.add_to_hass(hass)
    hass.data.setdefault(DOMAIN, {})
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result == {"error": "coordinator not loaded"}


@freeze_time(FROZEN_NOW)
async def test_diagnostics_full_payload(hass: HomeAssistant) -> None:
    """A loaded car entry dumps meta/config/decision/coordinator/providers/store."""
    entry = _entry()
    coord = _register(hass, entry)

    # Populate the store snapshot the way the real store caches it.
    coord._store._data = StoredData(  # type: ignore[attr-defined]
        wash_log=[WashEntry(timestamp="2026-06-10T09:00:00+00:00", source="manual")],
        snooze_until=None,
        last_failover_ts="2026-06-09T00:00:00+00:00",
        last_failover_from="weather.primary",
        last_failover_to="weather.backup",
        provider_health={
            "weather.primary": ProviderHealth(
                entity_id="weather.primary",
                success_count=42,
                failure_count=1,
                last_success_ts="2026-06-11T11:00:00+00:00",
                last_failure_ts="2026-06-01T00:00:00+00:00",
                last_error="no_forecast",
                last_seen_ts="2026-06-11T11:00:00+00:00",
            )
        },
    )
    # Give the active provider a live state so the providers block is populated.
    hass.states.async_set(
        "weather.primary",
        "sunny",
        {
            "friendly_name": "Primary",
            "temperature": 20,
            "wind_speed": 12,
            "wind_speed_unit": "km/h",
        },
    )

    result = await _run(hass, entry, coord)

    # meta
    assert result["meta"]["title"] == "Diag Car"
    assert result["meta"]["category"] == "car"
    assert result["meta"]["entry_id"] == "diag_entry"
    assert result["meta"]["version"]  # non-empty from manifest

    # config — resolved thresholds come from the coordinator, not raw keys
    cfg = result["config"]
    assert cfg["weather_entities"] == ["weather.primary", "weather.backup"]
    assert cfg["resolved_thresholds"]["days"] == 3  # car preset horizon
    assert cfg["invert"] is False
    assert "raw_data" in cfg and "raw_options" in cfg

    # decision mirrors the seeded Decision + aggregates
    dec = result["decision"]
    assert dec["can_wash"] is True
    assert dec["score"] == 90
    assert dec["reason"] == "clear"
    assert len(dec["forecast_summary"]) == 1
    assert dec["aggregates"]["precip_total_mm"] == 0.0
    assert dec["aggregates"]["max_wind_kmh"] == 12.0

    # coordinator block
    co = result["coordinator"]
    assert co["active_weather_entity"] == "weather.primary"
    assert co["enabled"] is True
    assert "irrigation" not in co  # car is not irrigation

    # providers block lists each configured provider; primary shows live state
    provs = {p["entity_id"]: p for p in result["providers"]}
    assert set(provs) == {"weather.primary", "weather.backup"}
    assert provs["weather.primary"]["available"] is True
    assert provs["weather.primary"]["state"] == "sunny"
    assert provs["weather.backup"]["available"] is False  # no state set

    # wash state + provider health + failover
    ws = result["wash_state"]
    assert ws["store_loaded"] is True
    assert ws["wash_log_count"] == 1
    assert ws["last_washed"] == "2026-06-10T09:00:00+00:00"
    assert ws["failover"]["last_failover_to"] == "weather.backup"
    assert ws["provider_health"]["weather.primary"]["last_error"] == "no_forecast"

    # store meta
    assert result["store"]["version"] >= 1
    assert result["store"]["key"].endswith("diag_entry")


@freeze_time(FROZEN_NOW)
async def test_diagnostics_irrigation_block(hass: HomeAssistant) -> None:
    """A garden_irrigation entry includes the irrigation sub-block."""
    entry = _entry(
        category="garden_irrigation",
        weather=["weather.primary"],
        entry_id="diag_irr",
        options={CONF_RAIN_GAUGE_ENTITY: "sensor.rain"},
    )
    coord = _register(hass, entry)

    result = await _run(hass, entry, coord)

    assert result["meta"]["category"] == "garden_irrigation"
    assert result["config"]["invert"] is True  # irrigation inverts
    irr = result["coordinator"]["irrigation"]
    assert "measured_rain_mm" in irr
    assert "irrigation_suppressed" in irr
    assert "irrigation_switch_state" in irr


@freeze_time(FROZEN_NOW)
async def test_diagnostics_cold_store_and_no_decision(hass: HomeAssistant) -> None:
    """No cached store snapshot + no decision → defensive empties, no crash."""
    entry = _entry(entry_id="diag_cold")
    entry.add_to_hass(hass)
    coord = WashWiseCoordinator(hass, entry)
    coord.data = None  # no decision yet
    # leave _store._data as None (cold store)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coord

    result = await _run(hass, entry, coord)

    assert result["decision"] is None
    assert result["wash_state"] == {"store_loaded": False}
    # providers still enumerated (just unavailable)
    assert [p["entity_id"] for p in result["providers"]] == ["weather.primary", "weather.backup"]


@freeze_time(FROZEN_NOW)
async def test_diagnostics_wash_count_30d_and_days_since(hass: HomeAssistant) -> None:
    """days_since_last_wash + wash_count_30d are derived from the log."""
    entry = _entry(entry_id="diag_counts")
    coord = _register(hass, entry)
    recent = (FROZEN_NOW - timedelta(days=3)).isoformat()
    old = (FROZEN_NOW - timedelta(days=90)).isoformat()
    coord._store._data = StoredData(  # type: ignore[attr-defined]
        wash_log=[
            WashEntry(timestamp=old, source="manual"),
            WashEntry(timestamp=recent, source="manual"),
        ],
        snooze_until=None,
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )

    result = await _run(hass, entry, coord)
    ws = result["wash_state"]
    assert ws["wash_log_count"] == 2
    assert ws["wash_count_30d"] == 1  # only the 3-day-ago entry
    assert ws["days_since_last_wash"] == 3


def test_dt_iso_handles_datetime_string_and_none() -> None:
    """_dt_iso: datetime → iso, non-empty str passes through, else None."""
    assert _dt_iso(datetime(2026, 6, 11, tzinfo=UTC)) == "2026-06-11T00:00:00+00:00"
    assert _dt_iso("2026-01-01T00:00:00+00:00") == "2026-01-01T00:00:00+00:00"
    assert _dt_iso("") is None
    assert _dt_iso(None) is None
    assert _dt_iso(123) is None


@freeze_time(FROZEN_NOW)
async def test_diagnostics_store_loaded_but_empty_wash_log(hass: HomeAssistant) -> None:
    """Store loaded with an empty wash log → last_washed/days_since None (no crash)."""
    entry = _entry(entry_id="diag_empty_log")
    coord = _register(hass, entry)
    coord._store._data = StoredData.empty()  # type: ignore[attr-defined]

    result = await _run(hass, entry, coord)
    ws = result["wash_state"]
    assert ws["store_loaded"] is True
    assert ws["wash_log_count"] == 0
    assert ws["last_washed"] is None
    assert ws["days_since_last_wash"] is None
    assert ws["wash_count_30d"] == 0
