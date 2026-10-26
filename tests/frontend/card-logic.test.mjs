// Unit tests for the WashWise card's pure logic.
//
// Harness note: the card is loaded via ./_harness.mjs, which stubs the browser
// globals the card reads at module-load and captures the card classes. Methods
// are reached off the prototype. _verdict/_score use no `this`; getCardSize /
// _mergedConfig / setConfig / shouldUpdate need a stub `this` carrying the
// relevant fields; render helpers inherit the stubbed html/nothing.
//
// Date.now()/new Date() are stubbed per-test where snooze math needs a fixed
// clock (the card reads wall-clock in _renderReason and connectedCallback).

import { test } from "node:test";
import assert from "node:assert/strict";
import { Card, Editor, NOTHING } from "./_harness.mjs";

// ── _verdict ─────────────────────────────────────────────────────────────────

test("_verdict: wash mode maps on→ok, off→bad, else unknown", () => {
  const v = (state) => Card.prototype._verdict({ state }, false);
  assert.equal(v("on"), "ok");
  assert.equal(v("off"), "bad");
  assert.equal(v("unavailable"), "unknown");
});

test("_verdict: irrigation mode inverts (on=rain→bad, off=dry→ok)", () => {
  const v = (state) => Card.prototype._verdict({ state }, true);
  assert.equal(v("on"), "bad", "rain expected → skip irrigation");
  assert.equal(v("off"), "ok", "no rain → irrigate");
  assert.equal(v("unknown"), "unknown");
});

// ── _score ───────────────────────────────────────────────────────────────────

test("_score: finite number passes through", () => {
  assert.equal(Card.prototype._score({ attributes: { score: 73 } }), 73);
  assert.equal(Card.prototype._score({ attributes: { score: 0 } }), 0);
});

test("_score: numeric string is parsed, blank/garbage → null", () => {
  assert.equal(Card.prototype._score({ attributes: { score: "42" } }), 42);
  assert.equal(Card.prototype._score({ attributes: { score: "  " } }), null);
  assert.equal(Card.prototype._score({ attributes: { score: "nope" } }), null);
});

test("_score: missing / non-finite → null", () => {
  assert.equal(Card.prototype._score({ attributes: {} }), null);
  assert.equal(Card.prototype._score({ attributes: { score: NaN } }), null);
  assert.equal(Card.prototype._score({ attributes: { score: Infinity } }), null);
});

// ── getCardSize (sums enabled sections) ───────────────────────────────────────

test("getCardSize: all defaults on sums to 7 (actions off by default)", () => {
  // base 2 + gauge 1 + reason 1 + forecast 2 + diagnostics 1 = 7; show_actions defaults off
  const size = Card.prototype.getCardSize.call({
    _config: { entity: "binary_sensor.x_can_wash" },
    _mergedConfig: Card.prototype._mergedConfig,
  });
  assert.equal(size, 7);
});

test("getCardSize: enabling actions adds 1", () => {
  const size = Card.prototype.getCardSize.call({
    _config: { entity: "binary_sensor.x_can_wash", show_actions: true },
    _mergedConfig: Card.prototype._mergedConfig,
  });
  assert.equal(size, 8);
});

test("getCardSize: disabling forecast + diagnostics drops 3", () => {
  const size = Card.prototype.getCardSize.call({
    _config: {
      entity: "binary_sensor.x_can_wash",
      show_forecast_strip: false,
      show_diagnostics: false,
    },
    _mergedConfig: Card.prototype._mergedConfig,
  });
  assert.equal(size, 4); // 7 - 2 (forecast) - 1 (diagnostics)
});

// ── _mergedConfig (defaults ∪ user) ───────────────────────────────────────────

test("_mergedConfig: user config overrides defaults, defaults fill gaps", () => {
  const merged = Card.prototype._mergedConfig.call({
    _config: { entity: "binary_sensor.x_can_wash", theme: "dark" },
  });
  assert.equal(merged.theme, "dark", "user override wins");
  assert.equal(merged.mode, "wash", "default filled");
  assert.equal(merged.show_score_gauge, true, "default filled");
  assert.equal(merged.show_actions, false, "action row off by default");
});

// ── setConfig (validation) ────────────────────────────────────────────────────

test("setConfig: throws on missing/invalid config object", () => {
  assert.throws(() => Card.prototype.setConfig.call({}, null), /Invalid configuration/);
  assert.throws(() => Card.prototype.setConfig.call({}, "x"), /Invalid configuration/);
});

test("setConfig: throws when entity missing or non-string", () => {
  assert.throws(() => Card.prototype.setConfig.call({}, {}), /must define an entity/);
  assert.throws(
    () => Card.prototype.setConfig.call({}, { entity: 123 }),
    /must define an entity/
  );
});

test("setConfig: accepts a valid binary_sensor entity (no throw, stored copy)", () => {
  const self = {};
  Card.prototype.setConfig.call(self, { entity: "binary_sensor.x_can_wash" });
  assert.equal(self._config.entity, "binary_sensor.x_can_wash");
});

test("setConfig: non-binary_sensor entity warns but does not throw", () => {
  const self = {};
  const orig = console.warn;
  let warned = false;
  console.warn = () => {
    warned = true;
  };
  try {
    Card.prototype.setConfig.call(self, { entity: "sensor.x" });
  } finally {
    console.warn = orig;
  }
  assert.equal(self._config.entity, "sensor.x");
  assert.ok(warned, "a warning is logged for non-binary_sensor entities");
});

// ── shouldUpdate (watched entities) ───────────────────────────────────────────

test("shouldUpdate: true when _config changed", () => {
  const result = Card.prototype.shouldUpdate.call({}, { has: () => true, get: () => undefined });
  assert.equal(result, true);
});

test("shouldUpdate: true when the watched can_wash state object changes", () => {
  const id = "binary_sensor.x_can_wash";
  const oldHass = { states: { [id]: { state: "on" } } };
  const newState = { state: "off" };
  const self = { hass: { states: { [id]: newState } }, _config: { entity: id } };
  const changed = { has: (k) => k === "hass", get: () => oldHass };
  assert.equal(Card.prototype.shouldUpdate.call(self, changed), true);
});

test("shouldUpdate: true when only the derived snooze sensor changes", () => {
  const id = "binary_sensor.x_can_wash";
  const snoozeId = "sensor.x_snooze_remaining";
  const same = { state: "on" };
  const oldHass = { states: { [id]: same, [snoozeId]: { state: "100" } } };
  const self = {
    hass: { states: { [id]: same, [snoozeId]: { state: "200" } } },
    _config: { entity: id },
  };
  const changed = { has: () => false, get: () => oldHass };
  assert.equal(Card.prototype.shouldUpdate.call(self, changed), true);
});

test("shouldUpdate: false when nothing watched changed", () => {
  const id = "binary_sensor.x_can_wash";
  const snoozeId = "sensor.x_snooze_remaining";
  const stateObj = { state: "on" };
  const snoozeObj = { state: "100" };
  const hass = { states: { [id]: stateObj, [snoozeId]: snoozeObj } };
  const self = { hass, _config: { entity: id } };
  const changed = { has: () => false, get: () => hass };
  assert.equal(Card.prototype.shouldUpdate.call(self, changed), false);
});

// ── _renderReason: snooze remaining formatting ────────────────────────────────

// The snooze branch computes remaining time from (snooze_until - Date.now())
// and formats it coarsely (w/d, d/h, h/m, or m). Pin Date.now() via a fixed
// snooze_until offset so the arithmetic is deterministic.

function renderSnooze(remainingSeconds) {
  const NOW = 1_700_000_000_000; // fixed epoch ms
  const until = new Date(NOW + remainingSeconds * 1000).toISOString();
  const id = "binary_sensor.x_can_wash";
  const self = {
    hass: {
      states: {
        "sensor.x_snooze_remaining": { attributes: { snooze_until: until } },
      },
    },
  };
  const stateObj = { entity_id: id, attributes: { reason: "snoozed" } };
  const origNow = Date.now;
  Date.now = () => NOW;
  try {
    return Card.prototype._renderReason.call(self, stateObj, false).__html;
  } finally {
    Date.now = origNow;
  }
}

test("_renderReason snooze: weeks+days format for multi-week remaining", () => {
  // 2w 3d = (2*7+3)*86400 = 1_468_800 s
  assert.match(renderSnooze(2 * 604800 + 3 * 86400), /2w 3d/);
});

test("_renderReason snooze: days+hours format", () => {
  assert.match(renderSnooze(3 * 86400 + 5 * 3600), /3d 5h/);
});

test("_renderReason snooze: hours+minutes format", () => {
  assert.match(renderSnooze(5 * 3600 + 20 * 60), /5h 20m/);
});

test("_renderReason snooze: minutes-only format", () => {
  assert.match(renderSnooze(45 * 60), /45m/);
});

test("_renderReason snooze: already elapsed shows em-dash", () => {
  assert.match(renderSnooze(-10), /—/);
});

// ── _renderReason: non-snooze reason labels ───────────────────────────────────

test("_renderReason: wash reason uses the human label map", () => {
  const frag = Card.prototype._renderReason.call(
    {},
    { attributes: { reason: "rain" } },
    false
  );
  assert.match(frag.__html, /Rain in forecast/);
});

test("_renderReason: irrigation reason uses the irrigation label map", () => {
  const frag = Card.prototype._renderReason.call(
    { _irrigationReason: Card.prototype._irrigationReason },
    { attributes: { reason: "rain" } },
    true
  );
  assert.match(frag.__html, /Rain in forecast — skip/);
});

test("_renderReason: unknown reason key passes through verbatim", () => {
  const frag = Card.prototype._renderReason.call(
    {},
    { attributes: { reason: "custom_key" } },
    false
  );
  assert.match(frag.__html, /custom_key/);
});

test("_renderReason: wind + no_data backend reasons have friendly labels (both modes)", () => {
  // Backend added REASON_WIND="wind" and REASON_NO_DATA="no_data"; the card must
  // not fall through to the raw key (regression the no-data honesty fix targets).
  const wash = (reason) =>
    Card.prototype._renderReason.call({}, { attributes: { reason } }, false).__html;
  assert.match(wash("wind"), /High wind/);
  assert.match(wash("no_data"), /No forecast data/);
  assert.doesNotMatch(wash("wind"), />wind</, "raw key not shown");
  assert.doesNotMatch(wash("no_data"), />no_data</, "raw key not shown");

  const irr = (reason) =>
    Card.prototype._renderReason.call(
      { _irrigationReason: Card.prototype._irrigationReason },
      { attributes: { reason } },
      true
    ).__html;
  assert.match(irr("wind"), /High wind — skip/);
  assert.match(irr("no_data"), /No forecast data/);
});

test("_renderReason: every backend reason key maps to a non-raw label (both modes)", () => {
  // The full REASON_* enum from decision.py. If the backend adds a reason and
  // the card map isn't updated, prettyReason falls through to `?? key` and the
  // dashboard shows a raw token — exactly the honesty bug this guards against.
  // Keep in sync with decision.py REASON_* values.
  const BACKEND_REASONS = [
    "clear", "rain", "freeze", "snow", "bad_condition",
    "bad_current_condition", "dirty_now", "no_data", "wind",
  ];
  for (const reason of BACKEND_REASONS) {
    const wash = Card.prototype._renderReason.call({}, { attributes: { reason } }, false).__html;
    assert.doesNotMatch(wash, new RegExp(`>${reason}<`), `wash mode shows raw key for ${reason}`);
    const irr = Card.prototype._renderReason.call(
      { _irrigationReason: Card.prototype._irrigationReason },
      { attributes: { reason } },
      true
    ).__html;
    assert.doesNotMatch(irr, new RegExp(`>${reason}<`), `irrigation mode shows raw key for ${reason}`);
  }
});

test("_renderReason: no reason attribute is hidden", () => {
  assert.equal(
    Card.prototype._renderReason.call({}, { attributes: {} }, false),
    NOTHING
  );
});

// ── _renderForecastStrip (icon + precip + blocked class) ──────────────────────

test("_renderForecastStrip: empty/absent summary is hidden", () => {
  assert.equal(
    Card.prototype._renderForecastStrip.call({}, { attributes: {} }),
    NOTHING
  );
  assert.equal(
    Card.prototype._renderForecastStrip.call({}, { attributes: { forecast_summary: [] } }),
    NOTHING
  );
});

test("_renderForecastStrip: known condition → emoji, precip formatted, blocked→class", () => {
  const frag = Card.prototype._renderForecastStrip.call(
    {},
    {
      attributes: {
        forecast_summary: [
          { date: "2026-01-15", condition: "rainy", precipitation_mm: 4.25, blocked: true },
          { date: "2026-01-16", condition: "sunny", precipitation_mm: 0, blocked: false },
        ],
      },
    }
  ).__html;
  // Bind class to the right day: within one day block, class precedes its emoji.
  assert.match(frag, /ww-day bad[\s\S]*?🌧️/, "blocked:true (rainy) → bad class on that day");
  assert.match(frag, /ww-day ok[\s\S]*?☀️/, "blocked:false (sunny) → ok class on that day");
  assert.doesNotMatch(frag, /ww-day ok[\s\S]*?🌧️/, "rainy day must not be ok");
  assert.match(frag, /4\.3mm/, "precip rounded to one decimal");
  assert.match(frag, /0\.0mm/);
});

test("_renderForecastStrip: blocked null/absent → no ok|bad class (default blank)", () => {
  const frag = Card.prototype._renderForecastStrip.call(
    {},
    { attributes: { forecast_summary: [{ date: "2026-01-15", condition: "cloudy" }] } }
  ).__html;
  // blocked unset → cls "" → class attr is exactly "ww-day " with no ok/bad.
  assert.doesNotMatch(frag, /ww-day (ok|bad)/, "no verdict class when blocked is absent");
});

test("_renderForecastStrip: unknown condition falls back to exactly first 2 chars", () => {
  const frag = Card.prototype._renderForecastStrip.call(
    {},
    { attributes: { forecast_summary: [{ date: "2026-01-15", condition: "zzzzz" }] } }
  ).__html;
  // Icon cell must be exactly "zz" (slice(0,2)) — anchored so slice(0,3) would fail.
  assert.match(frag, /class="icon">zz<\/span>/, "unknown condition → exactly condition.slice(0,2)");
  assert.match(frag, /—/, "missing precip → em-dash");
});

// ── Editor _washWiseInstances (discovery + sort) ──────────────────────────────

test("_washWiseInstances: picks can_wash sensors with washwise attrs, sorted by label", () => {
  const self = {
    hass: {
      states: {
        "binary_sensor.zebra_can_wash": {
          attributes: { friendly_name: "Zebra", score: 50 },
        },
        "binary_sensor.alpha_can_wash": {
          attributes: { friendly_name: "Alpha", forecast_summary: [] },
        },
        "binary_sensor.not_washwise_can_wash": {
          attributes: { friendly_name: "Nope" }, // no score, no forecast_summary → excluded
        },
        "binary_sensor.other": { attributes: { score: 1 } }, // not _can_wash → excluded
        "sensor.alpha_can_wash": { attributes: { score: 1 } }, // not binary_sensor → excluded
      },
    },
  };
  const out = Editor.prototype._washWiseInstances.call(self);
  assert.deepEqual(
    out.map((i) => i.label),
    ["Alpha", "Zebra"],
    "only valid washwise instances, sorted by label"
  );
});

test("_washWiseInstances: empty when hass has no states", () => {
  assert.deepEqual(Editor.prototype._washWiseInstances.call({ hass: undefined }), []);
});

// ── _renderGauge (score→pct clamp, null label, bad class) ─────────────────────

test("_renderGauge: score renders as rounded percent, fill width = clamped pct", () => {
  const frag = Card.prototype._renderGauge.call({}, 72.6, "ok").__html;
  assert.match(frag, /width: 72\.6%/, "fill width is the clamped score");
  assert.match(frag, />73%</, "label is Math.round(score)%");
  assert.doesNotMatch(frag, /ww-gauge-bar-fill bad/, "ok verdict → no bad class");
});

test("_renderGauge: out-of-range score clamps to 0..100", () => {
  assert.match(Card.prototype._renderGauge.call({}, 140, "ok").__html, /width: 100%/);
  assert.match(Card.prototype._renderGauge.call({}, -20, "ok").__html, /width: 0%/);
});

test("_renderGauge: null score → em-dash label, width falls back to 0", () => {
  const frag = Card.prototype._renderGauge.call({}, null, "bad").__html;
  assert.match(frag, />—</, "null score → em-dash, not '0%'");
  assert.match(frag, /width: 0%/, "null score ?? 0 → 0 width");
});

test("_renderGauge: bad verdict adds the bad fill class", () => {
  const frag = Card.prototype._renderGauge.call({}, 30, "bad").__html;
  assert.match(frag, /ww-gauge-bar-fill bad/, "bad verdict → bad class on fill");
});

// ── Actions: render (always shown in wash mode; irrigation hidden) ────────────

function actionsSelf() {
  return { _config: { entity: "binary_sensor.x_can_wash" } };
}

test("_renderActions: rendered in wash mode regardless of entity registry", () => {
  // Regression: the row used to be silently hidden without a resolvable
  // config_entry_id, so an enabled show_actions toggle appeared to do nothing in
  // the editor preview / early load. It now always renders in wash mode; buttons
  // target the service by entity_id and the backend resolves the entry.
  const frag = Card.prototype._renderActions.call(actionsSelf(), { state: "on" }, false);
  assert.notEqual(frag, NOTHING, "action row renders");
  assert.match(frag.__html, /Mark washed/);
});

test("_renderActions: hidden entirely in irrigation mode (wrong semantics)", () => {
  const frag = Card.prototype._renderActions.call(actionsSelf(), { state: "off" }, false, true);
  assert.equal(frag, NOTHING, "mark_washed/snooze are meaningless for irrigation");
});

test("_renderActions: not snoozed → Mark washed + Snooze (no Clear)", () => {
  const frag = Card.prototype._renderActions.call(actionsSelf("e1"), { state: "on" }, false).__html;
  assert.match(frag, /Mark washed/);
  assert.match(frag, /Snooze/);
  assert.doesNotMatch(frag, /Clear snooze/, "no Clear button when not snoozed");
});

test("_renderActions: snoozed → Mark washed + Clear snooze (no plain Snooze)", () => {
  const frag = Card.prototype._renderActions.call(actionsSelf("e1"), { state: "off" }, true).__html;
  assert.match(frag, /Mark washed/);
  assert.match(frag, /Clear snooze/);
  // "Snooze" also appears inside "Clear snooze"; assert the standalone label is gone.
  assert.doesNotMatch(frag, />⏸️<span>Snooze<\/span>/, "no standalone Snooze when snoozed");
});

test("_renderActions: every button disabled when state unavailable/unknown", () => {
  // The html stub renders Lit's ?disabled boolean binding literally as
  // "?disabled=<bool>" (it does not model attribute removal), so assert on the
  // bound value rather than the attribute's presence.
  const u = Card.prototype._renderActions.call(actionsSelf("e1"), { state: "unavailable" }, false).__html;
  assert.doesNotMatch(u, /\?disabled=false/, "unavailable → no button left enabled");
  assert.match(u, /\?disabled=true/, "unavailable → disabled bound true");
  const us = Card.prototype._renderActions.call(actionsSelf("e1"), { state: "unknown" }, true /*snoozed*/).__html;
  assert.doesNotMatch(us, /\?disabled=false/, "snoozed+unknown → Clear also disabled");
  const ok = Card.prototype._renderActions.call(actionsSelf("e1"), { state: "on" }, false).__html;
  assert.doesNotMatch(ok, /\?disabled=true/, "available → nothing disabled");
});

// ── Actions: handlers (confirm gate + service call shape) ─────────────────────

function captureCall(fn) {
  const calls = [];
  const self = {
    _config: { entity: "binary_sensor.x_can_wash" },
    _confirm: () => true,
    _callService: (service, data) => calls.push({ service, data }),
  };
  fn(self);
  return calls;
}

test("_onMarkWashed: confirmed → calls mark_washed targeted by entity_id", () => {
  const calls = captureCall((self) => Card.prototype._onMarkWashed.call(self));
  assert.deepEqual(calls, [
    { service: "mark_washed", data: { entity_id: "binary_sensor.x_can_wash" } },
  ]);
});

test("_onMarkWashed: declined confirm → no service call", () => {
  const calls = [];
  const self = {
    _config: { entity: "binary_sensor.x_can_wash" },
    _confirm: () => false,
    _callService: (service, data) => calls.push({ service, data }),
  };
  Card.prototype._onMarkWashed.call(self);
  assert.deepEqual(calls, [], "confirm returning false aborts the call");
});

test("_onSnooze: fires snooze with entity_id only (no hours → backend default)", () => {
  const calls = captureCall((self) => Card.prototype._onSnooze.call(self));
  assert.deepEqual(calls, [
    { service: "snooze", data: { entity_id: "binary_sensor.x_can_wash" } },
  ]);
});

test("_onClearSnooze: fires clear_snooze with entity_id", () => {
  const calls = captureCall((self) => Card.prototype._onClearSnooze.call(self));
  assert.deepEqual(calls, [
    { service: "clear_snooze", data: { entity_id: "binary_sensor.x_can_wash" } },
  ]);
});

test("action handlers: no entity configured → no-op (no service call, no throw)", () => {
  // Defensive: without a configured entity there is nothing to target.
  for (const h of ["_onMarkWashed", "_onSnooze", "_onClearSnooze"]) {
    const calls = [];
    const self = {
      _config: {},
      _confirm: () => true,
      _callService: (service, data) => calls.push({ service, data }),
    };
    Card.prototype[h].call(self);
    assert.deepEqual(calls, [], `${h} must not call a service without an entity`);
  }
});

test("_callService: forwards to hass.callService under the washwise domain", () => {
  const calls = [];
  const self = { hass: { callService: (...a) => calls.push(a) } };
  Card.prototype._callService.call(self, "snooze", { entry_id: "e1" });
  assert.deepEqual(calls, [["washwise", "snooze", { entry_id: "e1" }]]);
});

test("_callService: no-op when hass has no callService", () => {
  assert.doesNotThrow(() => Card.prototype._callService.call({ hass: {} }, "snooze", {}));
});

test("_callService: swallows a rejected service call (no unhandled rejection)", async () => {
  const origErr = console.error;
  let logged = false;
  console.error = () => {
    logged = true;
  };
  try {
    const self = {
      hass: { callService: () => Promise.reject(new Error("stale entry_id")) },
    };
    const p = Card.prototype._callService.call(self, "mark_washed", { entry_id: "x" });
    await p.catch(() => {}); // settle without surfacing to the test runner
    // give the attached .catch() a tick to run
    await Promise.resolve();
  } finally {
    console.error = origErr;
  }
  assert.ok(logged, "rejection is caught and logged, not left unhandled");
});

// ── _confirm fail-safe (destructive action must not fire without a prompt) ────

test("_confirm: returns false when window.confirm is unavailable", () => {
  const origWindow = globalThis.window;
  try {
    globalThis.window = {}; // no confirm
    assert.equal(Card.prototype._confirm.call({}, "x"), false);
    globalThis.window = { confirm: () => true };
    assert.equal(Card.prototype._confirm.call({}, "x"), true, "present confirm passes through");
    globalThis.window = { confirm: () => false };
    assert.equal(Card.prototype._confirm.call({}, "x"), false, "declined confirm → false");
  } finally {
    globalThis.window = origWindow;
  }
});

// ── shouldUpdate: watched-state changes (registry no longer gates the row) ────
// The action row targets services by entity_id and renders unconditionally in
// wash mode, so there is no entry_id-hydration render to assert any more.

// ── _renderLastWashed (relative time, derived sensor, hidden when no wash) ────

function lastWashedSelf(sensorState) {
  const canWash = "binary_sensor.x_can_wash";
  const lastWashedId = "sensor.x_last_washed";
  return {
    _config: { entity: canWash },
    hass: { states: sensorState === undefined ? {} : { [lastWashedId]: { state: sensorState } } },
  };
}

test("_renderLastWashed: hidden when no entity configured", () => {
  assert.equal(Card.prototype._renderLastWashed.call({ _config: {}, hass: { states: {} } }), NOTHING);
});

test("_renderLastWashed: hidden when sensor missing / unknown / unavailable", () => {
  assert.equal(Card.prototype._renderLastWashed.call(lastWashedSelf(undefined)), NOTHING);
  assert.equal(Card.prototype._renderLastWashed.call(lastWashedSelf("unknown")), NOTHING);
  assert.equal(Card.prototype._renderLastWashed.call(lastWashedSelf("unavailable")), NOTHING);
});

test("_renderLastWashed: formats a coarse '… ago' from the sensor timestamp", () => {
  const NOW = 1_700_000_000_000;
  const origNow = Date.now;
  Date.now = () => NOW;
  try {
    const iso = (secsAgo) => new Date(NOW - secsAgo * 1000).toISOString();
    const render = (secsAgo) =>
      Card.prototype._renderLastWashed.call(lastWashedSelf(iso(secsAgo))).__html;
    assert.match(render(30), /just now/);
    assert.match(render(5 * 60), /5 minutes ago/);
    assert.match(render(60), /1 minute ago/);
    assert.match(render(2 * 3600), /2 hours ago/);
    assert.match(render(3 * 86400), /3 days ago/);
    assert.match(render(2 * 604800), /2 weeks ago/);
    assert.match(render(30), /Last washed/);
  } finally {
    Date.now = origNow;
  }
});

test("_renderLastWashed: hidden for a future timestamp (unparseable guard)", () => {
  const NOW = 1_700_000_000_000;
  const origNow = Date.now;
  Date.now = () => NOW;
  try {
    const future = new Date(NOW + 3600 * 1000).toISOString();
    assert.equal(Card.prototype._renderLastWashed.call(lastWashedSelf(future)), NOTHING);
    assert.equal(Card.prototype._renderLastWashed.call(lastWashedSelf("not-a-date")), NOTHING);
  } finally {
    Date.now = origNow;
  }
});

// ── getCardSize: last-washed section adds 1 ───────────────────────────────────

test("getCardSize: enabling last_washed adds 1", () => {
  const size = Card.prototype.getCardSize.call({
    _config: { entity: "binary_sensor.x_can_wash", show_last_washed: true },
    _mergedConfig: Card.prototype._mergedConfig,
  });
  assert.equal(size, 8); // 7 default + 1 last_washed
});

// ── _renderDiagnostics (enriched rows from real attributes) ───────────────────

test("_renderDiagnostics: renders enriched rows from the real attribute names", () => {
  const stateObj = {
    attributes: {
      reason: "rain",
      score: 64,
      days_until_wash: 2,
      days_analyzed: 3,
      decision_details: { blocking_days: ["2026-06-12", "2026-06-13"] },
      precip_total_mm: 4.6,
      max_wind_kmh: 42,
      freeze_risk: false,
      worst_condition: "lightning-rainy",
      min_temp: 12,
      max_temp: 24,
      active_weather_entity: "weather.home",
    },
  };
  const self = {
    hass: { states: { "weather.home": { attributes: { friendly_name: "Home Weather" } } } },
  };
  const html = Card.prototype._renderDiagnostics.call(self, stateObj, false).__html;
  // Rows are icon · label · value-chip. Assert the label/value pairing via the
  // class-tagged spans (markup may sit between them).
  const row = (label, value) =>
    new RegExp(`ww-diag-label">${label}<[\\s\\S]*?ww-diag-value[^>]*>${value}<`);
  assert.match(html, row("Reason", "Rain in forecast"));
  assert.match(html, row("Score", "64 / 100"));
  assert.match(html, /ww-diag-value warn[^>]*>64 \/ 100</, "mid score (34–66) → warn tone");
  assert.match(html, row("Days until wash", "2"));
  assert.match(html, row("Blocking days", "2"));
  assert.match(html, row("Total precip", "4\\.6 mm"));
  assert.match(html, row("Max wind", "42 km/h"));
  assert.match(html, row("Freeze risk", "No"));
  assert.match(html, /ww-diag-value muted[^>]*>No</, "freeze_risk false → muted tone");
  assert.match(html, row("Worst condition", "Lightning rainy"));
  assert.match(html, /ww-diag-value bad[^>]*>Lightning rainy</, "severe condition → bad tone");
  // Temperature is merged into a single min–max range row.
  assert.match(html, row("Temperature", "12 – 24 °C"));
  assert.doesNotMatch(html, /Min temp|Max temp/, "min/max merged, not separate rows");
  // A group divider separates verdict from forecast metrics.
  assert.match(html, /ww-diag-divider/);
  // Weather source is a clickable chip-link showing the provider's friendly name.
  assert.match(html, /ww-diag-label">Weather source</);
  assert.match(html, /class="ww-diag-value ww-link"[\s\S]*?>Home Weather</);
});

test("_renderDiagnostics: score tones (ok ≥67, warn ≥34, bad <34)", () => {
  const h = (score) =>
    Card.prototype._renderDiagnostics.call(
      { hass: { states: {} } }, { attributes: { score } }, false
    ).__html;
  assert.match(h(80), /ww-diag-value ok[^>]*>80 \/ 100</);
  assert.match(h(50), /ww-diag-value warn[^>]*>50 \/ 100</);
  assert.match(h(10), /ww-diag-value bad[^>]*>10 \/ 100</);
});

test("_renderDiagnostics: worst-condition tone — severe→bad, mild→warn", () => {
  const h = (cond) =>
    Card.prototype._renderDiagnostics.call(
      { hass: { states: {} } }, { attributes: { worst_condition: cond } }, false
    ).__html;
  // Severe (BAD_CONDITION_SEVERITY ≥ 0.9): pouring / hail / lightning-rainy / exceptional.
  assert.match(h("pouring"), /ww-diag-value bad[^>]*>Pouring</);
  assert.match(h("hail"), /ww-diag-value bad[^>]*>Hail</);
  // Milder bad condition stays amber.
  assert.match(h("rainy"), /ww-diag-value warn[^>]*>Rainy</);
  assert.match(h("snowy"), /ww-diag-value warn[^>]*>Snowy</);
});

test("_renderDiagnostics: weather source falls back to entity id when no friendly name", () => {
  const html = Card.prototype._renderDiagnostics.call(
    { hass: { states: {} } },
    { attributes: { active_weather_entity: "weather.raw_id" } },
    false
  ).__html;
  assert.match(html, /class="ww-diag-value ww-link"[\s\S]*?>weather\.raw_id</);
});

test("_moreInfo: dispatches a bubbling, composed hass-more-info event", () => {
  const events = [];
  const self = { dispatchEvent: (e) => events.push(e) };
  Card.prototype._moreInfo.call(self, "weather.home");
  assert.equal(events.length, 1);
  assert.equal(events[0].type, "hass-more-info");
  assert.deepEqual(events[0].detail, { entityId: "weather.home" });
  assert.equal(events[0].bubbles, true);
  assert.equal(events[0].composed, true);
});

test("_moreInfo: no-op without an entity id", () => {
  const events = [];
  Card.prototype._moreInfo.call({ dispatchEvent: (e) => events.push(e) }, "");
  assert.equal(events.length, 0);
});

test("_renderDiagnostics: days-until-wash 0 renders as Today", () => {
  const html = Card.prototype._renderDiagnostics.call(
    { hass: { states: {} } }, { attributes: { days_until_wash: 0 } }, false
  ).__html;
  assert.match(html, /ww-diag-label">Days until wash<[\s\S]*?ww-diag-value[^>]*>Today</);
});

test("_renderDiagnostics: absent aggregate attributes produce no rows", () => {
  // Empty-forecast / no-data: only days_analyzed present → one row, no blanks.
  const html = Card.prototype._renderDiagnostics.call(
    { hass: { states: {} } }, { attributes: { days_analyzed: 0 } }, false
  ).__html;
  assert.doesNotMatch(html, /Total precip|Max wind|Worst condition|Min temp|Weather source/);
});
