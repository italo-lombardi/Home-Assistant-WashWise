"""Tests for the WashWise config flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise.const import (
    CATEGORY_PRESETS,
    CONF_CATEGORY,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_DAYS,
    CONF_FORECAST_TYPE,
    CONF_FREEZE_CHECK,
    CONF_NAME,
    CONF_PRECIP_THRESHOLD,
    CONF_WEATHER_ENTITIES,
    DEFAULT_CATEGORY,
    DEFAULT_FORECAST_TYPE,
    DOMAIN,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _init_flow(hass: HomeAssistant) -> dict:
    """Start a fresh user-flow and return the initial result."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


# ---------------------------------------------------------------------------
# user step
# ---------------------------------------------------------------------------


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    """The first step renders the user form."""
    result = await _init_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] in (None, {})


async def test_user_step_happy_path_creates_entry(hass: HomeAssistant) -> None:
    """Submitting valid data with customize off creates the entry immediately."""
    result = await _init_flow(hass)

    with patch(
        "custom_components.washwise.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_WEATHER_ENTITIES: ["weather.home"],
                CONF_NAME: "Audi",
                CONF_CATEGORY: "car",
                CONF_CUSTOMIZE_THRESHOLDS: False,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Audi"
    assert result["data"][CONF_WEATHER_ENTITIES] == ["weather.home"]
    assert result["data"][CONF_NAME] == "Audi"
    assert result["data"][CONF_CATEGORY] == "car"
    assert result["data"][CONF_CUSTOMIZE_THRESHOLDS] is False


async def test_user_step_customize_off_skips_thresholds_step(
    hass: HomeAssistant,
) -> None:
    """When customize is off, no thresholds step is shown — direct create."""
    result = await _init_flow(hass)

    with patch(
        "custom_components.washwise.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_WEATHER_ENTITIES: ["weather.home"],
                CONF_NAME: "NoCustom",
                CONF_CATEGORY: "car",
                CONF_CUSTOMIZE_THRESHOLDS: False,
            },
        )

    # Result is CREATE_ENTRY directly — never advanced to a thresholds form.
    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result.get("step_id") != "thresholds"
    # No threshold-only keys leak into the saved data.
    assert CONF_DAYS not in result["data"]
    assert CONF_PRECIP_THRESHOLD not in result["data"]


async def test_user_step_customize_on_shows_thresholds_with_category_defaults(
    hass: HomeAssistant,
) -> None:
    """Customize=True advances to thresholds step with category defaults."""
    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_WEATHER_ENTITIES: ["weather.home"],
            CONF_NAME: "Boat 1",
            CONF_CATEGORY: "boat",
            CONF_CUSTOMIZE_THRESHOLDS: True,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "thresholds"

    # Defaults shown in the thresholds schema must come from the category preset.
    boat_preset = CATEGORY_PRESETS["boat"]
    schema = result["data_schema"].schema
    defaults: dict[str, object] = {}
    for marker in schema:
        defaults[str(marker)] = marker.default() if callable(marker.default) else marker.default

    assert defaults[CONF_DAYS] == boat_preset["days"]
    assert defaults[CONF_PRECIP_THRESHOLD] == boat_preset["precip_threshold_mm"]
    assert defaults[CONF_FREEZE_CHECK] == boat_preset["freeze_check"]


async def test_user_step_customize_on_completes_via_thresholds(
    hass: HomeAssistant,
) -> None:
    """End-to-end: customize=True → thresholds step → create entry."""
    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_WEATHER_ENTITIES: ["weather.home"],
            CONF_NAME: "Audi",
            CONF_CATEGORY: "car",
            CONF_CUSTOMIZE_THRESHOLDS: True,
        },
    )
    assert result["step_id"] == "thresholds"

    with patch(
        "custom_components.washwise.async_setup_entry",
        return_value=True,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_DAYS: 4,
                CONF_PRECIP_THRESHOLD: 0.5,
                CONF_FREEZE_CHECK: True,
                CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
                "bad_conditions": ["rainy", "pouring"],
                "precip_weight": 50,
                "freeze_weight": 25,
                "condition_weight": 25,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DAYS] == 4
    assert result["data"][CONF_PRECIP_THRESHOLD] == 0.5


async def test_user_step_empty_weather_entities_error(hass: HomeAssistant) -> None:
    """Empty weather_entities list returns no_weather_entity error."""
    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_WEATHER_ENTITIES: [],
            CONF_NAME: "Empty",
            CONF_CATEGORY: DEFAULT_CATEGORY,
            CONF_CUSTOMIZE_THRESHOLDS: False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_WEATHER_ENTITIES: "no_weather_entity"}


async def test_user_step_duplicate_name_error(hass: HomeAssistant) -> None:
    """A second entry with the same case-folded name is rejected."""
    existing = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Audi",
        data={
            CONF_WEATHER_ENTITIES: ["weather.home"],
            CONF_NAME: "Audi",
            CONF_CATEGORY: "car",
            CONF_CUSTOMIZE_THRESHOLDS: False,
        },
        entry_id="existing_audi_entry",
        unique_id=f"{DOMAIN}_audi",
    )
    existing.add_to_hass(hass)

    result = await _init_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_WEATHER_ENTITIES: ["weather.home"],
            CONF_NAME: "audi",  # different case — should still match
            CONF_CATEGORY: "car",
            CONF_CUSTOMIZE_THRESHOLDS: False,
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {CONF_NAME: "duplicate_name"}


# ---------------------------------------------------------------------------
# reconfigure step
# ---------------------------------------------------------------------------


async def test_reconfigure_updates_entry_and_reloads(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reconfigure flow updates the entry and triggers a reload."""
    mock_config_entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.washwise.async_setup_entry",
            return_value=True,
        ),
        patch(
            "custom_components.washwise.async_unload_entry",
            return_value=True,
        ),
    ):
        # Start the reconfigure flow against the existing entry.
        result = await mock_config_entry.start_reconfigure_flow(hass)
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        # Submit updated data.
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_WEATHER_ENTITIES: [
                    "weather.home",
                    "weather.backup",
                ],
                CONF_NAME: "Audi Reborn",
                CONF_CATEGORY: "motorcycle",
                CONF_CUSTOMIZE_THRESHOLDS: False,
            },
        )
        await hass.async_block_till_done()

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.title == "Audi Reborn"
    assert mock_config_entry.data[CONF_WEATHER_ENTITIES] == [
        "weather.home",
        "weather.backup",
    ]
    assert mock_config_entry.data[CONF_CATEGORY] == "motorcycle"


async def test_reconfigure_rejects_empty_weather_entities(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reconfigure flow surfaces the same no_weather_entity validation."""
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.washwise.async_setup_entry",
        return_value=True,
    ):
        result = await mock_config_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_WEATHER_ENTITIES: [],
                CONF_NAME: "Whatever",
                CONF_CATEGORY: "car",
                CONF_CUSTOMIZE_THRESHOLDS: False,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {CONF_WEATHER_ENTITIES: "no_weather_entity"}


async def test_reconfigure_rejects_duplicate_name_against_other_entry(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Reconfigure rejects a name that collides with a different entry."""
    other = MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title="Sibling",
        data={
            CONF_WEATHER_ENTITIES: ["weather.home"],
            CONF_NAME: "Sibling",
            CONF_CATEGORY: "car",
            CONF_CUSTOMIZE_THRESHOLDS: False,
        },
        entry_id="sibling_entry",
        unique_id=f"{DOMAIN}_sibling",
    )
    other.add_to_hass(hass)
    mock_config_entry.add_to_hass(hass)

    with patch(
        "custom_components.washwise.async_setup_entry",
        return_value=True,
    ):
        result = await mock_config_entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_WEATHER_ENTITIES: ["weather.home"],
                CONF_NAME: "sibling",  # collides with `other`
                CONF_CATEGORY: "car",
                CONF_CUSTOMIZE_THRESHOLDS: False,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"] == {CONF_NAME: "duplicate_name"}
