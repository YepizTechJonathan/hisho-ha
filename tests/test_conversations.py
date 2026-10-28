"""The conversations people had with Hisho through Assist: Home Assistant's admins list, read and delete them."""

from __future__ import annotations

from typing import Any

import pytest

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import SNAPSHOT_URL, URL, setup_entry

LIST_URL = URL + "/api/ha/conversations"
CID = "0b4c6a40-1111-4c8e-9a1e-222222222222"
LISTED = {
    "conversations": [{"session_id": CID, "title": "how warm is it?", "person": "Ana", "area": "Kitchen",
                       "updated": "2026-10-28T21:10:00+00:00", "messages": 2, "by": "home-assistant"}],
    "timezone": "America/Los_Angeles",
    "keep_days": 30,
}


@pytest.fixture
def hisho(aioclient_mock: AiohttpClientMocker, snapshot_data: dict[str, Any]) -> AiohttpClientMocker:
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    aioclient_mock.get(LIST_URL, json=LISTED)
    aioclient_mock.get(f"{LIST_URL}/{CID}", json={"session_id": CID, "messages": [
        {"role": "user", "text": "how warm is it?"}, {"role": "assistant", "text": "72 degrees."}]})
    aioclient_mock.delete(f"{LIST_URL}/{CID}", json={"ok": True, "id": CID})
    aioclient_mock.get(f"{LIST_URL}/gone", status=404, json={"detail": "no such conversation"})
    return aioclient_mock


async def test_admins_list_read_and_delete(
    hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry, hass_ws_client
) -> None:
    await setup_entry(hass, config_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "hisho/conversations/list", "person": "Ana"})
    reply = await ws.receive_json()
    assert reply["success"] and reply["result"] == LISTED
    sent = next(c for c in hisho.mock_calls if c[0] == "GET" and str(c[1]).startswith(LIST_URL))
    assert sent[1].query["person"] == "Ana" and sent[1].query["limit"] == "100"

    await ws.send_json_auto_id({"type": "hisho/conversations/get", "conversation_id": CID})
    reply = await ws.receive_json()
    assert reply["success"] and reply["result"]["messages"][1]["text"] == "72 degrees."

    await ws.send_json_auto_id({"type": "hisho/conversations/delete", "conversation_id": CID})
    reply = await ws.receive_json()
    assert reply["success"] and reply["result"]["ok"]
    assert any(c[0] == "DELETE" and str(c[1]) == f"{LIST_URL}/{CID}" for c in hisho.mock_calls)

    await ws.send_json_auto_id({"type": "hisho/conversations/get", "conversation_id": "gone"})
    reply = await ws.receive_json()
    assert not reply["success"] and reply["error"]["code"] == "not_found"
    assert reply["error"]["message"] == "no such conversation"


async def test_everyone_else_is_refused(
    hass: HomeAssistant, hisho: AiohttpClientMocker, config_entry: MockConfigEntry, hass_ws_client,
    hass_read_only_access_token: str,
) -> None:
    """Like Hisho's services (1.3.0): admins only. Hisho is never asked."""
    await setup_entry(hass, config_entry)
    ws = await hass_ws_client(hass, hass_read_only_access_token)
    for msg in ({"type": "hisho/conversations/list"},
                {"type": "hisho/conversations/get", "conversation_id": CID},
                {"type": "hisho/conversations/delete", "conversation_id": CID}):
        await ws.send_json_auto_id(msg)
        reply = await ws.receive_json()
        assert not reply["success"] and reply["error"]["code"] == "unauthorized"
    assert not any(str(c[1]).startswith(LIST_URL) for c in hisho.mock_calls)


async def test_a_home_assistant_without_the_operate_scope_is_told_so(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, snapshot_data: dict[str, Any],
    config_entry: MockConfigEntry, hass_ws_client,
) -> None:
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    aioclient_mock.get(LIST_URL, status=403, json={"detail": "needs the operate scope"})
    await setup_entry(hass, config_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "hisho/conversations/list"})
    reply = await ws.receive_json()
    assert reply["error"] == {"code": "not_allowed", "message": "needs the operate scope"}


async def test_no_owner_time_zone_falls_back_to_this_homes(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, snapshot_data: dict[str, Any],
    config_entry: MockConfigEntry, hass_ws_client,
) -> None:
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    aioclient_mock.get(LIST_URL, json={"conversations": [], "timezone": None, "keep_days": 30})
    await hass.config.async_update(time_zone="America/Los_Angeles")
    await setup_entry(hass, config_entry)
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "hisho/conversations/list"})
    assert (await ws.receive_json())["result"]["timezone"] == "America/Los_Angeles"


async def test_no_hisho_set_up(hass: HomeAssistant, hass_ws_client) -> None:
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "hisho", {})
    ws = await hass_ws_client(hass)
    await ws.send_json_auto_id({"type": "hisho/conversations/list"})
    assert (await ws.receive_json())["error"]["code"] == "not_found"
