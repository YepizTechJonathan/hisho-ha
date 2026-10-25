"""Hisho's controls in Home Assistant: buttons, switches, needs-you list, alert event, phone, services."""

from __future__ import annotations

from datetime import timedelta
import json
from typing import Any

from freezegun.api import FrozenDateTimeFactory
import pytest

from homeassistant.components.todo import TodoItemStatus
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr, entity_registry as er

from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hisho.const import DOMAIN

from .conftest import SNAPSHOT_URL, URL, setup_entry

INSTANCE = "0123456789abcdef0123456789abcdef"
ACTION_URL = URL + "/api/ha/action"


@pytest.fixture
def hisho(aioclient_mock: AiohttpClientMocker, snapshot_data: dict[str, Any]) -> AiohttpClientMocker:
    """Hisho Core answering the snapshot and accepting actions."""
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    aioclient_mock.post(ACTION_URL, json={"ok": True})
    return aioclient_mock


def _eid(hass: HomeAssistant, platform: str, unique_suffix: str) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{INSTANCE}_{unique_suffix}")
    assert entity_id, f"no {platform} {unique_suffix}"
    return entity_id


def _actions(mock: AiohttpClientMocker) -> list[dict[str, Any]]:
    return [call[2] for call in mock.mock_calls if str(call[1]) == ACTION_URL]


async def _enable(hass: HomeAssistant, entity_id: str, entry: MockConfigEntry) -> None:
    er.async_get(hass).async_update_entity(entity_id, disabled_by=None)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()


async def test_wake_sends_the_action(hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry) -> None:
    """Wake is enabled and plain."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call("button", "press", {"entity_id": _eid(hass, "button", "hashira-ren_wake")}, blocking=True)
    assert _actions(hisho)[-1] == {"action": "machine.wake", "target": "hashira-ren", "args": {}}


async def test_danger_buttons_start_disabled_and_press_confirmed(
    hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Park and a VM shutdown are opt-in; once enabled, the press is sent confirmed."""
    await setup_entry(hass, config_entry)
    registry = er.async_get(hass)
    park = _eid(hass, "button", "hashira-ren_park")
    shutdown = _eid(hass, "button", "device_truenas_proxmox_PVE_521_shutdown")
    assert registry.async_get(park).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert registry.async_get(shutdown).disabled_by is er.RegistryEntryDisabler.INTEGRATION
    await _enable(hass, shutdown, config_entry)
    await hass.services.async_call("button", "press", {"entity_id": shutdown}, blocking=True)
    assert _actions(hisho)[-1] == {
        "action": "device.press", "target": "proxmox",
        "args": {"entry": "PVE", "item": "521", "action": "shutdown"}, "confirm": True,
    }


async def test_buttons_follow_what_hisho_allows(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry, snapshot_data: dict[str, Any]
) -> None:
    """A read-only Home Assistant gets status and no controls."""
    aioclient_mock.get(SNAPSHOT_URL, json={**snapshot_data, "allowed": []})
    await setup_entry(hass, config_entry)
    platforms = {e.domain for e in er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)}
    assert "button" not in platforms and "switch" not in platforms and "notify" not in platforms
    assert "sensor" in platforms and "todo" in platforms


async def test_machine_item_and_addon_buttons(
    hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """An add-on's button about a machine sits on the machine; declared actions on the add-on."""
    await setup_entry(hass, config_entry)
    check = _eid(hass, "button", "souko_prometheus_souko_exporter_check")
    assert hass.states.get(check).attributes["friendly_name"] == "Souko node_exporter: Check"
    deploy = _eid(hass, "button", "addon_web-factory_action_deploy-dev")
    await hass.services.async_call("button", "press", {"entity_id": deploy}, blocking=True)
    assert _actions(hisho)[-1] == {"action": "addon.run", "target": "web-factory", "args": {"action": "deploy-dev"}}
    restart = _eid(hass, "button", "kihon_restart_hisho-agent.service")
    assert hass.states.get(restart).attributes["friendly_name"] == "Kihon Restart Hisho agent"


async def test_hisho_refusing_an_action_is_an_error_in_ha(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry, snapshot_data: dict[str, Any]
) -> None:
    """403 from Hisho reads as "not allowed", with Hisho's reason."""
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    aioclient_mock.post(ACTION_URL, status=403, json={"detail": "needs the operate scope"})
    await setup_entry(hass, config_entry)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "button", "press", {"entity_id": _eid(hass, "button", "hashira-ren_wake")}, blocking=True
        )
    assert err.value.translation_key == "action_not_allowed"


async def test_maintenance_switches(hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry) -> None:
    """Fleet, machine, box and add-on maintenance; on is sent confirmed."""
    await setup_entry(hass, config_entry)
    ionos = _eid(hass, "switch", "device_ionos_maintenance")
    assert hass.states.get(ionos).state == STATE_ON
    assert hass.states.get(ionos).attributes["reason"] == "updates"
    assert hass.states.get(_eid(hass, "switch", "fleet_maintenance")).state == STATE_OFF
    await hass.services.async_call("switch", "turn_on", {"entity_id": _eid(hass, "switch", "souko_maintenance")}, blocking=True)
    assert _actions(hisho)[-1] == {
        "action": "maintenance.on", "target": "souko", "args": {"reason": "from Home Assistant"}, "confirm": True,
    }
    await hass.services.async_call("switch", "turn_off", {"entity_id": ionos}, blocking=True)
    assert _actions(hisho)[-1] == {"action": "maintenance.off", "target": "ionos", "args": {}}
    _eid(hass, "switch", "addon_web-factory_maintenance")


async def test_service_switch_and_addon_enabled(
    hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Start/stop a service; turning an add-on off asks Hisho confirmed."""
    await setup_entry(hass, config_entry)
    ollama = _eid(hass, "switch", "souko_unit_ollama.service")
    assert hass.states.get(ollama).state == STATE_OFF
    await hass.services.async_call("switch", "turn_on", {"entity_id": ollama}, blocking=True)
    assert _actions(hisho)[-1] == {"action": "unit.start", "target": "souko", "args": {"unit": "ollama.service"}}
    await hass.services.async_call("switch", "turn_off", {"entity_id": ollama}, blocking=True)
    assert _actions(hisho)[-1]["confirm"] is True
    email = _eid(hass, "switch", "addon_email_enabled")
    await hass.services.async_call("switch", "turn_off", {"entity_id": email}, blocking=True)
    assert _actions(hisho)[-1] == {"action": "addon.disable", "target": "email", "args": {}, "confirm": True}


async def test_needs_you_list(hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry) -> None:
    """Open items first, Claude's finding in the description, pairing requests left to Hisho; tick = ack."""
    await setup_entry(hass, config_entry)
    entity_id = _eid(hass, "todo", "needs_you")
    assert hass.states.get(entity_id).state == "1"  # one not acknowledged
    items = (await hass.services.async_call(
        "todo", "get_items", {"entity_id": entity_id}, blocking=True, return_response=True
    ))[entity_id]["items"]
    assert [i["uid"] for i in items] == ["d2eb836753a0", "7cfe27652174"]
    assert "Claude: Disk sdh" in items[0]["description"]
    assert items[1]["status"] == TodoItemStatus.COMPLETED
    await hass.services.async_call(
        "todo", "update_item", {"entity_id": entity_id, "item": "d2eb836753a0", "status": "completed"}, blocking=True
    )
    assert _actions(hisho)[-1] == {"action": "alert.ack", "target": "d2eb836753a0", "args": {}}
    await hass.services.async_call(
        "todo", "update_item", {"entity_id": entity_id, "item": "7cfe27652174", "status": "needs_action"}, blocking=True
    )
    assert _actions(hisho)[-1]["action"] == "alert.unack"


async def test_alert_event_fires_on_new_and_resolved(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry,
    snapshot_data: dict[str, Any], freezer: FrozenDateTimeFactory,
) -> None:
    """What is open at start-up is not news; a new item and a resolved one are."""
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    await setup_entry(hass, config_entry)
    event_id = _eid(hass, "event", "alert_event")
    assert hass.states.get(event_id).state == "unknown"

    new = {"id": "n3w", "severity": "critical", "title": "kihon is down", "detail": "", "source": "fleet",
           "machine": "kihon", "muted": None, "off": False, "pair": None, "investigation": None}
    changed = json.loads(json.dumps(snapshot_data))
    changed["alerts"] = [a for a in changed["alerts"] if a["id"] != "7cfe27652174"] + [new]
    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, json=changed)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    state = hass.states.get(event_id)
    assert state.attributes["event_type"] in ("opened", "resolved")
    assert state.attributes["id"] in ("n3w", "7cfe27652174")


async def test_phone_app_target(hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry) -> None:
    """notify.send_message goes to Hisho's phone app."""
    await setup_entry(hass, config_entry)
    await hass.services.async_call(
        "notify", "send_message",
        {"entity_id": _eid(hass, "notify", "phone"), "message": "Dinner", "title": "Home"}, blocking=True,
    )
    assert _actions(hisho)[-1] == {"action": "notify.send", "target": "", "args": {"message": "Dinner", "title": "Home"}}


async def test_add_on_boxes_and_add_ons_are_devices(
    hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """TrueNAS and IONOS under the hub; a box that is a fleet machine is not doubled; add-ons too."""
    await setup_entry(hass, config_entry)
    devices = dr.async_get(hass)
    hub = devices.async_get_device_by_identifier((DOMAIN, INSTANCE), config_entry.entry_id)
    truenas = devices.async_get_device_by_identifier((DOMAIN, f"{INSTANCE}_device_truenas"), config_entry.entry_id)
    assert truenas.via_device_id == hub.id and truenas.model == "Storage"
    assert devices.async_get_device_by_identifier((DOMAIN, f"{INSTANCE}_device_hashira-ren"), config_entry.entry_id) is None
    assert hass.states.get(_eid(hass, "sensor", "device_truenas_health")).state == "problem"
    pool = hass.states.get(_eid(hass, "sensor", "device_truenas_item_truenas_truenas_pool-Potter"))
    assert pool.state == "warn" and pool.attributes["friendly_name"] == "truenas Pool Potter"
    email = devices.async_get_device_by_identifier((DOMAIN, f"{INSTANCE}_addon_email"), config_entry.entry_id)
    assert email.entry_type is dr.DeviceEntryType.SERVICE and email.sw_version == "0.1.4"
    assert hass.states.get(_eid(hass, "sensor", "addon_email_status")).state == "needs_setup"


async def test_hub_counts_and_core_reachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry,
    snapshot_data: dict[str, Any], freezer: FrozenDateTimeFactory,
) -> None:
    """Counts on the hub; Core reachable stays available when Core is not."""
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    await setup_entry(hass, config_entry)
    assert hass.states.get(_eid(hass, "sensor", "needs_you")).state == "2"  # the pairing request counts too
    assert hass.states.get(_eid(hass, "sensor", "jobs_active")).state == "1"
    assert hass.states.get(_eid(hass, "sensor", "jobs_failed")).state == "1"
    assert hass.states.get(_eid(hass, "sensor", "projects_waiting")).state == "1"
    assert hass.states.get(_eid(hass, "binary_sensor", "critical_alert")).state == STATE_ON
    assert hass.states.get(_eid(hass, "sensor", "hashira-ren_awake")).state == "parks in 16m"
    reachable = _eid(hass, "binary_sensor", "core_reachable")
    assert hass.states.get(reachable).state == STATE_ON

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, status=502)
    freezer.tick(timedelta(seconds=61))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(reachable).state == STATE_OFF
    assert hass.states.get(_eid(hass, "sensor", "needs_you")).state == STATE_UNAVAILABLE


async def test_services(hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry) -> None:
    """run_action reaches everything; the friendly ones fill in the details."""
    await setup_entry(hass, config_entry)
    response = await hass.services.async_call(
        DOMAIN, "run_action", {"action": "jobs.reap"}, blocking=True, return_response=True
    )
    assert response == {"ok": True}
    await hass.services.async_call(DOMAIN, "alert", {"alert_id": "abc", "verb": "snooze", "hours": 2}, blocking=True)
    assert _actions(hisho)[-1] == {"action": "alert.snooze", "target": "abc", "args": {"hours": 2.0}}
    await hass.services.async_call(
        DOMAIN, "maintenance", {"target": "ionos", "mode": "on", "reason": "patching", "duration": "1h"}, blocking=True
    )
    assert _actions(hisho)[-1] == {
        "action": "maintenance.on", "target": "ionos",
        "args": {"reason": "patching", "duration": "1h", "keep_vms": False, "park": False}, "confirm": True,
    }
    await hass.services.async_call(DOMAIN, "job", {"job_id": "job-1", "verb": "cancel"}, blocking=True)
    assert _actions(hisho)[-1] == {"action": "job.cancel", "target": "job-1", "args": {}, "confirm": True}
    await hass.services.async_call(DOMAIN, "create_job", {"kind": "transcribe", "worker": "souko"}, blocking=True)
    assert _actions(hisho)[-1]["args"] == {"kind": "transcribe", "worker": "souko", "meta": {}}


async def test_services_need_a_loaded_hisho(hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry) -> None:
    """Called with no Hisho set up: a clear error."""
    await setup_entry(hass, config_entry)
    await hass.config_entries.async_unload(config_entry.entry_id)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "run_action", {"action": "jobs.reap"}, blocking=True)
