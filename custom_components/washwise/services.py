"""Services for WashWise."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta

import voluptuous as vol
from homeassistant.const import ATTR_DEVICE_ID, ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .coordinator import WashWiseCoordinator

SERVICE_MARK_WASHED = "mark_washed"
SERVICE_SNOOZE = "snooze"
SERVICE_CLEAR_SNOOZE = "clear_snooze"

ATTR_TIMESTAMP = "timestamp"
ATTR_HOURS = "hours"

_SERVICES_REGISTERED_FLAG = "_services_registered"

_TARGET_SCHEMA: dict = {
    vol.Optional(ATTR_ENTITY_ID): vol.Any(cv.entity_id, [cv.entity_id]),
    vol.Optional(ATTR_DEVICE_ID): vol.Any(cv.string, [cv.string]),
}

MARK_WASHED_SCHEMA = vol.Schema(
    {
        **_TARGET_SCHEMA,
        vol.Optional(ATTR_TIMESTAMP): cv.datetime,
    }
)

SNOOZE_SCHEMA = vol.Schema(
    {
        **_TARGET_SCHEMA,
        vol.Required(ATTR_HOURS): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)

CLEAR_SNOOZE_SCHEMA = vol.Schema(_TARGET_SCHEMA)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, Iterable):
        return [str(v) for v in value]
    return [str(value)]


def _entry_id_from_entity(hass: HomeAssistant, entity_id: str) -> str | None:
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry is None:
        return None
    return entry.config_entry_id


def _entry_ids_from_device(hass: HomeAssistant, device_id: str) -> list[str]:
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        return []
    domain_data = hass.data.get(DOMAIN, {})
    return [
        entry_id
        for entry_id in device.config_entries
        if entry_id in domain_data and isinstance(domain_data.get(entry_id), WashWiseCoordinator)
    ]


def _resolve_coordinators(hass: HomeAssistant, call: ServiceCall) -> list[WashWiseCoordinator]:
    domain_data = hass.data.get(DOMAIN, {})
    entity_ids = _as_list(call.data.get(ATTR_ENTITY_ID))
    device_ids = _as_list(call.data.get(ATTR_DEVICE_ID))

    if not entity_ids and not device_ids:
        raise HomeAssistantError(
            "WashWise service call requires a target (entity_id or device_id)."
        )

    entry_ids: list[str] = []
    for eid in entity_ids:
        entry_id = _entry_id_from_entity(hass, eid)
        if entry_id is not None and entry_id not in entry_ids:
            entry_ids.append(entry_id)
    for did in device_ids:
        for entry_id in _entry_ids_from_device(hass, did):
            if entry_id not in entry_ids:
                entry_ids.append(entry_id)

    coordinators: list[WashWiseCoordinator] = []
    for entry_id in entry_ids:
        coord = domain_data.get(entry_id)
        if isinstance(coord, WashWiseCoordinator):
            coordinators.append(coord)

    if not coordinators:
        raise HomeAssistantError("No WashWise instance matched the provided target.")
    return coordinators


async def _handle_mark_washed(hass: HomeAssistant, call: ServiceCall) -> None:
    timestamp: datetime | None = call.data.get(ATTR_TIMESTAMP)
    for coord in _resolve_coordinators(hass, call):
        await coord.async_mark_washed(timestamp)


async def _handle_snooze(hass: HomeAssistant, call: ServiceCall) -> None:
    hours: int = call.data[ATTR_HOURS]
    duration = timedelta(hours=hours)
    for coord in _resolve_coordinators(hass, call):
        await coord.async_snooze(duration)


async def _handle_clear_snooze(hass: HomeAssistant, call: ServiceCall) -> None:
    for coord in _resolve_coordinators(hass, call):
        await coord.async_clear_snooze()


async def async_register_services(hass: HomeAssistant) -> None:
    domain_data = hass.data.setdefault(DOMAIN, {})
    if domain_data.get(_SERVICES_REGISTERED_FLAG):
        return

    async def mark_washed(call: ServiceCall) -> None:
        await _handle_mark_washed(hass, call)

    async def snooze(call: ServiceCall) -> None:
        await _handle_snooze(hass, call)

    async def clear_snooze(call: ServiceCall) -> None:
        await _handle_clear_snooze(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_MARK_WASHED, mark_washed, schema=MARK_WASHED_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SNOOZE, snooze, schema=SNOOZE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_SNOOZE, clear_snooze, schema=CLEAR_SNOOZE_SCHEMA
    )

    domain_data[_SERVICES_REGISTERED_FLAG] = True


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data.get(_SERVICES_REGISTERED_FLAG):
        return
    for name in (SERVICE_MARK_WASHED, SERVICE_SNOOZE, SERVICE_CLEAR_SNOOZE):
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)
    domain_data.pop(_SERVICES_REGISTERED_FLAG, None)


__all__ = [
    "SERVICE_CLEAR_SNOOZE",
    "SERVICE_MARK_WASHED",
    "SERVICE_SNOOZE",
    "async_register_services",
    "async_unregister_services",
]
