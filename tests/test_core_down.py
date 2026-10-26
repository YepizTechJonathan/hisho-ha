"""Knowing Hisho Core is down without Core: the reachable and maintenance sensors, and the blueprint."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import shutil
from typing import Any

from freezegun.api import FrozenDateTimeFactory

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util

from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import SNAPSHOT_URL, setup_entry
from .test_init import _poll, _state

BLUEPRINT = Path(__file__).parents[1] / "blueprints" / "automation" / "hisho" / "core_down.yaml"


async def test_core_in_maintenance_is_remembered_while_core_is_down_and_ends_on_time(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry,
    snapshot_data: dict[str, Any], freezer: FrozenDateTimeFactory,
) -> None:
    """A planned outage of Kihon: still "in maintenance" after Core stops answering, off at its end time."""
    until = dt_util.utcnow() + timedelta(minutes=30)
    snapshot_data["maintenance"]["fleet"] = {"reason": "rebuild test", "since": None,
                                             "until": until.isoformat(), "by": "dashboard", "off": False}
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    await setup_entry(hass, config_entry)
    assert _state(hass, "binary_sensor", "core_maintenance") == STATE_ON
    assert _state(hass, "binary_sensor", "core_reachable") == STATE_ON

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, status=502)
    await _poll(hass, freezer)
    assert _state(hass, "binary_sensor", "core_reachable") == STATE_OFF
    assert _state(hass, "binary_sensor", "core_maintenance") == STATE_ON        # remembered, not unavailable

    freezer.move_to(until + timedelta(seconds=5))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert _state(hass, "binary_sensor", "core_maintenance") == STATE_OFF       # the window ended


async def test_maintenance_of_another_machine_is_not_core_maintenance(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    await setup_entry(hass, config_entry)                    # the fixture has proxmox1 and ionos in maintenance
    assert _state(hass, "binary_sensor", "core_maintenance") == STATE_OFF


async def test_the_core_machine_itself_in_maintenance_counts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry,
    snapshot_data: dict[str, Any],
) -> None:
    snapshot_data["maintenance"]["machines"][snapshot_data["core_host"]] = {
        "reason": "disk swap", "since": None, "until": None, "by": "dashboard", "off": False}
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    await setup_entry(hass, config_entry)
    assert _state(hass, "binary_sensor", "core_maintenance") == STATE_ON


# --- the blueprint -----------------------------------------------------------------------------------

async def _automation(hass: HomeAssistant) -> list:
    """The blueprint as a user would set it up, on stand-in sensors, with a fake notify action."""
    target = Path(hass.config.path("blueprints/automation/hisho/core_down.yaml"))
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, target)
    hass.states.async_set("binary_sensor.core_reachable", STATE_ON)
    hass.states.async_set("binary_sensor.core_maintenance", STATE_OFF)
    calls = async_mock_service(hass, "notify", "mobile_app_phone")
    assert await async_setup_component(hass, "automation", {"automation": {
        "use_blueprint": {"path": "hisho/core_down.yaml", "input": {
            "reachable": "binary_sensor.core_reachable",
            "maintenance": "binary_sensor.core_maintenance",
            "notify_action": "notify.mobile_app_phone"}}}})
    await hass.async_block_till_done()
    return calls


async def _wait(hass: HomeAssistant, freezer: FrozenDateTimeFactory, minutes: float) -> None:
    freezer.tick(timedelta(minutes=minutes))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


async def test_ten_minutes_down_tells_the_phone_once_and_once_when_back(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = await _automation(hass)
    hass.states.async_set("binary_sensor.core_reachable", STATE_OFF)
    await _wait(hass, freezer, 5)
    assert calls == []                                                   # a short blip says nothing
    await _wait(hass, freezer, 6)
    assert [c.data["title"] for c in calls] == ["Hisho Core is down"]
    await _wait(hass, freezer, 30)
    assert len(calls) == 1                                               # once, not every minute
    hass.states.async_set("binary_sensor.core_reachable", STATE_ON)
    await hass.async_block_till_done()
    assert [c.data["title"] for c in calls] == ["Hisho Core is down", "Hisho Core is back"]
    assert "about 41 min" in calls[1].data["message"]


async def test_a_blip_neither_alerts_nor_says_back(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    calls = await _automation(hass)
    hass.states.async_set("binary_sensor.core_reachable", STATE_OFF)
    await _wait(hass, freezer, 3)
    hass.states.async_set("binary_sensor.core_reachable", STATE_ON)
    await _wait(hass, freezer, 20)
    assert calls == []


async def test_planned_maintenance_is_quiet_until_its_window_ends(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = await _automation(hass)
    hass.states.async_set("binary_sensor.core_maintenance", STATE_ON)
    hass.states.async_set("binary_sensor.core_reachable", STATE_OFF)
    await _wait(hass, freezer, 30)
    assert calls == []                                                   # planned: quiet
    hass.states.async_set("binary_sensor.core_maintenance", STATE_OFF)   # the window ended, Core still down
    await hass.async_block_till_done()
    assert [c.data["title"] for c in calls] == ["Hisho Core is still down"]
    hass.states.async_set("binary_sensor.core_reachable", STATE_ON)      # back (and no 12 h timer left running)
    await hass.async_block_till_done()
    assert [c.data["title"] for c in calls] == ["Hisho Core is still down", "Hisho Core is back"]


async def test_back_within_maintenance_says_nothing(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    calls = await _automation(hass)
    hass.states.async_set("binary_sensor.core_maintenance", STATE_ON)
    hass.states.async_set("binary_sensor.core_reachable", STATE_OFF)
    await _wait(hass, freezer, 45)
    hass.states.async_set("binary_sensor.core_reachable", STATE_ON)
    await hass.async_block_till_done()
    assert calls == []


async def test_open_ended_maintenance_keeps_quiet_only_so_long(
    hass: HomeAssistant, freezer: FrozenDateTimeFactory
) -> None:
    calls = await _automation(hass)
    hass.states.async_set("binary_sensor.core_maintenance", STATE_ON)
    hass.states.async_set("binary_sensor.core_reachable", STATE_OFF)
    await _wait(hass, freezer, 11 * 60)
    assert calls == []
    await _wait(hass, freezer, 61)
    assert [c.data["title"] for c in calls] == ["Hisho Core is down for hours"]
