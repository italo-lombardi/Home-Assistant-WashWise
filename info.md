# WashWise for Home Assistant

Decide whether to wash your car (or motorcycle, boat, solar panels, patio…) based on the weather forecast. WashWise reads any Home Assistant `weather` entity and produces a verdict, a 0–100 score, a blocking reason, and per-day breakdown — all wrapped in a custom Lovelace card.

## Features

- **Generic weather model** — any HA `weather` entity works, no per-provider code
- **Ordered fallback** — list multiple sources; first available wins, failovers persisted
- **0–100 score** — weighted sum of precipitation, freeze, and bad-condition penalties
- **Nine categories** — Car, Motorcycle, Bicycle, Boat, RV, Windows, Solar Panels, Patio, Custom
- **Solar panel inversion** — rain helps clean panels; verdict flips automatically
- **Smart auto-recalc** — recomputes the moment the active weather entity changes state
- **Snooze** — pause the verdict for N hours via service call; countdown sensor included
- **Wash log** — mark washes manually; days-since and 30-day count tracked in persistent storage
- **Custom Lovelace card** — verdict, score, forecast strip, diagnostics panel, visual editor
- **11 backend languages**, 100% test coverage gate in CI

## Card

Add to any dashboard:

```yaml
type: custom:washwise-card
entity: binary_sensor.washwise_daily_driver_can_wash
```

The card auto-registers when the integration loads. No manual resource configuration needed.

## Setup

1. Install via HACS (custom repository: `italo-lombardi/Home-Assistant-WashWise`)
2. **Settings → Devices & Services → Add Integration → WashWise**
3. Pick one or more weather entities, choose a category, optionally customize thresholds
