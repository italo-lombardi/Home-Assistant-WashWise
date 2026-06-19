"""Tests for the WashWise options flow."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise.const import (
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
    CONF_NAME,
    CONF_PRECIP_THRESHOLD,
    CONF_PRECIP_WEIGHT,
    CONF_TEMPERATURE_UNIT,
    CONF_WEATHER_ENTITIES,
    DEFAULT_CATEGORY,
    DEFAULT_FORECAST_TYPE,
    DEFAULT_TEMPERATURE_UNIT,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_entry(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> MockConfigEntry:
    """Add the mock entry to HA — options flow needs the entry registered, not loaded."""
    mock_config_entry.add_to_hass(hass)
    return mock_config_entry


def _schema_defaults(schema) -> dict[str, object]:
    """Pull defaults out of a voluptuous schema for assertion convenience."""
    out: dict[str, object] = {}
    for marker in schema.schema:
        out[str(marker)] = marker.default() if callable(marker.default) else marker.default
    return out


# ---------------------------------------------------------------------------
# init / menu
# ---------------------------------------------------------------------------


async def test_options_init_shows_menu(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The init step shows a menu with the five expected options."""
    await _setup_entry(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    assert result["type"] == FlowResultType.MENU
    assert result["step_id"] == "init"
    assert sorted(result["menu_options"]) == sorted(
        [
            "providers",
            "thresholds",
            "scoring",
            "conditions",
            "advanced",
        ]
    )


# ---------------------------------------------------------------------------
# Each step is reachable from the menu
# ---------------------------------------------------------------------------


async def test_options_menu_navigates_to_each_step(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Each menu option is selectable and yields the matching form."""
    await _setup_entry(hass, mock_config_entry)

    for step in ("providers", "thresholds", "scoring", "conditions", "advanced"):
        result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": step}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == step


# ---------------------------------------------------------------------------
# providers step
# ---------------------------------------------------------------------------


async def test_options_providers_save_updates_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saving the providers step writes weather_entities to entry.options."""
    await _setup_entry(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "providers"}
    )

    new_list = ["weather.backup", "weather.home", "weather.tertiary"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_WEATHER_ENTITIES: new_list},
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    # Drag-reorder is exercised via the list-order sensitivity.
    assert mock_config_entry.options[CONF_WEATHER_ENTITIES] == new_list


async def test_options_providers_drag_reorder_preserves_order(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Submitting the same providers in a different order persists that order."""
    await _setup_entry(hass, mock_config_entry)

    # First save: A then B then C
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "providers"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_WEATHER_ENTITIES: ["weather.a", "weather.b", "weather.c"]},
    )
    assert mock_config_entry.options[CONF_WEATHER_ENTITIES] == [
        "weather.a",
        "weather.b",
        "weather.c",
    ]

    # Second save: reorder to C, A, B (drag-reorder)
    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "providers"}
    )
    await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_WEATHER_ENTITIES: ["weather.c", "weather.a", "weather.b"]},
    )
    assert mock_config_entry.options[CONF_WEATHER_ENTITIES] == [
        "weather.c",
        "weather.a",
        "weather.b",
    ]


async def test_options_providers_empty_returns_error(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Submitting an empty list raises no_weather_entity."""
    await _setup_entry(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "providers"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_WEATHER_ENTITIES: []}
    )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_WEATHER_ENTITIES: "no_weather_entity"}


# ---------------------------------------------------------------------------
# thresholds step
# ---------------------------------------------------------------------------


async def test_options_thresholds_defaults_match_category(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """When category=car, thresholds form defaults come from the car preset."""
    await _setup_entry(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "thresholds"}
    )
    assert result["step_id"] == "thresholds"

    defaults = _schema_defaults(result["data_schema"])
    car = CATEGORY_PRESETS["car"]
    assert defaults[CONF_DAYS] == car["days"]
    assert defaults[CONF_PRECIP_THRESHOLD] == car["precip_threshold_mm"]
    assert defaults[CONF_FREEZE_CHECK] == car["freeze_check"]
    assert defaults[CONF_FORECAST_TYPE] == DEFAULT_FORECAST_TYPE


async def test_options_thresholds_save_updates_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saving the thresholds step writes the values to entry.options."""
    await _setup_entry(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "thresholds"}
    )
    payload = {
        CONF_DAYS: 6,
        CONF_PRECIP_THRESHOLD: 1.25,
        CONF_FREEZE_CHECK: False,
        CONF_FORECAST_TYPE: "hourly",
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], payload)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    for key, value in payload.items():
        assert mock_config_entry.options[key] == value


async def test_options_thresholds_uses_new_category_after_change(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Changing category in the entry.data flows through to the next thresholds defaults.

    The providers step only edits weather_entities, but updating data with
    a new category and re-entering the thresholds step should use the new
    category preset for defaults.
    """
    await _setup_entry(hass, mock_config_entry)

    # Simulate a category change persisting to entry.data — the options flow
    # reads from `_current` which is data + options, so updating data is the
    # canonical path. Using async_update_entry mirrors what reconfigure does.
    hass.config_entries.async_update_entry(
        mock_config_entry,
        data={**mock_config_entry.data, CONF_CATEGORY: "boat"},
    )

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "thresholds"}
    )
    defaults = _schema_defaults(result["data_schema"])

    boat = CATEGORY_PRESETS["boat"]
    assert defaults[CONF_DAYS] == boat["days"]
    assert defaults[CONF_PRECIP_THRESHOLD] == boat["precip_threshold_mm"]
    assert defaults[CONF_FREEZE_CHECK] == boat["freeze_check"]


# ---------------------------------------------------------------------------
# scoring step
# ---------------------------------------------------------------------------


async def test_options_scoring_save_updates_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saving the scoring step writes the weights."""
    await _setup_entry(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "scoring"}
    )
    payload = {
        CONF_PRECIP_WEIGHT: 50,
        CONF_FREEZE_WEIGHT: 20,
        CONF_CONDITION_WEIGHT: 30,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], payload)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    for key, value in payload.items():
        assert mock_config_entry.options[key] == value


# ---------------------------------------------------------------------------
# conditions step
# ---------------------------------------------------------------------------


async def test_options_conditions_save_updates_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saving the conditions step writes a custom bad-conditions list."""
    await _setup_entry(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "conditions"}
    )
    new_conditions = ["rainy", "pouring", "hail"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_BAD_CONDITIONS: new_conditions}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_BAD_CONDITIONS] == new_conditions
    # Sanity: every saved condition must be a known code.
    assert set(new_conditions).issubset(set(BAD_CONDITIONS))


# ---------------------------------------------------------------------------
# advanced step
# ---------------------------------------------------------------------------


async def test_options_advanced_save_updates_options(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saving the advanced step writes snooze defaults and temperature unit."""
    await _setup_entry(hass, mock_config_entry)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "advanced"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "snooze_default_hours": 48,
            CONF_TEMPERATURE_UNIT: DEFAULT_TEMPERATURE_UNIT,
        },
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options["snooze_default_hours"] == 48


# ---------------------------------------------------------------------------
# cancel mid-step (no input)
# ---------------------------------------------------------------------------


async def test_options_cancel_midstep_makes_no_changes(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Opening a step without submitting leaves entry.options untouched."""
    await _setup_entry(hass, mock_config_entry)

    # Pre-condition: options is empty (per the conftest fixture).
    assert mock_config_entry.options == {}
    snapshot_data = dict(mock_config_entry.data)

    # Walk into each step but never submit.
    for step in ("providers", "thresholds", "scoring", "conditions", "advanced"):
        result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": step}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == step
        # Abandon the flow.
        hass.config_entries.options.async_abort(result["flow_id"])

    # Nothing changed.
    assert mock_config_entry.options == {}
    assert mock_config_entry.data == snapshot_data
    assert mock_config_entry.data[CONF_NAME] == snapshot_data[CONF_NAME]
    assert mock_config_entry.data[CONF_CATEGORY] == DEFAULT_CATEGORY


# ---------------------------------------------------------------------------
# Saving any customisation step auto-flips CONF_CUSTOMIZE_THRESHOLDS=True so
# the coordinator's customize gate picks the new override up without forcing
# the user to also reconfigure the entry.
# ---------------------------------------------------------------------------


async def test_options_thresholds_save_auto_flips_customize(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saving the thresholds step sets ``customize_thresholds=True``."""
    await _setup_entry(hass, mock_config_entry)
    # Pre-condition: customize is not set anywhere.
    assert not mock_config_entry.options.get(CONF_CUSTOMIZE_THRESHOLDS)
    assert not mock_config_entry.data.get(CONF_CUSTOMIZE_THRESHOLDS)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "thresholds"}
    )
    payload = {
        CONF_DAYS: 5,
        CONF_PRECIP_THRESHOLD: 0.75,
        CONF_FREEZE_CHECK: True,
        CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], payload)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_CUSTOMIZE_THRESHOLDS] is True
    # Submitted payload values still land in options too.
    for key, value in payload.items():
        assert mock_config_entry.options[key] == value


async def test_options_scoring_save_auto_flips_customize(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saving the scoring step sets ``customize_thresholds=True``."""
    await _setup_entry(hass, mock_config_entry)
    assert not mock_config_entry.options.get(CONF_CUSTOMIZE_THRESHOLDS)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "scoring"}
    )
    payload = {
        CONF_PRECIP_WEIGHT: 40,
        CONF_FREEZE_WEIGHT: 30,
        CONF_CONDITION_WEIGHT: 30,
    }
    result = await hass.config_entries.options.async_configure(result["flow_id"], payload)

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_CUSTOMIZE_THRESHOLDS] is True
    for key, value in payload.items():
        assert mock_config_entry.options[key] == value


async def test_options_conditions_save_auto_flips_customize(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Saving the conditions step sets ``customize_thresholds=True``."""
    await _setup_entry(hass, mock_config_entry)
    assert not mock_config_entry.options.get(CONF_CUSTOMIZE_THRESHOLDS)

    result = await hass.config_entries.options.async_init(mock_config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "conditions"}
    )
    new_conditions = ["rainy", "pouring"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_BAD_CONDITIONS: new_conditions}
    )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert mock_config_entry.options[CONF_CUSTOMIZE_THRESHOLDS] is True
    assert mock_config_entry.options[CONF_BAD_CONDITIONS] == new_conditions
