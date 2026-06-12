# Changelog

All notable changes to this project will be documented in this file.

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
