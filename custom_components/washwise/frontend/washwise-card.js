/**
 * WashWise Card v0.1.0
 * Custom Lovelace card for the Home Assistant WashWise integration.
 * Single-file, hand-coded, no build step.
 */

const CARD_VERSION = "0.1.0";

console.info(
  `%c WASHWISE-CARD %c v${CARD_VERSION} %c — github.com/italo-lombardi/Home-Assistant-WashWise `,
  "color: white; background: #2e7d32; font-weight: bold; padding: 2px 6px; border-radius: 3px 0 0 3px;",
  "color: #2e7d32; background: #e8f5e9; font-weight: bold; padding: 2px 6px;",
  "color: #9e9e9e; background: #e8f5e9; padding: 2px 6px; border-radius: 0 3px 3px 0;"
);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "washwise-card",
  name: "WashWise Card",
  description: "Display the WashWise wash advisor verdict, score, forecast and diagnostics.",
  preview: true,
  documentationURL: "https://github.com/italo-lombardi/Home-Assistant-WashWise",
});

// Canonical no-build LitElement bootstrap — matches thomasloven/lovelace-card-tools.
const LitElement = Object.getPrototypeOf(
  customElements.get("home-assistant-main") || customElements.get("hui-view")
);
const html = LitElement.prototype.html;
const nothing = LitElement.prototype.nothing ?? "";
const css = LitElement.prototype.css || (() => {
  class CSSResult {
    constructor(cssText) {
      this.cssText = cssText;
      this._styleSheet = null;
    }
    get styleSheet() {
      if (this._styleSheet === null && window.CSSStyleSheet) {
        try {
          this._styleSheet = new CSSStyleSheet();
          this._styleSheet.replaceSync(this.cssText);
        } catch (e) {
          this._styleSheet = null;
        }
      }
      return this._styleSheet;
    }
    toString() { return this.cssText; }
  }
  return (strings, ...values) => new CSSResult(
    strings.reduce((acc, str, i) => acc + str + (values[i] != null ? String(values[i]) : ""), "")
  );
})();

// ── Styles ──────────────────────────────────────────────────────────────────

const cardStyles = css`
  :host {
    --ww-accent: var(--washwise-accent, #2e7d32);
    --ww-bad: var(--washwise-bad, #c62828);
    --ww-radius: 20px;
    --ww-bg: var(--ha-card-background, var(--card-background-color, #fff));
    --ww-fg: var(--primary-text-color, #1a1a1a);
    --ww-fg-muted: var(--secondary-text-color, #6b6b6b);
    --ww-divider: var(--divider-color, rgba(0, 0, 0, 0.12));
    --ww-pad: 16px;
    --ww-gap: 12px;
    display: block;
    color: var(--ww-fg);
  }

  :host([data-theme='dark']) {
    --ww-bg: #1f1f23;
    --ww-fg: #f1f1f1;
    --ww-fg-muted: #aaa;
    --ww-divider: rgba(255, 255, 255, 0.12);
  }

  :host([data-theme='light']) {
    --ww-bg: #ffffff;
    --ww-fg: #1a1a1a;
    --ww-fg-muted: #6b6b6b;
    --ww-divider: rgba(0, 0, 0, 0.12);
  }

  :host([data-compact='true']) {
    --ww-pad: 10px;
    --ww-gap: 8px;
  }

  ha-card {
    background: var(--ww-bg);
    color: var(--ww-fg);
    border-radius: var(--ww-radius);
    overflow: hidden;
  }

  .ww-card {
    padding: var(--ww-pad);
    display: flex;
    flex-direction: column;
    gap: var(--ww-gap);
    box-sizing: border-box;
  }

  .ww-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--ww-gap);
  }

  .ww-title {
    font-size: 1.05rem;
    font-weight: 600;
    margin: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .ww-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border-radius: 999px;
    font-size: 0.85rem;
    font-weight: 600;
    color: #fff;
  }

  .ww-badge.ok { background: var(--ww-accent); }
  .ww-badge.bad { background: var(--ww-bad); }
  .ww-badge.unknown { background: var(--ww-fg-muted); }

  .ww-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 0.92rem;
    color: var(--ww-fg-muted);
  }

  .ww-row strong {
    color: var(--ww-fg);
    font-weight: 500;
  }

  /* Gauge */
  .ww-gauge {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .ww-gauge-bar-track {
    flex: 1 1 auto;
    height: 8px;
    background: var(--ww-divider);
    border-radius: 999px;
    overflow: hidden;
  }

  .ww-gauge-bar-fill {
    height: 100%;
    background: var(--ww-accent);
    border-radius: 999px;
    transition: width 240ms ease-out;
  }

  .ww-gauge-bar-fill.bad { background: var(--ww-bad); }

  .ww-gauge-value {
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    min-width: 4ch;
    text-align: right;
  }

  /* Forecast strip */
  .ww-forecast {
    display: grid;
    grid-auto-flow: column;
    grid-auto-columns: minmax(56px, 1fr);
    gap: 6px;
    overflow-x: auto;
  }

  .ww-day {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    padding: 8px 6px;
    border-radius: calc(var(--ww-radius) * 0.66);
    background: var(--ww-divider);
    color: var(--ww-fg);
    font-size: 0.78rem;
    text-align: center;
  }

  .ww-day.ok {
    background: color-mix(in srgb, var(--ww-accent) 18%, transparent);
  }

  .ww-day.bad {
    background: color-mix(in srgb, var(--ww-bad) 18%, transparent);
  }

  .ww-day .label {
    font-weight: 600;
    color: var(--ww-fg);
  }

  .ww-day .meta {
    color: var(--ww-fg-muted);
    font-variant-numeric: tabular-nums;
  }

  .ww-day .icon {
    font-size: 1.1rem;
    line-height: 1;
  }

  /* Diagnostics */
  details.ww-diagnostics {
    border-top: 1px solid var(--ww-divider);
    padding-top: 8px;
    font-size: 0.85rem;
    color: var(--ww-fg-muted);
  }

  details.ww-diagnostics summary {
    cursor: pointer;
    color: var(--ww-fg);
    font-weight: 500;
    list-style: none;
    user-select: none;
  }

  details.ww-diagnostics summary::-webkit-details-marker { display: none; }

  details.ww-diagnostics dl {
    display: grid;
    grid-template-columns: max-content 1fr;
    gap: 4px 12px;
    margin: 8px 0 0;
  }

  details.ww-diagnostics dt { color: var(--ww-fg-muted); }

  details.ww-diagnostics dd {
    color: var(--ww-fg);
    margin: 0;
    word-break: break-word;
  }

  .ww-error {
    color: var(--ww-bad);
    padding: var(--ww-pad);
    background: color-mix(in srgb, var(--ww-bad) 10%, transparent);
    border-radius: var(--ww-radius);
    font-size: 0.9rem;
  }
`;

// ── Constants & helpers ─────────────────────────────────────────────────────

const DEFAULT_CONFIG = {
  theme: "auto",
  show_score_gauge: true,
  show_reason: true,
  show_forecast_strip: true,
  show_diagnostics: true,
  diagnostics_open: false,
  compact_mode: false,
};

const REASON_MAP = {
  clear: "Clear conditions",
  rain: "Rain in forecast",
  freeze: "Freeze risk",
  snow: "Snow in forecast",
  bad_condition: "Bad weather expected",
  unavailable: "No data",
  snoozed: "Snoozed",
};

const FORECAST_ICON_MAP = {
  sunny: "☀️",
  "clear-night": "🌙",
  cloudy: "☁️",
  partlycloudy: "⛅",
  rainy: "🌧️",
  pouring: "🌧️",
  "lightning-rainy": "⛈️",
  snowy: "❄️",
  "snowy-rainy": "🌨️",
  hail: "🧊",
  fog: "🌫️",
  windy: "💨",
  exceptional: "⚠️",
};

function clampInt(raw, min, max, fallback) {
  if (raw === undefined || raw === null || raw === "") return fallback;
  const n = typeof raw === "number" ? raw : Number(raw);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.trunc(n)));
}

function forecastIcon(condition) {
  if (!condition) return "·";
  return FORECAST_ICON_MAP[condition] ?? condition.slice(0, 2);
}

function shortDay(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(5, 10);
  return d.toLocaleDateString(undefined, { weekday: "short" });
}

function prettyReason(key) {
  if (!key) return "—";
  return REASON_MAP[key] ?? key;
}

function _stripEntitySuffix(name) {
  if (!name) return name;
  // HA appends the entity translation name (e.g. "Can wash") to the device name.
  // Strip trailing " Can wash" (case-insensitive) to get the device/config name.
  return name.replace(/\s+can\s+wash$/i, "").trim() || name;
}

// Build a string for an inline `style="..."` attribute from an object.
function buildStyle(obj) {
  return Object.entries(obj)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${k}: ${v}`)
    .join("; ");
}

// ── Card class ──────────────────────────────────────────────────────────────

class WashWiseCard extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      _config: { state: true },
    };
  }

  static get styles() {
    return cardStyles;
  }

  static getConfigElement() {
    return document.createElement("washwise-card-editor");
  }

  static getStubConfig(hass) {
    const states = hass?.states ?? {};
    const candidate = Object.keys(states).find(
      (id) => id.startsWith("binary_sensor.") && /can_wash$/.test(id)
    );
    return {
      type: "custom:washwise-card",
      entity: candidate ?? "binary_sensor.washwise_can_wash",
    };
  }

  constructor() {
    super();
    this._config = undefined;
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error("Invalid configuration");
    }
    if (!config.entity || typeof config.entity !== "string") {
      throw new Error("You must define an entity");
    }
    if (!config.entity.startsWith("binary_sensor.")) {
      console.warn(
        `[washwise-card] entity ${config.entity} is not a binary_sensor; rendering anyway`
      );
    }
    this._config = { ...config };
  }

  getCardSize() {
    const cfg = this._mergedConfig();
    let size = 2; // header always shown
    if (cfg.show_score_gauge) size += 1;
    if (cfg.show_reason) size += 1;
    if (cfg.show_forecast_strip) size += 2;
    if (cfg.show_diagnostics) size += 1;
    return size;
  }

  shouldUpdate(changedProps) {
    if (changedProps.has("_config")) return true;
    if (!this.hass || !this._config) return false;
    const oldHass = changedProps.get("hass");
    if (!oldHass) return true;
    const id = this._config.entity;
    return oldHass.states?.[id] !== this.hass.states?.[id];
  }

  render() {
    if (!this.hass || !this._config) {
      return html`<ha-card><div class="ww-card"></div></ha-card>`;
    }
    const cfg = this._mergedConfig();
    const stateObj = this.hass.states[cfg.entity];
    if (!stateObj) {
      return html`
        <ha-card>
          <div class="ww-card">
            <div class="ww-error">Entity ${cfg.entity} not found.</div>
          </div>
        </ha-card>
      `;
    }

    this._applyHostAttributes(cfg);
    const hostStyle = buildStyle(this._hostStyle(cfg));

    const verdict = this._verdict(stateObj);
    const score = this._score(stateObj);
    const friendly =
      cfg.name ?? _stripEntitySuffix(stateObj.attributes.friendly_name) ?? cfg.entity;

    return html`
      <ha-card style=${hostStyle}>
        <div class="ww-card">
          ${this._renderHeader(friendly, verdict)}
          ${cfg.show_score_gauge ? this._renderGauge(score, verdict) : nothing}
          ${cfg.show_reason ? this._renderReason(stateObj) : nothing}
          ${cfg.show_forecast_strip
            ? this._renderForecastStrip(stateObj)
            : nothing}
          ${cfg.show_diagnostics ? this._renderDiagnostics(stateObj, cfg.diagnostics_open) : nothing}
        </div>
      </ha-card>
    `;
  }

  // ── Render helpers ─────────────────────────────────────────────────────

  _renderHeader(title, verdict) {
    const label = verdict === "ok" ? "OK" : verdict === "bad" ? "No" : "—";
    const symbol = verdict === "ok" ? "✅" : verdict === "bad" ? "⛔" : "❓";
    return html`
      <div class="ww-header">
        <h2 class="ww-title">${title}</h2>
        <span class="ww-badge ${verdict}">${symbol}<span>${label}</span></span>
      </div>
    `;
  }

  _renderGauge(score, verdict) {
    const value = score ?? 0;
    const pct = Math.max(0, Math.min(100, value));
    const label = score === null ? "—" : `${Math.round(value)}%`;
    const bad = verdict === "bad";
    return html`
      <div class="ww-gauge" role="progressbar" aria-valuemin="0" aria-valuemax="100"
           aria-valuenow=${Math.round(pct)}>
        <div class="ww-gauge-bar-track">
          <div
            class="ww-gauge-bar-fill ${bad ? "bad" : ""}"
            style="width: ${pct}%"
          ></div>
        </div>
        <div class="ww-gauge-value">${label}</div>
      </div>
    `;
  }

  _renderReason(stateObj) {
    const reason = stateObj.attributes.reason ?? "—";
    return html`
      <div class="ww-row">
        <span>Reason:</span>
        <strong>${prettyReason(reason)}</strong>
      </div>
    `;
  }

  _renderForecastStrip(stateObj) {
    const summary = stateObj.attributes.forecast_summary;
    if (!Array.isArray(summary) || summary.length === 0) return nothing;
    return html`
      <div class="ww-forecast">
        ${summary.map((day, idx) => {
          const blocked = day.blocked ?? null;
          const cls = blocked === false ? "ok" : blocked === true ? "bad" : "";
          const dayLabel = day.date ? shortDay(day.date) : `D${idx + 1}`;
          const precip = day.precipitation_mm ?? day.precipitation;
          return html`
            <div class="ww-day ${cls}" title=${day.condition ?? ""}>
              <span class="label">${dayLabel}</span>
              <span class="icon">${forecastIcon(day.condition)}</span>
              <span class="meta">
                ${typeof precip === "number" ? `${precip.toFixed(1)}mm` : "—"}
              </span>
            </div>
          `;
        })}
      </div>
    `;
  }

  _renderDiagnostics(stateObj, open) {
    const a = stateObj.attributes;
    const rows = [
      ["Active provider", a.active_provider],
      ["Days analyzed", a.days_analyzed],
      ["Total precip", typeof a.precip_total_mm === "number" ? `${a.precip_total_mm} mm` : null],
      ["Freeze risk", typeof a.freeze_risk === "boolean" ? (a.freeze_risk ? "Yes" : "No") : null],
      ["Worst condition", a.worst_condition],
      ["Min temp", typeof a.min_temp === "number" ? `${a.min_temp} °C` : null],
      ["Max temp", typeof a.max_temp === "number" ? `${a.max_temp} °C` : null],
    ];
    const populated = rows.filter(([, v]) => v !== undefined && v !== null && v !== "");
    return html`
      <details class="ww-diagnostics" ?open=${open}>
        <summary>Diagnostics</summary>
        <dl>
          ${populated.map(
            ([k, v]) => html`<dt>${k}</dt><dd>${String(v)}</dd>`
          )}
        </dl>
      </details>
    `;
  }

  // ── Internals ──────────────────────────────────────────────────────────

  _mergedConfig() {
    return { ...DEFAULT_CONFIG, ...(this._config ?? { entity: "" }) };
  }

  _applyHostAttributes(cfg) {
    const theme = cfg.theme ?? "auto";
    let resolved = theme;
    if (theme === "auto") {
      resolved = this.hass?.themes?.darkMode ? "dark" : "light";
    }
    this.setAttribute("data-theme", resolved);
    this.setAttribute("data-compact", cfg.compact_mode ? "true" : "false");
  }

  _hostStyle(cfg) {
    const out = {};
    if (cfg.accent_color) out["--ww-accent"] = cfg.accent_color;
    if (cfg.bad_color) out["--ww-bad"] = cfg.bad_color;
    return out;
  }

  _verdict(stateObj) {
    if (stateObj.state === "on") return "ok";
    if (stateObj.state === "off") return "bad";
    return "unknown";
  }

  _score(stateObj) {
    const s = stateObj.attributes.score;
    if (typeof s === "number" && Number.isFinite(s)) return s;
    if (typeof s === "string" && s.trim() !== "") {
      const n = Number(s);
      return Number.isFinite(n) ? n : null;
    }
    return null;
  }
}

customElements.define("washwise-card", WashWiseCard);

// ── Editor ──────────────────────────────────────────────────────────────────

const editorStyles = css`
  :host {
    display: block;
    color: var(--primary-text-color, #1a1a1a);
  }

  .form {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 12px 16px;
  }

  .field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  .field label {
    font-weight: 600;
    font-size: 0.92rem;
  }

  .field .helper {
    color: var(--secondary-text-color, #6b6b6b);
    font-size: 0.82rem;
    line-height: 1.35;
  }

  .field input[type='text'],
  .field input[type='color'],
  .field select {
    padding: 8px 10px;
    border-radius: 6px;
    border: 1px solid var(--divider-color, rgba(0, 0, 0, 0.2));
    background: var(--card-background-color, #fff);
    color: inherit;
    font: inherit;
    width: 100%;
    box-sizing: border-box;
  }

  .field input[type='color'] {
    height: 38px;
    padding: 2px;
    cursor: pointer;
  }

  .field.row {
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
  }

  .field.row > div {
    display: flex;
    flex-direction: column;
    flex: 1 1 auto;
  }

  .field.row input[type='checkbox'] {
    flex: 0 0 auto;
    width: 18px;
    height: 18px;
  }

  .group-title {
    font-weight: 700;
    font-size: 0.85rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    color: var(--secondary-text-color, #6b6b6b);
    margin-top: 4px;
  }

  .empty {
    padding: 8px 10px;
    border-radius: 6px;
    border: 1px dashed var(--divider-color, rgba(0, 0, 0, 0.2));
    color: var(--secondary-text-color, #6b6b6b);
    font-size: 0.85rem;
  }
`;

class WashWiseCardEditor extends LitElement {
  static get properties() {
    return {
      hass: { attribute: false },
      _config: { state: true },
    };
  }

  static get styles() {
    return editorStyles;
  }

  constructor() {
    super();
    this._config = { entity: "" };
  }

  setConfig(config) {
    this._config = { ...config };
  }

  // ── Instance discovery ─────────────────────────────────────────────────
  // Find all WashWise instances by looking for can_wash binary sensors.
  // Each returns { entity, label } where label is the friendly name.

  _washWiseInstances() {
    const states = this.hass?.states ?? {};
    const out = [];
    for (const [id, state] of Object.entries(states)) {
      if (!id.startsWith("binary_sensor.")) continue;
      if (!/_can_wash$/.test(id)) continue;
      const attrs = state?.attributes ?? {};
      // Must have forecast_summary or score to confirm it's a WashWise entity.
      if (!Array.isArray(attrs.forecast_summary) && attrs.score === undefined) continue;
      out.push({
        entity: id,
        label: attrs.friendly_name || id,
      });
    }
    out.sort((a, b) => a.label.localeCompare(b.label));
    return out;
  }

  render() {
    if (!this._config) return html``;
    const cfg = this._config;
    const instances = this._washWiseInstances();
    const currentEntity = cfg.entity || "";
    const currentInList = instances.some((i) => i.entity === currentEntity);

    return html`
      <div class="form">
        <div class="group-title">Source</div>

        <div class="field">
          <label for="ww-entity">WashWise configuration (required)</label>
          ${instances.length === 0
            ? html`<div class="empty">
                No WashWise entities found. Add the WashWise integration first
                (Settings → Devices &amp; Services → Add Integration → WashWise).
              </div>`
            : html`<select
                id="ww-entity"
                .value=${currentEntity}
                @change=${(e) => this._set("entity", e.target.value)}
              >
                <option value="" ?selected=${!currentEntity}>— pick a WashWise configuration —</option>
                ${instances.map(
                  (i) => html`<option value=${i.entity} ?selected=${currentEntity === i.entity}>
                    ${i.label}
                  </option>`
                )}
                ${currentEntity && !currentInList
                  ? html`<option value=${currentEntity} selected>${currentEntity}</option>`
                  : nothing}
              </select>`}
          <span class="helper">
            Select the WashWise configuration to display. Each configuration entry
            appears as its own option.
          </span>
        </div>

        <div class="field">
          <label for="ww-title">Title (optional)</label>
          <input
            id="ww-title"
            type="text"
            .value=${cfg.name ?? ""}
            placeholder="Defaults to entity friendly name"
            @input=${(e) => this._set("name", e.target.value || undefined)}
          />
          <span class="helper">Optional override for the card header title.</span>
        </div>

        <div class="group-title">Appearance</div>

        <div class="field">
          <label for="ww-theme">Theme</label>
          <select
            id="ww-theme"
            .value=${cfg.theme ?? "auto"}
            @change=${(e) => this._set("theme", e.target.value)}
          >
            <option value="auto" ?selected=${(cfg.theme ?? "auto") === "auto"}>
              Auto (follow Home Assistant)
            </option>
            <option value="light" ?selected=${cfg.theme === "light"}>Light</option>
            <option value="dark" ?selected=${cfg.theme === "dark"}>Dark</option>
          </select>
          <span class="helper">
            Auto matches the active HA theme; pick light or dark to lock the card.
          </span>
        </div>

        <div class="field">
          <label for="ww-accent">Accent colour</label>
          <input
            id="ww-accent"
            type="color"
            .value=${cfg.accent_color ?? "#2e7d32"}
            @input=${(e) => this._set("accent_color", e.target.value)}
          />
          <span class="helper">
            Used for the positive verdict badge and score gauge fill.
          </span>
        </div>

        <div class="field">
          <label for="ww-bad">Bad colour</label>
          <input
            id="ww-bad"
            type="color"
            .value=${cfg.bad_color ?? "#c62828"}
            @input=${(e) => this._set("bad_color", e.target.value)}
          />
          <span class="helper">
            Used for the negative verdict badge and blocking forecast days.
          </span>
        </div>

        <div class="group-title">Sections</div>

        ${this._renderToggle("show_score_gauge", "Show score gauge",
          "Display the 0–100 score as a horizontal bar.", true)}
        ${this._renderToggle("show_reason", "Show reason",
          "Display the human-readable reason behind the current verdict.", true)}
        ${this._renderToggle("show_forecast_strip", "Show forecast strip",
          "Display per-day forecast tiles below the verdict.", true)}
        ${this._renderToggle("show_diagnostics", "Show diagnostics",
          "Append a diagnostics block (provider, totals, temperatures, etc.).", true)}
        ${this._renderToggle("diagnostics_open", "Diagnostics open by default",
          "When enabled, the diagnostics section starts expanded. Otherwise it starts collapsed.", false)}
        ${this._renderToggle("compact_mode", "Compact mode",
          "Tighten paddings and typography to fit denser dashboards.", false)}
      </div>
    `;
  }

  _renderToggle(key, label, helper, fallback) {
    const current = this._config?.[key] ?? fallback;
    const id = `ww-${String(key)}`;
    return html`
      <div class="field row">
        <div>
          <label for=${id}>${label}</label>
          <span class="helper">${helper}</span>
        </div>
        <input
          id=${id}
          type="checkbox"
          .checked=${current === true}
          @change=${(e) => this._set(key, e.target.checked)}
        />
      </div>
    `;
  }

  _set(key, value) {
    if (!this._config) return;
    const next = { ...this._config };
    if (value === undefined || value === "" || value === null) {
      delete next[key];
    } else {
      next[key] = value;
    }
    this._config = next;
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: { config: next },
        bubbles: true,
        composed: true,
      })
    );
  }
}

customElements.define("washwise-card-editor", WashWiseCardEditor);
