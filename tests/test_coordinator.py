"""Tests for the WashWise coordinator.

Covers the full update pipeline:

* Provider chain walk + ordered fallback.
* Failover persistence (``last_failover_to`` / ``last_failover_from``).
* All-providers-dead → :class:`UpdateFailed`.
* Snooze short-circuit.
* Disabled switch behaviour (frozen verdict / ``UpdateFailed`` when no prior).
* Provider health counters (success / failure increments + last_error / ts).
* :func:`WashWiseStore.gc_stale_health` dropping old records past TTL.
* Solar-panels category inversion.
* Mutator helpers: ``async_mark_washed``, ``async_snooze``, ``async_clear_snooze``.

Time is controlled via :mod:`freezegun` so ISO-string assertions and TTL
windows are deterministic.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise.const import (
    CONF_BAD_CONDITIONS,
    CONF_CATEGORY,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_FORECAST_TYPE,
    CONF_NAME,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_WEATHER_ENTITIES,
    DEFAULT_CATEGORY,
    DEFAULT_FORECAST_TYPE,
    DOMAIN,
    SCAN_INTERVAL,
    STALE_PROVIDER_TTL_DAYS,
)
from custom_components.washwise.coordinator import WashWiseCoordinator
from custom_components.washwise.models import (
    CurrentWeather,
    ForecastDay,
    ProviderHealth,
    StoredData,
    WashEntry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


FROZEN_NOW = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


def _make_entry(
    weather_entities: list[str],
    *,
    category: str = DEFAULT_CATEGORY,
    customize: bool = False,
    options: dict[str, Any] | None = None,
    entry_id: str = "test_entry_id",
) -> MockConfigEntry:
    """Build a MockConfigEntry with the given provider list and category."""
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Test Wash",
        data={
            CONF_NAME: "Test Wash",
            CONF_WEATHER_ENTITIES: list(weather_entities),
            CONF_CATEGORY: category,
            CONF_CUSTOMIZE_THRESHOLDS: customize,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
        },
        options=options or {},
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_{entry_id}",
    )


def _sunny_current() -> CurrentWeather:
    """Return a clear-sky current snapshot."""
    return CurrentWeather(condition="sunny", temperature_c=20.0, raw={})


def _rainy_current() -> CurrentWeather:
    """Return a rainy current snapshot (forecast horizon irrelevant)."""
    return CurrentWeather(condition="rainy", temperature_c=10.0, raw={})


def _clear_forecast(days: int = 3) -> list[ForecastDay]:
    """Return a list of dry, mild forecast days starting today."""
    base = FROZEN_NOW.date()
    return [
        ForecastDay(
            date=base + timedelta(days=i),
            condition="sunny",
            precipitation_mm=0.0,
            temp_min_c=10.0,
            temp_max_c=20.0,
            raw={},
        )
        for i in range(days)
    ]


def _rainy_forecast(days: int = 3) -> list[ForecastDay]:
    """Return a list of rainy forecast days (used for the solar invert path)."""
    base = FROZEN_NOW.date()
    return [
        ForecastDay(
            date=base + timedelta(days=i),
            condition="rainy",
            precipitation_mm=5.0,
            temp_min_c=10.0,
            temp_max_c=15.0,
            raw={},
        )
        for i in range(days)
    ]


class _StubStore:
    """In-memory replacement for WashWiseStore.

    The real store goes through HA's ``Store`` (disk I/O), which we don't want
    in unit tests. This stub reimplements the public surface used by the
    coordinator + the asserts in this file.
    """

    def __init__(self) -> None:
        self.data: StoredData = StoredData.empty()
        self.health_calls: list[tuple[str, bool, str | None]] = []
        self.failover_calls: list[tuple[str | None, str]] = []

    async def load(self) -> StoredData:
        return self.data

    async def save(self, data: StoredData) -> None:
        self.data = data

    async def remove(self) -> None:  # pragma: no cover - not exercised here
        self.data = StoredData.empty()

    async def append_wash(self, entry: WashEntry) -> None:
        self.data = StoredData(
            wash_log=[*self.data.wash_log, entry],
            snooze_until=self.data.snooze_until,
            last_failover_ts=self.data.last_failover_ts,
            last_failover_from=self.data.last_failover_from,
            last_failover_to=self.data.last_failover_to,
            provider_health=dict(self.data.provider_health),
        )

    async def set_snooze(self, until: datetime | None) -> None:
        self.data = StoredData(
            wash_log=list(self.data.wash_log),
            snooze_until=until.isoformat() if until is not None else None,
            last_failover_ts=self.data.last_failover_ts,
            last_failover_from=self.data.last_failover_from,
            last_failover_to=self.data.last_failover_to,
            provider_health=dict(self.data.provider_health),
        )

    async def record_failover(self, frm: str | None, to: str) -> None:
        self.failover_calls.append((frm, to))
        now_iso = datetime.now(UTC).isoformat()
        self.data = StoredData(
            wash_log=list(self.data.wash_log),
            snooze_until=self.data.snooze_until,
            last_failover_ts=now_iso,
            last_failover_from=frm,
            last_failover_to=to,
            provider_health=dict(self.data.provider_health),
        )

    async def update_provider_health(self, entity_id: str, ok: bool, error: str | None) -> None:
        self.health_calls.append((entity_id, ok, error))
        now_iso = datetime.now(UTC).isoformat()
        existing = self.data.provider_health.get(entity_id)
        if existing is None:
            new_health = ProviderHealth(
                entity_id=entity_id,
                success_count=1 if ok else 0,
                failure_count=0 if ok else 1,
                last_success_ts=now_iso if ok else None,
                last_failure_ts=None if ok else now_iso,
                last_error=None if ok else error,
                last_seen_ts=now_iso,
            )
        else:
            new_health = ProviderHealth(
                entity_id=entity_id,
                success_count=existing.success_count + (1 if ok else 0),
                failure_count=existing.failure_count + (0 if ok else 1),
                last_success_ts=now_iso if ok else existing.last_success_ts,
                last_failure_ts=existing.last_failure_ts if ok else now_iso,
                last_error=existing.last_error if ok else error,
                last_seen_ts=now_iso,
            )
        new_health_map = dict(self.data.provider_health)
        new_health_map[entity_id] = new_health
        self.data = StoredData(
            wash_log=list(self.data.wash_log),
            snooze_until=self.data.snooze_until,
            last_failover_ts=self.data.last_failover_ts,
            last_failover_from=self.data.last_failover_from,
            last_failover_to=self.data.last_failover_to,
            provider_health=new_health_map,
        )


def _build_coordinator(
    hass: HomeAssistant, entry: MockConfigEntry
) -> tuple[WashWiseCoordinator, _StubStore]:
    """Construct a coordinator with the in-memory ``_StubStore`` swapped in."""
    coord = WashWiseCoordinator(hass, entry)
    stub = _StubStore()
    coord._store = stub  # type: ignore[assignment]
    # async_request_refresh is a no-op in tests; we drive _async_update_data
    # directly. Avoid scheduling refreshes on mutator calls.
    coord.async_request_refresh = AsyncMock()  # type: ignore[assignment]
    return coord, stub


# ---------------------------------------------------------------------------
# Provider chain
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_first_provider_available_used(hass: HomeAssistant) -> None:
    """The first available provider is used and a Decision is returned."""
    entry = _make_entry(["weather.primary", "weather.backup"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(return_value=_sunny_current()),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(return_value=_clear_forecast(3)),
        ),
    ):
        decision = await coord._async_update_data()

    assert decision.can_wash is True
    assert decision.score > 0
    assert coord.active_weather_entity == "weather.primary"
    # Health updated for the primary, never for the backup.
    primary_calls = [c for c in stub.health_calls if c[0] == "weather.primary"]
    backup_calls = [c for c in stub.health_calls if c[0] == "weather.backup"]
    assert primary_calls == [("weather.primary", True, None)]
    assert backup_calls == []


@freeze_time(FROZEN_NOW)
async def test_first_dead_second_available_records_failover(
    hass: HomeAssistant,
) -> None:
    """First provider dead → coordinator falls over and records the failover."""
    entry = _make_entry(["weather.primary", "weather.backup"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)
    # Pretend a previous successful run picked weather.primary.
    coord._active_weather_entity = "weather.primary"

    async def fake_is_available(_hass, eid):
        return eid == "weather.backup"

    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(side_effect=fake_is_available),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(return_value=_sunny_current()),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(return_value=_clear_forecast(3)),
        ),
    ):
        decision = await coord._async_update_data()

    assert decision.can_wash is True
    assert coord.active_weather_entity == "weather.backup"
    # Failover recorded with the previous active as ``frm``.
    assert stub.failover_calls == [("weather.primary", "weather.backup")]
    assert stub.data.last_failover_from == "weather.primary"
    assert stub.data.last_failover_to == "weather.backup"
    assert stub.data.last_failover_ts is not None


@freeze_time(FROZEN_NOW)
async def test_all_providers_dead_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """Every provider unavailable → coordinator raises UpdateFailed."""
    entry = _make_entry(["weather.primary", "weather.backup"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(return_value=[]),
        ),
        pytest.raises(UpdateFailed),
    ):
        await coord._async_update_data()

    # Both providers were marked unhealthy.
    assert ("weather.primary", False, "unavailable") in stub.health_calls
    assert ("weather.backup", False, "unavailable") in stub.health_calls


# ---------------------------------------------------------------------------
# Snooze / override short-circuits
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_snooze_active_returns_snoozed_decision(
    hass: HomeAssistant,
) -> None:
    """An active snooze short-circuits to ``can_wash=False, reason='snoozed'``."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    until = FROZEN_NOW + timedelta(hours=1)
    stub.data = StoredData(
        wash_log=[],
        snooze_until=until.isoformat(),
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )

    with patch(
        "custom_components.washwise.coordinator.weather_source.is_available",
        new=AsyncMock(return_value=True),
    ) as mock_avail:
        decision = await coord._async_update_data()

    assert decision.can_wash is False
    assert decision.reason == "snoozed"
    # Provider chain must not be walked while snoozed.
    mock_avail.assert_not_called()


# ---------------------------------------------------------------------------
# Disabled switch
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_disabled_returns_last_decision_when_present(
    hass: HomeAssistant,
) -> None:
    """When disabled and a prior decision exists, the prior decision is returned."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    # Run once with the provider available to seed _last_decision.
    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(return_value=_sunny_current()),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(return_value=_clear_forecast(3)),
        ),
    ):
        first = await coord._async_update_data()

    coord._enabled = False
    second = await coord._async_update_data()
    # Implementation freezes the previous Decision when disabled.
    assert second is first


@freeze_time(FROZEN_NOW)
async def test_disabled_without_prior_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """When disabled with no prior decision, UpdateFailed surfaces 'disabled'."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)
    coord._enabled = False

    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


# ---------------------------------------------------------------------------
# Provider health updates
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_provider_health_success_increments_counters(
    hass: HomeAssistant,
) -> None:
    """A successful read bumps ``success_count`` + ``last_success_ts``."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(return_value=_sunny_current()),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(return_value=_clear_forecast(3)),
        ),
    ):
        await coord._async_update_data()

    health = stub.data.provider_health["weather.primary"]
    assert health.success_count == 1
    assert health.failure_count == 0
    assert health.last_success_ts is not None
    assert health.last_error is None


@freeze_time(FROZEN_NOW)
async def test_provider_health_failure_increments_counters(
    hass: HomeAssistant,
) -> None:
    """A failed read bumps ``failure_count`` + records ``last_error``."""
    entry = _make_entry(["weather.primary", "weather.backup"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    async def fake_is_available(_hass, eid):
        return eid == "weather.backup"

    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(side_effect=fake_is_available),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(return_value=_sunny_current()),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(return_value=_clear_forecast(3)),
        ),
    ):
        await coord._async_update_data()

    primary_health = stub.data.provider_health["weather.primary"]
    assert primary_health.failure_count == 1
    assert primary_health.success_count == 0
    assert primary_health.last_error == "unavailable"
    assert primary_health.last_failure_ts is not None


# ---------------------------------------------------------------------------
# Storage GC
# ---------------------------------------------------------------------------


async def test_gc_stale_health_drops_old_records(hass: HomeAssistant) -> None:
    """``gc_stale_health`` removes records older than ``STALE_PROVIDER_TTL_DAYS``."""
    from custom_components.washwise.storage import WashWiseStore

    base = datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)
    fresh_ts = (base - timedelta(days=1)).isoformat()
    stale_ts = (base - timedelta(days=STALE_PROVIDER_TTL_DAYS + 1)).isoformat()

    seeded = StoredData(
        wash_log=[],
        snooze_until=None,
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={
            "weather.fresh": ProviderHealth(
                entity_id="weather.fresh",
                success_count=2,
                failure_count=0,
                last_success_ts=fresh_ts,
                last_failure_ts=None,
                last_error=None,
                last_seen_ts=fresh_ts,
            ),
            "weather.stale": ProviderHealth(
                entity_id="weather.stale",
                success_count=1,
                failure_count=5,
                last_success_ts=None,
                last_failure_ts=stale_ts,
                last_error="boom",
                last_seen_ts=stale_ts,
            ),
        },
    )

    store = WashWiseStore(hass, "gc_entry")
    store._store.async_load = AsyncMock(return_value=seeded.to_dict())  # type: ignore[attr-defined]
    saved: dict[str, Any] = {}

    async def fake_save(payload):
        saved.update(payload)

    store._store.async_save = AsyncMock(side_effect=fake_save)  # type: ignore[attr-defined]

    with freeze_time(base):
        await store.gc_stale_health()

    # Only the fresh record survives.
    assert "weather.fresh" in saved["provider_health"]
    assert "weather.stale" not in saved["provider_health"]


# ---------------------------------------------------------------------------
# Solar inversion
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_solar_panels_category_inverts_verdict(
    hass: HomeAssistant,
) -> None:
    """Solar panels: rainy forecast → ``can_wash=True`` (self-cleaning)."""
    entry = _make_entry(["weather.primary"], category="solar_panels")
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    # ``solar_panels`` preset has days=0; force horizon to >0 via customize so
    # the rainy forecast is actually walked. We use entry options for that.
    coord.entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Solar",
        data={
            CONF_NAME: "Solar",
            CONF_WEATHER_ENTITIES: ["weather.primary"],
            CONF_CATEGORY: "solar_panels",
            CONF_CUSTOMIZE_THRESHOLDS: True,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
        },
        options={"days": 3, "freeze_check": False, "precip_threshold_mm": 0.0},
        entry_id="solar_entry",
        unique_id=f"{DOMAIN}_solar",
    )

    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(return_value=_rainy_current()),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(return_value=_rainy_forecast(3)),
        ),
    ):
        decision = await coord._async_update_data()

    # Inverted: rain → wash verdict True.
    assert decision.can_wash is True


# ---------------------------------------------------------------------------
# Mutators
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_async_mark_washed_appends_entry(hass: HomeAssistant) -> None:
    """``async_mark_washed`` appends a ``WashEntry`` to the persisted log."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    await coord.async_mark_washed()

    assert len(stub.data.wash_log) == 1
    appended = stub.data.wash_log[0]
    assert appended.source == "manual"
    # Timestamp should be FROZEN_NOW iso (utcnow under freezegun).
    assert appended.timestamp.startswith("2026-06-11")


@freeze_time(FROZEN_NOW)
async def test_async_snooze_sets_snooze_until(hass: HomeAssistant) -> None:
    """``async_snooze`` writes ``snooze_until = now + duration``."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    await coord.async_snooze(timedelta(hours=2))

    assert stub.data.snooze_until is not None
    parsed = datetime.fromisoformat(stub.data.snooze_until)
    assert parsed == FROZEN_NOW + timedelta(hours=2)


@freeze_time(FROZEN_NOW)
async def test_async_clear_snooze_clears_snooze(hass: HomeAssistant) -> None:
    """``async_clear_snooze`` wipes the snooze_until value."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    # Pre-seed snooze.
    stub.data = StoredData(
        wash_log=[],
        snooze_until=(FROZEN_NOW + timedelta(hours=1)).isoformat(),
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )

    await coord.async_clear_snooze()

    assert stub.data.snooze_until is None


# ---------------------------------------------------------------------------
# Property accessors
# ---------------------------------------------------------------------------


async def test_enabled_property_returns_internal_flag(hass: HomeAssistant) -> None:
    """``enabled`` returns the internal _enabled flag."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    assert coord.enabled is True
    coord._enabled = False
    assert coord.enabled is False


async def test_active_provider_label_none_when_no_active(hass: HomeAssistant) -> None:
    """``active_provider_label`` returns None when no active entity."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    assert coord.active_provider_label is None


async def test_active_provider_label_uses_friendly_name(hass: HomeAssistant) -> None:
    """``active_provider_label`` returns the friendly_name attribute when set."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)
    coord._active_weather_entity = "weather.primary"

    hass.states.async_set("weather.primary", "sunny", {"friendly_name": "Met Office"})
    assert coord.active_provider_label == "Met Office"


async def test_active_provider_label_falls_back_to_eid(hass: HomeAssistant) -> None:
    """``active_provider_label`` falls back to entity_id when friendly_name missing."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)
    coord._active_weather_entity = "weather.primary"

    # No state at all → fall back to eid.
    assert coord.active_provider_label == "weather.primary"

    # State with empty friendly_name → still falls back.
    hass.states.async_set("weather.primary", "sunny", {"friendly_name": "   "})
    assert coord.active_provider_label == "weather.primary"

    # State with non-string friendly_name → falls back too.
    hass.states.async_set("weather.primary", "sunny", {"friendly_name": 42})
    assert coord.active_provider_label == "weather.primary"


# ---------------------------------------------------------------------------
# Mutators (pause + force-refresh + shutdown)
# ---------------------------------------------------------------------------


async def test_async_set_enabled_toggles_flag(hass: HomeAssistant) -> None:
    """``async_set_enabled`` writes the bool flag and requests refresh."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    await coord.async_set_enabled(False)
    assert coord._enabled is False
    coord.async_request_refresh.assert_awaited()  # type: ignore[attr-defined]

    await coord.async_set_enabled(True)
    assert coord._enabled is True


async def test_async_force_refresh_delegates(hass: HomeAssistant) -> None:
    """``async_force_refresh`` is a thin alias for async_request_refresh."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    await coord.async_force_refresh()
    coord.async_request_refresh.assert_awaited()  # type: ignore[attr-defined]


async def test_async_shutdown_detaches_registry_listener(hass: HomeAssistant) -> None:
    """``async_shutdown`` calls the registry-unsub once and clears it."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    calls: list[str] = []

    def fake_unsub() -> None:
        calls.append("unsub")

    coord._unsub_registry = fake_unsub
    await coord.async_shutdown()
    assert calls == ["unsub"]
    assert coord._unsub_registry is None

    # Calling again is a no-op (the if-guard skips the unsub branch).
    await coord.async_shutdown()
    assert calls == ["unsub"]


async def test_init_without_weather_entities_skips_registry_listener(
    hass: HomeAssistant,
) -> None:
    """When no weather_entities configured, no registry listener is attached."""
    entry = _make_entry([])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)
    assert coord._unsub_registry is None


# ---------------------------------------------------------------------------
# Snooze / override ISO parse error paths
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_snooze_invalid_iso_string_falls_through(hass: HomeAssistant) -> None:
    """A malformed snooze_until value is ignored (parse fails → fall through)."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    stub.data = StoredData(
        wash_log=[],
        snooze_until="not-a-real-iso",
        last_failover_ts=None,
        last_failover_from=None,
        last_failover_to=None,
        provider_health={},
    )

    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(return_value=_sunny_current()),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(return_value=_clear_forecast(3)),
        ),
    ):
        decision = await coord._async_update_data()

    # Parse failed → snooze branch skipped → real decision computed.
    assert decision.reason != "snoozed"


# ---------------------------------------------------------------------------
# Provider chain edge cases
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_provider_no_current_marks_no_current_and_falls_through(
    hass: HomeAssistant,
) -> None:
    """get_current returning None → record 'no_current' and try next provider."""
    entry = _make_entry(["weather.primary", "weather.backup"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    async def fake_get_current(_hass, eid):
        if eid == "weather.primary":
            return None
        return _sunny_current()

    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(side_effect=fake_get_current),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(return_value=_clear_forecast(3)),
        ),
    ):
        decision = await coord._async_update_data()

    assert coord.active_weather_entity == "weather.backup"
    assert ("weather.primary", False, "no_current") in stub.health_calls
    assert decision.can_wash is True


@freeze_time(FROZEN_NOW)
async def test_provider_no_forecast_marks_no_forecast_and_falls_through(
    hass: HomeAssistant,
) -> None:
    """Empty forecast (with horizon>0) → record 'no_forecast' and continue."""
    entry = _make_entry(["weather.primary", "weather.backup"])
    entry.add_to_hass(hass)
    coord, stub = _build_coordinator(hass, entry)

    async def fake_get_forecast(_hass, eid, _ftype, _horizon, **_kwargs):
        if eid == "weather.primary":
            return []
        return _clear_forecast(3)

    with (
        patch(
            "custom_components.washwise.coordinator.weather_source.is_available",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_current",
            new=AsyncMock(return_value=_sunny_current()),
        ),
        patch(
            "custom_components.washwise.coordinator.weather_source.get_forecast",
            new=AsyncMock(side_effect=fake_get_forecast),
        ),
    ):
        decision = await coord._async_update_data()

    assert coord.active_weather_entity == "weather.backup"
    assert ("weather.primary", False, "no_forecast") in stub.health_calls
    assert decision.can_wash is True


# ---------------------------------------------------------------------------
# Customize: bad_conditions override
# ---------------------------------------------------------------------------


@freeze_time(FROZEN_NOW)
async def test_resolve_thresholds_bad_conditions_override(hass: HomeAssistant) -> None:
    """When customize is on and bad_conditions option is set, it lands in thresholds."""
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Custom",
        data={
            CONF_NAME: "Custom",
            CONF_WEATHER_ENTITIES: ["weather.primary"],
            CONF_CATEGORY: DEFAULT_CATEGORY,
            CONF_CUSTOMIZE_THRESHOLDS: True,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
        },
        options={CONF_BAD_CONDITIONS: ["fog", "snowy"]},
        entry_id="custom_bad",
        unique_id=f"{DOMAIN}_custom_bad",
    )
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    thresholds, invert = coord._resolve_thresholds()
    assert thresholds["bad_conditions"] == ["fog", "snowy"]
    assert invert is False


# ---------------------------------------------------------------------------
# Scan interval resolution
# ---------------------------------------------------------------------------


def test_resolve_scan_interval_default_when_unset(hass: HomeAssistant) -> None:
    """No scan_interval_minutes anywhere → default SCAN_INTERVAL."""
    entry = _make_entry(["weather.primary"])
    assert WashWiseCoordinator._resolve_scan_interval(entry) == SCAN_INTERVAL


def test_resolve_scan_interval_from_options(hass: HomeAssistant) -> None:
    """Options value wins and produces a timedelta."""
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="X",
        data={
            CONF_NAME: "X",
            CONF_WEATHER_ENTITIES: ["weather.primary"],
            CONF_CATEGORY: DEFAULT_CATEGORY,
            CONF_CUSTOMIZE_THRESHOLDS: False,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
        },
        options={CONF_SCAN_INTERVAL_MINUTES: 7},
        entry_id="si_opt",
        unique_id=f"{DOMAIN}_si_opt",
    )
    assert WashWiseCoordinator._resolve_scan_interval(entry) == timedelta(minutes=7)


def test_resolve_scan_interval_from_data_when_options_missing(
    hass: HomeAssistant,
) -> None:
    """Data value used when options has no scan_interval_minutes."""
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="X",
        data={
            CONF_NAME: "X",
            CONF_WEATHER_ENTITIES: ["weather.primary"],
            CONF_CATEGORY: DEFAULT_CATEGORY,
            CONF_CUSTOMIZE_THRESHOLDS: False,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
            CONF_SCAN_INTERVAL_MINUTES: 12,
        },
        options={},
        entry_id="si_data",
        unique_id=f"{DOMAIN}_si_data",
    )
    assert WashWiseCoordinator._resolve_scan_interval(entry) == timedelta(minutes=12)


def test_resolve_scan_interval_invalid_value_falls_back(hass: HomeAssistant) -> None:
    """Non-int / non-coercible value → fall back to default."""
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="X",
        data={
            CONF_NAME: "X",
            CONF_WEATHER_ENTITIES: ["weather.primary"],
            CONF_CATEGORY: DEFAULT_CATEGORY,
            CONF_CUSTOMIZE_THRESHOLDS: False,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
        },
        options={CONF_SCAN_INTERVAL_MINUTES: "not-a-number"},
        entry_id="si_bad",
        unique_id=f"{DOMAIN}_si_bad",
    )
    assert WashWiseCoordinator._resolve_scan_interval(entry) == SCAN_INTERVAL


def test_resolve_scan_interval_zero_or_negative_falls_back(
    hass: HomeAssistant,
) -> None:
    """Zero / negative minutes are nonsensical → fall back to default."""
    entry = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="X",
        data={
            CONF_NAME: "X",
            CONF_WEATHER_ENTITIES: ["weather.primary"],
            CONF_CATEGORY: DEFAULT_CATEGORY,
            CONF_CUSTOMIZE_THRESHOLDS: False,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
        },
        options={CONF_SCAN_INTERVAL_MINUTES: 0},
        entry_id="si_zero",
        unique_id=f"{DOMAIN}_si_zero",
    )
    assert WashWiseCoordinator._resolve_scan_interval(entry) == SCAN_INTERVAL


# ---------------------------------------------------------------------------
# Registry-rename listener
# ---------------------------------------------------------------------------


async def test_handle_registry_updated_ignores_unknown_action(
    hass: HomeAssistant,
) -> None:
    """Unknown actions (e.g. ``create``) → callback is a no-op."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    fake_event = type("E", (), {"data": {"action": "create", "entity_id": "weather.primary"}})()
    with patch.object(hass.config_entries, "async_update_entry") as mock_update:
        coord._handle_registry_updated(fake_event)  # type: ignore[arg-type]
    mock_update.assert_not_called()


async def test_handle_registry_updated_ignores_non_entity_id_change(
    hass: HomeAssistant,
) -> None:
    """Update without ``entity_id`` in changes → callback is a no-op."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    fake_event = type(
        "E",
        (),
        {
            "data": {
                "action": "update",
                "entity_id": "weather.primary",
                "changes": {"name": "renamed"},
            }
        },
    )()
    with patch.object(hass.config_entries, "async_update_entry") as mock_update:
        coord._handle_registry_updated(fake_event)  # type: ignore[arg-type]
    mock_update.assert_not_called()


async def test_handle_registry_updated_entity_id_rename_rewrites_and_reloads(
    hass: HomeAssistant,
) -> None:
    """Rename rewrites ``weather_entities`` and schedules a reload."""
    entry = _make_entry(["weather.primary", "weather.backup"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    scheduled: list[Any] = []

    def fake_create_task(coro, *_a, **_kw):
        with contextlib.suppress(Exception):
            coro.close()
        scheduled.append(coro)
        return None

    with (
        patch.object(hass.config_entries, "async_update_entry") as mock_update,
        patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as mock_reload,
        patch.object(hass, "async_create_task", side_effect=fake_create_task),
    ):
        fake_event = type(
            "E",
            (),
            {
                "data": {
                    "action": "update",
                    "entity_id": "weather.primary_renamed",
                    "changes": {"entity_id": "weather.primary"},
                }
            },
        )()
        coord._handle_registry_updated(fake_event)  # type: ignore[arg-type]

    mock_update.assert_called_once()
    _args, kwargs = mock_update.call_args
    assert kwargs["data"][CONF_WEATHER_ENTITIES] == [
        "weather.primary_renamed",
        "weather.backup",
    ]
    # Reload coroutine scheduled (closed by fake_create_task to suppress warning).
    assert len(scheduled) == 1
    mock_reload.assert_called_once()


async def test_handle_registry_updated_remove_drops_entity_and_reloads(
    hass: HomeAssistant,
) -> None:
    """Remove drops the entity from ``weather_entities`` and reloads."""
    entry = _make_entry(["weather.primary", "weather.backup"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    scheduled: list[Any] = []

    def fake_create_task(coro, *_a, **_kw):
        with contextlib.suppress(Exception):
            coro.close()
        scheduled.append(coro)
        return None

    with (
        patch.object(hass.config_entries, "async_update_entry") as mock_update,
        patch.object(hass.config_entries, "async_reload", new=AsyncMock()),
        patch.object(hass, "async_create_task", side_effect=fake_create_task),
    ):
        fake_event = type(
            "E",
            (),
            {"data": {"action": "remove", "entity_id": "weather.primary"}},
        )()
        coord._handle_registry_updated(fake_event)  # type: ignore[arg-type]

    mock_update.assert_called_once()
    _args, kwargs = mock_update.call_args
    assert kwargs["data"][CONF_WEATHER_ENTITIES] == ["weather.backup"]
    assert len(scheduled) == 1


async def test_handle_registry_updated_remove_unknown_entity_noop(
    hass: HomeAssistant,
) -> None:
    """Remove for an entity not in ``weather_entities`` → no-op."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    with patch.object(hass.config_entries, "async_update_entry") as mock_update:
        fake_event = type("E", (), {"data": {"action": "remove", "entity_id": "weather.unknown"}})()
        coord._handle_registry_updated(fake_event)  # type: ignore[arg-type]
    mock_update.assert_not_called()


async def test_handle_registry_updated_missing_entity_id_noop(
    hass: HomeAssistant,
) -> None:
    """Event without ``entity_id`` payload → no-op."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    with patch.object(hass.config_entries, "async_update_entry") as mock_update:
        fake_event = type("E", (), {"data": {"action": "update"}})()
        coord._handle_registry_updated(fake_event)  # type: ignore[arg-type]
    mock_update.assert_not_called()


async def test_handle_registry_updated_rename_to_same_id_noop(
    hass: HomeAssistant,
) -> None:
    """Rename whose old==new id → no-op (defensive)."""
    entry = _make_entry(["weather.primary"])
    entry.add_to_hass(hass)
    coord, _stub = _build_coordinator(hass, entry)

    fake_event = type(
        "E",
        (),
        {
            "data": {
                "action": "update",
                "entity_id": "weather.primary",
                "changes": {"entity_id": "weather.primary"},
            }
        },
    )()
    with patch.object(hass.config_entries, "async_update_entry") as mock_update:
        coord._handle_registry_updated(fake_event)  # type: ignore[arg-type]
    mock_update.assert_not_called()
