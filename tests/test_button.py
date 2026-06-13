"""Tests for the WashWise button platform.

Covers the single button surfaced per config entry:

* ``mark_washed`` -- appends to the persisted wash log; ``days_since_wash``
  observes the new entry on the very next refresh.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from freezegun import freeze_time

from custom_components.washwise.button import (
    WashWiseMarkWashedButton,
    async_setup_entry,
)
from custom_components.washwise.const import CONF_CATEGORY, DOMAIN
from custom_components.washwise.models import WashEntry
from custom_components.washwise.sensor import DaysSinceWashSensor
from custom_components.washwise.storage import WashWiseStore

# ----------------------------------------------------------------------
# Coordinator stub built around a real WashWiseStore
# ----------------------------------------------------------------------


class _CoordinatorStub:
    """Coordinator stub backed by a real :class:`WashWiseStore`."""

    def __init__(self, store: WashWiseStore) -> None:
        self._store = store
        self.refresh_calls = 0

    @property
    def last_update_success(self) -> bool:
        return True

    @property
    def data(self) -> Any:
        return None

    def async_add_listener(self, update_callback, context=None):
        return lambda: None

    async def async_mark_washed(self, timestamp: datetime | None = None) -> None:
        ts = timestamp if timestamp is not None else datetime.now(UTC)
        await self._store.append_wash(WashEntry(timestamp=ts.isoformat(), source="manual"))
        self._store._data = await self._store.load()
        await self.async_request_refresh()

    async def async_request_refresh(self) -> None:
        self.refresh_calls += 1


def _make_entry(
    *,
    entry_id: str = "test_entry_id",
    title: str = "Test Wash",
    category: str = "car",
) -> SimpleNamespace:
    return SimpleNamespace(
        entry_id=entry_id,
        data={CONF_CATEGORY: category},
        options={},
        title=title,
    )


# ----------------------------------------------------------------------
# mark_washed
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_washed_appends_wash_entry(hass) -> None:
    """Pressing ``mark_washed`` appends a WashEntry to the persisted log."""
    store = WashWiseStore(hass, "test_entry_id")
    coordinator = _CoordinatorStub(store)
    entry = _make_entry()

    button = WashWiseMarkWashedButton(coordinator, entry)
    await button.async_press()

    data = await store.load()
    assert len(data.wash_log) == 1
    assert data.wash_log[0].source == "manual"
    assert coordinator.refresh_calls == 1


@pytest.mark.asyncio
async def test_mark_washed_updates_days_since_wash_sensor(hass) -> None:
    """After ``mark_washed`` the days_since_wash sensor reads a fresh value."""
    store = WashWiseStore(hass, "test_entry_id")
    coordinator = _CoordinatorStub(store)
    entry = _make_entry()
    sensor = DaysSinceWashSensor(coordinator, entry)

    assert sensor.native_value is None

    button = WashWiseMarkWashedButton(coordinator, entry)
    with freeze_time("2026-06-11 12:00:00", tz_offset=0):
        await button.async_press()
        assert sensor.native_value == 0


# ----------------------------------------------------------------------
# async_setup_entry
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_setup_entry_registers_one_button(hass) -> None:
    """``async_setup_entry`` registers exactly one button."""
    store = WashWiseStore(hass, "test_entry_id")
    coordinator = _CoordinatorStub(store)
    entry = _make_entry()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    added: list[Any] = []

    def _add(entities, update_before_add: bool = False) -> None:
        added.extend(entities)

    await async_setup_entry(hass, entry, _add)

    assert len(added) == 1
    assert added[0].unique_id == f"{entry.entry_id}_mark_washed"


@pytest.mark.asyncio
async def test_mark_irrigated_translation_key_for_garden_irrigation(hass) -> None:
    """Button uses mark_irrigated translation key for garden_irrigation category."""
    store = WashWiseStore(hass, "irr_entry")
    coordinator = _CoordinatorStub(store)
    entry = _make_entry(entry_id="irr_entry", category="garden_irrigation")
    button = WashWiseMarkWashedButton(coordinator, entry)
    assert button._attr_translation_key == "mark_irrigated"


@pytest.mark.asyncio
async def test_mark_washed_translation_key_for_non_irrigation(hass) -> None:
    """Button uses mark_washed translation key for non-irrigation categories."""
    store = WashWiseStore(hass, "car_entry")
    coordinator = _CoordinatorStub(store)
    entry = _make_entry(entry_id="car_entry", category="car")
    button = WashWiseMarkWashedButton(coordinator, entry)
    assert button._attr_translation_key == "mark_washed"
