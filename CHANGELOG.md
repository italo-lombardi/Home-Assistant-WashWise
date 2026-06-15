# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0b3] - 2026-06-15

### Fixed
- **Invert-mode bad-current-condition returned wrong verdict** — for `solar_panels`
  and `garden_irrigation` (both `invert=True`), a bad current weather condition (e.g.
  `exceptional`) was silently ignored and the algorithm fell through to the no-rain
  forecast path, returning `can_wash=False, reason=clear`. The fix short-circuits
  immediately with `can_wash=True, reason=dirty_now` — the surface is dirty or the
  ground is dry, act now. (`decision.py`)
- **Score sensor rendered as float** — `ScoreSensor` and `DayScoreSensor` had
  `SensorStateClass.MEASUREMENT`, causing HA to store the 0–100 integer in long-term
  statistics as a float and render it as `100.000…%` in card types that pull from
  stats. Scores are point-in-time verdicts, not time-series measurements; the state
  class is removed. (`sensor.py`)

### Added
- `REASON_DIRTY_NOW = "dirty_now"` reason key for the inverted short-circuit path,
  with translation "Surface dirty — wash now". Distinct from `bad_current_condition`
  (which blocks washing in non-inverted mode) so automations can branch on reason
  without checking `can_wash`. (`decision.py`, `sensor.py`, `strings.json`,
  `translations/en.json`)
- `docs/TESTING_DOCKER.md` — step-by-step guide for live integration testing against
  a local HA Docker dev container, covering all 6 test scenarios.

## [0.2.0b2] - 2026-06-14

### Fixed
- **Coordinator crash on naive snooze timestamp** — `fromisoformat()` could return a
  naive `datetime` from legacy stored data; subsequent comparison with the tz-aware
  `utcnow()` raised an unhandled `TypeError`. Now always promotes to UTC before
  comparing. (`coordinator.py`)
- **Spurious double-freeze blocker** — the carry-forward temperature used `tmin` even
  when the freeze was triggered by `tmax`, causing the *next* forecast day to
  incorrectly fire a second freeze blocker. Carry-forward now prefers `tmax`.
  (`decision.py`)
- **Empty forecast silently granted wash permission** — when no forecast data was
  available (`days_analyzed == 0`) the algorithm returned `can_wash=True`. It now
  returns `can_wash=False`, preventing unintended irrigation on weather-source outages.
  (`decision.py`)
- **`datetime.date` objects in binary-sensor attributes caused recorder crash** —
  `forecast_summary` dicts contained raw `datetime.date` objects which are not
  JSON-serialisable; HA raised a `TypeError` when writing to the recorder or the
  frontend WebSocket. Date values are now serialised to ISO strings at the source.
  (`decision.py`, `binary_sensor.py`)
- **`gc_stale_health` crash on mixed-timezone data** — `_parse_ts` returned a naive
  `datetime` for timestamps written without a UTC offset, causing `TypeError` on the
  `>=` comparison. `_parse_ts` now always returns a tz-aware value. (`storage.py`)
- **`gc_stale_health` kept records with unparseable `last_seen_ts` indefinitely** —
  records whose timestamp could not be parsed were never expired. They are now treated
  as stale and removed. (`storage.py`)
- **`last_error` not cleared on provider recovery** — after a weather provider
  recovered from a failure, the stale error message persisted in `provider_health`.
  It is now cleared on the first successful update. (`storage.py`)
- **`gc_stale_health` was never called** — the GC function existed but was never
  wired into the coordinator update pipeline. It now runs every 50 updates, preventing
  the `provider_health` dict from growing indefinitely. (`coordinator.py`)
- **Reconfigure flow skipped `async_step_thresholds`** — entries with
  `customize_thresholds=True` were saved immediately on reconfigure without showing
  the thresholds form. The flow now mirrors the initial user step (thresholds → then
  irrigation or save). (`config_flow.py`)
- **`async_step_thresholds` created a duplicate entry on reconfigure** — the step
  always called `async_create_entry` regardless of flow source. It now calls
  `async_update_reload_and_abort` on the reconfigure path. (`config_flow.py`)
- **Services unregistered even when platform unload failed** — `async_unregister_services`
  was called outside the `if unload_ok:` guard, removing services while a live
  coordinator remained in `hass.data`. (`__init__.py`)
- **Deprecated `OptionsFlow.__init__` pattern** — `WashWiseOptionsFlow` stored
  `config_entry` as `self._config_entry`, triggering a deprecation warning since
  HA 2025.12. The constructor is removed; `self.config_entry` is used directly.
  (`config_flow.py`)
- **Options flow irrigation entity fields appeared blank on edit** — `CONF_RAIN_GAUGE_ENTITY`
  and `CONF_IRRIGATION_SWITCH_ENTITY` selectors had no `default=` argument, so
  previously saved values were not pre-populated. (`config_flow.py`)
- **Freeze-blocked days not counted in invert mode** — sub-zero conditions did not
  suppress irrigation/solar-clean signals in the inverted-logic path; only rain and
  bad-condition days were tracked. Freeze-blocked days are now included. (`decision.py`)
- **Score inconsistency in invert mode no-rain branch** — when no rain was expected,
  the score was returned without inversion, producing `score=100` alongside
  `can_wash=False`. Score is now consistently inverted in both invert-mode branches.
  (`decision.py`)
- **Card JS version mismatch** — deployed `washwise-card.js` was v0.1.0 while source
  was v0.2.0. Redeployed as v0.2.0; Lovelace resource URL updated to bust browser
  cache.

## [0.2.0b1] - 2026-06-13

### Added
- **Garden irrigation** category preset (`garden_irrigation`) — inverted logic where `can_wash=True` means rain is forecast, so irrigation should be skipped.
- Rain gauge / pluviometer support: configure a `sensor` or `input_number` entity; measured rain suppresses irrigation automatically when it meets or exceeds a configurable mm threshold (default 5 mm).
- Irrigation switch control: configure an `input_boolean` or `switch` entity; the coordinator automatically turns it off when irrigation is suppressed (rain gauge threshold met or rain forecast) and on when conditions are dry.
- Event-driven updates for `garden_irrigation`: coordinator uses `update_interval=None` and subscribes to state-change events on the rain gauge entity, delivering instant updates with no polling.
- New binary sensors (garden irrigation only): `irrigation_suppressed` (primary — ON when irrigation should be skipped), `forecast_blocks_irrigation` (diagnostic), `irrigation_switch_state` (diagnostic mirror of the controlled switch).
- New sensors (garden irrigation only): `measured_rain_mm` (live reading from rain gauge; attribute: `threshold_mm`), `rain_gauge_threshold_mm` (diagnostic).
- `set_irrigation_switch` service: manually override the irrigation switch state via `entry_id` + `state` (on/off).
- Irrigation config step in config flow and options flow: `rain_gauge_entity`, `rain_gauge_threshold_mm`, `irrigation_switch_entity`.
- `mark_irrigated` button translation key for garden irrigation instances (replaces "Mark washed" label).
- Translations for all 11 languages updated: new irrigation step, irrigation entity names, `garden_irrigation` category label, `set_irrigation_switch` service descriptor.

### Changed
- **Advanced options step**: removed `scan_interval_minutes` — no longer exposed to users; garden irrigation is fully event-driven and other categories use a fixed sensible default.
- **Thresholds options step**: removed redundant `customize_thresholds` toggle.
- Category selector order: `garden_irrigation` inserted before `custom` (logical grouping; `custom` always last).
- Rain gauge entity selector accepts `sensor` and `input_number` domains (previously sensor-only).

## [0.1.0] - 2026-06-12

### Added
- Initial release of WashWise — Home Assistant integration that decides whether you can wash a surface (car, motorcycle, bicycle, boat, RV, windows, solar panels, patio/deck, or custom) based on weather forecast data.
- Rule-based decision engine: walks the configured forecast horizon, blocks on bad conditions / precipitation above threshold / freeze crossings, and emits a 0–100 score, blocking reason, and per-day breakdown.
- 9 category presets (`car`, `motorcycle`, `bicycle`, `boat`, `rv_camper`, `windows_house`, `solar_panels`, `patio_deck`, `custom`) with sensible default thresholds and optional inverted logic for `solar_panels` (rain helps clean panels).
- Multi-provider weather source: any HA `weather` entity works without code changes; configure an ordered fallback list and the coordinator walks the chain, using the first available provider.
- Generic forecast adapter (`weather_source.py`) handling unit conversions (°C / °F / K), multiple key aliases, and malformed entries.
- Daily and hourly forecast modes, user-selectable per instance (default daily).
- Failover tracking: coordinator records when it falls over from one provider to the next; provider-health stats (success / failure counts, last error, last seen) feed a `primary_provider_uptime` diagnostic sensor.
- Persistence layer (`storage.py`) backed by HA's `Store` helper: wash log, snooze-until, last failover details, and per-provider health survive restarts; corrupt payloads recover to empty state; stale provider records GC after 30 days.
- Config flow: `weather_entities` (multi, ordered, ≥1, required), optional `name`, optional `category` (default `car`), and a `customize_thresholds` toggle.
- Reconfigure flow + full options flow (providers / thresholds / scoring / conditions / advanced steps).
- Binary sensors: `can_wash` (primary verdict with full forecast / decision attributes) plus `freeze_risk` (diagnostic) and per-day `day_N_ok` sensors aligned with the configured horizon.
- Sensors: `score`, `reason`, `days_until_wash`, `days_since_wash`, `last_washed`, `wash_count_30d`, `active_provider`, `last_update`, plus diagnostics (`category`, `days_analyzed`, `precip_total_mm`, `worst_condition`, `min_temp`, `max_temp`, `primary_provider_uptime`), `snooze_remaining`, and per-day score sensors.
- Services: `mark_washed`, `snooze` (accepts `hours` integer), `clear_snooze`.
- Button: `mark_washed` — appends a manual entry to the wash log.
- Custom Lovelace card with configurable theme (auto / light / dark), accent + bad colors, score gauge bar, compact mode, and per-section toggles (gauge, reason, forecast strip, diagnostics). Visual editor with inline help under every field. Diagnostics section collapsible with animated chevron.
- Frontend resource auto-registration via Lovelace resource collection so the card persists across restarts.
- Multi-instance support: install N times for N vehicles / surfaces; storage and entity IDs are isolated per config entry.
- Backend translations seeded for 11 languages: `da`, `de`, `en`, `es`, `fr`, `it`, `nb`, `nl`, `pl`, `pt`, `sv`. Card stays English-only.
- HACS support via `hacs.json`.
- 100% test coverage gate (`pytest --cov-fail-under=100`).
- CI workflows: ruff + pytest + hassfest + HACS validate, plus tag-driven release workflow.
