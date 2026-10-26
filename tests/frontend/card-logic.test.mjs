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

// ── Actions: entry_id resolution ──────────────────────────────────────────────

test("_entryId: resolves config_entry_id from hass entity registry", () => {
  const self = {
    _config: { entity: "binary_sensor.x_can_wash" },
    hass: { entities: { "binary_sensor.x_can_wash": { config_entry_id: "abc123" } } },
  };
  assert.equal(Card.prototype._entryId.call(self), "abc123");
});

test("_entryId: null when entity, registry entry, or hass missing", () => {
  assert.equal(Card.prototype._entryId.call({ _config: {} }), null);
  assert.equal(
    Card.prototype._entryId.call({ _config: { entity: "binary_sensor.x_can_wash" }, hass: {} }),
    null,
    "no entities map"
  );
  assert.equal(
    Card.prototype._entryId.call({
      _config: { entity: "binary_sensor.x_can_wash" },
      hass: { entities: {} },
    }),
    null,
    "entity not in registry"
  );
});

// ── Actions: render (context-aware, hide without entry_id) ────────────────────

function actionsSelf(entryId) {
  return {
    _config: { entity: "binary_sensor.x_can_wash" },
    hass: entryId
      ? { entities: { "binary_sensor.x_can_wash": { config_entry_id: entryId } } }
      : {},
    _entryId: Card.prototype._entryId,
  };
}

test("_renderActions: hidden when entry_id cannot be resolved", () => {
  const frag = Card.prototype._renderActions.call(actionsSelf(null), { state: "on" }, false);
  assert.equal(frag, NOTHING);
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

test("_renderActions: Mark washed disabled when state unavailable/unknown", () => {
  // The html stub renders Lit's ?disabled boolean binding literally as
  // "?disabled=<bool>" (it does not model attribute removal), so assert on the
  // bound value rather than the attribute's presence.
  const u = Card.prototype._renderActions.call(actionsSelf("e1"), { state: "unavailable" }, false).__html;
  assert.match(u, /\?disabled=true/, "unavailable → disabled bound true");
  const k = Card.prototype._renderActions.call(actionsSelf("e1"), { state: "unknown" }, false).__html;
  assert.match(k, /\?disabled=true/, "unknown → disabled bound true");
  const ok = Card.prototype._renderActions.call(actionsSelf("e1"), { state: "on" }, false).__html;
  assert.match(ok, /\?disabled=false/, "available → disabled bound false");
});

// ── Actions: handlers (confirm gate + service call shape) ─────────────────────

function captureCall(fn) {
  const calls = [];
  const self = {
    _confirm: () => true,
    _callService: (service, data) => calls.push({ service, data }),
  };
  fn(self);
  return calls;
}

test("_onMarkWashed: confirmed → calls mark_washed with entry_id", () => {
  const calls = captureCall((self) => Card.prototype._onMarkWashed.call(self, "e9"));
  assert.deepEqual(calls, [{ service: "mark_washed", data: { entry_id: "e9" } }]);
});

test("_onMarkWashed: declined confirm → no service call", () => {
  const calls = [];
  const self = {
    _confirm: () => false,
    _callService: (service, data) => calls.push({ service, data }),
  };
  Card.prototype._onMarkWashed.call(self, "e9");
  assert.deepEqual(calls, [], "confirm returning false aborts the call");
});

test("_onSnooze: fires snooze with entry_id only (no hours → backend default)", () => {
  const calls = captureCall((self) => Card.prototype._onSnooze.call(self, "e9"));
  assert.deepEqual(calls, [{ service: "snooze", data: { entry_id: "e9" } }]);
});

test("_onClearSnooze: fires clear_snooze with entry_id", () => {
  const calls = captureCall((self) => Card.prototype._onClearSnooze.call(self, "e9"));
  assert.deepEqual(calls, [{ service: "clear_snooze", data: { entry_id: "e9" } }]);
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
