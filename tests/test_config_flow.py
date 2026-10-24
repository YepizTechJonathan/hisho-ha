"""Config flow: add, re-authenticate, reconfigure."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hisho.const import DOMAIN

from .conftest import SNAPSHOT_URL, TOKEN, URL

USER_INPUT = {CONF_URL: URL + "/", CONF_TOKEN: f" {TOKEN} ", CONF_VERIFY_SSL: True}


async def test_user_flow_creates_an_entry_keyed_on_the_hisho(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, snapshot_data: dict[str, Any]
) -> None:
    """URL and token are tidied; the entry is unique per Hisho instance."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Hisho"
    assert result["data"] == {CONF_URL: URL, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    assert result["result"].unique_id == snapshot_data["instance_id"]
    assert mock_hisho.mock_calls[-1][3]["Authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        ({"status": 401}, "invalid_auth"),
        ({"status": 404}, "not_hisho"),
        ({"status": 500}, "cannot_connect"),
        ({"exc": aiohttp.ClientConnectionError()}, "cannot_connect"),
        ({"exc": TimeoutError()}, "cannot_connect"),
        ({"text": "<html>router login</html>"}, "not_hisho"),
        ({"json": {"status": "ok"}}, "not_hisho"),
        ({"json": {"schema": 99, "instance_id": "x", "machines": []}}, "unsupported_schema"),
    ],
)
async def test_user_flow_errors_then_recovers(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, snapshot_data: dict[str, Any],
    answer: dict[str, Any], error: str,
) -> None:
    """Each failure is named on the form, and the form works once Hisho does."""
    aioclient_mock.get(SNAPSHOT_URL, **answer)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_unexpected_error(hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug is reported as unknown, not as a crash."""
    async def boom(self):  # noqa: ANN001
        raise RuntimeError("bug")

    monkeypatch.setattr("custom_components.hisho.api.HishoClient.async_get_snapshot", boom)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["errors"] == {"base": "unknown"}


async def test_same_hisho_twice_is_refused(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """Adding the same Hisho again aborts, even at another address."""
    config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_takes_the_rotated_token(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """A new token is saved and the entry reloads."""
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "new-token"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_TOKEN] == "new-token"


async def test_reauth_with_a_bad_token_says_so(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The old token stays until a working one is given."""
    aioclient_mock.get(SNAPSHOT_URL, status=401)
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "nope"})
    assert result["errors"] == {"base": "invalid_auth"}
    assert config_entry.data[CONF_TOKEN] == TOKEN


async def test_reauth_against_another_hisho_is_refused(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry,
    snapshot_data: dict[str, Any],
) -> None:
    """A token for a different Hisho does not rebind this entry."""
    aioclient_mock.get(SNAPSHOT_URL, json={**snapshot_data, "instance_id": "someone-else"})
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_TOKEN: "x"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_hisho"


async def test_reconfigure_moves_to_a_new_address(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry,
    snapshot_data: dict[str, Any],
) -> None:
    """Core moved host: same Hisho, new URL."""
    new_url = "https://core.example.ts.net"
    aioclient_mock.get(new_url + "/api/ha/snapshot", json=snapshot_data)
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    config_entry.add_to_hass(hass)
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_URL: new_url, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: False}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.data[CONF_URL] == new_url
    assert config_entry.data[CONF_VERIFY_SSL] is False


async def test_reconfigure_errors_and_wrong_hisho(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, config_entry: MockConfigEntry,
    snapshot_data: dict[str, Any],
) -> None:
    """An unreachable address shows an error; another Hisho aborts."""
    config_entry.add_to_hass(hass)
    aioclient_mock.get(SNAPSHOT_URL, exc=aiohttp.ClientConnectionError())
    result = await config_entry.start_reconfigure_flow(hass)
    form = {CONF_URL: URL, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["errors"] == {"base": "cannot_connect"}

    aioclient_mock.clear_requests()
    aioclient_mock.get(SNAPSHOT_URL, json={**snapshot_data, "instance_id": "someone-else"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], form)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_hisho"
