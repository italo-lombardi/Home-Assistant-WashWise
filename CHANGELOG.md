# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-06-15

### Added
- **Garden irrigation** category (`garden_irrigation`) — inverted logic where `can_wash=True` means rain is forecast, so irrigation should be skipped.
- Rain gauge support: configure a `sensor` or `input_number` entity; measured rain suppresses irrigation when it meets or exceeds a configurable mm threshold (default 5 mm).
- Irrigation switch control: configure an `input_boolean` or `switch` entity; coordinator turns it off when irrigation is suppressed and on when conditions are dry.
- Event-driven updates for `garden_irrigation`: `update_interval=None`, state-change events on rain gauge entity for instant updates.
- New binary sensors (garden irrigation): `irrigation_suppressed`, `forecast_blocks_irrigation`, `irrigation_switch_state`.
- New sensors (garden irrigation): `measured_rain_mm`, `rain_gauge_threshold_mm`.
- `set_irrigation_switch` service: manually override the irrigation switch via `entry_id` + `state`.
- `mark_irrigated` button translation key for garden irrigation instances.
- `REASON_DIRTY_NOW = "dirty_now"` reason key for inverted short-circuit path ("Surface dirty — wash now").
- `docs/TESTING_DOCKER.md` — live integration testing guide for HA Docker dev container.
- Translations updated for all 11 languages: new irrigation step, entity names, `garden_irrigation` category label, `set_irrigation_switch` service.

### Fixed
- **Invert-mode bad-current-condition short-circuit** — `solar_panels` / `garden_irrigation` silently fell through on bad weather (e.g. `exceptional`); now returns `can_wash=True, reason=dirty_now` immediately.
- **Score sensor float rendering** — `ScoreSensor` / `DayScoreSensor` had `MEASUREMENT` state class, storing scores as float in long-term statistics. State class removed (point-in-time verdict, not time-series).
- **Coordinator crash on naive snooze timestamp** — `fromisoformat()` could return naive `datetime` from legacy data; now always promoted to UTC before comparison.
- **Spurious double-freeze blocker** — carry-forward temperature used `tmin` even when freeze triggered by `tmax`; now prefers `tmax`.
- **Empty forecast granted wash permission** — `days_analyzed == 0` returned `can_wash=True`; now returns `can_wash=False` to block on weather-source outages.
- **`datetime.date` in binary-sensor attributes caused recorder crash** — `forecast_summary` dicts contained raw `date` objects (not JSON-serialisable); now serialised to ISO strings.
- **`gc_stale_health` crash on mixed-timezone data** — `_parse_ts` returned naive `datetime` for timestamps without UTC offset; now always tz-aware.
- **`gc_stale_health` kept unparseable records indefinitely** — unparseable timestamps now treated as stale and removed.
- **`last_error` not cleared on provider recovery** — stale error message now cleared on first successful update.
- **`gc_stale_health` was never called** — wired into coordinator update pipeline; runs every 50 updates.
- **Reconfigure flow skipped `async_step_thresholds`** — entries with `customize_thresholds=True` now mirror the initial user step.
- **`async_step_thresholds` created duplicate entry on reconfigure** — now calls `async_update_reload_and_abort` on reconfigure path.
- **Services unregistered even when platform unload failed** — `async_unregister_services` now inside `if unload_ok:` guard.
- **Deprecated `OptionsFlow.__init__` pattern** — `WashWiseOptionsFlow` constructor removed; uses `self.config_entry` directly.
- **Options flow irrigation fields blank on edit** — `CONF_RAIN_GAUGE_ENTITY` and `CONF_IRRIGATION_SWITCH_ENTITY` selectors now include `default=` to pre-populate saved values.
- **Freeze-blocked days not counted in invert mode** — freeze now suppresses irrigation/solar signals in the inverted-logic path.
- **Score inconsistency in invert mode no-rain branch** — score now consistently inverted in both invert-mode branches.
- **Card JS version mismatch** — `washwise-card.js` redeployed as v0.2.0; Lovelace resource URL updated to bust browser cache.

### Changed
- **Advanced options step**: removed `scan_interval_minutes` — garden irrigation is event-driven; other categories use a fixed default.
- **Thresholds options step**: removed redundant `customize_thresholds` toggle.
- Category selector order: `garden_irrigation` inserted before `custom`.

## [0.1.0] - 2026-06-12

### Added
- Initial release — decides whether to wash a surface (car, motorcycle, bicycle, boat, RV, windows, solar panels, patio/deck, or custom) based on HA weather forecast data.
- Rule-based decision engine: walks forecast horizon, blocks on bad conditions / precipitation / freeze crossings, emits 0–100 score, blocking reason, and per-day breakdown.
- 9 category presets with sensible defaults; optional inverted logic for `solar_panels`.
- Multi-provider weather source: any HA `weather` entity; ordered fallback list; unit conversions; daily and hourly forecast modes.
- Failover tracking and `primary_provider_uptime` diagnostic sensor.
- Persistence layer (`storage.py`): wash log, snooze-until, provider health — survives restarts; stale records GC after 30 days.
- Config flow, reconfigure flow, full options flow.
- Binary sensors: `can_wash`, `freeze_risk`, per-day `day_N_ok`.
- Sensors: `score`, `reason`, `days_until_wash`, `days_since_wash`, `last_washed`, `wash_count_30d`, `active_provider`, `last_update`, diagnostics, `snooze_remaining`, per-day scores.
- Services: `mark_washed`, `snooze`, `clear_snooze`. Button: `mark_washed`.
- Custom Lovelace card: theme (auto/light/dark), score gauge, compact mode, section toggles, visual editor, collapsible diagnostics.
- Frontend resource auto-registration.
- Multi-instance support.
- Backend translations for 11 languages.
- HACS support via `hacs.json`.
- 100% test coverage gate; CI: ruff + pytest + hassfest + HACS validate + tag-driven release.
