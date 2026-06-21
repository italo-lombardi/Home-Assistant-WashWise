"""Persistent storage helper for WashWise.

The store keeps a single in-memory copy of :class:`StoredData` and only
hits disk on cold start (first read after setup) and on writes. Every
mutator (``append_wash``, ``set_snooze``, ``record_failover``,
``update_provider_health``, ``gc_stale_health``) routes its read through
the cached accessor so the coordinator's per-tick health-update path no
longer issues redundant ``Store.async_load()`` calls. See WW-3.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from json import JSONDecodeError
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STALE_PROVIDER_TTL_DAYS, STORAGE_KEY_FMT, STORAGE_VERSION
from .models import (
    ProviderHealth,
    StoredData,
    WashEntry,
)

_LOGGER = logging.getLogger(__name__)

_WASH_LOG_MAX = 365


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except (TypeError, ValueError):
        return None


class WashWiseStore:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._hass = hass
        self._entry_id = entry_id
        self._store: Store = Store(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY_FMT.format(entry_id=entry_id),
        )
        self._data: StoredData | None = None
        # Single-flight guard so concurrent first-readers don't both
        # trigger ``Store.async_load()`` while the cache is empty.
        self._load_lock: asyncio.Lock = asyncio.Lock()
        # Diagnostics: count actual disk reads (not cache hits).
        self._disk_read_count: int = 0

    @property
    def disk_read_count(self) -> int:
        """Return the number of ``Store.async_load()`` calls made so far."""
        return self._disk_read_count

    async def load(self) -> StoredData:
        """Return cached :class:`StoredData`, hitting disk only when cold."""
        if self._data is not None:
            return self._data
        return await self._load_from_disk("cold cache")

    async def _load_from_disk(self, reason: str) -> StoredData:
        """Read from disk under the single-flight lock and cache the result."""
        async with self._load_lock:
            # Re-check inside the lock: another waiter may have populated
            # the cache while we were queued.
            if self._data is not None:
                return self._data

            self._disk_read_count += 1
            _LOGGER.debug(
                "WashWise storage read for %s: %s (total reads=%d)",
                self._entry_id,
                reason,
                self._disk_read_count,
            )
            try:
                raw = await self._store.async_load()
            except (JSONDecodeError, OSError) as err:
                _LOGGER.warning(
                    "WashWise storage for %s is corrupt (%s); resetting to empty.",
                    self._entry_id,
                    err,
                )
                self._data = StoredData.empty()
                return self._data

            if raw is None:
                self._data = StoredData.empty()
                return self._data

            try:
                self._data = StoredData.from_dict(raw)
                return self._data
            except (TypeError, ValueError, KeyError) as err:
                _LOGGER.warning(
                    "WashWise storage for %s failed to deserialize (%s); resetting to empty.",
                    self._entry_id,
                    err,
                )
                self._data = StoredData.empty()
                return self._data

    async def save(self, data: StoredData) -> None:
        # Update the in-memory cache FIRST so subsequent reads see the new
        # state immediately, even if the disk write is still in flight.
        # If the disk write fails, revert the cache so a follow-up read
        # doesn't observe state that was never persisted.
        previous = self._data
        self._data = data
        try:
            await self._store.async_save(data.to_dict())
        except Exception:
            self._data = previous
            raise

    async def remove(self) -> None:
        # Hold the load lock so an in-flight ``_load_from_disk`` cannot
        # re-populate the cache after the file is deleted.
        async with self._load_lock:
            await self._store.async_remove()
            self._data = None

    async def append_wash(self, entry: WashEntry) -> None:
        data = await self.load()
        wash_log = list(data.wash_log)
        wash_log.append(entry)
        if len(wash_log) > _WASH_LOG_MAX:
            wash_log = wash_log[-_WASH_LOG_MAX:]
        await self.save(
            StoredData(
                wash_log=wash_log,
                snooze_until=data.snooze_until,
                last_failover_ts=data.last_failover_ts,
                last_failover_from=data.last_failover_from,
                last_failover_to=data.last_failover_to,
                provider_health=dict(data.provider_health),
            )
        )

    async def set_snooze(self, until: datetime | None) -> None:
        data = await self.load()
        await self.save(
            StoredData(
                wash_log=list(data.wash_log),
                snooze_until=until.isoformat() if until is not None else None,
                last_failover_ts=data.last_failover_ts,
                last_failover_from=data.last_failover_from,
                last_failover_to=data.last_failover_to,
                provider_health=dict(data.provider_health),
            )
        )

    async def record_failover(self, frm: str | None, to: str) -> None:
        data = await self.load()
        await self.save(
            StoredData(
                wash_log=list(data.wash_log),
                snooze_until=data.snooze_until,
                last_failover_ts=_utcnow().isoformat(),
                last_failover_from=frm,
                last_failover_to=to,
                provider_health=dict(data.provider_health),
            )
        )

    async def update_provider_health(
        self,
        entity_id: str,
        ok: bool,
        error: str | None,
    ) -> None:
        data = await self.load()
        health = dict(data.provider_health)
        now_iso = _utcnow().isoformat()
        existing = health.get(entity_id)
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
                last_error=None if ok else error,
                last_seen_ts=now_iso,
            )
        health[entity_id] = new_health
        await self.save(
            StoredData(
                wash_log=list(data.wash_log),
                snooze_until=data.snooze_until,
                last_failover_ts=data.last_failover_ts,
                last_failover_from=data.last_failover_from,
                last_failover_to=data.last_failover_to,
                provider_health=health,
            )
        )

    async def gc_stale_health(self) -> None:
        data = await self.load()
        if not data.provider_health:
            return
        cutoff = _utcnow() - timedelta(days=STALE_PROVIDER_TTL_DAYS)
        kept: dict[str, ProviderHealth] = {}
        changed = False
        for entity_id, record in data.provider_health.items():
            seen = _parse_ts(record.last_seen_ts)
            # Keep only records seen within the TTL; expire missing/unparseable timestamps.
            if seen is not None and seen >= cutoff:
                kept[entity_id] = record
            else:
                changed = True
        if not changed:
            return
        await self.save(
            StoredData(
                wash_log=list(data.wash_log),
                snooze_until=data.snooze_until,
                last_failover_ts=data.last_failover_ts,
                last_failover_from=data.last_failover_from,
                last_failover_to=data.last_failover_to,
                provider_health=kept,
            )
        )

    async def migrate(self, old_data: dict[str, Any], old_version: int) -> dict[str, Any]:
        del old_version
        return old_data
