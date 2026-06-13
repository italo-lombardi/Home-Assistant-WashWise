"""Button platform for WashWise.

Exposes one button per config entry:

* ``mark_washed`` -- append a manual entry to the wash log.

Snoozing and clearing snooze are service calls (``washwise.snooze`` / ``washwise.clear_snooze``).
The coordinator recomputes automatically whenever the active weather entity changes state.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_CATEGORY, DOMAIN
from .coordinator import WashWiseCoordinator
from .device import device_info


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Instantiate the WashWise button for a config entry."""
    coordinator: WashWiseCoordinator = hass.data[DOMAIN][entry.entry_id]

    async_add_entities(
        [
            WashWiseMarkWashedButton(coordinator, entry),
        ]
    )


class _WashWiseButtonBase(CoordinatorEntity[WashWiseCoordinator], ButtonEntity):
    """Common wiring shared by every WashWise button."""

    _attr_has_entity_name = True
    _KEY: str = ""

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Anchor the button to the coordinator and shared device."""
        super().__init__(coordinator)
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{self._KEY}"
        self._attr_translation_key = self._KEY
        self._attr_device_info = device_info(entry)


class WashWiseMarkWashedButton(_WashWiseButtonBase):
    """Append a manual entry to the persisted wash log."""

    _KEY = "mark_washed"

    def __init__(self, coordinator: WashWiseCoordinator, entry: ConfigEntry) -> None:
        """Use irrigation-specific label when category is garden_irrigation."""
        super().__init__(coordinator, entry)
        if entry.data.get(CONF_CATEGORY) == "garden_irrigation":
            self._attr_translation_key = "mark_irrigated"

    async def async_press(self) -> None:
        """Record a wash and refresh the coordinator."""
        await self.coordinator.async_mark_washed()


__all__ = [
    "WashWiseMarkWashedButton",
    "async_setup_entry",
]
