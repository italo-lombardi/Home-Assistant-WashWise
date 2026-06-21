"""Tests for ``custom_components.washwise.storage``.

Covers the round-tripping of :class:`StoredData` through HA's ``Store``,
the helper mutation methods (``append_wash``, ``set_snooze``,
``set_override``, ``record_failover``, ``update_provider_health``,
``gc_stale_health``), corrupt-file recovery, and the v1 migration stub.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from unittest.mock import AsyncMock, patch

import pytest
from freezegun import freeze_time
from homeassistant.core import HomeAssistant

from custom_components.washwise.const import STALE_PROVIDER_TTL_DAYS
from custom_components.washwise.models import (
    ProviderHealth,
    StoredData,
    WashEntry,
)
from custom_components.washwise.storage import WashWiseStore, _parse_ts

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(hass: HomeAssistant) -> WashWiseStore:
    """Return a fresh WashWiseStore bound to a unique entry_id."""
    return WashWiseStore(hass, "test_entry_id")


@pytest.fixture
def utc_now() -> datetime:
    """Return a fixed UTC reference timestamp."""
    return datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# load() — missing file → empty StoredData
# ---------------------------------------------------------------------------


async def test_load_missing_returns_empty(store: WashWiseStore) -> None:
    """Loading when no file exists returns an empty StoredData blob."""
    data = await store.load()
    assert isinstance(data, StoredData)
    assert data.wash_log == []
    assert data.snooze_until is None
    assert data.last_failover_ts is None
    assert data.last_failover_from is None
    assert data.last_failover_to is None
    assert data.provider_health == {}


# ---------------------------------------------------------------------------
# save() / load() roundtrip
# ---------------------------------------------------------------------------


async def test_save_load_roundtrip(store: WashWiseStore, utc_now: datetime) -> None:
    """A blob written by save() is recoverable bit-for-bit via load()."""
    payload = StoredData(
        wash_log=[
            WashEntry(timestamp=utc_now.isoformat(), source="manual"),
            WashEntry(
                timestamp=(utc_now - timedelta(days=1)).isoformat(),
                source="auto",
            ),
        ],
        snooze_until=(utc_now + timedelta(hours=2)).isoformat(),
        last_failover_ts=utc_now.isoformat(),
        last_failover_from="weather.primary",
        last_failover_to="weather.secondary",
        provider_health={
            "weather.primary": ProviderHealth(
                entity_id="weather.primary",
                success_count=42,
                failure_count=3,
                last_success_ts=utc_now.isoformat(),
                last_failure_ts=(utc_now - timedelta(hours=12)).isoformat(),
                last_error="timeout",
                last_seen_ts=utc_now.isoformat(),
            )
        },
    )

    await store.save(payload)
    restored = await store.load()

    assert restored == payload


# ---------------------------------------------------------------------------
# append_wash — trims to last 365 entries
# ---------------------------------------------------------------------------


async def test_append_wash_trims_to_365(store: WashWiseStore, utc_now: datetime) -> None:
    """append_wash never lets the wash log grow past the 365-entry cap."""
    base = StoredData(
        wash_log=[
            WashEntry(
                timestamp=(utc_now - timedelta(days=400 - i)).isoformat(),
                source="auto",
            )
            for i in range(370)
        ],
    )
    await store.save(base)

    new_entry = WashEntry(timestamp=utc_now.isoformat(), source="manual")
    await store.append_wash(new_entry)

    data = await store.load()
    assert len(data.wash_log) == 365
    # The freshly appended entry is always preserved (it's the newest).
    assert data.wash_log[-1] == new_entry
    # The oldest entries fell off the front.
    assert data.wash_log[0].timestamp != base.wash_log[0].timestamp


async def test_append_wash_under_cap_preserves_all(store: WashWiseStore, utc_now: datetime) -> None:
    """Below the cap append_wash is just a list append."""
    initial = StoredData(
        wash_log=[
            WashEntry(timestamp=utc_now.isoformat(), source="auto"),
        ],
    )
    await store.save(initial)
    new_entry = WashEntry(
        timestamp=(utc_now + timedelta(minutes=5)).isoformat(),
        source="manual",
    )
    await store.append_wash(new_entry)

    data = await store.load()
    assert len(data.wash_log) == 2
    assert data.wash_log[-1] == new_entry


# ---------------------------------------------------------------------------
# set_snooze
# ---------------------------------------------------------------------------


async def test_set_snooze_writes_iso_value(store: WashWiseStore, utc_now: datetime) -> None:
    """set_snooze persists the ISO-encoded datetime."""
    target = utc_now + timedelta(hours=4)
    await store.set_snooze(target)

    data = await store.load()
    assert data.snooze_until == target.isoformat()


async def test_set_snooze_none_clears(store: WashWiseStore, utc_now: datetime) -> None:
    """Passing None to set_snooze clears the snooze field."""
    await store.set_snooze(utc_now + timedelta(hours=1))
    await store.set_snooze(None)

    data = await store.load()
    assert data.snooze_until is None


# ---------------------------------------------------------------------------
# record_failover
# ---------------------------------------------------------------------------


async def test_record_failover_updates_fields(store: WashWiseStore) -> None:
    """record_failover stores from/to + a fresh timestamp."""
    frozen = "2026-06-11T12:00:00+00:00"
    with freeze_time(frozen):
        await store.record_failover("weather.a", "weather.b")

    data = await store.load()
    assert data.last_failover_from == "weather.a"
    assert data.last_failover_to == "weather.b"
    # Stored timestamp matches the frozen moment.
    assert data.last_failover_ts == datetime(2026, 6, 11, 12, 0, 0, tzinfo=UTC).isoformat()


async def test_record_failover_first_pick_allows_none_from(
    store: WashWiseStore,
) -> None:
    """record_failover accepts ``frm=None`` for the very first pick."""
    await store.record_failover(None, "weather.b")

    data = await store.load()
    assert data.last_failover_from is None
    assert data.last_failover_to == "weather.b"
    assert data.last_failover_ts is not None


# ---------------------------------------------------------------------------
# update_provider_health
# ---------------------------------------------------------------------------


async def test_update_provider_health_first_success(
    store: WashWiseStore,
) -> None:
    """First success creates a record with success_count=1."""
    frozen = "2026-06-11T12:00:00+00:00"
    with freeze_time(frozen):
        await store.update_provider_health("weather.a", True, None)

    data = await store.load()
    record = data.provider_health["weather.a"]
    assert record.success_count == 1
    assert record.failure_count == 0
    assert record.last_success_ts is not None
    assert record.last_failure_ts is None
    assert record.last_error is None
    assert record.last_seen_ts is not None


async def test_update_provider_health_first_failure(
    store: WashWiseStore,
) -> None:
    """First failure creates a record with failure_count=1 + last_error."""
    await store.update_provider_health("weather.a", False, "boom")

    data = await store.load()
    record = data.provider_health["weather.a"]
    assert record.success_count == 0
    assert record.failure_count == 1
    assert record.last_success_ts is None
    assert record.last_failure_ts is not None
    assert record.last_error == "boom"


async def test_update_provider_health_increments_counters(
    store: WashWiseStore,
) -> None:
    """Repeated calls accumulate success / failure counters independently."""
    await store.update_provider_health("weather.a", True, None)
    await store.update_provider_health("weather.a", True, None)
    await store.update_provider_health("weather.a", False, "transient")

    data = await store.load()
    record = data.provider_health["weather.a"]
    assert record.success_count == 2
    assert record.failure_count == 1
    assert record.last_error == "transient"
    # last_success_ts stays at the most recent successful tick.
    assert record.last_success_ts is not None


async def test_update_provider_health_keeps_last_seen_on_each_call(
    store: WashWiseStore,
) -> None:
    """``last_seen_ts`` is bumped on every call regardless of outcome."""
    with freeze_time("2026-06-11T12:00:00+00:00"):
        await store.update_provider_health("weather.a", True, None)
    with freeze_time("2026-06-11T13:00:00+00:00"):
        await store.update_provider_health("weather.a", False, "x")

    data = await store.load()
    seen = data.provider_health["weather.a"].last_seen_ts
    assert seen.startswith("2026-06-11T13:00:00")


# ---------------------------------------------------------------------------
# gc_stale_health — drops records older than TTL (uses freezegun)
# ---------------------------------------------------------------------------


async def test_gc_stale_health_drops_old(store: WashWiseStore) -> None:
    """Records past the TTL window are removed; fresh ones survive."""
    # Seed two records: one from "long ago", one fresh.
    with freeze_time("2026-01-01T00:00:00+00:00"):
        await store.update_provider_health("weather.old", True, None)
    with freeze_time("2026-06-10T00:00:00+00:00"):
        await store.update_provider_health("weather.fresh", True, None)

    # Run GC well past the TTL relative to the old record.
    with freeze_time("2026-06-11T00:00:00+00:00"):
        await store.gc_stale_health()

    data = await store.load()
    assert "weather.old" not in data.provider_health
    assert "weather.fresh" in data.provider_health


async def test_gc_stale_health_no_records_is_noop(store: WashWiseStore) -> None:
    """gc_stale_health is a no-op when there are no provider_health entries."""
    await store.gc_stale_health()
    data = await store.load()
    assert data.provider_health == {}


async def test_gc_stale_health_keeps_all_when_within_ttl(
    store: WashWiseStore,
) -> None:
    """Records inside the TTL window are all preserved."""
    with freeze_time("2026-06-10T00:00:00+00:00"):
        await store.update_provider_health("weather.a", True, None)
        await store.update_provider_health("weather.b", True, None)

    # GC the next day — all still inside TTL.
    with freeze_time("2026-06-11T00:00:00+00:00"):
        await store.gc_stale_health()

    data = await store.load()
    assert set(data.provider_health.keys()) == {"weather.a", "weather.b"}


async def test_gc_stale_health_unparseable_seen_kept(
    store: WashWiseStore, utc_now: datetime
) -> None:
    """Records whose last_seen_ts cannot be parsed are expired (defensive)."""
    bad = ProviderHealth(
        entity_id="weather.weird",
        success_count=1,
        failure_count=0,
        last_success_ts=None,
        last_failure_ts=None,
        last_error=None,
        last_seen_ts="not-a-real-iso-timestamp",
    )
    await store.save(StoredData(provider_health={"weather.weird": bad}))

    with freeze_time(utc_now + timedelta(days=STALE_PROVIDER_TTL_DAYS + 5)):
        await store.gc_stale_health()

    data = await store.load()
    assert "weather.weird" not in data.provider_health


# ---------------------------------------------------------------------------
# Corrupt JSON file → recoverable empty state, warning logged
# ---------------------------------------------------------------------------


async def test_corrupt_json_returns_empty_and_warns(
    store: WashWiseStore, caplog: pytest.LogCaptureFixture
) -> None:
    """A JSONDecodeError surfaces as an empty StoredData + a warning log."""
    with (
        patch.object(
            store._store,
            "async_load",
            side_effect=JSONDecodeError("expecting value", "", 0),
        ),
        caplog.at_level(logging.WARNING),
    ):
        data = await store.load()

    assert data == StoredData.empty()
    assert "corrupt" in caplog.text.lower()


async def test_oserror_returns_empty_and_warns(
    store: WashWiseStore, caplog: pytest.LogCaptureFixture
) -> None:
    """An OSError on load is also recovered to an empty StoredData."""
    with (
        patch.object(store._store, "async_load", side_effect=OSError("disk gone")),
        caplog.at_level(logging.WARNING),
    ):
        data = await store.load()

    assert data == StoredData.empty()
    assert "corrupt" in caplog.text.lower()


async def test_malformed_payload_returns_empty_and_warns(
    store: WashWiseStore, caplog: pytest.LogCaptureFixture
) -> None:
    """Structurally-broken payloads are recovered to an empty StoredData."""
    fake_load = AsyncMock(return_value={"wash_log": [{"timestamp": object()}]})
    with (
        patch.object(store._store, "async_load", new=fake_load),
        # The model layer raises TypeError for non-string timestamps via str(),
        # so force the failure with a deeper invalid shape.
        patch(
            "custom_components.washwise.storage.StoredData.from_dict",
            side_effect=ValueError("bad shape"),
        ),
        caplog.at_level(logging.WARNING),
    ):
        data = await store.load()

    assert data == StoredData.empty()
    assert "deserialize" in caplog.text.lower()


# ---------------------------------------------------------------------------
# remove()
# ---------------------------------------------------------------------------


async def test_remove_deletes_file(store: WashWiseStore, utc_now: datetime) -> None:
    """remove() clears the persisted file so a follow-up load is empty."""
    await store.save(
        StoredData(wash_log=[WashEntry(timestamp=utc_now.isoformat(), source="manual")])
    )
    await store.remove()
    data = await store.load()
    assert data == StoredData.empty()


# ---------------------------------------------------------------------------
# _parse_ts — naive ISO string gets UTC tzinfo attached (storage.py:35)
# ---------------------------------------------------------------------------


def test_parse_ts_naive_iso_string_becomes_utc() -> None:
    """A naive ISO string (no tz suffix) is made tz-aware with UTC."""
    result = _parse_ts("2026-06-10T00:00:00")
    assert result is not None
    assert result.tzinfo is not None
    assert result.tzinfo == UTC
    assert result.year == 2026
    assert result.month == 6
    assert result.day == 10


def test_parse_ts_aware_iso_string_preserved() -> None:
    """An already tz-aware ISO string is returned as-is."""
    result = _parse_ts("2026-06-10T00:00:00+00:00")
    assert result is not None
    assert result.tzinfo is not None


def test_parse_ts_none_returns_none() -> None:
    """None/empty input returns None."""
    assert _parse_ts(None) is None
    assert _parse_ts("") is None


def test_parse_ts_invalid_returns_none() -> None:
    """Non-ISO strings return None."""
    assert _parse_ts("not-a-timestamp") is None


# ---------------------------------------------------------------------------
# Migration v1 stub returns input unchanged
# ---------------------------------------------------------------------------


async def test_migrate_v1_returns_input_unchanged(
    store: WashWiseStore,
) -> None:
    """The v1 migration stub is a pass-through."""
    payload = {"wash_log": [{"timestamp": "x", "source": "manual"}], "extra": 1}
    result = await store.migrate(payload, 1)
    assert result == payload


async def test_migrate_v0_returns_input_unchanged(
    store: WashWiseStore,
) -> None:
    """Migrating from v0 (hypothetical) is currently a no-op too."""
    payload = {"snooze_until": None}
    result = await store.migrate(payload, 0)
    assert result == payload


# ---------------------------------------------------------------------------
# WW-3: in-memory cache eliminates per-tick disk reads
# ---------------------------------------------------------------------------


async def test_storage_read_once_per_setup(store: WashWiseStore) -> None:
    """First load hits disk; subsequent loads return the cached blob.

    Asserts ``Store.async_load`` is called exactly once across the cold
    read and 100 follow-up "tick" reads.
    """
    fake_load = AsyncMock(return_value=None)
    with patch.object(store._store, "async_load", new=fake_load):
        first = await store.load()
        assert first == StoredData.empty()
        for _ in range(100):
            await store.load()

    assert fake_load.await_count == 1
    assert store.disk_read_count == 1


async def test_storage_write_updates_cache_in_memory(
    store: WashWiseStore, utc_now: datetime
) -> None:
    """``save`` updates the cache so subsequent reads bypass disk."""
    payload = StoredData(
        wash_log=[WashEntry(timestamp=utc_now.isoformat(), source="manual")],
    )

    fake_load = AsyncMock(return_value=None)
    fake_save = AsyncMock(return_value=None)
    with (
        patch.object(store._store, "async_load", new=fake_load),
        patch.object(store._store, "async_save", new=fake_save),
    ):
        # Cold read populates the cache once.
        await store.load()
        # Write updates the cache in-memory.
        await store.save(payload)
        # Read-after-write returns the written payload without another disk hit.
        result = await store.load()

    assert result == payload
    assert fake_load.await_count == 1
    assert fake_save.await_count == 1


async def test_concurrent_first_reads_are_single_flight(
    store: WashWiseStore,
) -> None:
    """10 concurrent first-readers must trigger exactly one disk read."""

    # Slow async_load so all 10 callers race into the lock at once.
    async def slow_load() -> None:
        await asyncio.sleep(0.01)
        return None

    fake_load = AsyncMock(side_effect=slow_load)
    with patch.object(store._store, "async_load", new=fake_load):
        results = await asyncio.gather(*(store.load() for _ in range(10)))

    assert all(r == StoredData.empty() for r in results)
    assert fake_load.await_count == 1
    assert store.disk_read_count == 1


async def test_corrupt_storage_falls_back_gracefully(
    store: WashWiseStore, caplog: pytest.LogCaptureFixture
) -> None:
    """A corrupt file still recovers to an empty StoredData (cache populated)."""
    fake_load = AsyncMock(side_effect=JSONDecodeError("bad", "", 0))
    with (
        patch.object(store._store, "async_load", new=fake_load),
        caplog.at_level(logging.WARNING),
    ):
        first = await store.load()
        # Subsequent loads must come from cache, not retry disk.
        second = await store.load()

    assert first == StoredData.empty()
    assert second == StoredData.empty()
    assert fake_load.await_count == 1
    assert "corrupt" in caplog.text.lower()
