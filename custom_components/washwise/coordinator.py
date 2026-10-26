"""DataUpdateCoordinator for WashWise.

Drives the wash decision pipeline:

1. Walk the user's ordered ``weather_entities`` list.
2. Fetch current conditions + forecast from the active provider.
3. Honour persisted snooze: a live snooze short-circuits to ``can_wash=False``.
4. Otherwise call :func:`decision.compute` and return the resulting
   :class:`models.Decision`.
"""

from __future__ import annotations

import contextlib
import logging
from datetime import UTC, datetime, timedelta
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
    CONF_IRRIGATION_SWITCH_ENTITY,
    CONF_PRECIP_THRESHOLD,
    CONF_PRECIP_WEIGHT,
    CONF_RAIN_GAUGE_ENTITY,
    CONF_RAIN_GAUGE_THRESHOLD_MM,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_TEMPERATURE_UNIT,
    CONF_WEATHER_ENTITIES,
    CONF_WIND_THRESHOLD,
    DEFAULT_CATEGORY,
    DEFAULT_CONDITION_WEIGHT,
    DEFAULT_FORECAST_TYPE,
    DEFAULT_FREEZE_WEIGHT,
    DEFAULT_PRECIP_WEIGHT,
    DEFAULT_RAIN_GAUGE_THRESHOLD_MM,
    DEFAULT_TEMPERATURE_UNIT,
    DOMAIN,
    EVENT_VERDICT_CHANGED,
    SCAN_INTERVAL,
    TEMPERATURE_UNIT_AUTO,
    TEMPERATURE_UNIT_CELSIUS,
    TEMPERATURE_UNIT_FAHRENHEIT,
    TEMPERATURE_UNIT_KELVIN,
)
from .models import Decision, WashEntry
from .storage import WashWiseStore

_LOGGER = logging.getLogger(__name__)


def _verdict_fingerprint(decision: Decision) -> tuple[Any, ...]:
    """The user-visible subset of a Decision used to detect a verdict flip.

    Deliberately NOT the full dataclass ``__eq__``: ``forecast_summary`` /
    ``blocking_days`` churn on every poll as dates roll forward, which would
    fire ``washwise_verdict_changed`` constantly. Only these four fields are
    surfaced to users (card + sensors), so only they define a "change".
    """
    return (
        decision.can_wash,
        decision.reason,
        decision.score,
        decision.days_until_wash,
    )


class WashWiseCoordinator(DataUpdateCoordinator[Decision]):
    """Coordinator that runs the WashWise decision pipeline."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the coordinator and persistent store."""
        category = entry.data.get(CONF_CATEGORY, DEFAULT_CATEGORY)
        # garden_irrigation is fully event-driven (weather + gauge state changes).
        # No periodic polling needed — set interval to None.
        interval = None if category == "garden_irrigation" else self._resolve_scan_interval(entry)
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
        # Last weather-derived (non-snoozed) verdict, used to restore the card
        # instantly on clear_snooze without waiting for a forecast fetch.
        self._last_real_decision: Decision | None = None
        self._unsub_registry: CALLBACK_TYPE | None = None
        self._unsub_state: CALLBACK_TYPE | None = None
        self._unsub_gauge: CALLBACK_TYPE | None = None
        self._measured_rain_mm: float | None = None
        self._irrigation_suppressed: bool = False
        self._rain_gauge_threshold_mm: float | None = None
        self._forecast_blocks_irrigation: bool = False
        self._update_count: int = 0
        # Seed ``last_update_success_time`` so the ``last_update`` sensor has
        # a real timestamp the moment the entry is created, rather than
        # ``Unknown`` until the first coordinator tick lands.
        seed_ts = getattr(entry, "created_at", None) or dt_util.utcnow()
        with contextlib.suppress(Exception):  # pragma: no cover - HA versions without setter
            self.last_update_success_time = seed_ts  # type: ignore[attr-defined]

        # React to weather-entity renames so we keep tracking the same
        # underlying provider when the user changes its entity_id.
        weather_ids = self._weather_ids()
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

        # React immediately when the rain gauge sensor changes — no need to
        # wait for the periodic poll. Only wired when category is garden_irrigation.
        if category == "garden_irrigation":
            gauge_entity: str | None = (entry.options or {}).get(
                CONF_RAIN_GAUGE_ENTITY
            ) or entry.data.get(CONF_RAIN_GAUGE_ENTITY)
            if gauge_entity:
                self._unsub_gauge = async_track_state_change_event(
                    hass,
                    [gauge_entity],
                    self._handle_gauge_change,
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

    @property
    def measured_rain_mm(self) -> float | None:
        """Return the last read rain gauge value in mm, or None if not configured."""
        return self._measured_rain_mm

    @property
    def irrigation_suppressed(self) -> bool:
        """Return True when measured rain has exceeded the gauge threshold."""
        return self._irrigation_suppressed

    @property
    def rain_gauge_threshold_mm(self) -> float | None:
        """Return the configured rain gauge threshold in mm, or None if not irrigation category."""
        return self._rain_gauge_threshold_mm

    @property
    def forecast_blocks_irrigation(self) -> bool:
        """Return True when the forecast alone is blocking irrigation (rain expected)."""
        return self._forecast_blocks_irrigation

    @property
    def irrigation_switch_state(self) -> bool | None:
        """Return current ON/OFF state of the configured irrigation switch, or None if not set."""
        options = self.entry.options or {}
        data = self.entry.data or {}
        switch_entity = options.get(CONF_IRRIGATION_SWITCH_ENTITY) or data.get(
            CONF_IRRIGATION_SWITCH_ENTITY
        )
        if not switch_entity:
            return None
        state = self.hass.states.get(switch_entity)
        if state is None or state.state in ("unavailable", "unknown"):
            return None
        return state.state not in ("off",)

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

        Marking washed does not change the weather-derived verdict — it only
        updates the wash log (and thus the ``last_washed`` / ``days_since``
        sensors, which read the store directly). So push the CURRENT decision to
        listeners synchronously instead of ``async_request_refresh`` — the latter
        re-fetches the forecast over the network, adding seconds of lag before
        the card reflects the tap. The store write above is already applied, so
        the sensors re-render immediately with the new timestamp.
        """
        ts = timestamp if timestamp is not None else dt_util.utcnow()
        entry = WashEntry(timestamp=ts.isoformat(), source="manual")
        await self._store.append_wash(entry)
        if self.data is not None:
            self.async_set_updated_data(self.data)
        else:
            await self.async_request_refresh()

    async def async_snooze(self, duration: timedelta) -> None:
        """Snooze the wash advisor for ``duration`` from now.

        The snoozed verdict is deterministic and needs no forecast, so build and
        push it synchronously rather than triggering a network refresh.
        """
        until = dt_util.utcnow() + duration
        await self._store.set_snooze(until)
        snooze_decision = Decision(
            can_wash=False,
            score=0,
            reason="snoozed",
            days_until_wash=None,
            blocking_days=[],
            forecast_summary=[],
            days_analyzed=0,
        )
        self._set_decision(snooze_decision)
        self.async_set_updated_data(snooze_decision)

    async def async_clear_snooze(self) -> None:
        """Clear any active snooze.

        Restoring the real verdict needs the forecast. Push the last cached
        non-snoozed decision immediately (so the card reacts instantly), then
        kick a background refresh to recompute against current weather.
        """
        await self._store.set_snooze(None)
        if self._last_real_decision is not None:
            self._set_decision(self._last_real_decision)
            self.async_set_updated_data(self._last_real_decision)
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
        if self._unsub_gauge is not None:
            self._unsub_gauge()
            self._unsub_gauge = None
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
                if snooze_dt.tzinfo is None:
                    snooze_dt = snooze_dt.replace(tzinfo=UTC)
            except (ValueError, TypeError):
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
                self._set_decision(snooze_decision)
                return snooze_decision

        # Step 4: walk the provider chain.
        weather_ids: list[str] = self._weather_ids()
        forecast_type = (self.entry.options or {}).get(CONF_FORECAST_TYPE) or self.entry.data.get(
            CONF_FORECAST_TYPE
        )
        if not forecast_type:
            category = (self.entry.options or {}).get(CONF_CATEGORY) or self.entry.data.get(
                CONF_CATEGORY, DEFAULT_CATEGORY
            )
            preset = CATEGORY_PRESETS.get(category, CATEGORY_PRESETS[DEFAULT_CATEGORY])
            forecast_type = preset.get("forecast_type", DEFAULT_FORECAST_TYPE)
        thresholds, invert = self._resolve_thresholds()
        horizon = int(thresholds.get("days", 3))

        previous_active = self._active_weather_entity
        last_error: str | None = None
        today = dt_util.now().date()

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

            if horizon > 0:
                filtered_forecast = [fd for fd in forecast if fd.date >= today]
                if not filtered_forecast and forecast:
                    _LOGGER.debug(
                        "WashWise: all forecast entries for %s predate today;"
                        " provider may start from tomorrow",
                        eid,
                    )
                    await self._store.update_provider_health(eid, False, "stale_forecast")
                    last_error = "stale_forecast"
                    continue
                forecast = filtered_forecast

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
                        wind_speed_kmh=fd.wind_speed_kmh,
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
            # Remember the real (weather-derived) verdict so clear_snooze can
            # restore it instantly without re-fetching the forecast.
            self._last_real_decision = decision
            # Settle the irrigation switch BEFORE firing the verdict-changed
            # event, so an automation reacting to the event sees the updated
            # switch state rather than the previous tick's.
            await self._async_handle_irrigation(decision)
            self._set_decision(decision)
            self._update_count += 1
            if self._update_count % 50 == 0:
                await self._store.gc_stale_health()
            _LOGGER.debug(
                "WashWise update via %s: can_wash=%s reason=%s score=%s days_analyzed=%s",
                eid,
                decision.can_wash,
                decision.reason,
                decision.score,
                decision.days_analyzed,
            )
            return decision

        # Step 5: every provider failed.
        raise UpdateFailed(last_error or "no available weather provider")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @callback
    def _set_decision(self, decision: Decision) -> Decision:
        """Store the new decision and fire the verdict-changed event on a flip.

        Called from every path that produces a verdict (snooze short-circuit
        and the normal provider walk). Fires ``washwise_verdict_changed`` on
        the HA bus only when the user-visible outcome actually changes —
        ``can_wash``, ``reason``, ``score``, or ``days_until_wash``. Entering/
        leaving snooze or a no-data state is a real change and fires; an idle
        re-poll with an identical verdict does not.

        The FIRST decision after construction (``prev is None``) never fires:
        there is no prior verdict to have "changed" from, and ``_last_decision``
        is not restored across restarts, so firing on first-load would make
        every HA restart/reload re-fire the event (and double-trigger any
        listening automation) even when the verdict is unchanged.
        """
        prev = self._last_decision
        self._last_decision = decision
        changed = prev is not None and _verdict_fingerprint(prev) != _verdict_fingerprint(decision)
        if changed:
            self.hass.bus.async_fire(
                EVENT_VERDICT_CHANGED,
                {
                    "entry_id": self.entry.entry_id,
                    "title": self.entry.title,
                    "category": self.entry.data.get(CONF_CATEGORY, DEFAULT_CATEGORY),
                    "can_wash": decision.can_wash,
                    "previous_can_wash": prev.can_wash if prev is not None else None,
                    "score": decision.score,
                    "reason": decision.reason,
                    "days_until_wash": decision.days_until_wash,
                },
            )
        return decision

    async def _async_handle_irrigation(self, decision: Decision) -> None:
        """Read rain gauge and toggle irrigation switch for garden_irrigation category."""
        category = self.entry.data.get(CONF_CATEGORY, DEFAULT_CATEGORY)
        if category != "garden_irrigation":
            self._measured_rain_mm = None
            self._irrigation_suppressed = False
            self._rain_gauge_threshold_mm = None
            self._forecast_blocks_irrigation = False
            return

        options = self.entry.options or {}
        data = self.entry.data or {}

        def _pick(key: str, default: Any) -> Any:
            if key in options:
                return options[key]
            if key in data:
                return data[key]
            return default

        gauge_entity: str | None = _pick(CONF_RAIN_GAUGE_ENTITY, None)
        threshold_mm: float = float(
            _pick(CONF_RAIN_GAUGE_THRESHOLD_MM, DEFAULT_RAIN_GAUGE_THRESHOLD_MM)
        )
        switch_entity: str | None = _pick(CONF_IRRIGATION_SWITCH_ENTITY, None)

        # Read gauge.
        measured: float | None = None
        if gauge_entity:
            gauge_state = self.hass.states.get(gauge_entity)
            if gauge_state is not None and gauge_state.state not in (
                "unavailable",
                "unknown",
                "",
                None,
            ):
                try:
                    measured = float(gauge_state.state)
                except (TypeError, ValueError):
                    _LOGGER.warning(
                        "WashWise: rain gauge entity %s has non-numeric state %r",
                        gauge_entity,
                        gauge_state.state,
                    )

        self._measured_rain_mm = measured
        self._rain_gauge_threshold_mm = threshold_mm

        # Suppress irrigation when: measured rain >= threshold OR forecast blocks irrigation.
        # garden_irrigation uses invert=True: can_wash=True means rain expected (skip irrigation).
        gauge_suppressed = measured is not None and measured >= threshold_mm
        forecast_suppressed = decision.can_wash
        self._forecast_blocks_irrigation = forecast_suppressed
        suppressed = gauge_suppressed or forecast_suppressed
        self._irrigation_suppressed = suppressed

        # Toggle irrigation switch when suppression state changes.
        if switch_entity and "." in switch_entity:
            current_switch_state = self.hass.states.get(switch_entity)
            if current_switch_state is not None:
                state_val = current_switch_state.state
                if state_val in ("unavailable", "unknown"):
                    return
                switch_on = state_val not in ("off",)
                if suppressed and switch_on:
                    domain, _ = switch_entity.split(".", 1)
                    await self.hass.services.async_call(
                        domain,
                        "turn_off",
                        {"entity_id": switch_entity},
                        blocking=False,
                    )
                elif not suppressed and not switch_on:
                    domain, _ = switch_entity.split(".", 1)
                    await self.hass.services.async_call(
                        domain,
                        "turn_on",
                        {"entity_id": switch_entity},
                        blocking=False,
                    )

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
            # Wind is seeded into entry.data at create time for new entries and
            # otherwise set explicitly by the user. Read only the stored value —
            # never fall back to the preset, so existing pre-0.2.7 entries that
            # never stored a wind key stay disabled rather than retroactively
            # inheriting the new per-category default. ``None`` / absent leaves
            # the decision engine's wind blocker disabled.
            wind_threshold = _pick(CONF_WIND_THRESHOLD, None)
            if wind_threshold is not None:
                thresholds["wind_threshold_kmh"] = float(wind_threshold)
        else:
            thresholds = {
                "days": int(preset.get("days", 3)),
                "precip_threshold_mm": float(preset.get("precip_threshold_mm", 0.2)),
                "freeze_check": bool(preset.get("freeze_check", True)),
                "precip_weight": float(DEFAULT_PRECIP_WEIGHT),
                "freeze_weight": float(DEFAULT_FREEZE_WEIGHT),
                "condition_weight": float(DEFAULT_CONDITION_WEIGHT),
            }
            # Wind default is seeded into entry.data at create time for NEW
            # entries (config_flow), so read the stored value — never the preset
            # directly. Existing pre-0.2.7 entries have no stored key and stay
            # disabled, exactly as before; only freshly-created entries carry the
            # per-category default. options win over data if somehow set.
            stored_wind = options.get(CONF_WIND_THRESHOLD, self.entry.data.get(CONF_WIND_THRESHOLD))
            if stored_wind is not None:
                thresholds["wind_threshold_kmh"] = float(stored_wind)

        return thresholds, invert

    @staticmethod
    def _resolve_scan_interval(entry: ConfigEntry) -> timedelta:
        # CONF_SCAN_INTERVAL_MINUTES is no longer exposed in the options UI (removed in v0.2.0).
        # Retained here so existing entries that had it configured keep their setting without
        # a forced migration. New entries always get SCAN_INTERVAL.
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

    def _weather_ids(self) -> list[str]:
        """Merged ``weather_entities`` list — options win over data."""
        options = self.entry.options or {}
        data = self.entry.data or {}
        ids = options.get(CONF_WEATHER_ENTITIES)
        if ids is None:
            ids = data.get(CONF_WEATHER_ENTITIES)
        return list(ids or [])

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
        weather_ids = self._weather_ids()
        if eid not in weather_ids:
            return

        active = self._active_weather_entity
        primary = weather_ids[0] if weather_ids else None

        # Active provider tick -> always recompute.
        if active is not None and eid == active:
            self.entry.async_create_background_task(
                self.hass, self.async_request_refresh(), "washwise_refresh"
            )
            return

        # Cold start -> primary triggers the first decision.
        if active is None and eid == primary:
            self.entry.async_create_background_task(
                self.hass, self.async_request_refresh(), "washwise_refresh"
            )
            return

        # Fallback path: primary is sick, anything in the chain may unstick it.
        if primary is not None:  # pragma: no branch  # eid in weather_ids implies primary set
            primary_state = self.hass.states.get(primary)
            primary_dead = primary_state is None or primary_state.state in (
                "unavailable",
                "unknown",
                "",
                None,
            )
            if primary_dead:
                self.entry.async_create_background_task(
                    self.hass, self.async_request_refresh(), "washwise_refresh"
                )

    @callback
    def _handle_gauge_change(self, event: Event) -> None:
        """Trigger immediate refresh when the rain gauge sensor value changes."""
        self.entry.async_create_background_task(
            self.hass, self.async_request_refresh(), "washwise_refresh"
        )

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

        weather_ids = self._weather_ids()
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
        options = self.entry.options or {}
        if CONF_WEATHER_ENTITIES in options:
            new_options = {**options, CONF_WEATHER_ENTITIES: new_ids}
            self.hass.config_entries.async_update_entry(
                self.entry, data=new_data, options=new_options
            )
        else:
            self.hass.config_entries.async_update_entry(self.entry, data=new_data)
        # Reload re-runs setup, which re-instantiates the coordinator with
        # the updated weather_ids and re-attaches the registry listener.
        self.entry.async_create_background_task(
            self.hass,
            self.hass.config_entries.async_reload(self.entry.entry_id),
            "washwise_reload",
        )


__all__ = ["WashWiseCoordinator"]
