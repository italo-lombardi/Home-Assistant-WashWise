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

test("getCardSize: all defaults on sums to 7", () => {
  // base 2 + gauge 1 + reason 1 + forecast 2 + diagnostics 1 = 7
  const size = Card.prototype.getCardSize.call({
    _config: { entity: "binary_sensor.x_can_wash" },
    _mergedConfig: Card.prototype._mergedConfig,
  });
  assert.equal(size, 7);
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
  const changed = new Map();
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
  assert.match(frag, /🌧️/, "rainy → rain emoji");
  assert.match(frag, /☀️/, "sunny → sun emoji");
  assert.match(frag, /4\.3mm/, "precip rounded to one decimal");
  assert.match(frag, /0\.0mm/);
  assert.match(frag, /ww-day bad/, "blocked:true → bad class");
  assert.match(frag, /ww-day ok/, "blocked:false → ok class");
});

test("_renderForecastStrip: unknown condition falls back to first 2 chars", () => {
  const frag = Card.prototype._renderForecastStrip.call(
    {},
    { attributes: { forecast_summary: [{ date: "2026-01-15", condition: "zzzzz" }] } }
  ).__html;
  assert.match(frag, /zz/, "unknown condition → condition.slice(0,2)");
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
