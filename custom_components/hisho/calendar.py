"""Calendars: "My calendar" (the owner's own, kept by Hisho: his events, each Zoho calendar and Hisho's due dates in
one, each tagged with its company and colour; Hisho's Calendar card paints the colours) and "Due dates" (the
businesses' due dates alone). His own events can be added, changed and deleted here (Hisho's operate scope); Zoho
events and due dates are read only."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEntityFeature, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the due-dates calendar when Hisho shares them, and each company's calendar as it appears."""
    coordinator = entry.runtime_data
    if "due" in coordinator.data.raw:
        async_add_entities([HishoDueDates(coordinator)])
    if coordinator.data.raw.get("calendars"):
        async_add_entities([HishoMyCalendar(coordinator)])


def _events(raw: list[dict]) -> list[CalendarEvent]:
    events = []
    for due in raw:
        try:
            day = date.fromisoformat(str(due.get("date")))
        except ValueError:
            continue
        lines = [line for line in (due.get("note"), f"Aim to start by {due['start']}" if due.get("start") else None) if line]
        events.append(
            CalendarEvent(
                start=day,
                end=day + timedelta(days=1),
                summary=f"{due.get('company')}: {due.get('what')}",
                description="\n".join(lines) or None,
                uid=f"{due.get('company')}|{due.get('what')}|{day.isoformat()}",
            )
        )
    return sorted(events, key=lambda e: e.start)


class HishoDueDates(HishoEntity, CalendarEntity):
    """All-day events on each due date (the next one of a recurring due)."""

    _attr_translation_key = "due_dates"

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the calendar."""
        super().__init__(coordinator, "due_dates")

    def _all(self) -> list[CalendarEvent]:
        return _events(list(self.coordinator.data.raw.get("due") or []))

    @property
    def event(self) -> CalendarEvent | None:
        """The next due date from today."""
        today = dt_util.now().date()
        return next((e for e in self._all() if e.end > today), None)

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Due dates in the window."""
        start, end = start_date.date(), end_date.date()
        return [e for e in self._all() if e.end > start and e.start <= end]


def _when(value: str) -> date | datetime | None:
    if len(value) == 10:
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return dt_util.parse_datetime(value)


def _my_events(raw: list[dict]) -> list[CalendarEvent]:
    events = []
    for ev in raw:
        start, end = _when(str(ev.get("start") or "")), _when(str(ev.get("end") or ""))
        if start is None or end is None or type(start) is not type(end):
            continue
        company = str(ev.get("company") or "")
        lines = [f"Company: {company}" if company else None,
                 "Hisho due date" if ev.get("kind") == "due" else "From Zoho (read only)" if ev.get("kind") == "event" else None,
                 ev.get("notes") or None]
        events.append(
            CalendarEvent(
                start=start,
                end=end,
                # HA colours a calendar, not an event: the company's coloured square and name lead the title
                summary=" ".join(x for x in (ev.get("mark"), f"{company}:" if company else None,
                                             str(ev.get("title") or "(no title)")) if x),
                location=ev.get("place") or None,
                description="\n".join(x for x in lines if x) or None,
                # the owner's own events carry Hisho's id (they can be changed); others a stable read-only key
                uid=f"own:{ev['id']}" if ev.get("kind") == "own" and ev.get("id") else f"{ev.get('uid')}|{ev.get('start')}",
            )
        )
    return events


class HishoMyCalendar(HishoEntity, CalendarEntity):
    """The owner's calendar, read only: every company's events and Hisho's due dates, yesterday to two months ahead."""

    _attr_translation_key = "my_calendar"

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the calendar."""
        super().__init__(coordinator, "my_calendar")

    def _cal(self) -> dict:
        return next(iter(self.coordinator.data.raw.get("calendars") or []), None) or {}

    def _all(self) -> list[CalendarEvent]:
        return _my_events(list(self._cal().get("events") or []))

    @property
    def supported_features(self) -> CalendarEntityFeature:
        """Add, change and delete need Hisho's calendar store and the operate scope."""
        features = CalendarEntityFeature(0)
        if not self._cal().get("editable"):
            return features
        for action, feature in (("calendar.add", CalendarEntityFeature.CREATE_EVENT),
                                ("calendar.edit", CalendarEntityFeature.UPDATE_EVENT),
                                ("calendar.delete", CalendarEntityFeature.DELETE_EVENT)):
            if self.coordinator.data.can(action):
                features |= feature
        return features

    def _own(self, uid: str) -> dict:
        """The owner's own event behind ``uid``; a Zoho event or a due date is read only."""
        ev = next((e for e in self._cal().get("events") or []
                   if e.get("kind") == "own" and uid == f"own:{e.get('id')}"), None)
        if ev is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="calendar_read_only")
        return ev

    def _payload(self, event: dict[str, Any]) -> dict[str, Any]:
        """HA's event fields as Hisho's form: "Company: title" files it under that company (default Personal)."""
        if event.get("rrule"):
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="calendar_repeating")
        start, end = event["dtstart"], event["dtend"]
        summary = str(event.get("summary") or "")
        companies = [str(c["name"]) for c in self._cal().get("companies") or [] if c.get("name")]
        company, _, rest = summary.partition(":")
        if rest.strip() and company.strip().lower() in {c.lower() for c in companies + ["Personal"]}:
            company = next(c for c in companies + ["Personal"] if c.lower() == company.strip().lower())
            summary = rest.strip()
        else:
            company = "Personal"
        all_day = not isinstance(start, datetime)
        return {"title": summary, "company": company, "all_day": all_day, "start": start.isoformat(),
                "end": end.isoformat(), "place": event.get("location") or "", "notes": event.get("description") or ""}

    async def async_create_event(self, **kwargs: Any) -> None:
        """A new event in the owner's calendar."""
        await self.coordinator.async_action("calendar.add", "", self._payload(kwargs), context=self._context)

    async def async_update_event(
        self, uid: str, event: dict[str, Any], recurrence_id: str | None = None, recurrence_range: str | None = None
    ) -> None:
        """Change one of his own events (Hisho refuses it if the phone changed it meanwhile)."""
        own = self._own(uid)
        await self.coordinator.async_action(
            "calendar.edit", own["id"], {**self._payload(event), "etag": own.get("etag") or ""}, context=self._context)

    async def async_delete_event(
        self, uid: str, recurrence_id: str | None = None, recurrence_range: str | None = None
    ) -> None:
        """Delete one of his own events (a repeating one: the whole series)."""
        own = self._own(uid)
        await self.coordinator.async_action("calendar.delete", own["id"], {"etag": own.get("etag") or ""},
                                            context=self._context)

    @property
    def extra_state_attributes(self) -> dict:
        """Each company's colour (for Hisho's Calendar card) and any account Hisho can't read."""
        cal = self._cal()
        return {"companies": {c.get("name"): c.get("color") for c in cal.get("companies") or []},
                "problems": [f"{p.get('company')}: {p.get('error') or p.get('status')}" for p in cal.get("problems") or []]}

    @property
    def event(self) -> CalendarEvent | None:
        """The event going on now, else the next one."""
        now = dt_util.now()
        for e in self._all():
            end = e.end if isinstance(e.end, datetime) else dt_util.start_of_local_day(e.end)
            if end > now:
                return e
        return None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        """Events in the window."""
        out = []
        for e in self._all():
            s = e.start if isinstance(e.start, datetime) else dt_util.start_of_local_day(e.start)
            f = e.end if isinstance(e.end, datetime) else dt_util.start_of_local_day(e.end)
            if f > start_date and s < end_date:
                out.append(e)
        return out
