"""Hisho's services: every control the Hisho dashboard has, for scripts, automations and cards.

``hisho.run_action`` reaches all of them (the list Hisho allows this Home Assistant
is the ``allowed`` attribute of the hub's "Actions allowed" sensor); the others are
the everyday ones with friendlier fields.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from http import HTTPStatus
import io
import logging
import secrets
from typing import Any
import wave

from aiohttp import web

import voluptuous as vol

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.util import dt as dt_util

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
READ_ALOUD_LEAD = 0.6    # s a speaker takes from play_media to sound (it fetches and buffers first)
READ_ALOUD_TAIL = 0.2    # s of quiet after a sentence's measured end before the next is sent
READ_ALOUD_FIRST = 8.0   # s of speech a reply's first clip waits for (when more of it is coming) before it plays
READ_ALOUD_HOLD = 3.0    # s at most it waits for that, from when its first sentence is ready
READ_ALOUD_CLIP_MAX = 60.0   # s of speech joined into one clip at most (more waits for the next clip)
READ_ALOUD_FORMAT = {   # speech made as plain WAV in one format, so it can be joined (Kokoro's own rate)
    "preferred_format": "wav", "preferred_sample_rate": 24000, "preferred_sample_channels": 1,
    "preferred_sample_bytes": 2,
}
READ_ALOUD_CLIP_URL = f"/api/{DOMAIN}/read_aloud"
READ_ALOUD_CLIPS_KEPT = 20   # the latest clips stay fetchable (a speaker that retries its fetch finds them)
READ_ALOUD_QUEUES = f"{DOMAIN}_read_aloud"   # hass.data: speaker -> its queue
READ_ALOUD_CLIPS = f"{DOMAIN}_read_aloud_clips"     # hass.data: clip token -> WAV bytes
READ_ALOUD_MAKING = f"{DOMAIN}_read_aloud_making"   # hass.data: speaker -> the speech being made last
READ_ALOUD_LAST = f"{DOMAIN}_read_aloud_last"   # hass.data: speaker -> the last reply sent to it, for repeat
READ_ALOUD_CUT = f"{DOMAIN}_read_aloud_cut"     # hass.data: speaker -> set while a stop silences it
READ_ALOUD_STARTED = f"{DOMAIN}_read_aloud_started"   # hass.data: message id -> done when it starts playing
READ_ALOUD_SPOKEN = f"{DOMAIN}_read_aloud_spoken"     # hass.data: message id -> done when it has been spoken

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
        # id names a message; await_start answers once the message with that id starts playing (or is over),
        # so a reader's highlight moves with the voice instead of when the sentence is sent.
        vol.Optional("id"): cv.string,
        vol.Optional("await_start"): cv.string,
        # more: more of the same reply follows (a reader sending it sentence by sentence), so its start waits
        # a little to play with the rest in one clip.
        vol.Optional("more", default=False): cv.boolean,
        # thread / color: where the reply came from (the reader's name and colour band), shown on speaker screens
        vol.Optional("thread"): cv.string,
        vol.Optional("color"): cv.string,
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


READ_ALOUD_ROOM = f"{DOMAIN}_read_aloud_room"     # hass.data: the room follow me plays in now
READ_ALOUD_REPLY = f"{DOMAIN}_read_aloud_reply"   # hass.data: the speakers the reply being read plays on
READ_ALOUD_SETTLE = 45.0   # s the follow sensor must say a new room before replies move there (BLE rooms flicker)
READ_ALOUD_REPLY_GAP = 60.0   # s after which a reply that said "more" is taken as over


def _room_speakers(hass: HomeAssistant, options: Any, area: str | None) -> str | None:
    # every room speaker in that room plays it (e.g. a satellite and a Dante speaker), as one group
    if not area:
        return None
    return ",".join(s for s in options.get(CONF_READ_ALOUD_ROOM_SPEAKERS) or [] if _area_of(hass, s) == area) or None


def _follow_speaker(hass: HomeAssistant, options: Any) -> str | None:
    """Follow me: the room speakers in the room the owner is in ("a,b"), or None. A room counts once the follow
    sensor has said it for READ_ALOUD_SETTLE seconds, and a room without room speakers keeps the last one, so a
    flickering Bluetooth room sensor doesn't bounce replies between speakers (or back to the default)."""
    follow = options.get(CONF_READ_ALOUD_FOLLOW)
    state = hass.states.get(follow) if follow else None
    if state is None:
        return None
    area = state.attributes.get("area_id")
    if not area:   # a sensor that says the room by name
        found = ar.async_get(hass).async_get_area_by_name(str(state.state))
        area = found.id if found else None
    room = hass.data.setdefault(READ_ALOUD_ROOM, {})
    here = _room_speakers(hass, options, area)
    settled = (dt_util.utcnow() - state.last_changed).total_seconds() >= READ_ALOUD_SETTLE
    if here and (settled or not room.get("area")) and area != room.get("area"):
        room["area"] = area
    return _room_speakers(hass, options, room.get("area"))


@dataclass
class _Speech:
    """A message's speech, made ahead: plain 16-bit PCM, so speech made for the messages behind it can be
    joined on to it and played as one clip (no start-up gap on the speaker between them)."""

    pcm: bytes
    rate: int
    width: int
    channels: int

    @property
    def seconds(self) -> float:
        return len(self.pcm) / (self.rate * self.width * self.channels)

    def joins(self, other: _Speech) -> bool:
        return (self.rate, self.width, self.channels) == (other.rate, other.width, other.channels)


async def _prepare(hass: HomeAssistant, voice: str, message: str) -> _Speech:
    """Make a message's speech ahead of time, as WAV in one fixed format, and measure it. The speaker's own
    playing/idle state can't be trusted for timing (the Satellite1 says idle in mid-sentence and playing
    after it ended), and its length must be known to join speech and move the reader's highlight."""
    from homeassistant.components.tts import async_get_media_source_audio  # noqa: PLC0415
    from homeassistant.components.tts.media_source import generate_media_source_id  # noqa: PLC0415

    media = generate_media_source_id(hass, message, engine=voice, cache=True, options=READ_ALOUD_FORMAT)
    _ext, data = await async_get_media_source_audio(hass, media)
    with wave.open(io.BytesIO(data)) as wav:
        return _Speech(wav.readframes(wav.getnframes()), wav.getframerate(), wav.getsampwidth(), wav.getnchannels())


async def _prepare_after(hass: HomeAssistant, before: asyncio.Task | None, voice: str, message: str) -> _Speech:
    """Speech is made one message at a time, in order: the voice gives the message that plays next all its
    speed instead of sharing it with the ones behind (a stop that cancels the one before doesn't stop this)."""
    if before is not None:
        await asyncio.wait([before])
    return await _prepare(hass, voice, message)


def _clip(hass: HomeAssistant, speech: list[_Speech]) -> str:
    """Join speech into one WAV clip served by Home Assistant itself; the URL the speaker fetches it from."""
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(speech[0].channels)
        wav.setsampwidth(speech[0].width)
        wav.setframerate(speech[0].rate)
        wav.writeframes(b"".join(s.pcm for s in speech))
    clips: dict[str, bytes] = hass.data.setdefault(READ_ALOUD_CLIPS, {})
    token = secrets.token_urlsafe(16)
    clips[token] = out.getvalue()
    while len(clips) > READ_ALOUD_CLIPS_KEPT:
        clips.pop(next(iter(clips)))
    return f"{READ_ALOUD_CLIP_URL}/{token}.wav"


class ReadAloudClipView(HomeAssistantView):
    """Read aloud's clips, for the speaker to fetch (like TTS's own proxy: an unguessable name, no login)."""

    url = READ_ALOUD_CLIP_URL + "/{token}.wav"
    name = "api:hisho:read_aloud"
    requires_auth = False

    async def get(self, request: web.Request, token: str) -> web.Response:
        data = request.app[KEY_HASS].data.get(READ_ALOUD_CLIPS, {}).get(token)
        if data is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.Response(body=data, content_type="audio/wav")


class _Waiting(asyncio.Queue):
    """A speaker's queue that can look at the message behind the one being played."""

    def peek(self) -> tuple:
        return self._queue[0]


READ_ALOUD_EVENT = f"{DOMAIN}_read_aloud"   # fired as each message starts sounding: speaker screens show it


def _targets(speaker: str) -> str | list[str]:
    """One speaker, or a room's group of them ("a,b") as a list of entity ids."""
    each = speaker.split(",")
    return each[0] if len(each) == 1 else each


def _sounding(hass: HomeAssistant, speaker: str, queue: _Waiting, message: str, seconds: float,
              info: dict[str, Any]) -> None:
    """Tell whoever listens (a speaker's screen) which message is sounding now, for how long, and what follows."""
    following = None if queue.empty() else queue.peek()[1]
    for each in speaker.split(","):   # a group: each speaker's screen hears it
        hass.bus.async_fire(READ_ALOUD_EVENT, {"speaker": each, "text": message, "seconds": round(seconds, 2),
                                               "next": following, "waiting": queue.qsize(), **info})


async def _read_aloud_worker(hass: HomeAssistant, speaker: str, queue: _Waiting) -> None:
    """Speak what is queued for one speaker, each message after the one before has finished. Messages whose
    speech is already made when one starts are joined on to it and played as one clip."""
    loop = asyncio.get_running_loop()
    cut: asyncio.Event = hass.data.setdefault(READ_ALOUD_CUT, {}).setdefault(speaker, asyncio.Event())
    mid_reply = False   # the last clip ended inside a reply (more of it was coming)
    while True:
        batch = [await queue.get()]
        voice, message, context, _more, info, spoken, begins, ready = batch[0]
        cut.clear()
        try:
            try:
                speech = [await ready]
            except Exception:  # noqa: BLE001  # no measured speech: tts.speak, timed by the speaker's state
                _LOGGER.debug("read aloud: could not make %r ahead", message, exc_info=True)
            else:
                # a reply's start waits a little for the rest of it (on its way, or being made), so it plays as
                # one clip; inside a reply the next clip doesn't wait (that would lengthen the pause it's in)
                hold = loop.time() + (0 if mid_reply else READ_ALOUD_HOLD)
                while sum(s.seconds for s in speech) < READ_ALOUD_CLIP_MAX:
                    if queue.empty():
                        if batch[-1][3] and sum(s.seconds for s in speech) < READ_ALOUD_FIRST \
                                and loop.time() < hold and not cut.is_set():
                            await asyncio.sleep(READ_ALOUD_POLL)
                            continue
                        break
                    behind = queue.peek()[-1]
                    if not behind.done():   # a short start waits a little for the speech behind it
                        if sum(s.seconds for s in speech) >= READ_ALOUD_FIRST or loop.time() >= hold:
                            break
                        await asyncio.wait([behind], timeout=hold - loop.time())
                        continue
                    if behind.cancelled() or behind.exception() is not None or not speech[0].joins(behind.result()):
                        break
                    batch.append(queue.get_nowait())
                    speech.append(behind.result())
                mid_reply = batch[-1][3]
                if cut.is_set():   # stopped while it waited
                    mid_reply = False
                    continue
                await hass.services.async_call(
                    "media_player", "play_media",
                    {"entity_id": _targets(speaker), "media_content_id": _clip(hass, speech), "media_content_type": "music",
                     "announce": True},
                    blocking=True, context=context,
                )
                try:   # the speaker starts sounding a moment after play_media; a stop cuts the wait short
                    await asyncio.wait_for(cut.wait(), READ_ALOUD_LEAD)
                except TimeoutError:
                    pass
                for item, each in zip(batch, speech, strict=True):
                    *_, spoken_, begins_, _ready = item
                    _sounding(hass, speaker, queue, item[1], each.seconds, item[4])
                    if not begins_.done():
                        begins_.set_result(None)
                    try:   # as long as its speech lasts, unless a stop cuts it
                        await asyncio.wait_for(cut.wait(), each.seconds)
                    except TimeoutError:
                        pass
                    if each is not speech[-1] and not spoken_.done():
                        spoken_.set_result(None)
                    if cut.is_set():
                        break
                try:
                    await asyncio.wait_for(cut.wait(), READ_ALOUD_TAIL)
                except TimeoutError:
                    pass
                continue
            await hass.services.async_call(
                "tts", "speak",
                {"entity_id": voice, "media_player_entity_id": _targets(speaker), "message": message, "cache": False},
                blocking=True, context=context,
            )
            begun = loop.time()
            deadline = begun + READ_ALOUD_START + len(message) / READ_ALOUD_PACE
            started = False
            while loop.time() < deadline and not cut.is_set():
                states = [hass.states.get(each) for each in speaker.split(",")]
                if any(st is not None and st.state in ("playing", "buffering") for st in states):
                    if not started:
                        _sounding(hass, speaker, queue, message, len(message) / READ_ALOUD_PACE, info)
                    started = True
                    if not begins.done():
                        begins.set_result(None)
                elif started or loop.time() > begun + READ_ALOUD_START:
                    break
                await asyncio.sleep(READ_ALOUD_POLL)
        except Exception:  # one message that can't be spoken must not stop the ones after it
            _LOGGER.exception("read aloud on %s failed", speaker)
        finally:
            for *_, spoken_, begins_, _ready in batch:
                for each in (begins_, spoken_):
                    if not each.done():
                        each.set_result(None)
                queue.task_done()


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services."""
    if getattr(hass, "http", None) is not None:
        hass.http.register_view(ReadAloudClipView)

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
        default = options.get(CONF_READ_ALOUD_SPEAKER)   # one speaker, or several that all play it together
        if isinstance(default, list):
            default = ",".join(default) or None
        # a reply keeps the speakers its first sentence went to, for all the sentences that follow it ("more")
        reply = hass.data.setdefault(READ_ALOUD_REPLY, {})
        going_on = reply.get("more") and hass.loop.time() - reply.get("at", 0) < READ_ALOUD_REPLY_GAP
        speaker = call.data.get("speaker") or (reply.get("speaker") if going_on and not call.data["stop"] else None) \
            or _follow_speaker(hass, options) or default
        if not speaker:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="read_aloud_no_speaker")
        voice = options.get(CONF_READ_ALOUD_VOICE) or DEFAULT_READ_ALOUD_VOICE
        queues: dict[str, asyncio.Queue] = hass.data.setdefault(READ_ALOUD_QUEUES, {})
        if call.data["stop"]:   # every speaker read aloud may be using (follow me moves it between rooms)
            playing = [speaker] if call.data.get("speaker") else sorted(
                {speaker, *([default] if default else []), *queues, *(options.get(CONF_READ_ALOUD_ROOM_SPEAKERS) or [])})
            for key in playing:   # a key is one speaker or a room's group of them ("a,b")
                waiting = queues.get(key)
                while waiting is not None and not waiting.empty():
                    *_, spoken_, begins_, ready_ = waiting.get_nowait()
                    ready_.cancel()
                    for done in (begins_, spoken_):
                        if not done.done():
                            done.set_result(None)
                    waiting.task_done()
                hass.data.setdefault(READ_ALOUD_CUT, {}).setdefault(key, asyncio.Event()).set()
            await hass.services.async_call("media_player", "media_stop",
                                           {"entity_id": sorted({s for key in playing for s in key.split(",")})},
                                           blocking=True, context=call.context)
            return {"speaker": speaker, "stopped": True}
        starts: dict[str, asyncio.Future] = hass.data.setdefault(READ_ALOUD_STARTED, {})
        if wanted := call.data.get("await_start"):
            if (begins := starts.get(wanted)) is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(begins), READ_ALOUD_START + 60)
                except TimeoutError:
                    pass
            return {"speaker": speaker, "started": True}
        ident = call.data.get("id")
        done_by_id: dict[str, asyncio.Future] = hass.data.setdefault(READ_ALOUD_SPOKEN, {})
        if ident and ident in done_by_id:   # sent again (a reader that retried): never said twice
            if call.data["wait"]:
                await asyncio.shield(done_by_id[ident])
            return {"speaker": speaker, "voice": voice, "duplicate": True}
        last: dict[str, str] = hass.data.setdefault(READ_ALOUD_LAST, {})
        if call.data["repeat"]:
            message = last.get(speaker) or last.get("")   # follow me: the last reply, wherever it played
            if not message:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="read_aloud_nothing_to_repeat")
        elif message := call.data.get("message"):
            last[speaker] = last[""] = message
            if not call.data.get("speaker"):
                reply.update(speaker=speaker, more=call.data["more"], at=hass.loop.time())
        else:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="read_aloud_no_message")
        queue = queues.get(speaker)
        if queue is None:
            queue = queues[speaker] = _Waiting()
            hass.async_create_background_task(_read_aloud_worker(hass, speaker, queue), f"hisho read aloud {speaker}")
        spoken: asyncio.Future = hass.loop.create_future()
        begins = hass.loop.create_future()
        if ident:
            starts[ident], done_by_id[ident] = begins, spoken
            for kept in (starts, done_by_id):
                while len(kept) > 200:   # only the latest messages are asked about
                    kept.pop(next(iter(kept)))
        # the speech is made now, while what is ahead of it plays (after the speech queued before it): no gap
        # for making it later
        making: dict[str, asyncio.Task] = hass.data.setdefault(READ_ALOUD_MAKING, {})
        ready = making[speaker] = hass.async_create_task(
            _prepare_after(hass, making.get(speaker), voice, message), "hisho read aloud prepare")
        info = {k: call.data[k] for k in ("thread", "color") if call.data.get(k)}
        queue.put_nowait((voice, message, call.context, call.data["more"], info, spoken, begins, ready))
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
