# Live integration testing — HA Docker dev container

The unit test suite (`pytest`) covers 100% of the integration logic. This guide
describes how to run **live scenario tests** against a real Home Assistant
instance running in a Docker dev container.

## Prerequisites

- Docker running with an HA dev container (e.g. `gifted_gates`) on port 8123
- Config path inside container: `/workspaces/home-assistant-core/config/`

## Step 1 — Deploy the integration

```bash
docker cp custom_components/washwise gifted_gates:/workspaces/home-assistant-core/config/custom_components/washwise
```

## Step 2 — Create the mock weather platform

Create `tests/ha_config/` locally (gitignored — not committed):

### `tests/ha_config/custom_components/mock_weather/__init__.py`

```python
"""Mock weather custom component."""
```

### `tests/ha_config/custom_components/mock_weather/manifest.json`

```json
{
  "domain": "mock_weather",
  "name": "Mock Weather (WashWise testing)",
  "codeowners": [],
  "config_flow": false,
  "dependencies": [],
  "documentation": "https://github.com/italo-lombardi/Home-Assistant-WashWise",
  "iot_class": "local_push",
  "requirements": [],
  "version": "1.0.0"
}
```

### `tests/ha_config/custom_components/mock_weather/weather.py`

See the full source file in the git history of branch `fix/integration-test-bugs`
(commit `aca8294`). It is a `WeatherEntity` driven by `input_select` /
`input_number` helpers so forecast condition, temperature and precipitation
can be changed from the UI without a restart.

## Step 3 — Deploy the mock platform and package

```bash
docker cp tests/ha_config/custom_components/mock_weather \
    gifted_gates:/workspaces/home-assistant-core/config/custom_components/mock_weather

docker cp tests/ha_config/packages/washwise_test.yaml \
    gifted_gates:/workspaces/home-assistant-core/config/packages/washwise_test.yaml

docker cp tests/ha_config/washwise_test_dashboard.yaml \
    gifted_gates:/workspaces/home-assistant-core/config/washwise_test_dashboard.yaml
```

### `tests/ha_config/packages/washwise_test.yaml`

Defines `input_number`, `input_select`, `input_boolean` helpers and five
`weather:` platform entries using `mock_weather`:

| Entity | Purpose |
|--------|---------|
| `weather.washwise_clear_sky` | Sunny, 0 mm precip, 15 °C |
| `weather.washwise_rainy` | Rainy, 5 mm precip, 12 °C |
| `weather.washwise_freezing` | Clear, −3 °C (tmax crosses 0 to trigger freeze check) |
| `weather.washwise_exceptional` | Exceptional condition (solar panel invert) |
| `weather.washwise_provider_b` | Partly cloudy, 0.1 mm (multi-provider fallback) |

## Step 4 — Register the dashboard

Add to `configuration.yaml` under `lovelace.dashboards`:

```yaml
washwise-test:
  mode: yaml
  filename: washwise_test_dashboard.yaml
  title: WashWise Test
  icon: mdi:water
  show_in_sidebar: true
  require_admin: false
```

## Step 5 — Create WashWise config entries via the UI

Go to **Settings → Devices & Services → Add Integration → WashWise**.
Create exactly these entries (names must match for dashboard entity IDs):

| Entry name | Weather entity | Category | Notes |
|------------|---------------|----------|-------|
| `Test Car Clear` | `weather.washwise_clear_sky` | Car | defaults |
| `Test Car Rainy` | `weather.washwise_rainy` | Car | defaults |
| `Test Car Freezing` | `weather.washwise_freezing` | Car | enable freeze check in options |
| `Test Solar Panels` | `weather.washwise_exceptional` | Solar Panels | defaults |
| `Test Irrigation` | `weather.washwise_clear_sky` | Garden Irrigation | rain gauge = `input_number.washwise_rain_gauge_mm`, switch = `input_boolean.washwise_irrigation_switch` |
| `Test Multi Provider` | `weather.washwise_clear_sky` + `weather.washwise_provider_b` | Car | two sources |

## Step 6 — Restart and verify

```bash
docker restart gifted_gates
```

Expected results after restart:

| Scenario | Key entity | Expected state | Reason |
|----------|-----------|---------------|--------|
| S1 Car clear | `binary_sensor.washwise_test_car_clear_can_wash` | `on` | `clear` |
| S2 Car rainy | `binary_sensor.washwise_test_car_rainy_can_wash` | `off` | `bad_current_condition` |
| S3 Car freezing | `binary_sensor.washwise_test_car_freezing_can_wash` | `off` | `freeze` |
| S4 Solar panels | `binary_sensor.washwise_test_solar_panels_can_wash` | `on` | `dirty_now` |
| S5 Irrigation | `binary_sensor.washwise_test_irrigation_irrigation_suppressed` | `off` | `clear` |
| S6 Multi-provider | `sensor.washwise_test_multi_provider_active_provider` | `WashWise Clear Sky` | — |

## Controlling scenarios at runtime

Use the **WashWise Test** dashboard (sidebar) to change conditions without
restarting:

- `input_select.washwise_rainy_condition` → switch between `rainy`, `pouring`, `hail`, etc.
- `input_select.washwise_solar_condition` → switch between `exceptional`, `sunny`, etc.
- `input_number.washwise_temperature_freezing` → dial the freeze temperature
- `input_number.washwise_rain_gauge_mm` → set gauge above 5 mm to trigger irrigation suppression
