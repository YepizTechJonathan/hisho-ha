"""To-do lists: "Needs you" (ticking acknowledges in Hisho) and "Today" (the tasks in Today.md)."""

from __future__ import annotations

import hashlib

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoEntity

PARALLEL_UPDATES = 1

SEVERITY_ORDER = {"critical": 0, "decision": 1, "warn": 2, "info": 3}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the needs-you list."""
    coordinator = entry.runtime_data
    entities: list[TodoListEntity] = [HishoNeedsYou(coordinator)]
    if "today" in coordinator.data.raw:
        entities.append(HishoToday(coordinator))
    async_add_entities(entities)


class HishoNeedsYou(HishoEntity, TodoListEntity):
    """Open alerts, most urgent first. Done = acknowledged in Hisho (no more pushes until it resolves)."""

    _attr_translation_key = "needs_you"

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the list."""
        super().__init__(coordinator, "needs_you")

    @property
    def supported_features(self) -> TodoListEntityFeature:
        """Ticking needs Hisho's operate scope; without it the list is read-only."""
        if self.coordinator.data.can("alert.ack"):
            return TodoListEntityFeature.UPDATE_TODO_ITEM
        return TodoListEntityFeature(0)

    @property
    def todo_items(self) -> list[TodoItem]:
        """One item per needs-you entry (pairing requests are answered in Hisho, so not here)."""
        alerts = [
            a for a in self.coordinator.data.alerts.values() if not a.get("off") and not a.get("pair")
        ]
        alerts.sort(key=lambda a: (bool(a.get("muted")), SEVERITY_ORDER.get(a.get("severity") or "", 9)))
        items = []
        for alert in alerts:
            lines = [f"{(alert.get('severity') or '').upper()}: {alert.get('detail') or ''}".strip()]
            inv = alert.get("investigation") or {}
            if inv.get("cause"):
                lines.append(f"Claude: {inv['cause']}")
            elif inv.get("status") == "running":
                lines.append("Claude is investigating…")
            items.append(
                TodoItem(
                    uid=alert["id"],
                    summary=alert.get("title") or alert["id"],
                    status=TodoItemStatus.COMPLETED if alert.get("muted") else TodoItemStatus.NEEDS_ACTION,
                    description="\n\n".join(line for line in lines if line),
                )
            )
        return items

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Ticked = acknowledge; unticked = un-acknowledge."""
        if item.uid not in self.coordinator.data.alerts:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="alert_gone")
        verb = "ack" if item.status == TodoItemStatus.COMPLETED else "unack"
        await self.coordinator.async_action(f"alert.{verb}", item.uid, context=self._context)


def _task_uid(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


class HishoToday(HishoEntity, TodoListEntity):
    """The owner's Tasks in Today.md: add one, tick it (Hisho moves it to Done recently next morning)."""

    _attr_translation_key = "today"

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the list."""
        super().__init__(coordinator, "today")

    def _tasks(self) -> list[dict]:
        return list((self.coordinator.data.raw.get("today") or {}).get("tasks") or [])

    @property
    def supported_features(self) -> TodoListEntityFeature:
        """Add and tick need Hisho's operate scope."""
        features = TodoListEntityFeature(0)
        if self.coordinator.data.can("task.add"):
            features |= TodoListEntityFeature.CREATE_TODO_ITEM
        if self.coordinator.data.can("task.done"):
            features |= TodoListEntityFeature.UPDATE_TODO_ITEM
        return features

    @property
    def todo_items(self) -> list[TodoItem]:
        """Open tasks first, as in the note."""
        return [
            TodoItem(
                uid=_task_uid(t["text"]),
                summary=t["text"],
                status=TodoItemStatus.COMPLETED if t.get("done") else TodoItemStatus.NEEDS_ACTION,
            )
            for t in self._tasks()
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """A new task at the end of Tasks."""
        await self.coordinator.async_action("task.add", "", {"text": item.summary or ""}, context=self._context)

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Ticked or unticked (renaming is done in the note itself)."""
        task = next((t for t in self._tasks() if _task_uid(t["text"]) == item.uid), None)
        if task is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="task_gone")
        if item.summary and item.summary != task["text"]:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="task_rename")
        verb = "done" if item.status == TodoItemStatus.COMPLETED else "undone"
        await self.coordinator.async_action(f"task.{verb}", "", {"text": task["text"]}, context=self._context)

