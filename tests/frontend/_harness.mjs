// Shared test harness for the WashWise card.
//
// The card (../../custom_components/washwise/frontend/washwise-card.js) is a
// no-build LitElement served to the browser as `type: module`. It derives its
// LitElement base at module-load time from
//   Object.getPrototypeOf(customElements.get("home-assistant-main"))
// and reads `.prototype.html` / `.css` / `.nothing` off it. In node none of
// those globals exist, so importing the card bare throws.
//
// We stub just enough of the browser before importing the card, then capture
// the card classes from their own customElements.define() calls (they are not
// exported — and must not be, since exporting would mean editing the shipped
// file).
//
// The html stub joins the template's `${}` interpolations with String.raw so a
// rendered fragment's text is assertable. nothing is a unique sentinel so tests
// can assert "render was hidden". window.customCards is stubbed because the card
// push()es its registration there at import. We deliberately do NOT stub `css`:
// leaving LitElement.prototype.css undefined makes the card fall into its own
// CSSResult fallback (washwise-card.js:31-53), so that shipped branch runs — and
// is exercised — under the harness rather than being short-circuited away.

export const NOTHING = "__NOTHING__";

// Flatten a tagged-template call to its rendered text. Nested html`` fragments
// (produced by the same stub) carry a __html string; splice those in. Arrays of
// fragments (e.g. .map() over forecast days) are joined.
function htmlTag(strings, ...values) {
  const flat = values.map((v) => flatten(v));
  return { __html: String.raw({ raw: strings }, ...flat) };
}

function flatten(v) {
  if (v == null) return "";
  if (Array.isArray(v)) return v.map(flatten).join("");
  if (typeof v === "object" && "__html" in v) return v.__html;
  return v;
}

// The card does `LitElement = Object.getPrototypeOf(customElements.get(...))`,
// so get() must return a subclass whose prototype (the superclass) carries the
// Lit members. class Sub extends Base ⇒ Object.getPrototypeOf(Sub) === Base.
class LitBase {}
LitBase.prototype.html = htmlTag;
LitBase.prototype.nothing = NOTHING;
class HaMainStub extends LitBase {}

const defined = {};
globalThis.window = globalThis.window || {};
globalThis.window.customCards = globalThis.window.customCards || [];
globalThis.customElements = {
  get: () => HaMainStub,
  define: (name, cls) => {
    defined[name] = cls;
  },
};
// The card logs a banner at import; keep test output clean.
globalThis.console.info = () => {};

await import("../../custom_components/washwise/frontend/washwise-card.js");

export const Card = defined["washwise-card"];
export const Editor = defined["washwise-card-editor"];
if (!Card) throw new Error("card class not captured from customElements.define");
if (!Editor) throw new Error("editor class not captured from customElements.define");
