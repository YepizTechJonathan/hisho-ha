"""Setup, devices, entities, and how the integration follows the fleet."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from freezegun.api import FrozenDateTimeFactory

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.setup import async_setup_component

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hisho.const import DOMAIN

from .conftest import SNAPSHOT_URL, setup_entry

INSTANCE = "0123456789abcdef0123456789abcdef"


def _entity_id(hass: HomeAssistant, platform: str, key: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{INSTANCE}_{key}")
    assert entity_id, f"no {platform} entity {key}"
    return entity_id


def _state(hass: HomeAssistant, platform: str, key: str) -> str:
    return hass.states.get(_entity_id(hass, platform, key)).state


async def _poll(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_setup_and_unload(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The entry loads and unloads cleanly."""
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_unreachable_hisho_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Core down at startup: HA retries instead of failing for good."""
    aioclient_mock.get(SNAPSHOT_URL, status=502)
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A rotated token asks the user for the new one."""
    aioclient_mock.get(SNAPSHOT_URL, status=401)
    await setup_entry(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]


async def test_a_hub_device_and_one_device_per_machine(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Machines hang off the hub with via_device."""
    await setup_entry(hass, config_entry)
    devices = dr.async_get(hass)
    hub = devices.async_get_device_by_identifier((DOMAIN, INSTANCE), config_entry.entry_id)
    assert hub.name == "Hisho" and hub.manufacturer == "Hisho"
    assert hub.entry_type is dr.DeviceEntryType.SERVICE
    assert hub.configuration_url == "https://kihon.example.ts.net"
    assert hub.sw_version == "0.2.0"
    for machine_id, name in (("kihon", "Kihon"), ("souko", "Souko"),
                             ("hashira-ten", "Hashira Ten"), ("hashira-ren", "Hashira Ren")):
        device = devices.async_get_device_by_identifier((DOMAIN, f"{INSTANCE}_{machine_id}"), config_entry.entry_id)
        assert device.name == name
        assert device.via_device_id == hub.id
    kihon = devices.async_get_device_by_identifier((DOMAIN, f"{INSTANCE}_kihon"), config_entry.entry_id)
    assert kihon.model == "Kihon (Hisho Core + dashboard + agent)"
    assert kihon.sw_version == "0.1.0"


async def test_entity_names_and_states(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Names come from translations; values from the snapshot."""
    await setup_entry(hass, config_entry)
    online = hass.states.get(_entity_id(hass, "binary_sensor", "kihon_online"))
    assert online.state == STATE_ON
    assert online.attributes["friendly_name"] == "Kihon Online"
    assert online.attributes["device_class"] == "connectivity"
    assert _state(hass, "sensor", "kihon_memory_usage") == "17.8"
    assert _state(hass, "sensor", "kihon_state") == "up"
    assert _state(hass, "sensor", "souko_jobs_queued") == "2"
    assert _state(hass, "sensor", "kihon_last_boot") == "2026-10-20T10:34:00+00:00"
    assert _state(hass, "sensor", "machines_online") == "3"
    assert _state(hass, "sensor", "sync_conflicts") == "0"
    assert _state(hass, "binary_sensor", "sync_problem") == STATE_OFF
    folder = hass.states.get(_entity_id(hass, "sensor", "sync_folder_hisho-vault"))
    assert folder.state == "ok" and folder.attributes["friendly_name"] == "Hisho Sync Hisho vault"
    assert _state(hass, "binary_sensor", "sync_peer_phone") == STATE_ON
    assert _state(hass, "sensor", "compute_worker_hashira-ren") == "running"


async def test_core_services_sit_on_the_core_machine(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Each service is a running sensor on Kihon's device."""
    await setup_entry(hass, config_entry)
    core = hass.states.get(_entity_id(hass, "binary_sensor", "kihon_service_hisho-core"))
    assert core.state == STATE_ON
    assert core.attributes["friendly_name"] == "Kihon hisho-core"
    assert core.attributes["device_class"] == "running"
    assert core.attributes["unit"] == "hisho-fluent.service"
    assert _state(hass, "binary_sensor", "kihon_service_hisho-yepiz-backup") == STATE_OFF


async def test_a_sleeping_machine_is_off_with_its_readings_unavailable(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Asleep: not zero memory, just no reading."""
    await setup_entry(hass, config_entry)
    assert _state(hass, "binary_sensor", "hashira-ten_online") == STATE_OFF
    assert _state(hass, "sensor", "hashira-ten_state") == "asleep"
    assert _state(hass, "sensor", "hashira-ten_memory_usage") == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "hashira-ten_safe_to_shut_down") == STATE_UNAVAILABLE
    assert _state(hass, "binary_sensor", "hashira-ten_sync_connected") == STATE_OFF


async def test_only_what_a_machine_has(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """No GPU sensor without a GPU; Core is not its own sync peer; heartbeat age off by default."""
    await setup_entry(hass, config_entry)
    registry = er.async_get(hass)
    assert registry.async_get_entity_id("sensor", DOMAIN, f"{INSTANCE}_kihon_gpu_usage") is None
    assert _state(hass, "sensor", "souko_gpu_usage") == "6.0"
    assert registry.async_get_entity_id("binary_sensor", DOMAIN, f"{INSTANCE}_kihon_sync_connected") is None
    heartbeat = registry.async_get(_entity_id(hass, "sensor", "souko_heartbeat_age"))
    assert heartbeat.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert heartbeat.entity_category is er.EntityCategory.DIAGNOSTIC


async def test_hisho_going_away_makes_everything_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry,
    snapshot_data: dict[str, Any], freezer: FrozenDateTimeFactory,
) -> None:
    """No stale tiles: when Core stops answering, the entities say so."""
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    await setup_entry(hass, config_entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, status=502)
    await _poll(hass, freezer)
    assert _state(hass, "binary_sensor", "kihon_online") == STATE_UNAVAILABLE
    assert _state(hass, "sensor", "machines_online") == STATE_UNAVAILABLE

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    await _poll(hass, freezer)
    assert _state(hass, "binary_sensor", "kihon_online") == STATE_ON


async def test_a_new_machine_appears_and_a_retired_one_leaves(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry,
    snapshot_data: dict[str, Any], freezer: FrozenDateTimeFactory,
) -> None:
    """The devices follow the fleet without a reload."""
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    await setup_entry(hass, config_entry)

    souko = dict(next(m for m in snapshot_data["machines"] if m["id"] == "souko"))
    changed = {**snapshot_data, "machines": [
        m for m in snapshot_data["machines"] if m["id"] != "hashira-ten"
    ] + [{**souko, "id": "box3", "name": "Box 3"}]}
    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, json=changed)
    await _poll(hass, freezer)

    devices = dr.async_get(hass)
    assert devices.async_get_device_by_identifier((DOMAIN, f"{INSTANCE}_box3"), config_entry.entry_id).name == "Box 3"
    assert _state(hass, "binary_sensor", "box3_online") == STATE_ON
    assert devices.async_get_device_by_identifier((DOMAIN, f"{INSTANCE}_hashira-ten"), config_entry.entry_id) is None


async def test_a_device_can_be_deleted_only_once_hisho_drops_it(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Live machines and the hub stay; a leftover one may go."""
    assert await async_setup_component(hass, "config", {})
    await setup_entry(hass, config_entry)
    from custom_components.hisho import async_remove_config_entry_device

    devices = dr.async_get(hass)
    hub = devices.async_get_device_by_identifier((DOMAIN, INSTANCE), config_entry.entry_id)
    kihon = devices.async_get_device_by_identifier((DOMAIN, f"{INSTANCE}_kihon"), config_entry.entry_id)
    leftover = devices.async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={(DOMAIN, f"{INSTANCE}_old-box")}
    )
    assert not await async_remove_config_entry_device(hass, config_entry, hub)
    assert not await async_remove_config_entry_device(hass, config_entry, kihon)
    assert await async_remove_config_entry_device(hass, config_entry, leftover)


async def test_the_hisho_cards_load_with_the_frontend_when_installed(hass: HomeAssistant, tmp_path) -> None:
    """The sidebar's hisho:kanji needs the cards before the sidebar is drawn: the frontend loads them at startup."""
    from unittest.mock import patch

    from custom_components.hisho import CARDS_FILE, async_load_cards_early

    hass.config.config_dir = str(tmp_path)
    with patch("homeassistant.components.frontend.add_extra_js_url") as add:
        assert await async_load_cards_early(hass) is None  # not installed: nothing asked of the frontend
        add.assert_not_called()
        cards = tmp_path.joinpath(*CARDS_FILE)
        cards.parent.mkdir(parents=True)
        cards.write_text("// cards")
        url = await async_load_cards_early(hass)
    assert url and url.startswith("/hacsfiles/hisho-ha-cards/hisho-ha-cards.js?v=")
    add.assert_called_once_with(hass, url)


async def test_the_dashboard_picture_is_served(hass: HomeAssistant) -> None:
    """The Hisho dashboard's background comes with the integration, at /hisho_static/forest.jpg."""
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.hisho import STATIC_DIR, STATIC_URL, async_serve_static

    assert (STATIC_DIR / "forest.jpg").is_file()
    hass.http = None
    assert await async_serve_static(hass) is False  # no web server: nothing to serve
    hass.http = MagicMock(async_register_static_paths=AsyncMock())
    assert await async_serve_static(hass) is True
    (config,) = hass.http.async_register_static_paths.call_args.args[0]
    assert (config.url_path, config.path, config.cache_headers) == (STATIC_URL, str(STATIC_DIR), True)
