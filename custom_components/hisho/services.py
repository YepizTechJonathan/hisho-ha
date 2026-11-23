"""Hisho's services: every control the Hisho dashboard has, for scripts, automations and cards.

``hisho.run_action`` reaches all of them (the list Hisho allows this Home Assistant
is the ``allowed`` attribute of the hub's "Actions allowed" sensor); the others are
the everyday ones with friendlier fields.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .api import HishoError
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from .const import (
    CONF_READ_ALOUD,
    CONF_READ_ALOUD_FOLLOW,
    CONF_READ_ALOUD_ROOM_SPEAKERS,
    CONF_READ_ALOUD_SPEAKER,
    CONF_READ_ALOUD_VOICE,
    DEFAULT_READ_ALOUD_VOICE,
    DOMAIN,
)
from .coordinator import HishoCoordinator

ATTR_ENTRY = "config_entry_id"

_LOGGER = logging.getLogger(__name__)

# Read aloud plays one reply at a time per speaker: tts.speak returns once playing starts, so the next
# reply waits until the speaker stops (or the longest the reply could take, if the speaker never says).
READ_ALOUD_START = 10.0  # s a speaker may take to start playing
READ_ALOUD_PACE = 10.0   # characters spoken per second, at the slowest
READ_ALOUD_POLL = 0.1    # s between looks at the speaker (the next sentence starts this soon after)
READ_ALOUD_QUEUES = f"{DOMAIN}_read_aloud"   # hass.data: speaker -> its queue
READ_ALOUD_LAST = f"{DOMAIN}_read_aloud_last"   # hass.data: speaker -> the last reply sent to it, for repeat
READ_ALOUD_CUT = f"{DOMAIN}_read_aloud_cut"     # hass.data: speaker -> set while a stop silences it

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


READ_ALOUD_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Optional("message"): vol.All(cv.string, vol.Length(min=1)),
        vol.Optional("speaker"): cv.entity_id,
        vol.Optional("repeat", default=False): cv.boolean,
        # wait: answer once this message has been spoken (a reader that pauses and skips between sentences);
        # stop: silence the speaker now and drop what waits for it.
        vol.Optional("wait", default=False): cv.boolean,
        vol.Optional("stop", default=False): cv.boolean,
    }
)


TRUST_SCHEMA = vol.Schema(
    {
        **BASE,
        # Timed only from Home Assistant: a press nobody remembers mustn't leave it open (until-I-end-it is on the
        # dashboard).
        vol.Optional("minutes", default=30): vol.All(vol.Coerce(int), vol.In([15, 30, 60])),
        vol.Optional("machines", default=[]): vol.All(cv.ensure_list, [cv.string]),
    }
)
_MINUTES = vol.All(vol.Coerce(int), vol.Range(min=0, max=1440))
PARENTAL_SCHEMA = vol.Schema(
    {
        **BASE,
        vol.Required("machine"): cv.string,
        vol.Required("control"): vol.In(["lock", "stop", "more", "limits", "hours", "off"]),
        # more: minutes to add; limits: 7 daily minutes Monday to Sunday (empty / none = no limit, 0 = no play)
        vol.Optional("minutes"): vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
        vol.Optional("limits"): vol.All(cv.ensure_list, vol.Length(min=1, max=7),
                                        [vol.Any(None, "", "none", _MINUTES)]),
        vol.Optional("start"): vol.All(vol.Coerce(int), vol.Range(min=0, max=23)),
        vol.Optional("end"): vol.All(vol.Coerce(int), vol.Range(min=1, max=24)),
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
def _area_of(hass: HomeAssistant, entity_id: str) -> str | None:
    """The area an entity is in: its own, else its device's."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    return device.area_id if device else None


def _follow_speaker(hass: HomeAssistant, options: Any) -> str | None:
    """Follow me: the room speaker in the area the follow sensor says the owner is in, or None."""
    follow = options.get(CONF_READ_ALOUD_FOLLOW)
    state = hass.states.get(follow) if follow else None
    if state is None:
        return None
    area = state.attributes.get("area_id")
    if not area:   # a sensor that says the room by name
        found = ar.async_get(hass).async_get_area_by_name(str(state.state))
        area = found.id if found else None
    if not area:
        return None
    return next((s for s in options.get(CONF_READ_ALOUD_ROOM_SPEAKERS) or [] if _area_of(hass, s) == area), None)


async def _read_aloud_worker(hass: HomeAssistant, speaker: str, queue: asyncio.Queue) -> None:
    """Speak what is queued for one speaker, each reply after the one before has finished."""
    loop = asyncio.get_running_loop()
    cut: asyncio.Event = hass.data.setdefault(READ_ALOUD_CUT, {}).setdefault(speaker, asyncio.Event())
    while True:
        voice, message, context, spoken = await queue.get()
        cut.clear()
        try:
            await hass.services.async_call(
                "tts", "speak",
                {"entity_id": voice, "media_player_entity_id": speaker, "message": message, "cache": False},
                blocking=True, context=context,
            )
            begun = loop.time()
            deadline = begun + READ_ALOUD_START + len(message) / READ_ALOUD_PACE
            started = False
            while loop.time() < deadline and not cut.is_set():
                state = hass.states.get(speaker)
                if state is not None and state.state in ("playing", "buffering"):
                    started = True
                elif started or loop.time() > begun + READ_ALOUD_START:
                    break
                await asyncio.sleep(READ_ALOUD_POLL)
        except Exception:  # one reply that can't be spoken must not stop the ones after it
            _LOGGER.exception("read aloud on %s failed", speaker)
        finally:
            if not spoken.done():
                spoken.set_result(None)
            queue.task_done()


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

    async def trust_start(call: ServiceCall) -> ServiceResponse:
        args: dict[str, Any] = {"minutes": call.data["minutes"], "machines": call.data["machines"]}
        if call.context.user_id:
            found = await hass.auth.async_get_user(call.context.user_id)
            if found:
                args["user"] = found.name           # Hisho's journal: "Home Assistant user <name>" opened it
        return _response(await _coordinator(hass, call).async_action("trust.start", "", args, confirm=True,
                                                                     context=call.context))

    async def parental(call: ServiceCall) -> ServiceResponse:
        control, args = call.data["control"], {}
        if control == "more":
            args["value"] = call.data.get("minutes", 30)
        elif control == "limits":
            days = [None if v in (None, "", "none") else v for v in call.data.get("limits") or []]
            if len(days) not in (1, 7):
                raise ServiceValidationError("limits: one value for every day, or 7 (Monday to Sunday)")
            args["value"] = days * 7 if len(days) == 1 else days
        elif control == "hours":
            args["value"], args["end"] = call.data.get("start", 0), call.data.get("end", 24)
        # time's up and shut down: calling the service is the owner's (or the automation's) yes
        return _response(await _coordinator(hass, call).async_action(
            f"parental.{control}", call.data["machine"], args, confirm=control in ("stop", "off"), context=call.context))

    async def read_aloud(call: ServiceCall) -> ServiceResponse:
        # Speaks one of Hisho's replies on the speaker chosen in the options, in the voice chosen there
        # (Hisho's own, tts.hisho_voice, by default). Off unless the owner turned it on.
        options = _coordinator(hass, call).config_entry.options
        if not options.get(CONF_READ_ALOUD):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="read_aloud_off")
        default = options.get(CONF_READ_ALOUD_SPEAKER)
        speaker = call.data.get("speaker") or _follow_speaker(hass, options) or default
        if not speaker:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="read_aloud_no_speaker")
        voice = options.get(CONF_READ_ALOUD_VOICE) or DEFAULT_READ_ALOUD_VOICE
        queues: dict[str, asyncio.Queue] = hass.data.setdefault(READ_ALOUD_QUEUES, {})
        if call.data["stop"]:   # every speaker read aloud may be using (follow me moves it between rooms)
            playing = [speaker] if call.data.get("speaker") else sorted(
                {speaker, *([default] if default else []), *queues, *(options.get(CONF_READ_ALOUD_ROOM_SPEAKERS) or [])})
            for each in playing:
                waiting = queues.get(each)
                while waiting is not None and not waiting.empty():
                    *_, dropped = waiting.get_nowait()
                    dropped.set_result(None)
                    waiting.task_done()
                hass.data.setdefault(READ_ALOUD_CUT, {}).setdefault(each, asyncio.Event()).set()
            await hass.services.async_call("media_player", "media_stop", {"entity_id": playing},
                                           blocking=True, context=call.context)
            return {"speaker": speaker, "stopped": True}
        last: dict[str, str] = hass.data.setdefault(READ_ALOUD_LAST, {})
        if call.data["repeat"]:
            message = last.get(speaker) or last.get("")   # follow me: the last reply, wherever it played
            if not message:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="read_aloud_nothing_to_repeat")
        elif message := call.data.get("message"):
            last[speaker] = last[""] = message
        else:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="read_aloud_no_message")
        queue = queues.get(speaker)
        if queue is None:
            queue = queues[speaker] = asyncio.Queue()
            hass.async_create_background_task(_read_aloud_worker(hass, speaker, queue), f"hisho read aloud {speaker}")
        spoken: asyncio.Future = hass.loop.create_future()
        queue.put_nowait((voice, message, call.context, spoken))
        waiting = queue.qsize()
        if call.data["wait"]:
            await spoken
        return {"speaker": speaker, "voice": voice, "waiting": waiting}

    for name, handler, schema in (
        ("read_aloud", read_aloud, READ_ALOUD_SCHEMA),
        ("run_action", run_action, RUN_ACTION_SCHEMA),
        ("alert", alert, ALERT_SCHEMA),
        ("maintenance", maintenance, MAINTENANCE_SCHEMA),
        ("job", job, JOB_SCHEMA),
        ("create_job", create_job, CREATE_JOB_SCHEMA),
        ("ask", ask, ASK_SCHEMA),
        ("trust_start", trust_start, TRUST_SCHEMA),
        ("parental", parental, PARENTAL_SCHEMA),
    ):
        # Admins only (Jonathan, 2026-10-26); automations and scripts still call them.
        async_register_admin_service(
            hass, DOMAIN, name, handler, schema=schema, supports_response=SupportsResponse.OPTIONAL
        )
