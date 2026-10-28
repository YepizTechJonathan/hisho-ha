"""The conversations people had with Hisho through Home Assistant, for Home Assistant's admins only.

Hisho keeps each one 30 days (its own record, ``GET/DELETE /api/ha/conversations`` on Core). Any
person's conversation can hold an admin's answer, so like Hisho's services (1.3.0) these commands are
admins only: everyone else gets *unauthorized*. The Hisho dashboard's Conversations view uses them.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .api import HishoActionError, HishoAuthError, HishoError
from .const import DOMAIN
from .coordinator import HishoCoordinator

ENTRY = vol.Optional("config_entry_id")


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register the commands (once, for every entry)."""
    websocket_api.async_register_command(hass, ws_list)
    websocket_api.async_register_command(hass, ws_get)
    websocket_api.async_register_command(hass, ws_delete)


def _coordinator(hass: HomeAssistant, entry_id: str | None) -> HishoCoordinator | None:
    """The Hisho the message names, or the only one there is."""
    entries = [e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED]
    if entry_id:
        entries = [e for e in entries if e.entry_id == entry_id]
    return entries[0].runtime_data if len(entries) == 1 else None


async def _answer(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any],
                  call: Any) -> None:
    coordinator = _coordinator(hass, msg.get("config_entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND,
                              "No Hisho here (or more than one: name its config_entry_id)")
        return
    try:
        result = await call(coordinator.client)
    except HishoActionError as err:
        code = websocket_api.ERR_NOT_FOUND if err.status == 404 else (
            websocket_api.ERR_NOT_ALLOWED if err.status == 403 else websocket_api.ERR_HOME_ASSISTANT_ERROR)
        connection.send_error(msg["id"], code, err.detail)
        return
    except HishoAuthError:
        connection.send_error(msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR,
                              "Hisho no longer accepts this Home Assistant (re-authenticate it)")
        return
    except HishoError as err:
        connection.send_error(msg["id"], websocket_api.ERR_HOME_ASSISTANT_ERROR,
                              f"Can't reach Hisho ({err or type(err).__name__})")
        return
    if "timezone" in result and not result.get("timezone"):
        result["timezone"] = hass.config.time_zone  # Hisho has no owner's time zone set: this home's
    connection.send_result(msg["id"], result)


@websocket_api.require_admin
@websocket_api.websocket_command(
    {
        vol.Required("type"): "hisho/conversations/list",
        ENTRY: str,
        vol.Optional("person"): str,
        vol.Optional("limit", default=100): vol.All(int, vol.Range(min=1, max=500)),
    }
)
@websocket_api.async_response
async def ws_list(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Newest first: who asked, where, when, the first question."""
    await _answer(hass, connection, msg, lambda c: c.async_conversations(msg.get("person"), msg["limit"]))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "hisho/conversations/get", ENTRY: str, vol.Required("conversation_id"): str}
)
@websocket_api.async_response
async def ws_get(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """One conversation's transcript."""
    await _answer(hass, connection, msg, lambda c: c.async_conversation(msg["conversation_id"]))


@websocket_api.require_admin
@websocket_api.websocket_command(
    {vol.Required("type"): "hisho/conversations/delete", ENTRY: str, vol.Required("conversation_id"): str}
)
@websocket_api.async_response
async def ws_delete(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """Delete one conversation (Hisho journals it)."""
    await _answer(hass, connection, msg, lambda c: c.async_delete_conversation(msg["conversation_id"]))
