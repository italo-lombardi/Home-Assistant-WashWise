"""Tests for ``custom_components.washwise.services``.

Covers:
* ``mark_washed`` — appends a wash-log entry
* ``snooze`` — accepts ``hours`` integer
* ``clear_snooze`` — cancels the active snooze
Plus entry_id validation and idempotent registration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise.const import DOMAIN
from custom_components.washwise.coordinator import WashWiseCoordinator
from custom_components.washwise.services import (
    ATTR_ENTITY_ID,
    ATTR_ENTRY_ID,
    ATTR_HOURS,
    ATTR_TIMESTAMP,
    SERVICE_CLEAR_SNOOZE,
    SERVICE_MARK_WASHED,
    SERVICE_SET_IRRIGATION_SWITCH,
    SERVICE_SNOOZE,
    async_register_services,
    async_unregister_services,
)


@pytest.fixture
async def stub_coordinator(hass: HomeAssistant, mock_config_entry: MockConfigEntry):
    mock_config_entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.entry = mock_config_entry
    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coord

    await async_register_services(hass)
    yield hass, coord, mock_config_entry.entry_id
    async_unregister_services(hass)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def test_all_services_registered(stub_coordinator) -> None:
    hass, _, _ = stub_coordinator
    for name in (
        SERVICE_MARK_WASHED,
        SERVICE_SNOOZE,
        SERVICE_CLEAR_SNOOZE,
        SERVICE_SET_IRRIGATION_SWITCH,
    ):
        assert hass.services.has_service(DOMAIN, name)


async def test_register_services_idempotent(stub_coordinator) -> None:
    hass, _, _ = stub_coordinator
    await async_register_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_MARK_WASHED)


async def test_unregister_services_when_not_registered(hass: HomeAssistant) -> None:
    async_unregister_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_MARK_WASHED)


# ---------------------------------------------------------------------------
# mark_washed
# ---------------------------------------------------------------------------


async def test_mark_washed_calls_coordinator(stub_coordinator) -> None:
    hass, coord, entry_id = stub_coordinator
    await hass.services.async_call(
        DOMAIN, SERVICE_MARK_WASHED, {ATTR_ENTRY_ID: entry_id}, blocking=True
    )
    coord.async_mark_washed.assert_awaited_once()
    args, _ = coord.async_mark_washed.call_args
    assert args[0] is None


async def test_mark_washed_with_explicit_timestamp(stub_coordinator) -> None:
    hass, coord, entry_id = stub_coordinator
    ts = datetime(2026, 6, 11, 10, 30, 0, tzinfo=UTC)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_MARK_WASHED,
        {ATTR_ENTRY_ID: entry_id, ATTR_TIMESTAMP: ts.isoformat()},
        blocking=True,
    )
    coord.async_mark_washed.assert_awaited_once()
    args, _ = coord.async_mark_washed.call_args
    assert args[0] == ts


async def test_mark_washed_via_service_equivalent_to_button(stub_coordinator) -> None:
    hass, coord, entry_id = stub_coordinator
    await hass.services.async_call(
        DOMAIN, SERVICE_MARK_WASHED, {ATTR_ENTRY_ID: entry_id}, blocking=True
    )
    await coord.async_mark_washed(None)
    assert coord.async_mark_washed.await_count == 2


# ---------------------------------------------------------------------------
# snooze
# ---------------------------------------------------------------------------


async def test_snooze_with_hours(stub_coordinator) -> None:
    hass, coord, entry_id = stub_coordinator
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SNOOZE,
        {ATTR_ENTRY_ID: entry_id, ATTR_HOURS: 24},
        blocking=True,
    )
    coord.async_snooze.assert_awaited_once()
    args, _ = coord.async_snooze.call_args
    assert args[0] == timedelta(hours=24)


async def test_snooze_schema_rejects_zero_hours(stub_coordinator) -> None:
    hass, _, entry_id = stub_coordinator
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SNOOZE,
            {ATTR_ENTRY_ID: entry_id, ATTR_HOURS: 0},
            blocking=True,
        )


async def test_snooze_missing_hours_uses_default_24(stub_coordinator) -> None:
    hass, coord, entry_id = stub_coordinator
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SNOOZE,
        {ATTR_ENTRY_ID: entry_id},
        blocking=True,
    )
    coord.async_snooze.assert_awaited_once()
    args, _ = coord.async_snooze.call_args
    assert args[0] == timedelta(hours=24)


# ---------------------------------------------------------------------------
# clear_snooze
# ---------------------------------------------------------------------------


async def test_clear_snooze_calls_coordinator(stub_coordinator) -> None:
    hass, coord, entry_id = stub_coordinator
    await hass.services.async_call(
        DOMAIN, SERVICE_CLEAR_SNOOZE, {ATTR_ENTRY_ID: entry_id}, blocking=True
    )
    coord.async_clear_snooze.assert_awaited_once()


# ---------------------------------------------------------------------------
# Entry ID validation
# ---------------------------------------------------------------------------


async def test_unknown_entry_id_raises(stub_coordinator) -> None:
    hass, coord, _ = stub_coordinator
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_MARK_WASHED,
            {ATTR_ENTRY_ID: "does-not-exist"},
            blocking=True,
        )
    coord.async_mark_washed.assert_not_called()


async def test_no_target_raises(stub_coordinator) -> None:
    """Neither entry_id nor entity_id → the resolver raises (not a schema error)."""
    hass, coord, _ = stub_coordinator
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, SERVICE_MARK_WASHED, {}, blocking=True)
    coord.async_mark_washed.assert_not_called()


async def test_entry_id_and_entity_id_mutually_exclusive(stub_coordinator) -> None:
    """Supplying both target keys is a schema (vol.Invalid) error."""
    hass, coord, entry_id = stub_coordinator
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_MARK_WASHED,
            {ATTR_ENTRY_ID: entry_id, ATTR_ENTITY_ID: "binary_sensor.washwise_x_can_wash"},
            blocking=True,
        )
    coord.async_mark_washed.assert_not_called()


async def test_mark_washed_targeted_by_entity_id(stub_coordinator) -> None:
    """entity_id target resolves the owning config entry via the registry.

    This is the card's path: the Lovelace card only reliably knows the
    entity_id, so the backend maps entity → entry server-side.
    """
    hass, coord, _entry_id = stub_coordinator
    entity_id = (
        er.async_get(hass)
        .async_get_or_create(
            "binary_sensor",
            DOMAIN,
            "car_can_wash",
            config_entry=coord.entry,
            suggested_object_id="washwise_car_can_wash",
        )
        .entity_id
    )

    await hass.services.async_call(
        DOMAIN, SERVICE_MARK_WASHED, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    coord.async_mark_washed.assert_called_once()


async def test_entity_id_unregistered_raises(stub_coordinator) -> None:
    """An entity_id not in the registry raises rather than silently no-op."""
    hass, coord, _ = stub_coordinator
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_MARK_WASHED,
            {ATTR_ENTITY_ID: "binary_sensor.not_a_real_entity"},
            blocking=True,
        )
    coord.async_mark_washed.assert_not_called()
