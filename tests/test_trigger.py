"""Smoke test for the WashWise automation-trigger relay.

Requires the HA Trigger platform (HA Core >= 2025.11), which the integration's
hacs.json floor now mandates, so these run unconditionally in CI.
"""

from __future__ import annotations

import pytest
import voluptuous as vol
from homeassistant.const import CONF_OPTIONS
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers.trigger import TriggerConfig
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise import trigger as ww_trigger
from custom_components.washwise.const import (
    CONF_CONFIG_ENTRY_ID,
    DOMAIN,
    EVENT_SHOULD_WASH_ON,
)

_SLUGS = {
    "should_wash_on",
    "should_wash_off",
    "can_wash_on",
    "can_wash_off",
    "verdict_changed",
}


async def test_async_get_triggers_exposes_all_events(hass: HomeAssistant) -> None:
    """All five bus events are surfaced as named triggers."""
    triggers = await ww_trigger.async_get_triggers(hass)
    assert set(triggers) == _SLUGS


async def test_async_validate_config_drops_empty_options(hass: HomeAssistant) -> None:
    """Unset scoping leaves no options block, keeping the stored YAML clean."""
    validated = await ww_trigger._ShouldWashOnTrigger.async_validate_config(hass, {})
    assert validated == {}


def _ww_entry(hass: HomeAssistant, entry_id: str = "ww-1") -> MockConfigEntry:
    """Register a WashWise config entry so validation finds it."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, title="Wash", entry_id=entry_id)
    entry.add_to_hass(hass)
    return entry


async def test_async_validate_config_round_trips_scope(hass: HomeAssistant) -> None:
    """A configured, existing config_entry_id survives validation inside options."""
    entry = _ww_entry(hass)
    validated = await ww_trigger._ShouldWashOnTrigger.async_validate_config(
        hass, {CONF_OPTIONS: {CONF_CONFIG_ENTRY_ID: entry.entry_id}}
    )
    assert validated == {CONF_OPTIONS: {CONF_CONFIG_ENTRY_ID: entry.entry_id}}


async def test_async_validate_config_rejects_unknown_entry(hass: HomeAssistant) -> None:
    """A typo'd/removed config_entry_id is rejected loudly, not silently dead."""
    with pytest.raises(vol.Invalid):
        await ww_trigger._ShouldWashOnTrigger.async_validate_config(
            hass, {CONF_OPTIONS: {CONF_CONFIG_ENTRY_ID: "does-not-exist"}}
        )


async def test_async_validate_config_rejects_foreign_entry(hass: HomeAssistant) -> None:
    """An entry from another integration is rejected — its events never match."""
    foreign = MockConfigEntry(domain="other", data={}, entry_id="foreign-1")
    foreign.add_to_hass(hass)
    with pytest.raises(vol.Invalid):
        await ww_trigger._ShouldWashOnTrigger.async_validate_config(
            hass, {CONF_OPTIONS: {CONF_CONFIG_ENTRY_ID: foreign.entry_id}}
        )


async def test_complete_config_migrates_top_level_scope(hass: HomeAssistant) -> None:
    """A top-level config_entry_id (how the UI/YAML write it) lands in options."""
    entry = _ww_entry(hass)
    validated = await ww_trigger._ShouldWashOnTrigger.async_validate_complete_config(
        hass,
        {
            "platform": "washwise.should_wash_on",
            CONF_CONFIG_ENTRY_ID: entry.entry_id,
        },
    )
    assert validated[CONF_OPTIONS] == {CONF_CONFIG_ENTRY_ID: entry.entry_id}
    assert CONF_CONFIG_ENTRY_ID not in validated  # moved, not duplicated


async def test_trigger_relays_event_data(hass: HomeAssistant) -> None:
    """Attaching the unscoped should_wash_on trigger forwards raw event.data."""
    trig = ww_trigger._TRIGGERS["should_wash_on"](hass, TriggerConfig(key="should_wash_on"))
    captured: list = []

    def run_action(run_vars, description, context):
        captured.append((run_vars, description, context))

    unsub = await trig.async_attach_runner(run_action)
    try:
        ctx = Context()
        payload = {"entity_id": "binary_sensor.x", "entry_id": "E", "should_wash": True}
        hass.bus.async_fire(EVENT_SHOULD_WASH_ON, payload, context=ctx)
        await hass.async_block_till_done()
    finally:
        unsub()

    assert len(captured) == 1
    run_vars, description, context = captured[0]
    assert run_vars == payload
    assert description == "should wash on"
    assert context is ctx


async def test_trigger_scopes_to_config_entry(hass: HomeAssistant) -> None:
    """A scoped trigger fires only for its own config entry, skips others."""
    trig = ww_trigger._TRIGGERS["should_wash_on"](
        hass, TriggerConfig(key="should_wash_on", options={CONF_CONFIG_ENTRY_ID: "abc"})
    )
    captured: list = []

    unsub = await trig.async_attach_runner(lambda rv, d, c: captured.append(rv))
    try:
        hass.bus.async_fire(EVENT_SHOULD_WASH_ON, {"entry_id": "other"})
        hass.bus.async_fire(EVENT_SHOULD_WASH_ON, {"entry_id": "abc"})
        await hass.async_block_till_done()
    finally:
        unsub()

    assert len(captured) == 1
    assert captured[0]["entry_id"] == "abc"
