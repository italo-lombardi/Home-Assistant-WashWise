"""Multi-instance tests for WashWise.

Verify that two simultaneous config entries do not collide on:
  * persistent storage keys
  * entity unique_ids
  * teardown side effects (removing one entry leaves the other operational)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.washwise.const import (
    CONF_CATEGORY,
    CONF_CUSTOMIZE_THRESHOLDS,
    CONF_FORECAST_TYPE,
    CONF_NAME,
    CONF_WEATHER_ENTITIES,
    DEFAULT_CATEGORY,
    DEFAULT_FORECAST_TYPE,
    DOMAIN,
    STORAGE_KEY_FMT,
)
from custom_components.washwise.coordinator import WashWiseCoordinator
from custom_components.washwise.storage import WashWiseStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(
    entry_id: str,
    name: str,
    weather_entities: list[str],
    category: str = DEFAULT_CATEGORY,
) -> MockConfigEntry:
    """Build a MockConfigEntry for WashWise."""
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=name,
        data={
            CONF_NAME: name,
            CONF_WEATHER_ENTITIES: weather_entities,
            CONF_CATEGORY: category,
            CONF_CUSTOMIZE_THRESHOLDS: False,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
        },
        options={},
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_{entry_id}",
    )


@pytest.fixture
def two_entries() -> tuple[MockConfigEntry, MockConfigEntry]:
    """Two WashWise entries with overlapping weather entities."""
    entry_a = _make_entry(
        "entry_a",
        "Car",
        weather_entities=["weather.home", "weather.outdoor"],  # primary + backup
        category="car",
    )
    entry_b = _make_entry(
        "entry_b",
        "Bike",
        # Overlaps with entry_a on `weather.home`.
        weather_entities=["weather.home"],
        category="bicycle",
    )
    return entry_a, entry_b


# ---------------------------------------------------------------------------
# Storage isolation
# ---------------------------------------------------------------------------


def test_storage_keys_are_isolated_per_entry() -> None:
    """STORAGE_KEY_FMT yields a distinct key for each entry_id."""
    key_a = STORAGE_KEY_FMT.format(entry_id="entry_a")
    key_b = STORAGE_KEY_FMT.format(entry_id="entry_b")

    assert key_a != key_b
    # Entry id is embedded in the key, guaranteeing namespace separation.
    assert "entry_a" in key_a
    assert "entry_b" in key_b


def test_store_instances_use_different_keys(hass: HomeAssistant) -> None:
    """Two WashWiseStore instances point at different on-disk files."""
    store_a = WashWiseStore(hass, "entry_a")
    store_b = WashWiseStore(hass, "entry_b")

    # Internal Store.key carries the namespaced storage key.
    assert store_a._store.key != store_b._store.key
    assert store_a._store.key == STORAGE_KEY_FMT.format(entry_id="entry_a")
    assert store_b._store.key == STORAGE_KEY_FMT.format(entry_id="entry_b")


# ---------------------------------------------------------------------------
# Coordinator isolation
# ---------------------------------------------------------------------------


async def test_two_entries_register_distinct_coordinators(
    hass: HomeAssistant,
    two_entries: tuple[MockConfigEntry, MockConfigEntry],
) -> None:
    """Both entries land in hass.data under their own keys with their own coordinator."""
    entry_a, entry_b = two_entries
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    with (
        patch.object(
            WashWiseCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.washwise.async_register_services",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.washwise._async_install_card",
            new_callable=AsyncMock,
        ),
    ):
        await async_setup_entry(hass, entry_a)
        await async_setup_entry(hass, entry_b)

    coords = hass.data[DOMAIN]
    assert entry_a.entry_id in coords
    assert entry_b.entry_id in coords

    coord_a: WashWiseCoordinator = coords[entry_a.entry_id]
    coord_b: WashWiseCoordinator = coords[entry_b.entry_id]
    assert coord_a is not coord_b

    # Each coordinator carries its own storage instance pointing at its
    # entry-specific key — no cross-talk.
    assert coord_a._store._store.key != coord_b._store._store.key
    assert coord_a.entry.entry_id == entry_a.entry_id
    assert coord_b.entry.entry_id == entry_b.entry_id


# ---------------------------------------------------------------------------
# Unique_id isolation across entities
# ---------------------------------------------------------------------------


async def test_no_unique_id_collisions_across_entries(
    hass: HomeAssistant,
    two_entries: tuple[MockConfigEntry, MockConfigEntry],
) -> None:
    """Binary sensors (and sensors) emit unique_ids prefixed with entry_id."""
    from custom_components.washwise.binary_sensor import (
        async_setup_entry as binary_setup,
    )

    entry_a, entry_b = two_entries
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    with (
        patch.object(
            WashWiseCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.washwise.async_register_services",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.washwise._async_install_card",
            new_callable=AsyncMock,
        ),
    ):
        await async_setup_entry(hass, entry_a)
        await async_setup_entry(hass, entry_b)

    captured: list[list] = []

    def _capture(entities, update_before_add=False):
        captured.append(list(entities))

    await binary_setup(hass, entry_a, _capture)
    await binary_setup(hass, entry_b, _capture)

    # Flatten unique_ids from both entries' binary sensors.
    unique_ids: list[str] = []
    for batch in captured:
        for ent in batch:
            uid = getattr(ent, "_attr_unique_id", None) or getattr(ent, "unique_id", None)
            assert uid is not None, f"{type(ent).__name__} missing unique_id"
            unique_ids.append(uid)

    # Sanity: at least one entity per entry was created.
    assert any(uid.startswith("entry_a_") for uid in unique_ids)
    assert any(uid.startswith("entry_b_") for uid in unique_ids)

    # No duplicates: entry_id prefix guarantees namespace separation even
    # though the two entries share an upstream weather entity.
    assert len(unique_ids) == len(set(unique_ids))


# ---------------------------------------------------------------------------
# Teardown isolation
# ---------------------------------------------------------------------------


async def test_unloading_one_entry_keeps_other_functional(
    hass: HomeAssistant,
    two_entries: tuple[MockConfigEntry, MockConfigEntry],
) -> None:
    """Removing entry A leaves entry B's coordinator in hass.data untouched."""
    entry_a, entry_b = two_entries
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    with (
        patch.object(
            WashWiseCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.washwise.async_register_services",
            new_callable=AsyncMock,
        ),
        patch(
            "custom_components.washwise._async_install_card",
            new_callable=AsyncMock,
        ),
    ):
        await async_setup_entry(hass, entry_a)
        await async_setup_entry(hass, entry_b)

    coord_b_before = hass.data[DOMAIN][entry_b.entry_id]

    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "custom_components.washwise.async_unregister_services",
            new_callable=MagicMock,
        ) as mock_unregister,
    ):
        result = await async_unload_entry(hass, entry_a)

    assert result is True
    assert entry_a.entry_id not in hass.data[DOMAIN]
    # Entry B remains intact AND is still the same instance — not rebuilt.
    assert entry_b.entry_id in hass.data[DOMAIN]
    assert hass.data[DOMAIN][entry_b.entry_id] is coord_b_before
    # Services should NOT be unregistered while another entry remains loaded.
    mock_unregister.assert_not_called()


async def test_removing_one_entry_only_removes_its_storage(
    hass: HomeAssistant,
    two_entries: tuple[MockConfigEntry, MockConfigEntry],
) -> None:
    """async_remove_entry calls Store.remove for the removed entry only."""
    from custom_components.washwise import async_remove_entry

    entry_a, _entry_b = two_entries

    with patch("custom_components.washwise.WashWiseStore") as mock_store_cls:
        instance = AsyncMock()
        mock_store_cls.return_value = instance

        await async_remove_entry(hass, entry_a)

    # Only entry_a's storage was scoped here; entry_b is untouched.
    mock_store_cls.assert_called_once_with(hass, entry_a.entry_id)
    instance.remove.assert_called_once()
