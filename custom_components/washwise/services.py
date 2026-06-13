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
SERVICE_SET_IRRIGATION_SWITCH = "set_irrigation_switch"

ATTR_ENTRY_ID = "entry_id"
ATTR_TIMESTAMP = "timestamp"
ATTR_HOURS = "hours"
ATTR_STATE = "state"

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
        vol.Optional(ATTR_HOURS): vol.All(vol.Coerce(int), vol.Range(min=1)),
    }
)

CLEAR_SNOOZE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
    }
)

SET_IRRIGATION_SWITCH_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTRY_ID): cv.string,
        vol.Required(ATTR_STATE): vol.In(["on", "off"]),
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
    coord = _resolve_coordinator(hass, call)
    hours: int | None = call.data.get(ATTR_HOURS)
    if hours is None:
        hours = int(
            (coord.entry.options or {}).get("snooze_default_hours")
            or coord.entry.data.get("snooze_default_hours")
            or 24
        )
    await coord.async_snooze(timedelta(hours=hours))


async def _handle_clear_snooze(hass: HomeAssistant, call: ServiceCall) -> None:
    await _resolve_coordinator(hass, call).async_clear_snooze()


async def _handle_set_irrigation_switch(hass: HomeAssistant, call: ServiceCall) -> None:
    from .const import CONF_IRRIGATION_SWITCH_ENTITY

    coord = _resolve_coordinator(hass, call)
    state: str = call.data[ATTR_STATE]
    switch_entity: str | None = (coord.entry.options or {}).get(
        CONF_IRRIGATION_SWITCH_ENTITY
    ) or coord.entry.data.get(CONF_IRRIGATION_SWITCH_ENTITY)
    if not switch_entity:
        raise HomeAssistantError(
            "No irrigation switch entity configured for this WashWise instance."
        )
    domain, _ = switch_entity.split(".", 1)
    service = "turn_on" if state == "on" else "turn_off"
    await hass.services.async_call(
        domain, service, {"entity_id": switch_entity}, blocking=True
    )


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

    async def set_irrigation_switch(call: ServiceCall) -> None:
        await _handle_set_irrigation_switch(hass, call)

    hass.services.async_register(
        DOMAIN, SERVICE_MARK_WASHED, mark_washed, schema=MARK_WASHED_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SNOOZE, snooze, schema=SNOOZE_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_CLEAR_SNOOZE, clear_snooze, schema=CLEAR_SNOOZE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_IRRIGATION_SWITCH,
        set_irrigation_switch,
        schema=SET_IRRIGATION_SWITCH_SCHEMA,
    )

    domain_data[_SERVICES_REGISTERED_FLAG] = True


@callback
def async_unregister_services(hass: HomeAssistant) -> None:
    domain_data = hass.data.get(DOMAIN, {})
    if not domain_data.get(_SERVICES_REGISTERED_FLAG):
        return
    for name in (SERVICE_MARK_WASHED, SERVICE_SNOOZE, SERVICE_CLEAR_SNOOZE, SERVICE_SET_IRRIGATION_SWITCH):
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)
    domain_data.pop(_SERVICES_REGISTERED_FLAG, None)


__all__ = [
    "SERVICE_CLEAR_SNOOZE",
    "SERVICE_MARK_WASHED",
    "SERVICE_SET_IRRIGATION_SWITCH",
    "SERVICE_SNOOZE",
    "async_register_services",
    "async_unregister_services",
]
