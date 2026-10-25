"""Event "Hisho alert": fires when a needs-you item appears or resolves, for automations."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the alert event."""
    async_add_entities([HishoAlertEvent(entry.runtime_data)])


class HishoAlertEvent(HishoEntity, EventEntity):
    """``opened`` / ``resolved`` with the alert's id, severity and title."""

    _attr_translation_key = "alert"
    _attr_event_types = ["opened", "resolved"]

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the event; what is open at start-up is not news."""
        super().__init__(coordinator, "alert_event")
        self._seen = dict(coordinator.data.alerts)

    @callback
    def _handle_coordinator_update(self) -> None:
        current = self.coordinator.data.alerts
        fired = False
        for alert_id in current.keys() - self._seen.keys():
            alert = current[alert_id]
            if alert.get("pair") or alert.get("off"):
                continue
            self._trigger_event("opened", _attrs(alert))
            fired = True
        for alert_id in self._seen.keys() - current.keys():
            self._trigger_event("resolved", _attrs(self._seen[alert_id]))
            fired = True
        self._seen = dict(current)
        if fired:
            self.async_write_ha_state()
        else:
            super()._handle_coordinator_update()


def _attrs(alert: dict) -> dict:
    return {k: alert.get(k) for k in ("id", "severity", "title", "detail", "machine", "source")}
