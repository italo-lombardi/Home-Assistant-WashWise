"""Services for WashWise."""

from __future__ import annotations

from datetime import datetime, timedelta

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import WashWiseCoordinator

SERVICE_MARK_WASHED = "mark_washed"
SERVICE_SNOOZE = "snooze"
SERVICE_CLEAR_SNOOZE = "clear_snooze"

ATTR_ENTRY_ID = "entry_id"
ATTR_TIMESTAMP = "timestamp"
ATTR_HOURS = "hours"

_SERVICES_REGISTERED_FLAG = "_services_registered"

MARK_WASHED_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_TIMESTAMP): cv.datetime,
    }
)

SNOOZE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_HOURS): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)

CLEAR_SNOOZE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
    }
)


def _resolve_coordinator(hass: HomeAssistant, call: ServiceCall) -> WashWiseCoordinator:
    entry_id: str = call.data[ATTR_ENTRY_ID]
    domain_data = hass.data.get(DOMAIN, {})
    coord = domain_data.get(entry_id)
    if not isinstance(coord, WashWiseCoordinator):
        raise HomeAssistantError(f"No WashWise instance found for entry_id '{entry_id}'.")
    return coord


async def _handle_mark_washed(hass: HomeAssistant, call: ServiceCall) -> None:
    timestamp: datetime | None = call.data.get(ATTR_TIMESTAMP)
    await _resolve_coordinator(hass, call).async_mark_washed(timestamp)


async def _handle_snooze(hass: HomeAssistant, call: ServiceCall) -> None:
    hours: int = call.data[ATTR_HOURS]
    await _resolve_coordinator(hass, call).async_snooze(timedelta(hours=hours))


async def _handle_clear_snooze(hass: HomeAssistant, call: ServiceCall) -> None:
    await _resolve_coordinator(hass, call).async_clear_snooze()


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
