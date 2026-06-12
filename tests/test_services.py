"""Tests for ``custom_components.washwise.services``.

Covers:
* ``mark_washed`` — appends a wash-log entry
* ``snooze`` — accepts ``hours`` integer
* ``clear_snooze`` — cancels the active snooze
Plus target validation and idempotent registration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise.const import DOMAIN
from custom_components.washwise.coordinator import WashWiseCoordinator
from custom_components.washwise.services import (
    ATTR_HOURS,
    ATTR_TIMESTAMP,
    SERVICE_CLEAR_SNOOZE,
    SERVICE_MARK_WASHED,
    SERVICE_SNOOZE,
    async_register_services,
    async_unregister_services,
)


@pytest.fixture
async def stub_coordinator(hass: HomeAssistant, mock_config_entry: MockConfigEntry):
    from homeassistant.helpers import entity_registry as er

    mock_config_entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.entry = mock_config_entry
    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coord

    registry = er.async_get(hass)
    entity_entry = registry.async_get_or_create(
        domain="binary_sensor",
        platform=DOMAIN,
        unique_id=f"{mock_config_entry.entry_id}_can_wash",
        suggested_object_id="test_wash_can_wash",
        config_entry=mock_config_entry,
    )

    await async_register_services(hass)
    yield hass, coord, entity_entry.entity_id
    async_unregister_services(hass)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


async def test_all_services_registered(stub_coordinator) -> None:
    hass, _, _ = stub_coordinator
    for name in (SERVICE_MARK_WASHED, SERVICE_SNOOZE, SERVICE_CLEAR_SNOOZE):
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
    hass, coord, entity_id = stub_coordinator
    await hass.services.async_call(
        DOMAIN, SERVICE_MARK_WASHED, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    coord.async_mark_washed.assert_awaited_once()
    args, _ = coord.async_mark_washed.call_args
    assert args[0] is None


async def test_mark_washed_with_explicit_timestamp(stub_coordinator) -> None:
    hass, coord, entity_id = stub_coordinator
    ts = datetime(2026, 6, 11, 10, 30, 0, tzinfo=UTC)
    await hass.services.async_call(
        DOMAIN,
        SERVICE_MARK_WASHED,
        {ATTR_ENTITY_ID: entity_id, ATTR_TIMESTAMP: ts.isoformat()},
        blocking=True,
    )
    coord.async_mark_washed.assert_awaited_once()
    args, _ = coord.async_mark_washed.call_args
    assert args[0] == ts


async def test_mark_washed_via_service_equivalent_to_button(stub_coordinator) -> None:
    hass, coord, entity_id = stub_coordinator
    await hass.services.async_call(
        DOMAIN, SERVICE_MARK_WASHED, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    await coord.async_mark_washed(None)
    assert coord.async_mark_washed.await_count == 2


# ---------------------------------------------------------------------------
# snooze
# ---------------------------------------------------------------------------


async def test_snooze_with_hours(stub_coordinator) -> None:
    hass, coord, entity_id = stub_coordinator
    await hass.services.async_call(
        DOMAIN,
        SERVICE_SNOOZE,
        {ATTR_ENTITY_ID: entity_id, ATTR_HOURS: 24},
        blocking=True,
    )
    coord.async_snooze.assert_awaited_once()
    args, _ = coord.async_snooze.call_args
    assert args[0] == timedelta(hours=24)


async def test_snooze_schema_rejects_zero_hours(stub_coordinator) -> None:
    hass, _, entity_id = stub_coordinator
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SNOOZE,
            {ATTR_ENTITY_ID: entity_id, ATTR_HOURS: 0},
            blocking=True,
        )


async def test_snooze_schema_rejects_missing_hours(stub_coordinator) -> None:
    hass, _, entity_id = stub_coordinator
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SNOOZE,
            {ATTR_ENTITY_ID: entity_id},
            blocking=True,
        )


# ---------------------------------------------------------------------------
# clear_snooze
# ---------------------------------------------------------------------------


async def test_clear_snooze_calls_coordinator(stub_coordinator) -> None:
    hass, coord, entity_id = stub_coordinator
    await hass.services.async_call(
        DOMAIN, SERVICE_CLEAR_SNOOZE, {ATTR_ENTITY_ID: entity_id}, blocking=True
    )
    coord.async_clear_snooze.assert_awaited_once()


# ---------------------------------------------------------------------------
# Target validation
# ---------------------------------------------------------------------------


async def test_invalid_entity_raises(stub_coordinator) -> None:
    hass, coord, _ = stub_coordinator
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_MARK_WASHED,
            {ATTR_ENTITY_ID: "binary_sensor.does_not_exist"},
            blocking=True,
        )
    coord.async_mark_washed.assert_not_called()


async def test_no_target_raises(stub_coordinator) -> None:
    hass, coord, _ = stub_coordinator
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(DOMAIN, SERVICE_MARK_WASHED, {}, blocking=True)
    coord.async_mark_washed.assert_not_called()


async def test_device_target_resolves_to_coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    from homeassistant.const import ATTR_DEVICE_ID
    from homeassistant.helpers import device_registry as dr

    mock_config_entry.add_to_hass(hass)
    coord = AsyncMock(spec=WashWiseCoordinator)
    coord.entry = mock_config_entry
    hass.data.setdefault(DOMAIN, {})[mock_config_entry.entry_id] = coord

    registry = dr.async_get(hass)
    device = registry.async_get_or_create(
        config_entry_id=mock_config_entry.entry_id,
        identifiers={(DOMAIN, "test_device")},
        name="Test Device",
    )

    await async_register_services(hass)
    try:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_MARK_WASHED,
            {ATTR_DEVICE_ID: device.id},
            blocking=True,
        )
    finally:
        async_unregister_services(hass)

    coord.async_mark_washed.assert_awaited_once()


async def test_unknown_device_id_raises(stub_coordinator) -> None:
    from homeassistant.const import ATTR_DEVICE_ID

    hass, coord, _ = stub_coordinator
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_MARK_WASHED,
            {ATTR_DEVICE_ID: "no-such-device-id"},
            blocking=True,
        )
    coord.async_mark_washed.assert_not_called()
