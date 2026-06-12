"""Tests for WashWise integration setup, unload, remove, and migrate."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.washwise import (
    _CARD_INSTALLED_KEY,
    _async_install_card,
    _update_listener,
    async_migrate_entry,
    async_remove_entry,
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
    PLATFORMS,
)
from custom_components.washwise.coordinator import WashWiseCoordinator

# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------


async def test_async_setup_entry_success(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Setup stores the coordinator in hass.data and forwards to platforms."""
    mock_config_entry.add_to_hass(hass)

    with (
        patch.object(
            WashWiseCoordinator,
            "async_config_entry_first_refresh",
            new_callable=AsyncMock,
        ) as mock_refresh,
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            new_callable=AsyncMock,
        ) as mock_forward,
        patch(
            "custom_components.washwise.async_register_services",
            new_callable=AsyncMock,
        ) as mock_register_services,
        patch(
            "custom_components.washwise._async_install_card",
            new_callable=AsyncMock,
        ) as mock_register_frontend,
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True
    assert DOMAIN in hass.data
    assert mock_config_entry.entry_id in hass.data[DOMAIN]
    assert isinstance(
        hass.data[DOMAIN][mock_config_entry.entry_id],
        WashWiseCoordinator,
    )
    mock_refresh.assert_called_once()
    mock_forward.assert_called_once_with(mock_config_entry, PLATFORMS)
    mock_register_services.assert_called_once_with(hass)
    mock_register_frontend.assert_called_once_with(hass)


# ---------------------------------------------------------------------------
# async_unload_entry
# ---------------------------------------------------------------------------


async def test_async_unload_entry_removes_coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Unload pops the coordinator and forwards platform unload."""
    mock_config_entry.add_to_hass(hass)

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
        await async_setup_entry(hass, mock_config_entry)

    assert mock_config_entry.entry_id in hass.data[DOMAIN]

    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new_callable=AsyncMock,
            return_value=True,
        ) as mock_unload,
        patch(
            "custom_components.washwise.async_unregister_services",
            new_callable=MagicMock,
        ) as mock_unregister,
    ):
        result = await async_unload_entry(hass, mock_config_entry)

    assert result is True
    assert mock_config_entry.entry_id not in hass.data[DOMAIN]
    mock_unload.assert_called_once_with(mock_config_entry, PLATFORMS)
    # Last entry removed → services unregistered.
    mock_unregister.assert_called_once_with(hass)


async def test_async_unload_entry_failure_keeps_coordinator(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """When platform unload fails, coordinator stays and we return False."""
    mock_config_entry.add_to_hass(hass)

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
        await async_setup_entry(hass, mock_config_entry)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new_callable=AsyncMock,
        return_value=False,
    ):
        result = await async_unload_entry(hass, mock_config_entry)

    assert result is False
    assert mock_config_entry.entry_id in hass.data[DOMAIN]


# ---------------------------------------------------------------------------
# async_remove_entry
# ---------------------------------------------------------------------------


async def test_async_remove_entry_calls_store_remove(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """async_remove_entry routes through WashWiseStore.remove()."""
    with patch("custom_components.washwise.WashWiseStore") as mock_store_cls:
        instance = MagicMock()
        instance.remove = AsyncMock()
        mock_store_cls.return_value = instance

        await async_remove_entry(hass, mock_config_entry)

    mock_store_cls.assert_called_once_with(hass, mock_config_entry.entry_id)
    instance.remove.assert_called_once()


async def test_async_remove_entry_swallows_storage_errors(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry, caplog
) -> None:
    """A failing Store.remove must not raise out of async_remove_entry."""
    import logging

    with patch("custom_components.washwise.WashWiseStore") as mock_store_cls:
        instance = MagicMock()
        instance.remove = AsyncMock(side_effect=OSError("disk gone"))
        mock_store_cls.return_value = instance

        with caplog.at_level(logging.ERROR):
            # Must not raise.
            await async_remove_entry(hass, mock_config_entry)

    assert "Failed to remove WashWise storage" in caplog.text


# ---------------------------------------------------------------------------
# Update listener triggers reload
# ---------------------------------------------------------------------------


async def test_update_listener_triggers_reload(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """_update_listener calls hass.config_entries.async_reload(entry_id)."""
    mock_config_entry.add_to_hass(hass)

    with patch.object(
        hass.config_entries,
        "async_reload",
        new_callable=AsyncMock,
    ) as mock_reload:
        await _update_listener(hass, mock_config_entry)

    mock_reload.assert_called_once_with(mock_config_entry.entry_id)


async def test_update_listener_registered_during_setup(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """async_setup_entry wires the update listener and updating the entry triggers reload."""
    mock_config_entry.add_to_hass(hass)

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
        await async_setup_entry(hass, mock_config_entry)

    with patch.object(
        hass.config_entries,
        "async_reload",
        new_callable=AsyncMock,
    ) as mock_reload:
        # Mutate options → triggers update listener.
        hass.config_entries.async_update_entry(mock_config_entry, options={"changed": True})
        await hass.async_block_till_done()

    mock_reload.assert_called_once_with(mock_config_entry.entry_id)


# ---------------------------------------------------------------------------
# Migration stub
# ---------------------------------------------------------------------------


async def test_async_migrate_entry_returns_true(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """The v1 migration stub always returns True."""
    result = await async_migrate_entry(hass, mock_config_entry)
    assert result is True


# ---------------------------------------------------------------------------
# Frontend resource — registered once across multiple entries
# ---------------------------------------------------------------------------


def _make_entry(entry_id: str, name: str) -> MockConfigEntry:
    """Build a MockConfigEntry with a unique id and name."""
    return MockConfigEntry(
        version=1,
        domain=DOMAIN,
        title=name,
        data={
            CONF_NAME: name,
            CONF_WEATHER_ENTITIES: ["weather.home"],
            CONF_CATEGORY: DEFAULT_CATEGORY,
            CONF_CUSTOMIZE_THRESHOLDS: False,
            CONF_FORECAST_TYPE: DEFAULT_FORECAST_TYPE,
        },
        options={},
        entry_id=entry_id,
        unique_id=f"{DOMAIN}_{entry_id}",
    )


def _patch_card_path(exists: bool = True):
    """Return a context manager patching the Path(__file__) chain in __init__."""
    fake_source = MagicMock()
    fake_source.exists.return_value = exists
    fake_source.__str__ = lambda self: "/fake/path/washwise-card.js"
    chain = MagicMock()
    chain.__truediv__.return_value.__truediv__.return_value = fake_source
    chain_root = MagicMock()
    chain_root.parent = chain
    return patch("custom_components.washwise.Path", return_value=chain_root)


def _attach_http(hass: HomeAssistant) -> AsyncMock:
    """Attach a fake hass.http with async_register_static_paths AsyncMock."""
    fake_http = MagicMock()
    fake_http.async_register_static_paths = AsyncMock()
    hass.http = fake_http
    return fake_http.async_register_static_paths


async def test_async_install_card_runs_once(hass: HomeAssistant) -> None:
    """Two calls to _async_install_card only register once."""
    hass.data.setdefault(DOMAIN, {})
    mock_register_static = _attach_http(hass)

    fake_resources = MagicMock()
    fake_resources.loaded = True
    fake_resources.async_items = MagicMock(return_value=[])
    fake_resources.async_create_item = AsyncMock()
    hass.data["lovelace"] = MagicMock(resources=fake_resources)

    with (
        _patch_card_path(exists=True),
        patch("custom_components.washwise._get_version", return_value="0.1.0"),
    ):
        await _async_install_card(hass)
        await _async_install_card(hass)

    assert mock_register_static.call_count == 1
    assert fake_resources.async_create_item.call_count == 1
    assert hass.data[DOMAIN][_CARD_INSTALLED_KEY] is True


async def test_async_install_card_once_across_multiple_entries(
    hass: HomeAssistant,
) -> None:
    """Setting up two entries registers the frontend resource exactly once."""
    entry_a = _make_entry("entry_a", "Wash A")
    entry_b = _make_entry("entry_b", "Wash B")
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    mock_register_static = _attach_http(hass)

    fake_resources = MagicMock()
    fake_resources.loaded = True
    fake_resources.async_items = MagicMock(return_value=[])
    fake_resources.async_create_item = AsyncMock()
    hass.data.setdefault("lovelace", MagicMock(resources=fake_resources))

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
        _patch_card_path(exists=True),
        patch("custom_components.washwise._get_version", return_value="0.1.0"),
    ):
        await async_setup_entry(hass, entry_a)
        await async_setup_entry(hass, entry_b)

    assert mock_register_static.call_count == 1
    assert fake_resources.async_create_item.call_count == 1


async def test_async_install_card_skips_when_card_missing(hass: HomeAssistant, caplog) -> None:
    """Missing card JS file logs a warning and does not register anything."""
    import logging

    hass.data.setdefault(DOMAIN, {})
    mock_register_static = _attach_http(hass)

    with (
        _patch_card_path(exists=False),
        caplog.at_level(logging.WARNING),
    ):
        await _async_install_card(hass)

    assert "card js not found" in caplog.text.lower()
    mock_register_static.assert_not_called()
    assert _CARD_INSTALLED_KEY not in hass.data.get(DOMAIN, {})


# ---------------------------------------------------------------------------
# Platform list sanity check
# ---------------------------------------------------------------------------


def test_platforms_defined() -> None:
    """All three expected platforms are declared."""
    assert "binary_sensor" in PLATFORMS
    assert "sensor" in PLATFORMS
    assert "button" in PLATFORMS
    assert "switch" not in PLATFORMS
    assert len(PLATFORMS) == 3
