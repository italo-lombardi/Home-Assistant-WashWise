"""Tests for ``custom_components.washwise.weather_source``.

The weather adapter is a pure-Python module that reads any HA weather entity
and normalizes its current state plus forecast payload into typed models.
These tests exercise:

- parametrized round-trip over every JSON fixture in ``tests/fixtures/weather/``
  (each fixture must produce a list of ``ForecastDay`` with no ``None`` gaps);
- ``is_available`` state-string handling (unavailable / None / valid);
- ``get_current`` unit conversion (°C, °F) and missing-temperature path;
- ``_to_celsius`` numeric conversion across °C / °F / K and ``None``;
- ``_resolve_key`` first-non-null fallback semantics;
- ``_parse_time`` accepts ISO strings, epoch-ms ints and ``datetime`` objects,
  rejects malformed strings;
- malformed forecast entries (missing time field) are filtered out by
  ``_normalize`` / ``get_forecast``.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse

from custom_components.washwise import weather_source
from custom_components.washwise.models import CurrentWeather, ForecastDay

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "weather"

# Resolved at import time so pytest can parametrize before the test loop runs.
WEATHER_FIXTURE_FILES = sorted(name for name in os.listdir(FIXTURES_DIR) if name.endswith(".json"))


def _load_fixture(filename: str) -> dict[str, Any]:
    """Load and return a JSON fixture by filename."""
    with (FIXTURES_DIR / filename).open("r", encoding="utf-8") as handle:
        return json.loads(handle.read())


def _register_forecast_service(hass: HomeAssistant, response: dict[str, Any]) -> None:
    """Register (or replace) a fake ``weather.get_forecasts`` service.

    ``hass.services.async_call`` is read-only on the slotted ``ServiceRegistry``,
    so the cleanest mock is to install a real service handler that returns the
    desired payload. ``async_register`` overwrites any existing entry under the
    same domain/service pair, which lets tests run in any order.
    """

    async def _handler(call: ServiceCall) -> dict[str, Any]:
        return response

    hass.services.async_register(
        "weather",
        "get_forecasts",
        _handler,
        supports_response=SupportsResponse.ONLY,
    )


# ---------------------------------------------------------------------------
# Parametrized fixture sweep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture_filename", WEATHER_FIXTURE_FILES)
async def test_get_forecast_parses_fixture(
    hass: HomeAssistant,
    fixture_filename: str,
) -> None:
    """Every shipped fixture must round-trip through ``get_forecast`` cleanly."""
    fixture = _load_fixture(fixture_filename)
    forecast_entries = fixture["forecast"]
    entity_id = f"weather.{Path(fixture_filename).stem}"

    response = {entity_id: {"forecast": forecast_entries}}
    _register_forecast_service(hass, response)

    result = await weather_source.get_forecast(
        hass, entity_id, mode="daily", days=len(forecast_entries)
    )

    assert isinstance(result, list)
    assert len(result) == len(forecast_entries)
    assert all(isinstance(day, ForecastDay) for day in result)
    assert all(day is not None for day in result)
    assert all(isinstance(day.date, date) for day in result)


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


async def test_is_available_unavailable_state(hass: HomeAssistant) -> None:
    """``state == "unavailable"`` -> ``False``."""
    hass.states.async_set("weather.home", STATE_UNAVAILABLE, {})
    assert await weather_source.is_available(hass, "weather.home") is False


async def test_is_available_missing_state(hass: HomeAssistant) -> None:
    """No state registered (returns ``None``) -> ``False``."""
    assert await weather_source.is_available(hass, "weather.does_not_exist") is False


async def test_is_available_valid_state(hass: HomeAssistant) -> None:
    """A normal weather state -> ``True``."""
    hass.states.async_set("weather.home", "sunny", {"temperature": 20})
    assert await weather_source.is_available(hass, "weather.home") is True


# ---------------------------------------------------------------------------
# get_current
# ---------------------------------------------------------------------------


async def test_get_current_returns_celsius(hass: HomeAssistant) -> None:
    """Plain °C input is preserved unchanged on ``CurrentWeather``."""
    hass.states.async_set(
        "weather.home",
        "sunny",
        {"temperature": 21.5, "temperature_unit": "°C"},
    )
    current = await weather_source.get_current(hass, "weather.home")
    assert isinstance(current, CurrentWeather)
    assert current.condition == "sunny"
    assert current.temperature_c == pytest.approx(21.5)


async def test_get_current_converts_fahrenheit(hass: HomeAssistant) -> None:
    """°F input is converted to °C."""
    hass.states.async_set(
        "weather.home",
        "sunny",
        {"temperature": 32.0, "temperature_unit": "°F"},
    )
    current = await weather_source.get_current(hass, "weather.home")
    assert current is not None
    assert current.temperature_c == pytest.approx(0.0, abs=1e-6)


async def test_get_current_missing_temperature(hass: HomeAssistant) -> None:
    """Missing ``temperature`` attribute -> ``temperature_c`` is ``None``."""
    hass.states.async_set("weather.home", "sunny", {})
    current = await weather_source.get_current(hass, "weather.home")
    assert current is not None
    assert current.temperature_c is None
    assert current.condition == "sunny"


async def test_get_current_unavailable_returns_none(hass: HomeAssistant) -> None:
    """Unavailable entity -> ``None`` (not an empty CurrentWeather)."""
    hass.states.async_set("weather.home", STATE_UNAVAILABLE, {})
    assert await weather_source.get_current(hass, "weather.home") is None


# ---------------------------------------------------------------------------
# _to_celsius
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (32.0, "°F", 0.0),
        (273.15, "K", 0.0),
        (25.0, "°C", 25.0),
        (None, "°C", None),
        (None, None, None),
    ],
)
def test_to_celsius_conversions(value: Any, unit: Any, expected: float | None) -> None:
    """Verify supported unit conversions plus the ``None`` short-circuit."""
    result = weather_source._to_celsius(value, unit)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected, abs=1e-6)


# ---------------------------------------------------------------------------
# _resolve_key
# ---------------------------------------------------------------------------


def test_resolve_key_returns_first_non_null() -> None:
    """``_resolve_key`` walks keys in order, picking the first non-``None``."""
    payload = {"a": None, "b": 5, "c": 7}
    assert weather_source._resolve_key(payload, ("a", "b", "c")) == 5


def test_resolve_key_skips_string_null() -> None:
    """Literal ``"null"`` strings are treated as missing."""
    payload = {"a": "null", "b": "value"}
    assert weather_source._resolve_key(payload, ("a", "b")) == "value"


def test_resolve_key_all_none_returns_none() -> None:
    """Every candidate ``None`` -> ``None``."""
    payload = {"a": None, "b": None}
    assert weather_source._resolve_key(payload, ("a", "b", "c")) is None


def test_resolve_key_missing_keys_returns_none() -> None:
    """No key present in the dict -> ``None``."""
    assert weather_source._resolve_key({}, ("a", "b")) is None


# ---------------------------------------------------------------------------
# _parse_time
# ---------------------------------------------------------------------------


def test_parse_time_iso_string() -> None:
    """ISO-8601 strings parse to a ``date``."""
    assert weather_source._parse_time("2026-06-11T12:00:00+00:00") == date(2026, 6, 11)


def test_parse_time_iso_date_only() -> None:
    """Pure ISO date strings also parse."""
    assert weather_source._parse_time("2026-06-11") == date(2026, 6, 11)


def test_parse_time_epoch_milliseconds() -> None:
    """Integer epoch ms (e.g. ``1700000000000``) -> a real ``date``."""
    parsed = weather_source._parse_time(1700000000000)
    assert isinstance(parsed, date)
    # 1700000000000 ms = 2023-11-14 22:13:20 UTC -> local date varies by tz,
    # but the conversion itself must succeed and round-trip via fromtimestamp.
    expected = datetime.fromtimestamp(1700000000000 / 1000).date()
    assert parsed == expected


def test_parse_time_datetime_object() -> None:
    """A ``datetime`` instance returns its ``date()`` part."""
    dt = datetime(2026, 6, 11, 9, 30)
    assert weather_source._parse_time(dt) == date(2026, 6, 11)


def test_parse_time_malformed_string_returns_none() -> None:
    """Unparseable strings -> ``None`` (not an exception)."""
    assert weather_source._parse_time("not-a-date") is None


def test_parse_time_none_returns_none() -> None:
    """``None`` -> ``None``."""
    assert weather_source._parse_time(None) is None


# ---------------------------------------------------------------------------
# Malformed forecast entry filtering
# ---------------------------------------------------------------------------


def test_normalize_missing_datetime_returns_none() -> None:
    """An entry with no recognised time key cannot be normalized."""
    raw = {"condition": "sunny", "temperature": 22.0, "precipitation": 0.0}
    assert weather_source._normalize(raw) is None


async def test_get_forecast_filters_malformed_entries(
    hass: HomeAssistant,
) -> None:
    """``get_forecast`` drops entries that ``_normalize`` rejects."""
    entity_id = "weather.home"
    forecast = [
        {
            "datetime": "2026-06-11T00:00:00+00:00",
            "condition": "sunny",
            "precipitation": 0.0,
            "templow": 12.0,
            "temperature": 22.0,
        },
        # Missing every TIME_KEYS variant -> normalizer must reject it.
        {
            "condition": "rainy",
            "precipitation": 5.0,
            "temperature": 18.0,
        },
        {
            "datetime": "2026-06-13T00:00:00+00:00",
            "condition": "cloudy",
            "precipitation": 0.1,
            "templow": 11.0,
            "temperature": 19.0,
        },
    ]
    response = {entity_id: {"forecast": forecast}}
    _register_forecast_service(hass, response)

    result = await weather_source.get_forecast(hass, entity_id, mode="daily", days=len(forecast))

    # Two valid entries returned, malformed one dropped.
    assert len(result) == 2
    assert all(isinstance(day, ForecastDay) for day in result)
    assert all(day is not None for day in result)
    assert result[0].date == date(2026, 6, 11)
    assert result[1].date == date(2026, 6, 13)


# ---------------------------------------------------------------------------
# get_current — exception path
# ---------------------------------------------------------------------------


async def test_get_current_swallows_exception(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If anything throws inside ``get_current`` it returns ``None``."""
    hass.states.async_set("weather.home", "sunny", {"temperature": 20})

    def _boom(value: Any, unit: Any) -> float | None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr(weather_source, "_to_celsius", _boom)
    assert await weather_source.get_current(hass, "weather.home") is None


# ---------------------------------------------------------------------------
# get_forecast — short-circuit + error paths
# ---------------------------------------------------------------------------


async def test_get_forecast_zero_days_returns_empty(hass: HomeAssistant) -> None:
    """``days <= 0`` short-circuits without invoking the service."""
    result = await weather_source.get_forecast(hass, "weather.home", mode="daily", days=0)
    assert result == []


async def test_get_forecast_negative_days_returns_empty(hass: HomeAssistant) -> None:
    """Negative ``days`` is also a no-op."""
    result = await weather_source.get_forecast(hass, "weather.home", mode="daily", days=-1)
    assert result == []


async def test_get_forecast_service_raises_returns_empty(
    hass: HomeAssistant,
) -> None:
    """A raising ``weather.get_forecasts`` service yields ``[]``."""
    entity_id = "weather.home"

    async def _handler(call: ServiceCall) -> dict[str, Any]:
        raise RuntimeError("service failed")

    hass.services.async_register(
        "weather",
        "get_forecasts",
        _handler,
        supports_response=SupportsResponse.ONLY,
    )
    result = await weather_source.get_forecast(hass, entity_id, mode="daily", days=3)
    assert result == []


async def test_get_forecast_empty_response_returns_empty(
    hass: HomeAssistant,
) -> None:
    """Service returning an empty (falsy) dict yields ``[]``."""
    entity_id = "weather.home"
    _register_forecast_service(hass, {})
    result = await weather_source.get_forecast(hass, entity_id, mode="daily", days=3)
    assert result == []


async def test_get_forecast_payload_not_dict_returns_empty(
    hass: HomeAssistant,
) -> None:
    """Response keyed under entity_id but value not a dict -> ``[]``."""
    entity_id = "weather.home"
    _register_forecast_service(hass, {entity_id: "not-a-dict"})
    result = await weather_source.get_forecast(hass, entity_id, mode="daily", days=3)
    assert result == []


async def test_get_forecast_missing_payload_returns_empty(
    hass: HomeAssistant,
) -> None:
    """Response dict missing the entity_id key -> ``[]``."""
    entity_id = "weather.home"
    _register_forecast_service(hass, {"weather.other": {"forecast": []}})
    result = await weather_source.get_forecast(hass, entity_id, mode="daily", days=3)
    assert result == []


async def test_get_forecast_forecast_not_list_returns_empty(
    hass: HomeAssistant,
) -> None:
    """``payload['forecast']`` not being a list -> ``[]``."""
    entity_id = "weather.home"
    _register_forecast_service(hass, {entity_id: {"forecast": "oops"}})
    result = await weather_source.get_forecast(hass, entity_id, mode="daily", days=3)
    assert result == []


async def test_get_forecast_skips_non_dict_entries(
    hass: HomeAssistant,
) -> None:
    """Non-dict entries inside the forecast list are silently skipped."""
    entity_id = "weather.home"
    forecast = [
        "not-a-dict",
        {
            "datetime": "2026-06-11T00:00:00+00:00",
            "condition": "sunny",
            "precipitation": 0.0,
            "templow": 12.0,
            "temperature": 22.0,
        },
    ]
    _register_forecast_service(hass, {entity_id: {"forecast": forecast}})
    result = await weather_source.get_forecast(hass, entity_id, mode="daily", days=len(forecast))
    assert len(result) == 1
    assert result[0].date == date(2026, 6, 11)


# ---------------------------------------------------------------------------
# _normalize — coercion + exception paths
# ---------------------------------------------------------------------------


def test_normalize_non_string_condition_is_coerced() -> None:
    """A non-string ``condition`` is converted via ``str()``."""
    raw = {
        "datetime": "2026-06-11",
        "condition": 42,
        "precipitation": 0.0,
        "templow": 10.0,
        "temperature": 20.0,
    }
    result = weather_source._normalize(raw)
    assert result is not None
    assert result.condition == "42"


def test_normalize_unparseable_precip_returns_none_field() -> None:
    """Non-numeric precipitation becomes ``None`` (entry still returned)."""
    raw = {
        "datetime": "2026-06-11",
        "condition": "sunny",
        "precipitation": "not-a-number",
        "templow": 10.0,
        "temperature": 20.0,
    }
    result = weather_source._normalize(raw)
    assert result is not None
    assert result.precipitation_mm is None


def test_normalize_swallows_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected error inside ``_normalize`` returns ``None``."""

    def _boom(value: Any) -> Any:
        raise RuntimeError("boom")

    monkeypatch.setattr(weather_source, "_parse_time", _boom)
    raw = {"datetime": "2026-06-11", "condition": "sunny"}
    assert weather_source._normalize(raw) is None


# ---------------------------------------------------------------------------
# _to_celsius — additional branches
# ---------------------------------------------------------------------------


def test_to_celsius_unparseable_value_returns_none() -> None:
    """Non-numeric strings short-circuit to ``None``."""
    assert weather_source._to_celsius("not-a-number", "°C") is None


def test_to_celsius_lowercase_unit_alias() -> None:
    """Mixed-case unit strings normalise via ``.lower()``."""
    # "FAHRENHEIT" isn't in the literal map, but lower() == "fahrenheit" is.
    result = weather_source._to_celsius(32.0, "FAHRENHEIT")
    assert result == pytest.approx(0.0, abs=1e-6)


def test_to_celsius_unknown_unit_falls_back_to_numeric() -> None:
    """An unknown unit string returns the raw numeric value."""
    assert weather_source._to_celsius(15.0, "rankine") == pytest.approx(15.0)


def test_to_celsius_non_string_unit_falls_back_to_numeric() -> None:
    """A non-string ``unit`` (e.g. an int) returns the raw numeric value."""
    assert weather_source._to_celsius(15.0, 42) == pytest.approx(15.0)


def test_to_celsius_handles_converter_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If ``TemperatureConverter`` raises, ``_to_celsius`` returns ``None``."""

    def _boom(value: float, source: str, target: str) -> float:
        raise RuntimeError("converter failed")

    monkeypatch.setattr(weather_source.TemperatureConverter, "convert", staticmethod(_boom))
    # °F is non-target, so the converter is invoked.
    assert weather_source._to_celsius(32.0, "°F") is None


# ---------------------------------------------------------------------------
# _resolve_key — non-dict input
# ---------------------------------------------------------------------------


def test_resolve_key_non_dict_returns_none() -> None:
    """Passing a non-dict to ``_resolve_key`` short-circuits to ``None``."""
    assert weather_source._resolve_key("not-a-dict", ("a",)) is None  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# _parse_time — additional branches
# ---------------------------------------------------------------------------


def test_parse_time_date_object_returns_self() -> None:
    """A bare ``date`` instance is returned unchanged."""
    d = date(2026, 6, 11)
    assert weather_source._parse_time(d) == d


def test_parse_time_bool_rejected() -> None:
    """``bool`` is an ``int`` subclass but must not be parsed as epoch ms."""
    assert weather_source._parse_time(True) is None
    assert weather_source._parse_time(False) is None


def test_parse_time_epoch_overflow_returns_none() -> None:
    """Wildly out-of-range epoch values return ``None`` instead of raising."""
    assert weather_source._parse_time(10**18) is None


def test_parse_time_empty_string_returns_none() -> None:
    """Empty / whitespace-only strings -> ``None``."""
    assert weather_source._parse_time("   ") is None


def test_parse_time_trailing_z_iso_string() -> None:
    """An ISO string ending in ``Z`` is normalised to ``+00:00`` and parses."""
    assert weather_source._parse_time("2026-06-11T12:00:00Z") == date(2026, 6, 11)


def test_parse_time_date_only_via_fallback() -> None:
    """Date-only string falls through ``datetime.fromisoformat`` to ``date.fromisoformat``."""
    # On Python < 3.11 ``datetime.fromisoformat`` rejects pure date strings,
    # exercising the inner ``date.fromisoformat`` fallback. On 3.11+ this is
    # still safe — ``datetime.fromisoformat`` succeeds and the same date is
    # returned. Either way the assertion holds.
    assert weather_source._parse_time("2026-06-11") == date(2026, 6, 11)


def test_parse_time_unsupported_type_returns_none() -> None:
    """Types that hit no branch (e.g. a list) return ``None``."""
    assert weather_source._parse_time([2026, 6, 11]) is None
