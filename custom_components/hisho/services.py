"""Hisho's services: every control the Hisho dashboard has, for scripts, automations and cards.

``hisho.run_action`` reaches all of them (the list Hisho allows this Home Assistant
is the ``allowed`` attribute of the hub's "Actions allowed" sensor); the others are
the everyday ones with friendlier fields.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .api import HishoError
from .const import DOMAIN
from .coordinator import HishoCoordinator

ATTR_ENTRY = "config_entry_id"

BASE = {vol.Optional(ATTR_ENTRY): cv.string}

RUN_ACTION_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("action"): cv.string,
        vol.Optional("target", default=""): cv.string,
        vol.Optional("args", default={}): dict,
        vol.Optional("confirm", default=False): cv.boolean,
    }
)
ALERT_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("alert_id"): cv.string,
        vol.Required("verb"): vol.In(["ack", "unack", "snooze", "notify", "investigate"]),
        vol.Optional("hours", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=168)),
    }
)
MAINTENANCE_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("target"): cv.string,
        vol.Required("mode"): vol.In(["on", "off"]),
        vol.Optional("reason", default=""): cv.string,
        vol.Optional("duration", default=""): cv.string,
        vol.Optional("keep_vms", default=False): cv.boolean,
        vol.Optional("park", default=False): cv.boolean,
    }
)
JOB_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("job_id"): cv.string,
        vol.Required("verb"): vol.In(["cancel", "retry", "clear", "dispatch", "resume"]),
    }
)
CREATE_JOB_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("kind"): cv.string,
        vol.Optional("worker"): cv.string,
        vol.Optional("meta", default={}): dict,
    }
)


ASK_SCHEMA = vol.Schema(
    {**BASE, vol.Required("text"): cv.string, vol.Optional("conversation_id"): cv.string}
)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> HishoCoordinator:
    """The Hisho the call names, or the only one there is."""
    entries = [
        e for e in hass.config_entries.async_entries(DOMAIN) if e.state is ConfigEntryState.LOADED
    ]
    if wanted := call.data.get(ATTR_ENTRY):
        entries = [e for e in entries if e.entry_id == wanted]
    if len(entries) != 1:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_entry" if not entries else "which_entry",
        )
    return entries[0].runtime_data


def _response(result: Any) -> ServiceResponse:
    return result if isinstance(result, dict) else {"result": result}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services."""

    async def run_action(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        return _response(
            await coordinator.async_action(
                call.data["action"], call.data["target"], call.data["args"], call.data["confirm"]
            )
        )

    async def alert(call: ServiceCall) -> ServiceResponse:
        verb = call.data["verb"]
        args = {"hours": call.data["hours"]} if verb == "snooze" else {}
        return _response(
            await _coordinator(hass, call).async_action(f"alert.{verb}", call.data["alert_id"], args)
        )

    async def maintenance(call: ServiceCall) -> ServiceResponse:
        args = {k: call.data[k] for k in ("reason", "duration", "keep_vms", "park")}
        mode = call.data["mode"]
        return _response(
            await _coordinator(hass, call).async_action(
                f"maintenance.{mode}", call.data["target"], args if mode == "on" else {}, confirm=mode == "on"
            )
        )

    async def job(call: ServiceCall) -> ServiceResponse:
        verb = call.data["verb"]
        return _response(
            await _coordinator(hass, call).async_action(
                f"job.{verb}", call.data["job_id"], confirm=verb == "cancel"
            )
        )

    async def create_job(call: ServiceCall) -> ServiceResponse:
        args = {k: call.data[k] for k in ("kind", "worker", "meta") if k in call.data}
        return _response(await _coordinator(hass, call).async_action("job.create", "", args))

    async def ask(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        user = {"id": None, "name": "Home Assistant", "is_admin": True}
        if call.context.user_id:
            found = await hass.auth.async_get_user(call.context.user_id)
            user = {"id": call.context.user_id, "name": found.name if found else "someone",
                    "is_admin": bool(found and found.is_admin)}
        try:
            return await coordinator.client.async_converse(
                {"text": call.data["text"], "conversation_id": call.data.get("conversation_id"), "user": user,
                 "via": "service"}
            )
        except HishoError as err:
            raise HomeAssistantError(str(err) or type(err).__name__) from err

    for name, handler, schema in (
        ("run_action", run_action, RUN_ACTION_SCHEMA),
        ("alert", alert, ALERT_SCHEMA),
        ("maintenance", maintenance, MAINTENANCE_SCHEMA),
        ("job", job, JOB_SCHEMA),
        ("create_job", create_job, CREATE_JOB_SCHEMA),
        ("ask", ask, ASK_SCHEMA),
    ):
        # Admins only (Jonathan, 2026-10-26); automations and scripts still call them.
        async_register_admin_service(
            hass, DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.OPTIONAL
        )
