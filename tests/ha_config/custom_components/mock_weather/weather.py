"""Mock weather platform for WashWise integration testing.

Loaded via the platform: key in configuration.yaml.  Each entity is driven by
input_* helpers so forecast conditions can be changed without a restart.

Add to configuration.yaml:

  weather:
    - platform: mock_weather
      name: WashWise Clear Sky
      unique_id: washwise_mock_clear
      condition_entity: input_select.washwise_clear_condition
      temperature_entity: input_number.washwise_temperature_clear
      precipitation_entity: input_number.washwise_precip_clear
      precipitation_prob: 0

    - platform: mock_weather
      name: WashWise Rainy
      unique_id: washwise_mock_rainy
      condition_entity: input_select.washwise_rainy_condition
      temperature_entity: input_number.washwise_temperature_clear
      precipitation_entity: input_number.washwise_precip_rainy
      precipitation_prob: 95

    - platform: mock_weather
      name: WashWise Freezing
      unique_id: washwise_mock_freezing
      condition_entity: input_select.washwise_clear_condition
      temperature_entity: input_number.washwise_temperature_freezing
      precipitation_entity: input_number.washwise_precip_clear
      precipitation_prob: 5

    - platform: mock_weather
      name: WashWise Exceptional
      unique_id: washwise_mock_exceptional
      condition_entity: input_select.washwise_solar_condition
      temperature_entity: input_number.washwise_temperature_clear
      precipitation_entity: input_number.washwise_precip_clear
      precipitation_prob: 0

    - platform: mock_weather
      name: WashWise Provider B
      unique_id: washwise_mock_provider_b
      condition_entity: input_select.washwise_clear_condition
      temperature_entity: input_number.washwise_temperature_clear
      precipitation_entity: input_number.washwise_precip_clear
      precipitation_prob: 15
"""

from __future__ import annotations

import contextlib
from datetime import timedelta
from typing import Any

import voluptuous as vol
from homeassistant.components.weather import (
    PLATFORM_SCHEMA,
    Forecast,
    WeatherEntity,
    WeatherEntityFeature,
)
from homeassistant.const import (
    CONF_NAME,
    CONF_UNIQUE_ID,
    STATE_UNKNOWN,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType
from homeassistant.util import dt as dt_util

CONF_CONDITION_ENTITY = "condition_entity"
CONF_TEMPERATURE_ENTITY = "temperature_entity"
CONF_PRECIP_ENTITY = "precipitation_entity"
CONF_PRECIP_PROB = "precipitation_prob"
CONF_FORECAST_DAYS = "forecast_days"

DEFAULT_FORECAST_DAYS = 5
DEFAULT_NAME = "Mock Weather"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_CONDITION_ENTITY): cv.entity_id,
        vol.Required(CONF_TEMPERATURE_ENTITY): cv.entity_id,
        vol.Optional(CONF_PRECIP_ENTITY): cv.entity_id,
        vol.Optional(CONF_PRECIP_PROB, default=20): vol.Coerce(int),
        vol.Optional(CONF_FORECAST_DAYS, default=DEFAULT_FORECAST_DAYS): vol.Coerce(int),
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up mock weather entities."""
    async_add_entities(
        [
            MockWeatherEntity(
                name=config[CONF_NAME],
                unique_id=config.get(CONF_UNIQUE_ID),
                condition_entity=config[CONF_CONDITION_ENTITY],
                temperature_entity=config[CONF_TEMPERATURE_ENTITY],
                precip_entity=config.get(CONF_PRECIP_ENTITY),
                precip_prob=config[CONF_PRECIP_PROB],
                forecast_days=config[CONF_FORECAST_DAYS],
            )
        ],
        update_before_add=True,
    )


class MockWeatherEntity(WeatherEntity):
    """A weather entity driven by input_* helpers."""

    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_humidity = 65
    _attr_native_wind_speed = 10
    _attr_supported_features = (
        WeatherEntityFeature.FORECAST_DAILY | WeatherEntityFeature.FORECAST_HOURLY
    )

    def __init__(
        self,
        name: str,
        unique_id: str | None,
        condition_entity: str,
        temperature_entity: str,
        precip_entity: str | None,
        precip_prob: int,
        forecast_days: int,
    ) -> None:
        self._attr_name = name
        self._attr_unique_id = unique_id
        self._condition_entity = condition_entity
        self._temperature_entity = temperature_entity
        self._precip_entity = precip_entity
        self._precip_prob = precip_prob
        self._forecast_days = forecast_days
        self._attr_condition = "sunny"
        self._attr_native_temperature = 15.0
        self._precip_mm = 0.0

    async def async_added_to_hass(self) -> None:
        watch = [self._condition_entity, self._temperature_entity]
        if self._precip_entity:
            watch.append(self._precip_entity)

        @callback
        def _state_changed(_event: Any) -> None:
            self._refresh_from_helpers()
            self.async_write_ha_state()

        self.async_on_remove(async_track_state_change_event(self.hass, watch, _state_changed))
        self._refresh_from_helpers()
        self.async_write_ha_state()

    async def async_update(self) -> None:
        self._refresh_from_helpers()

    def _refresh_from_helpers(self) -> None:
        cond_state = self.hass.states.get(self._condition_entity)
        if cond_state and cond_state.state not in (STATE_UNKNOWN, "unavailable"):
            self._attr_condition = cond_state.state

        temp_state = self.hass.states.get(self._temperature_entity)
        if temp_state and temp_state.state not in (STATE_UNKNOWN, "unavailable"):
            with contextlib.suppress(ValueError):
                self._attr_native_temperature = float(temp_state.state)

        if self._precip_entity:
            precip_state = self.hass.states.get(self._precip_entity)
            if precip_state and precip_state.state not in (STATE_UNKNOWN, "unavailable"):
                with contextlib.suppress(ValueError):
                    self._precip_mm = float(precip_state.state)

    def _make_forecast(self) -> list[Forecast]:
        now = dt_util.utcnow()
        forecasts: list[Forecast] = []
        temp = self._attr_native_temperature or 15.0
        for i in range(self._forecast_days):
            dt = now + timedelta(days=i)
            # Ensure tmax crosses 0 when temp is sub-zero so the freeze-check
            # carry-forward logic (temp_check < 0 <= tmax) fires correctly.
            tmax = max(temp + 1, 1.0) if temp < 0 else temp + 1
            forecasts.append(
                Forecast(
                    datetime=dt.strftime("%Y-%m-%dT12:00:00+00:00"),
                    condition=self._attr_condition,
                    native_temperature=tmax,
                    native_templow=temp - 5,
                    native_precipitation=self._precip_mm,
                    precipitation_probability=self._precip_prob,
                )
            )
        return forecasts

    async def async_forecast_daily(self) -> list[Forecast]:
        return self._make_forecast()

    async def async_forecast_hourly(self) -> list[Forecast]:
        forecasts: list[Forecast] = []
        now = dt_util.utcnow()
        temp = self._attr_native_temperature or 15.0
        for i in range(self._forecast_days * 24):
            dt = now + timedelta(hours=i)
            tmax = max(temp + 1, 1.0) if temp < 0 else temp + 1
            forecasts.append(
                Forecast(
                    datetime=dt.strftime("%Y-%m-%dT%H:00:00+00:00"),
                    condition=self._attr_condition,
                    native_temperature=tmax,
                    native_templow=temp - 5,
                    native_precipitation=self._precip_mm / 24,
                    precipitation_probability=self._precip_prob,
                )
            )
        return forecasts
