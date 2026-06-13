"""Config flow for the WashWise integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    BAD_CONDITIONS,
    CATEGORY_PRESETS,
    CONF_BAD_CONDITIONS,
    CONF_CATEGORY,
    CONF_CONDITION_WEIGHT,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_DAYS,
    CONF_FORECAST_TYPE,
    CONF_FREEZE_CHECK,
    CONF_FREEZE_WEIGHT,
    CONF_IRRIGATION_SWITCH_ENTITY,
    CONF_NAME,
    CONF_PRECIP_THRESHOLD,
    CONF_PRECIP_WEIGHT,
    CONF_RAIN_GAUGE_ENTITY,
    CONF_RAIN_GAUGE_THRESHOLD_MM,
    CONF_TEMPERATURE_UNIT,
    CONF_WEATHER_ENTITIES,
    DEFAULT_CATEGORY,
    DEFAULT_CONDITION_WEIGHT,
    DEFAULT_FORECAST_TYPE,
    DEFAULT_FREEZE_WEIGHT,
    DEFAULT_PRECIP_WEIGHT,
    DEFAULT_RAIN_GAUGE_THRESHOLD_MM,
    DEFAULT_TEMPERATURE_UNIT,
    DOMAIN,
    TEMPERATURE_UNIT_OPTIONS,
)

_LOGGER = logging.getLogger(__name__)


def _category_label(category: str) -> str:
    """Return a friendly fallback label for a category."""
    return category.replace("_", " ").title()


def _weather_entity_selector() -> selector.EntitySelector:
    """Build the (multi, ordered) weather entity selector."""
    return selector.EntitySelector(selector.EntitySelectorConfig(multiple=True, domain="weather"))


def _category_selector() -> selector.SelectSelector:
    """Build the category dropdown selector."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(CATEGORY_PRESETS.keys()),
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key="category",
        )
    )


def _forecast_type_selector() -> selector.SelectSelector:
    """Build the forecast-type selector."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=["daily", "hourly"],
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key="forecast_type",
        )
    )


def _bad_conditions_selector() -> selector.SelectSelector:
    """Build the bad-conditions multi-select."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(BAD_CONDITIONS),
            multiple=True,
            translation_key="bad_condition",
        )
    )


def _temperature_unit_selector() -> selector.SelectSelector:
    """Build the temperature-unit override dropdown.

    Default ``auto`` reads the unit from the source weather entity (or falls
    back to HA's system unit). The explicit options force a specific unit
    when a provider's ``temperature_unit`` attribute is wrong or missing.
    """
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(TEMPERATURE_UNIT_OPTIONS),
            mode=selector.SelectSelectorMode.DROPDOWN,
            translation_key="temperature_unit",
        )
    )


def _existing_names(hass) -> list[str]:
    """Collect the case-folded names of all existing WashWise entries."""
    names: list[str] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        name = (entry.data.get(CONF_NAME) or "").strip().casefold()
        if name:
            names.append(name)
    return names


class WashWiseConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for WashWise."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the config flow."""
        self._data: dict[str, Any] = {}

    # ---------------------------------------------------------------- user

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Step 1: weather sources, name, category, customise toggle."""
        errors: dict[str, str] = {}

        if user_input is not None:
            weather_entities = user_input.get(CONF_WEATHER_ENTITIES) or []
            name = (user_input.get(CONF_NAME) or "").strip()
            category = user_input.get(CONF_CATEGORY, DEFAULT_CATEGORY)

            if not weather_entities:
                errors[CONF_WEATHER_ENTITIES] = "no_weather_entity"
            elif name and name.casefold() in _existing_names(self.hass):
                errors[CONF_NAME] = "duplicate_name"
            else:
                self._data = {
                    CONF_WEATHER_ENTITIES: weather_entities,
                    CONF_NAME: name,
                    CONF_CATEGORY: category,
                    CONF_CUSTOMIZE_THRESHOLDS: bool(
                        user_input.get(CONF_CUSTOMIZE_THRESHOLDS, False)
                    ),
                }
                if self._data[CONF_CUSTOMIZE_THRESHOLDS]:
                    return await self.async_step_thresholds()

                if category == "garden_irrigation":
                    return await self.async_step_irrigation()

                title = name or _category_label(category)
                return self.async_create_entry(title=title, data=self._data)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_WEATHER_ENTITIES): _weather_entity_selector(),
                vol.Optional(CONF_NAME, default=""): selector.TextSelector(
                    selector.TextSelectorConfig()
                ),
                vol.Optional(CONF_CATEGORY, default=DEFAULT_CATEGORY): _category_selector(),
                vol.Optional(CONF_CUSTOMIZE_THRESHOLDS, default=False): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
        )

    # --------------------------------------------------------- thresholds

    async def async_step_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2 (optional): customise thresholds + scoring."""
        category = self._data.get(CONF_CATEGORY, DEFAULT_CATEGORY)
        preset = CATEGORY_PRESETS.get(category, CATEGORY_PRESETS[DEFAULT_CATEGORY])

        if user_input is not None:
            self._data.update(user_input)
            if category == "garden_irrigation":
                return await self.async_step_irrigation()
            title = (self._data.get(CONF_NAME) or "").strip() or _category_label(category)
            return self.async_create_entry(title=title, data=self._data)

        data_schema = vol.Schema(
            {
                vol.Required(CONF_DAYS, default=preset["days"]): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=7,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="days",
                    )
                ),
                vol.Required(
                    CONF_PRECIP_THRESHOLD,
                    default=preset["precip_threshold_mm"],
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=50,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="mm",
                    )
                ),
                vol.Required(
                    CONF_FREEZE_CHECK, default=preset["freeze_check"]
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_FORECAST_TYPE, default=DEFAULT_FORECAST_TYPE
                ): _forecast_type_selector(),
                vol.Required(
                    CONF_BAD_CONDITIONS, default=list(BAD_CONDITIONS)
                ): _bad_conditions_selector(),
                vol.Required(
                    CONF_PRECIP_WEIGHT, default=DEFAULT_PRECIP_WEIGHT
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="%",
                    )
                ),
                vol.Required(
                    CONF_FREEZE_WEIGHT, default=DEFAULT_FREEZE_WEIGHT
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="%",
                    )
                ),
                vol.Required(
                    CONF_CONDITION_WEIGHT, default=DEFAULT_CONDITION_WEIGHT
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="%",
                    )
                ),
            }
        )

        return self.async_show_form(step_id="thresholds", data_schema=data_schema)

    # ------------------------------------------------------ irrigation setup

    async def async_step_irrigation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step for garden_irrigation: configure rain gauge + switch entities."""
        category = self._data.get(CONF_CATEGORY, DEFAULT_CATEGORY)

        if user_input is not None:
            self._data.update(user_input)
            title = (self._data.get(CONF_NAME) or "").strip() or _category_label(category)
            if self.source == SOURCE_RECONFIGURE:
                entry = self._get_reconfigure_entry()
                return self.async_update_reload_and_abort(
                    entry,
                    title=title,
                    data=self._data,
                    reason="reconfigure_successful",
                )
            return self.async_create_entry(title=title, data=self._data)

        data_schema = vol.Schema(
            {
                vol.Optional(CONF_RAIN_GAUGE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number"])
                ),
                vol.Optional(
                    CONF_RAIN_GAUGE_THRESHOLD_MM, default=DEFAULT_RAIN_GAUGE_THRESHOLD_MM
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=200,
                        step=0.5,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="mm",
                    )
                ),
                vol.Optional(CONF_IRRIGATION_SWITCH_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["switch", "input_boolean", "automation"])
                ),
            }
        )

        return self.async_show_form(step_id="irrigation", data_schema=data_schema)

    # -------------------------------------------------------- reconfigure

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Reconfigure an existing entry — same fields as the user step."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()
        current = {**entry.data, **entry.options}

        if user_input is not None:
            weather_entities = user_input.get(CONF_WEATHER_ENTITIES) or []
            name = (user_input.get(CONF_NAME) or "").strip()
            category = user_input.get(CONF_CATEGORY, DEFAULT_CATEGORY)

            if not weather_entities:
                errors[CONF_WEATHER_ENTITIES] = "no_weather_entity"
            else:
                # Duplicate-name check ignores the entry being reconfigured.
                duplicates = [
                    (e.data.get(CONF_NAME) or "").strip().casefold()
                    for e in self.hass.config_entries.async_entries(DOMAIN)
                    if e.entry_id != entry.entry_id
                ]
                if name and name.casefold() in duplicates:
                    errors[CONF_NAME] = "duplicate_name"
                else:
                    new_data = {
                        **entry.data,
                        CONF_WEATHER_ENTITIES: weather_entities,
                        CONF_NAME: name,
                        CONF_CATEGORY: category,
                        CONF_CUSTOMIZE_THRESHOLDS: bool(
                            user_input.get(CONF_CUSTOMIZE_THRESHOLDS, False)
                        ),
                    }
                    title = name or _category_label(category)
                    if category == "garden_irrigation":
                        self._data = new_data
                        return await self.async_step_irrigation()
                    return self.async_update_reload_and_abort(
                        entry,
                        title=title,
                        data=new_data,
                        reason="reconfigure_successful",
                    )

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_WEATHER_ENTITIES,
                    default=current.get(CONF_WEATHER_ENTITIES, []),
                ): _weather_entity_selector(),
                vol.Optional(CONF_NAME, default=current.get(CONF_NAME, "")): selector.TextSelector(
                    selector.TextSelectorConfig()
                ),
                vol.Optional(
                    CONF_CATEGORY,
                    default=current.get(CONF_CATEGORY, DEFAULT_CATEGORY),
                ): _category_selector(),
                vol.Optional(
                    CONF_CUSTOMIZE_THRESHOLDS,
                    default=current.get(CONF_CUSTOMIZE_THRESHOLDS, False),
                ): selector.BooleanSelector(),
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=data_schema,
            errors=errors,
        )

    # ---------------------------------------------------- options factory

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WashWiseOptionsFlow:
        """Return the options flow handler."""
        return WashWiseOptionsFlow(config_entry)


class WashWiseOptionsFlow(OptionsFlow):
    """Handle the options flow for a WashWise config entry."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialise the options flow."""
        self._config_entry = config_entry

    # ---------------------------------------------------------------- helpers

    def _current(self) -> dict[str, Any]:
        """Merged view of entry data + saved options."""
        return {**self.config_entry.data, **self.config_entry.options}

    def _save(self, updates: dict[str, Any]) -> ConfigFlowResult:
        """Persist a partial options update and finish this step."""
        merged = {**self.config_entry.options, **updates}
        return self.async_create_entry(title="", data=merged)

    # -------------------------------------------------------------------- init

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Top-level menu — pick which group of options to edit."""
        current = self._current()
        category = current.get(CONF_CATEGORY, DEFAULT_CATEGORY)
        menu_options = [
            "providers",
            "thresholds",
            "scoring",
            "conditions",
            "advanced",
        ]
        if category == "garden_irrigation":
            menu_options.append("irrigation")
        return self.async_show_menu(
            step_id="init",
            menu_options=menu_options,
        )

    # --------------------------------------------------------------- providers

    async def async_step_providers(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the ordered list of weather providers."""
        errors: dict[str, str] = {}
        current = self._current()

        if user_input is not None:
            weather_entities = user_input.get(CONF_WEATHER_ENTITIES) or []
            if not weather_entities:
                errors[CONF_WEATHER_ENTITIES] = "no_weather_entity"
            else:
                return self._save({CONF_WEATHER_ENTITIES: weather_entities})

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_WEATHER_ENTITIES,
                    default=current.get(CONF_WEATHER_ENTITIES, []),
                ): _weather_entity_selector(),
            }
        )

        return self.async_show_form(
            step_id="providers",
            data_schema=data_schema,
            errors=errors,
        )

    # -------------------------------------------------------------- thresholds

    async def async_step_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the thresholds (forecast horizon, precipitation, freeze)."""
        current = self._current()
        category = current.get(CONF_CATEGORY, DEFAULT_CATEGORY)
        preset = CATEGORY_PRESETS.get(category, CATEGORY_PRESETS[DEFAULT_CATEGORY])

        if user_input is not None:
            return self._save(user_input)

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_DAYS,
                    default=current.get(CONF_DAYS, preset["days"]),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=7,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="days",
                    )
                ),
                vol.Required(
                    CONF_PRECIP_THRESHOLD,
                    default=current.get(CONF_PRECIP_THRESHOLD, preset["precip_threshold_mm"]),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=50,
                        step=0.1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="mm",
                    )
                ),
                vol.Required(
                    CONF_FREEZE_CHECK,
                    default=current.get(CONF_FREEZE_CHECK, preset["freeze_check"]),
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_FORECAST_TYPE,
                    default=current.get(CONF_FORECAST_TYPE, DEFAULT_FORECAST_TYPE),
                ): _forecast_type_selector(),
            }
        )

        return self.async_show_form(step_id="thresholds", data_schema=data_schema)

    # ----------------------------------------------------------------- scoring

    async def async_step_scoring(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the score weights."""
        current = self._current()

        if user_input is not None:
            return self._save(user_input)

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_PRECIP_WEIGHT,
                    default=current.get(CONF_PRECIP_WEIGHT, DEFAULT_PRECIP_WEIGHT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="%",
                    )
                ),
                vol.Required(
                    CONF_FREEZE_WEIGHT,
                    default=current.get(CONF_FREEZE_WEIGHT, DEFAULT_FREEZE_WEIGHT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="%",
                    )
                ),
                vol.Required(
                    CONF_CONDITION_WEIGHT,
                    default=current.get(CONF_CONDITION_WEIGHT, DEFAULT_CONDITION_WEIGHT),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                        unit_of_measurement="%",
                    )
                ),
            }
        )

        return self.async_show_form(step_id="scoring", data_schema=data_schema)

    # -------------------------------------------------------------- conditions

    async def async_step_conditions(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit the set of weather condition codes that block washing."""
        current = self._current()

        if user_input is not None:
            return self._save(user_input)

        data_schema = vol.Schema(
            {
                vol.Required(
                    CONF_BAD_CONDITIONS,
                    default=current.get(CONF_BAD_CONDITIONS, list(BAD_CONDITIONS)),
                ): _bad_conditions_selector(),
            }
        )

        return self.async_show_form(step_id="conditions", data_schema=data_schema)

    # ---------------------------------------------------------------- advanced

    async def async_step_advanced(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit advanced runtime options (snooze defaults, temperature unit)."""
        current = self._current()

        if user_input is not None:
            return self._save(user_input)

        data_schema = vol.Schema(
            {
                vol.Required(
                    "snooze_default_hours",
                    default=current.get("snooze_default_hours", 24),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1,
                        max=720,
                        step=1,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="hours",
                    )
                ),
                vol.Required(
                    CONF_TEMPERATURE_UNIT,
                    default=current.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMPERATURE_UNIT),
                ): _temperature_unit_selector(),
            }
        )

        return self.async_show_form(step_id="advanced", data_schema=data_schema)

    # --------------------------------------------------------------- irrigation

    async def async_step_irrigation(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Edit irrigation-specific options (rain gauge entity, threshold, switch)."""
        current = self._current()

        if user_input is not None:
            return self._save(user_input)

        data_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_RAIN_GAUGE_ENTITY,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain=["sensor", "input_number"])
                ),
                vol.Optional(
                    CONF_RAIN_GAUGE_THRESHOLD_MM,
                    default=current.get(
                        CONF_RAIN_GAUGE_THRESHOLD_MM, DEFAULT_RAIN_GAUGE_THRESHOLD_MM
                    ),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=200,
                        step=0.5,
                        mode=selector.NumberSelectorMode.BOX,
                        unit_of_measurement="mm",
                    )
                ),
                vol.Optional(
                    CONF_IRRIGATION_SWITCH_ENTITY,
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["switch", "input_boolean", "automation"]
                    )
                ),
            }
        )

        return self.async_show_form(step_id="irrigation", data_schema=data_schema)
