"""Calendars: "My calendar" (the owner's own: every company's events and Hisho's due dates in one, each tagged with its
company and colour; Hisho's Calendar card paints the colours) and "Due dates" (the businesses' due dates alone)."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

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
                 "Hisho due date" if ev.get("kind") == "due" else None]
        events.append(
            CalendarEvent(
                start=start,
                end=end,
                # HA colours a calendar, not an event: the company's coloured square and name lead the title
                summary=" ".join(x for x in (ev.get("mark"), f"{company}:" if company else None,
                                             str(ev.get("title") or "(no title)")) if x),
                location=ev.get("place") or None,
                description="\n".join(x for x in lines if x) or None,
                uid=f"{ev.get('uid')}|{ev.get('start')}",
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
