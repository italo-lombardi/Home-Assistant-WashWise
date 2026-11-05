"""Automation triggers for WashWise.

Surfaces the integration's bus events as named, UI-discoverable triggers in the
automation editor's Triggers tab. The raw ``washwise_*`` events keep firing
regardless, so existing ``platform: event`` automations are unaffected.

Requires the HA Trigger platform (``async_attach_runner`` surface), which
landed in HA Core 2025.11 — matching the integration's min-HA floor in
``hacs.json``.
"""

from collections.abc import Callable
from typing import override

import voluptuous as vol
from homeassistant.const import CONF_OPTIONS
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.automation import move_top_level_schema_fields_to_options
from homeassistant.helpers.trigger import Trigger, TriggerActionRunner, TriggerConfig
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_CONFIG_ENTRY_ID,
    DOMAIN,
    EVENT_CAN_WASH_OFF,
    EVENT_CAN_WASH_ON,
    EVENT_SHOULD_WASH_OFF,
    EVENT_SHOULD_WASH_ON,
    EVENT_VERDICT_CHANGED,
)

# Plain {marker: type} dict — reused by move_top_level_schema_fields_to_options
# (needs the raw markers) and wrapped in a Schema for validation below.
_OPTIONS_SCHEMA_DICT = {vol.Optional(CONF_CONFIG_ENTRY_ID): str}
_OPTIONS_SCHEMA = vol.Schema(_OPTIONS_SCHEMA_DICT)


class _EventTrigger(Trigger):
    """Relay a single WashWise bus event to an automation."""

    _event_type: str
    _description: str

    @classmethod
    @override
    async def async_validate_complete_config(
        cls, hass: HomeAssistant, complete_config: ConfigType
    ) -> ConfigType:
        """Migrate a top-level ``config_entry_id`` into the ``options`` block.

        The automation editor (and hand-written YAML) put trigger fields at the
        top level; the new-style Trigger base expects them under ``options``.
        Mirror the zwave_js precedent: move them before the base validates.
        """
        complete_config = move_top_level_schema_fields_to_options(
            complete_config, _OPTIONS_SCHEMA_DICT
        )
        return await super().async_validate_complete_config(hass, complete_config)

    @classmethod
    @override
    async def async_validate_config(cls, hass: HomeAssistant, config: ConfigType) -> ConfigType:
        """Validate the trigger configuration.

        The only user-selectable option is ``config_entry_id`` (entry scoping).
        When it is unset we return an empty config so the ``options`` key stays
        out of the stored YAML (the base merges back only keys we return); when
        it is set we return the validated ``options`` block so it round-trips.

        A named entry is checked for existence (and that it belongs to this
        integration) here, at validation time: a typo'd, removed, or
        wrong-integration ``config_entry_id`` is loud (``vol.Invalid`` surfaces
        in the editor / on reload) instead of silently yielding a scoped trigger
        that matches no event and never fires.
        """
        options = _OPTIONS_SCHEMA(config.get(CONF_OPTIONS) or {})
        entry_id = options.get(CONF_CONFIG_ENTRY_ID)
        if entry_id is not None:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is None or entry.domain != DOMAIN:
                raise vol.Invalid(f"'{entry_id}' is not a WashWise entry")
        return {CONF_OPTIONS: options} if options else {}

    @override
    def __init__(self, hass: HomeAssistant, config: TriggerConfig) -> None:
        """Capture the optional entry-scoping config entry id."""
        super().__init__(hass, config)
        self._entry_id = (config.options or {}).get(CONF_CONFIG_ENTRY_ID)

    @override
    async def async_attach_runner(
        self,
        run_action: TriggerActionRunner,
        _did_not_trigger: Callable[..., None] | None = None,
    ) -> CALLBACK_TYPE:
        """Listen for the event and forward its data to the action.

        When scoped to a single config entry, events from other entries are
        skipped.
        """

        @callback
        def _handle(event: Event) -> None:
            # Every WashWise event payload carries entry_id (coordinator), so a
            # direct compare is enough — no unique_id resolution needed.
            if self._entry_id and event.data.get("entry_id") != self._entry_id:
                return
            run_action(dict(event.data), self._description, event.context)

        return self._hass.bus.async_listen(self._event_type, _handle)


class _ShouldWashOnTrigger(_EventTrigger):
    _event_type = EVENT_SHOULD_WASH_ON
    _description = "should wash on"


class _ShouldWashOffTrigger(_EventTrigger):
    _event_type = EVENT_SHOULD_WASH_OFF
    _description = "should wash off"


class _VerdictChangedTrigger(_EventTrigger):
    _event_type = EVENT_VERDICT_CHANGED
    _description = "verdict changed"


class _CanWashOnTrigger(_EventTrigger):
    _event_type = EVENT_CAN_WASH_ON
    _description = "can wash on"


class _CanWashOffTrigger(_EventTrigger):
    _event_type = EVENT_CAN_WASH_OFF
    _description = "can wash off"


_TRIGGERS: dict[str, type[Trigger]] = {
    "should_wash_on": _ShouldWashOnTrigger,
    "should_wash_off": _ShouldWashOffTrigger,
    "can_wash_on": _CanWashOnTrigger,
    "can_wash_off": _CanWashOffTrigger,
    "verdict_changed": _VerdictChangedTrigger,
}


async def async_get_triggers(hass: HomeAssistant) -> dict[str, type[Trigger]]:
    """Return the triggers provided by WashWise."""
    return dict(_TRIGGERS)
