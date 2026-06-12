"""Shared :class:`DeviceInfo` helper for the WashWise integration.

All platforms (binary_sensor, sensor, button) anchor their entities to the same
device entry by passing identical ``DeviceInfo`` payloads. Centralising the
construction here prevents drift in ``model`` / ``entry_type`` / ``manufacturer``
between platforms — Home Assistant merges device records by ``identifiers`` and
the last-registered platform wins, so divergent payloads produce flaky UI.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import CONF_CATEGORY, DEFAULT_CATEGORY, DOMAIN


def device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the canonical :class:`DeviceInfo` for ``entry``.

    The device name is always prefixed with ``"WashWise "`` so the entities
    HA generates from ``has_entity_name=True`` slugify to ``washwise_<title>``.
    Without the prefix the user-supplied title (``"Kia XCeed"``) would
    collide with entities from other integrations of the same device.
    """
    raw_name = entry.title or entry.data.get("name") or "WashWise"
    name = raw_name if raw_name.lower().startswith("washwise") else f"WashWise {raw_name}"
    category = entry.data.get(CONF_CATEGORY, DEFAULT_CATEGORY)
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=name,
        manufacturer="WashWise",
        model=category,
        entry_type=DeviceEntryType.SERVICE,
    )


__all__ = ["device_info"]
