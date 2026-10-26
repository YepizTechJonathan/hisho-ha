"""Calendar "Due dates": every company's compliance and tax due dates from Hisho's Businesses."""

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
    """Add the due-dates calendar when Hisho shares them."""
    coordinator = entry.runtime_data
    if "due" in coordinator.data.raw:
        async_add_entities([HishoDueDates(coordinator)])


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
