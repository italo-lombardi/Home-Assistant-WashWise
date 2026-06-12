"""Smoke test for the WashWise Lovelace card bundle."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CARD_JS = ROOT / "custom_components" / "washwise" / "frontend" / "washwise-card.js"
DIST_JS = ROOT / "custom_components" / "washwise" / "frontend_src" / "dist" / "washwise-card.js"


def test_card_bundle_exists_and_is_non_empty() -> None:
    assert CARD_JS.is_file(), f"missing card bundle: {CARD_JS}"
    assert CARD_JS.stat().st_size > 0, "card bundle is empty"


def test_card_bundle_contains_tag_and_registration() -> None:
    text = CARD_JS.read_text(encoding="utf-8")
    assert "washwise-card" in text, "card tag name not found in bundle"
    assert "customCards" in text, "customCards registration not found in bundle"


def test_optional_dist_bundle_exports_class() -> None:
    if not DIST_JS.is_file():
        return
    text = DIST_JS.read_text(encoding="utf-8")
    assert "class" in text, "dist bundle has no class declaration"
    assert "washwise-card" in text, "dist bundle missing card tag name"
