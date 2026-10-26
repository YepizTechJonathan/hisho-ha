"""Config flow: pair (approve in Hisho), the CLI-token fallback, re-pair, reconfigure."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import pytest

from homeassistant.components import persistent_notification
from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hisho.const import DOMAIN

from .conftest import SNAPSHOT_URL, TOKEN, URL

PAIR_URL = URL + "/api/ha/pair"
INSTANCE = "0123456789abcdef0123456789abcdef"
ADDRESS = {CONF_URL: URL + "/", CONF_VERIFY_SSL: True}


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Poll Hisho without the real three-second pauses."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("custom_components.hisho.config_flow.asyncio.sleep", instant)


def _hisho_pairs(aioclient_mock: AiohttpClientMocker, answer: dict[str, Any], instance: str = INSTANCE) -> None:
    aioclient_mock.post(
        PAIR_URL, json={"pair_id": "secret-id", "code": "K7Q-4ZP", "instance_id": instance, "poll_seconds": 3}
    )
    aioclient_mock.get(PAIR_URL + "/secret-id", json=answer)


async def _submit_and_wait(hass: HomeAssistant, flow_id: str, user_input: dict[str, Any] | None) -> Any:
    """Submit, and follow the wait for approval to its end (instant here: polling does not sleep)."""
    result = await hass.config_entries.flow.async_configure(flow_id, user_input)
    while result["type"] is FlowResultType.SHOW_PROGRESS:
        await hass.async_block_till_done()
        result = await hass.config_entries.flow.async_configure(flow_id)
    return result


async def test_the_wait_shows_the_code_to_compare(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """While Hisho has not answered, the form says what to do and shows the code."""
    release = asyncio.Event()

    async def held(_seconds: float) -> None:
        await release.wait()

    monkeypatch.setattr("custom_components.hisho.config_flow.asyncio.sleep", held)
    _hisho_pairs(aioclient_mock, {"state": "approved", "token": "paired-token"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ADDRESS)
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["progress_action"] == "wait_for_approval"
    assert result["description_placeholders"]["code"] == "K7Q-4ZP"
    notes = persistent_notification._async_get_or_create_notifications(hass)
    assert any("K7Q-4ZP" in n["message"] for n in notes.values())
    release.set()
    await hass.async_block_till_done()
    result = await hass.config_entries.flow.async_configure(result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert not persistent_notification._async_get_or_create_notifications(hass)


@pytest.mark.parametrize("source", ["reauth", "reconfigure"])
async def test_re_pairing_shows_the_code_too(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, monkeypatch: pytest.MonkeyPatch,
    config_entry: MockConfigEntry, source: str,
) -> None:
    """Reauth and reconfigure wait with the code in the dialog and in a notification."""
    release = asyncio.Event()

    async def held(_seconds: float) -> None:
        await release.wait()

    monkeypatch.setattr("custom_components.hisho.config_flow.asyncio.sleep", held)
    _hisho_pairs(aioclient_mock, {"state": "approved", "token": "t"})
    aioclient_mock.get(SNAPSHOT_URL, status=401)
    config_entry.add_to_hass(hass)
    result = await (config_entry.start_reauth_flow(hass) if source == "reauth"
                    else config_entry.start_reconfigure_flow(hass))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {} if source == "reauth" else ADDRESS
    )
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["description_placeholders"]["code"] == "K7Q-4ZP"
    notes = persistent_notification._async_get_or_create_notifications(hass)
    assert any("K7Q-4ZP" in n["message"] for n in notes.values())
    hass.config_entries.flow.async_abort(result["flow_id"])  # the user closes the dialog
    assert not persistent_notification._async_get_or_create_notifications(hass)
    release.set()
    await hass.async_block_till_done()


async def test_pairing_only_needs_the_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Address in, approve in Hisho, entry out with Hisho's token, keyed on the Hisho."""
    _hisho_pairs(aioclient_mock, {"state": "approved", "token": "paired-token", "scopes": ["read"]})
    aioclient_mock.get(SNAPSHOT_URL, status=401)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await _submit_and_wait(hass, result["flow_id"], ADDRESS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_URL: URL, CONF_TOKEN: "paired-token", CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == INSTANCE
    pair_request = next(c for c in aioclient_mock.mock_calls if str(c[1]) == PAIR_URL)
    assert pair_request[2]["name"] and pair_request[2]["ha_id"]


@pytest.mark.parametrize(("state", "reason"), [("denied", "pair_denied"), ("expired", "pair_expired")])
async def test_pairing_denied_or_unanswered(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, state: str, reason: str
) -> None:
    """No entry without an approval."""
    _hisho_pairs(aioclient_mock, {"state": state})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await _submit_and_wait(hass, result["flow_id"], ADDRESS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ({"status": 429, "json": {"detail": "try again in 20 s"}}, "pair_busy"),
        ({"status": 404}, "not_hisho"),
        ({"status": 500}, "cannot_connect"),
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"text": "<html>router login</html>"}, "not_hisho"),
    ],
)
async def test_pairing_errors_are_named_on_the_form(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, answer: dict[str, Any], error: str
) -> None:
    """Busy, not Hisho, unreachable: each says so."""
    aioclient_mock.post(PAIR_URL, **answer)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ADDRESS)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}


async def test_the_same_hisho_is_not_paired_twice(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Known Hisho: abort before anyone is asked to approve anything."""
    config_entry.add_to_hass(hass)
    _hisho_pairs(aioclient_mock, {"state": "approved", "token": "x"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ADDRESS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_cli_token_fallback(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, snapshot_data: dict[str, Any]
) -> None:
    """A token made with `hisho home-assistant token` still works, no pairing."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**ADDRESS, CONF_TOKEN: f" {TOKEN} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TOKEN] == TOKEN
    assert result["result"].unique_id == snapshot_data["instance_id"]


@pytest.mark.parametrize(
    ("answer", "error"),
    [({"status": 401}, "invalid_auth"), ({"json": {"schema": 99, "instance_id": "x", "machines": []}}, "unsupported_schema")],
)
async def test_cli_token_errors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, answer: dict[str, Any], error: str
) -> None:
    """A bad CLI token, or a Hisho too new."""
    aioclient_mock.get(SNAPSHOT_URL, **answer)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {**ADDRESS, CONF_TOKEN: "nope"})
    assert result["errors"] == {"base": error}


async def test_unexpected_error(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug is reported as unknown, not as a crash."""

    async def boom(*args: Any) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr("custom_components.hisho.api.HishoClient.async_pair_start", boom)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ADDRESS)
    assert result["errors"] == {"base": "unknown"}


async def test_reauth_pairs_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Revoked in Hisho: ask again, approve, the new token is saved."""
    config_entry.add_to_hass(hass)
    _hisho_pairs(aioclient_mock, {"state": "approved", "token": "new-token"})
    aioclient_mock.get(SNAPSHOT_URL, status=401)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await _submit_and_wait(hass, result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] == "new-token"


async def test_reauth_against_another_hisho_is_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The address now answers as a different Hisho: never rebind the entry."""
    config_entry.add_to_hass(hass)
    _hisho_pairs(aioclient_mock, {"state": "approved", "token": "x"}, instance="someone-else")
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_hisho"
    assert config_entry.data[CONF_TOKEN] == TOKEN


async def test_reconfigure_moves_to_a_new_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Core moved: same Hisho at a new address, approved again."""
    new_url = "https://core.example.ts.net"
    aioclient_mock.post(new_url + "/api/ha/pair", json={"pair_id": "s2", "code": "K7Q-4ZP", "instance_id": INSTANCE})
    aioclient_mock.get(new_url + "/api/ha/pair/s2", json={"state": "approved", "token": "moved-token"})
    aioclient_mock.get(new_url + "/api/ha/snapshot", status=401)
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await _submit_and_wait(hass, result["flow_id"], {CONF_URL: new_url, CONF_VERIFY_SSL: False})
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data == {CONF_URL: new_url, CONF_TOKEN: "moved-token", CONF_VERIFY_SSL: False}


async def test_reconfigure_to_another_hisho_is_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A different Hisho at the new address is a new integration, not this entry."""
    aioclient_mock.post(PAIR_URL, exc=aiohttp.ClientConnectionError())
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ADDRESS)
    assert result["errors"] == {"base": "cannot_connect"}
    aioclient_mock.clear_requests()
    _hisho_pairs(aioclient_mock, {"state": "approved", "token": "x"}, instance="someone-else")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], ADDRESS)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_hisho"
