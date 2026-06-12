"""DataUpdateCoordinator for WashWise.

Drives the wash decision pipeline:

1. Walk the user's ordered ``weather_entities`` list.
2. Fetch current conditions + forecast from the active provider.
3. Honour persisted snooze: a live snooze short-circuits to ``can_wash=False``.
4. Otherwise call :func:`decision.compute` and return the resulting
   :class:`models.Decision`.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, Event, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_track_entity_registry_updated_event,
    async_track_state_change_event,
)
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import decision as decision_module
from . import weather_source
from .const import (
    CATEGORY_PRESETS,
    CONF_BAD_CONDITIONS,
    CONF_CATEGORY,
    CONF_CONDITION_WEIGHT,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_DAYS,
    CONF_FORECAST_TYPE,
    CONF_FREEZE_CHECK,
    CONF_FREEZE_WEIGHT,
    CONF_PRECIP_THRESHOLD,
    CONF_PRECIP_WEIGHT,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_TEMPERATURE_UNIT,
    CONF_WEATHER_ENTITIES,
    DEFAULT_CATEGORY,
    DEFAULT_CONDITION_WEIGHT,
    DEFAULT_FORECAST_TYPE,
    DEFAULT_FREEZE_WEIGHT,
    DEFAULT_PRECIP_WEIGHT,
    DEFAULT_TEMPERATURE_UNIT,
    DOMAIN,
    SCAN_INTERVAL,
    TEMPERATURE_UNIT_AUTO,
    TEMPERATURE_UNIT_CELSIUS,
    TEMPERATURE_UNIT_FAHRENHEIT,
    TEMPERATURE_UNIT_KELVIN,
)
from .models import Decision, WashEntry
from .storage import WashWiseStore

_LOGGER = logging.getLogger(__name__)


class WashWiseCoordinator(DataUpdateCoordinator[Decision]):
    """Coordinator that runs the WashWise decision pipeline."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator and persistent store."""
        interval = self._resolve_scan_interval(entry)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=interval,
        )
        self.entry = entry
        self._store = WashWiseStore(hass, entry.entry_id)
        self._enabled: bool = True
        self._active_weather_entity: str | None = None
        self._last_decision: Decision | None = None
        self._unsub_registry: CALLBACK_TYPE | None = None
        self._unsub_state: CALLBACK_TYPE | None = None
        # Seed ``last_update_success_time`` so the ``last_update`` sensor has
        # a real timestamp the moment the entry is created, rather than
        # ``Unknown`` until the first coordinator tick lands.
        seed_ts = getattr(entry, "created_at", None) or dt_util.utcnow()
        import contextlib

        with contextlib.suppress(Exception):  # pragma: no cover - HA versions without setter
            self.last_update_success_time = seed_ts  # type: ignore[attr-defined]

        # React to weather-entity renames so we keep tracking the same
        # underlying provider when the user changes its entity_id.
        weather_ids = list(entry.data.get(CONF_WEATHER_ENTITIES, []) or [])
        if weather_ids:
            self._unsub_registry = async_track_entity_registry_updated_event(
                hass,
                weather_ids,
                self._handle_registry_updated,
            )
            # Smart auto-recalc: react immediately when a relevant weather
            # entity's state changes. Cheaper and snappier than waiting for
            # the periodic SCAN_INTERVAL tick. Priority logic lives in
            # ``_handle_state_change``: only the active provider triggers an
            # unconditional refresh; later providers only matter when the
            # primary is currently unavailable.
            self._unsub_state = async_track_state_change_event(
                hass,
                weather_ids,
                self._handle_state_change,
            )

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Return whether the coordinator is currently running updates."""
        return self._enabled

    @property
    def active_weather_entity(self) -> str | None:
        """Return the entity_id of the weather provider used for the last update."""
        return self._active_weather_entity

    @property
    def active_provider_label(self) -> str | None:
        """Return a human-friendly label for the active provider.

        Falls back to the entity's friendly_name attribute, then the
        entity_id itself.
        """
        eid = self._active_weather_entity
        if eid is None:
            return None
        state = self.hass.states.get(eid)
        if state is not None:
            friendly = state.attributes.get("friendly_name")
            if isinstance(friendly, str) and friendly.strip():
                return friendly
        return eid

    # ------------------------------------------------------------------
    # Mutators called from buttons / services
    # ------------------------------------------------------------------

    async def async_set_enabled(self, value: bool) -> None:
        """Enable or disable the coordinator.

        While disabled the coordinator returns the last successful decision
        (frozen) so the UI can surface "paused" without the entities going
        ``unavailable``.
        """
        self._enabled = bool(value)
        await self.async_request_refresh()

    async def async_mark_washed(self, timestamp: datetime | None = None) -> None:
        """Append a wash entry to the persisted log and refresh.

        ``timestamp`` defaults to ``utcnow``. The ``source`` field is set
        to ``"manual"`` -- the algorithm never appends "auto" entries
        itself.
        """
        ts = timestamp if timestamp is not None else dt_util.utcnow()
        entry = WashEntry(timestamp=ts.isoformat(), source="manual")
        await self._store.append_wash(entry)
        await self.async_request_refresh()

    async def async_snooze(self, duration: timedelta) -> None:
        """Snooze the wash advisor for ``duration`` from now."""
        until = dt_util.utcnow() + duration
        await self._store.set_snooze(until)
        await self.async_request_refresh()

    async def async_clear_snooze(self) -> None:
        """Clear any active snooze."""
        await self._store.set_snooze(None)
        await self.async_request_refresh()

    async def async_force_refresh(self) -> None:
        """Public alias for ``async_request_refresh``."""
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Detach registry listener on unload."""
        if self._unsub_registry is not None:
            self._unsub_registry()
            self._unsub_registry = None
        if self._unsub_state is not None:
            self._unsub_state()
            self._unsub_state = None
        await super().async_shutdown()

    # ------------------------------------------------------------------
    # Update pipeline
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> Decision:
        """Run the decision pipeline once."""
        # Step 1: pause-mode check.
        if not self._enabled:
            if self._last_decision is not None:
                return self._last_decision
            raise UpdateFailed("disabled")

        # Step 2: load persisted state.
        data = await self._store.load()
        now = dt_util.utcnow()

        # Step 3: snooze short-circuit.
        if data.snooze_until:
            try:
                snooze_dt = datetime.fromisoformat(data.snooze_until)
            except ValueError:
                snooze_dt = None
            if snooze_dt is not None and snooze_dt > now:
                snooze_decision = Decision(
                    can_wash=False,
                    score=0,
                    reason="snoozed",
                    days_until_wash=None,
                    blocking_days=[],
                    forecast_summary=[],
                    days_analyzed=0,
                )
                self._last_decision = snooze_decision
                return snooze_decision

        # Step 4: walk the provider chain.
        weather_ids: list[str] = list(self.entry.data.get(CONF_WEATHER_ENTITIES, []) or [])
        forecast_type = self.entry.data.get(CONF_FORECAST_TYPE, DEFAULT_FORECAST_TYPE)
        thresholds, invert = self._resolve_thresholds()
        horizon = int(thresholds.get("days", 3))

        previous_active = self._active_weather_entity
        last_error: str | None = None

        for eid in weather_ids:
            if not await weather_source.is_available(self.hass, eid):
                await self._store.update_provider_health(eid, False, "unavailable")
                last_error = "unavailable"
                continue

            current = await weather_source.get_current(self.hass, eid)
            if current is None:
                await self._store.update_provider_health(eid, False, "no_current")
                last_error = "no_current"
                continue

            forecast = await weather_source.get_forecast(
                self.hass,
                eid,
                forecast_type,
                max(horizon, 1),
                fallback_unit=self._resolve_temperature_unit(),
            )
            if not forecast and horizon > 0:
                await self._store.update_provider_health(eid, False, "no_forecast")
                last_error = "no_forecast"
                continue

            await self._store.update_provider_health(eid, True, None)
            if previous_active is not None and previous_active != eid:
                await self._store.record_failover(previous_active, eid)

            decision_obj = decision_module.compute(
                current=decision_module.CurrentWeather(
                    condition=current.condition,
                    temperature_c=current.temperature_c,
                    raw=dict(current.raw or {}),
                ),
                forecast=[
                    decision_module.ForecastDay(
                        date=fd.date,
                        condition=fd.condition,
                        precipitation_mm=fd.precipitation_mm,
                        temp_min_c=fd.temp_min_c,
                        temp_max_c=fd.temp_max_c,
                        raw=dict(fd.raw or {}),
                    )
                    for fd in forecast
                ],
                thresholds=thresholds,
                invert=invert,
                now=now,
            )

            decision = Decision(
                can_wash=decision_obj.can_wash,
                score=decision_obj.score,
                reason=decision_obj.reason,
                days_until_wash=decision_obj.days_until_wash,
                blocking_days=list(decision_obj.blocking_days),
                forecast_summary=list(decision_obj.forecast_summary),
                days_analyzed=decision_obj.days_analyzed,
            )

            self._active_weather_entity = eid
            self._last_decision = decision
            return decision

        # Step 5: every provider failed.
        raise UpdateFailed(last_error or "no available weather provider")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _resolve_thresholds(self) -> tuple[dict[str, Any], bool]:
        """Return ``(thresholds, invert)`` based on entry data + options.

        When ``customize_thresholds`` is on, the options override the
        category preset; otherwise the preset wins. ``invert`` always comes
        from the category preset (solar panels flip).
        """
        category = self.entry.data.get(CONF_CATEGORY, DEFAULT_CATEGORY)
        preset = CATEGORY_PRESETS.get(category, CATEGORY_PRESETS[DEFAULT_CATEGORY])
        invert = bool(preset.get("invert", False))

        options = self.entry.options or {}
        customize = bool(
            options.get(CONF_CUSTOMIZE_THRESHOLDS, False)
            or self.entry.data.get(CONF_CUSTOMIZE_THRESHOLDS, False)
        )

        if customize:
            data = self.entry.data or {}

            # Customize values may live in entry.data (set by initial
            # config-flow thresholds step) or in entry.options (set by the
            # options flow). Options win when both are present.
            def _pick(key: str, default: Any) -> Any:
                if key in options:
                    return options[key]
                if key in data:
                    return data[key]
                return default

            thresholds: dict[str, Any] = {
                "days": int(_pick(CONF_DAYS, preset.get("days", 3))),
                "precip_threshold_mm": float(
                    _pick(CONF_PRECIP_THRESHOLD, preset.get("precip_threshold_mm", 0.2))
                ),
                "freeze_check": bool(_pick(CONF_FREEZE_CHECK, preset.get("freeze_check", True))),
                "precip_weight": float(_pick(CONF_PRECIP_WEIGHT, DEFAULT_PRECIP_WEIGHT)),
                "freeze_weight": float(_pick(CONF_FREEZE_WEIGHT, DEFAULT_FREEZE_WEIGHT)),
                "condition_weight": float(_pick(CONF_CONDITION_WEIGHT, DEFAULT_CONDITION_WEIGHT)),
            }
            bad_override = _pick(CONF_BAD_CONDITIONS, None)
            if bad_override:
                thresholds["bad_conditions"] = list(bad_override)
        else:
            thresholds = {
                "days": int(preset.get("days", 3)),
                "precip_threshold_mm": float(preset.get("precip_threshold_mm", 0.2)),
                "freeze_check": bool(preset.get("freeze_check", True)),
                "precip_weight": float(DEFAULT_PRECIP_WEIGHT),
                "freeze_weight": float(DEFAULT_FREEZE_WEIGHT),
                "condition_weight": float(DEFAULT_CONDITION_WEIGHT),
            }

        return thresholds, invert

    @staticmethod
    def _resolve_scan_interval(entry: ConfigEntry) -> timedelta:
        """Return the configured scan interval, defaulting to ``SCAN_INTERVAL``."""
        minutes = (entry.options or {}).get(CONF_SCAN_INTERVAL_MINUTES)
        if minutes is None:
            minutes = entry.data.get(CONF_SCAN_INTERVAL_MINUTES)
        if minutes is None:
            return SCAN_INTERVAL
        try:
            value = int(minutes)
        except (TypeError, ValueError):
            return SCAN_INTERVAL
        if value <= 0:
            return SCAN_INTERVAL
        return timedelta(minutes=value)

    def _resolve_temperature_unit(self) -> str | None:
        """Return the explicit fallback unit for forecast normalization.

        ``auto`` (the default) returns ``None`` so the weather adapter falls
        back to the source entity's ``temperature_unit`` attribute, then to
        HA's system temperature unit. The explicit ``celsius`` / ``fahrenheit``
        / ``kelvin`` values force that unit when a provider's metadata is
        wrong (or omitted, as some providers do for forecast frames).
        """
        options = self.entry.options or {}
        choice = options.get(CONF_TEMPERATURE_UNIT)
        if choice is None:
            choice = self.entry.data.get(CONF_TEMPERATURE_UNIT, DEFAULT_TEMPERATURE_UNIT)
        if not isinstance(choice, str):
            choice = DEFAULT_TEMPERATURE_UNIT
        choice = choice.strip().lower()
        if choice == TEMPERATURE_UNIT_CELSIUS:
            return "°C"
        if choice == TEMPERATURE_UNIT_FAHRENHEIT:
            return "°F"
        if choice == TEMPERATURE_UNIT_KELVIN:
            return "K"
        # ``auto`` (or anything unrecognised) -> let the adapter decide.
        if choice != TEMPERATURE_UNIT_AUTO:
            return None
        # Fall back to HA's system temperature unit when set.
        sys_unit = getattr(self.hass.config.units, "temperature_unit", None)
        if isinstance(sys_unit, str) and sys_unit.strip():
            return sys_unit
        return None

    @callback
    def _handle_state_change(self, event: Event) -> None:
        """Smart auto-recalc on weather entity state change.

        Behaviour:
        * If the *active* (currently used) provider changes, refresh
          immediately -- the decision is built from this entity's data.
        * If the active provider has never been resolved yet (cold start),
          a state change on the configured *primary* (index 0) triggers a
          refresh.
        * Lower-priority providers only matter when the primary is
          ``unavailable`` / ``unknown``; in that case any state change in the
          chain may unstick the failover, so we refresh.
        * Otherwise (e.g. provider 3 ticks while provider 1 is healthy) we
          do nothing -- the periodic interval still picks it up if needed.
        """
        eid = event.data.get("entity_id")
        if not eid:
            return
        weather_ids = list(self.entry.data.get(CONF_WEATHER_ENTITIES, []) or [])
        if eid not in weather_ids:
            return

        active = self._active_weather_entity
        primary = weather_ids[0] if weather_ids else None

        # Active provider tick -> always recompute.
        if active is not None and eid == active:
            self.hass.async_create_task(self.async_request_refresh())
            return

        # Cold start -> primary triggers the first decision.
        if active is None and eid == primary:
            self.hass.async_create_task(self.async_request_refresh())
            return

        # Fallback path: primary is sick, anything in the chain may unstick it.
        if primary is not None:
            primary_state = self.hass.states.get(primary)
            primary_dead = primary_state is None or primary_state.state in (
                "unavailable",
                "unknown",
                "",
                None,
            )
            if primary_dead:
                self.hass.async_create_task(self.async_request_refresh())

    @callback
    def _handle_registry_updated(self, event: Event) -> None:
        """Track weather-entity renames and removals.

        On ``update`` with an ``entity_id`` change, rewrite the stored
        ``weather_entities`` list with the new id and reload the entry so
        the registry listener re-binds to the new id.

        On ``remove``, drop the entity from the list and reload so the
        coordinator stops trying to read a non-existent entity.
        """
        action = event.data.get("action")
        if action not in ("update", "remove"):
            return

        event_entity_id = event.data.get("entity_id")
        if not event_entity_id:
            return

        weather_ids = list(self.entry.data.get(CONF_WEATHER_ENTITIES, []) or [])
        new_ids: list[str] | None = None

        if action == "remove":
            if event_entity_id in weather_ids:
                new_ids = [eid for eid in weather_ids if eid != event_entity_id]
        else:  # update
            changes = event.data.get("changes") or {}
            if "entity_id" not in changes:
                return
            old_id = changes.get("entity_id")
            new_id = event_entity_id
            if old_id in weather_ids and old_id != new_id:
                new_ids = [new_id if eid == old_id else eid for eid in weather_ids]

        if new_ids is None:
            return

        new_data = {**self.entry.data, CONF_WEATHER_ENTITIES: new_ids}
        self.hass.config_entries.async_update_entry(self.entry, data=new_data)
        # Reload re-runs setup, which re-instantiates the coordinator with
        # the updated weather_ids and re-attaches the registry listener.
        self.hass.async_create_task(self.hass.config_entries.async_reload(self.entry.entry_id))


__all__ = ["WashWiseCoordinator"]
