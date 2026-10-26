"""Binary sensors: online, sync, services, and whether Core answers / is in planned maintenance."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.util import dt as dt_util

from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoEntity, HishoMachineEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class HishoMachineBinaryDescription(BinarySensorEntityDescription):
    """A yes/no about one machine."""

    value_fn: Callable[[dict[str, Any]], bool | None]
    exists_fn: Callable[[dict[str, Any]], bool] = lambda _: True
    from_agent: bool = True


MACHINE_BINARY_SENSORS: tuple[HishoMachineBinaryDescription, ...] = (
    HishoMachineBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda m: m.get("online"),
        from_agent=False,
    ),
    HishoMachineBinaryDescription(
        key="sync_connected",
        translation_key="sync_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda m: m.get("sync_connected"),
        # Core's own Syncthing is not a peer of itself.
        exists_fn=lambda m: m.get("sync_connected") is not None,
        from_agent=False,
    ),
    HishoMachineBinaryDescription(
        key="safe_to_shut_down",
        translation_key="safe_to_shut_down",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.get("safe_to_shut_down"),
    ),
)

SYNC_PROBLEM = BinarySensorEntityDescription(
    key="sync_problem", translation_key="sync_problem", device_class=BinarySensorDeviceClass.PROBLEM
)
SYNC_PEER = BinarySensorEntityDescription(
    key="sync_peer", translation_key="sync_peer", device_class=BinarySensorDeviceClass.CONNECTIVITY
)
SERVICE = BinarySensorEntityDescription(
    key="service", translation_key="service", device_class=BinarySensorDeviceClass.RUNNING
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the hub's sync sensor, then machines, services and peers as they appear."""
    coordinator = entry.runtime_data
    async_add_entities([HishoSyncProblem(coordinator), HishoCoreReachable(coordinator), HishoCoreMaintenance(coordinator),
                        HishoCritical(coordinator)])
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        new: list[BinarySensorEntity] = []
        for machine_id, machine in data.machines.items():
            for desc in MACHINE_BINARY_SENSORS:
                key = f"machine:{machine_id}:{desc.key}"
                if key not in known and desc.exists_fn(machine):
                    known.add(key)
                    new.append(HishoMachineBinarySensor(coordinator, machine_id, desc))
            for service in machine.get("services") or []:
                key = f"service:{machine_id}:{service.get('id')}"
                if service.get("id") and key not in known:
                    known.add(key)
                    new.append(HishoServiceSensor(coordinator, machine_id, service["id"]))
        for peer_id in data.peers:
            if (key := f"peer:{peer_id}") not in known:
                known.add(key)
                new.append(HishoSyncPeer(coordinator, peer_id))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class HishoSyncProblem(HishoEntity, BinarySensorEntity):
    """On when the synced vault needs attention."""

    entity_description = SYNC_PROBLEM

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, SYNC_PROBLEM.key)

    @property
    def available(self) -> bool:
        """Unavailable when Hisho could not read Syncthing."""
        return super().available and bool(self.coordinator.data.sync.get("available"))

    @property
    def is_on(self) -> bool:
        """True when something needs attention."""
        return bool(self.coordinator.data.sync.get("problem"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What is wrong, in Hisho's words."""
        sync = self.coordinator.data.sync
        return {"severity": sync.get("severity"), "problems": sync.get("problems") or []}


class HishoSyncPeer(HishoEntity, BinarySensorEntity):
    """A Syncthing peer that is not a fleet machine, e.g. a phone."""

    entity_description = SYNC_PEER

    def __init__(self, coordinator: HishoCoordinator, peer_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, f"sync_peer_{peer_id}")
        self.peer_id = peer_id
        peer = coordinator.data.peers[peer_id]
        self._attr_translation_placeholders = {"peer": peer.get("name") or peer_id}

    @property
    def available(self) -> bool:
        """Unavailable once the peer is removed."""
        return super().available and self.peer_id in self.coordinator.data.peers

    @property
    def is_on(self) -> bool | None:
        """Connected now."""
        peer = self.coordinator.data.peers.get(self.peer_id)
        return bool(peer.get("connected")) if peer else None


class HishoMachineBinarySensor(HishoMachineEntity, BinarySensorEntity):
    """A yes/no about one machine."""

    entity_description: HishoMachineBinaryDescription

    def __init__(
        self, coordinator: HishoCoordinator, machine_id: str, description: HishoMachineBinaryDescription
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, machine_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """A reading the agent supplies is unavailable while it is missing."""
        if not super().available:
            return False
        return not self.entity_description.from_agent or self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        """The reading."""
        machine = self.machine
        if machine is None:
            return None
        value = self.entity_description.value_fn(machine)
        return None if value is None else bool(value)


class HishoServiceSensor(HishoMachineEntity, BinarySensorEntity):
    """One Hisho service on the machine that runs it."""

    entity_description = SERVICE

    def __init__(self, coordinator: HishoCoordinator, machine_id: str, service_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, machine_id, f"service_{service_id}")
        self.service_id = service_id
        self._attr_translation_placeholders = {"service": service_id}

    @property
    def _service(self) -> dict[str, Any] | None:
        machine = self.machine or {}
        return next((s for s in machine.get("services") or [] if s.get("id") == self.service_id), None)

    @property
    def available(self) -> bool:
        """Unavailable once Hisho stops watching the service."""
        return super().available and self._service is not None

    @property
    def is_on(self) -> bool | None:
        """Running (a one-shot: its last run succeeded)."""
        service = self._service
        return bool(service.get("running")) if service else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The systemd unit and its state."""
        service = self._service or {}
        return {"unit": service.get("unit"), "state": service.get("state")}


class HishoCoreReachable(HishoEntity, BinarySensorEntity):
    """Whether Hisho Core answers. Stays available when it does not: that is the point
    (the "Hisho Core down" blueprint triggers on it)."""

    _attr_translation_key = "core_reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "core_reachable")

    @property
    def available(self) -> bool:
        """Always: it reports the outage instead of joining it."""
        return True

    @property
    def is_on(self) -> bool:
        """The last poll worked."""
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When Core last answered (the blueprint's message says since when it is down)."""
        seen = getattr(self.coordinator, "last_seen", None)
        return {"last_seen": seen.isoformat() if seen else None}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    return dt_util.parse_datetime(str(value))


class HishoCoreMaintenance(HishoEntity, BinarySensorEntity):
    """On while Hisho's Core machine is in planned maintenance (or the whole fleet is), as the last good poll
    said. Stays available when Core does not answer, and remembers: a planned outage of Kihon must not alarm.
    A window with an end time turns off at that time by itself, Core answering or not, so an outage that
    outlasts its window does alarm. The "Hisho Core down" blueprint reads it."""

    _attr_translation_key = "core_maintenance"

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "core_maintenance")
        self._unsub_end: Any = None

    def _entry(self) -> dict[str, Any] | None:
        data = self.coordinator.data
        if data is None:
            return None
        maint = data.maintenance
        return maint.get("fleet") or (maint.get("machines") or {}).get(data.raw.get("core_host") or "")

    @property
    def available(self) -> bool:
        """Always: it is what tells a planned outage from a real one."""
        return True

    @property
    def is_on(self) -> bool:
        """In maintenance now: an entry, and its end (if any) not yet passed."""
        entry = self._entry()
        if not entry:
            return False
        until = _parse_time(entry.get("until"))
        return until is None or dt_util.utcnow() < until

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Why, since when, until when (none = until someone ends it)."""
        entry = self._entry() or {}
        return {k: entry.get(k) for k in ("reason", "since", "until", "by")}

    async def async_added_to_hass(self) -> None:
        """Also re-check at the window's end."""
        await super().async_added_to_hass()
        self._schedule_end()

    @callback
    def _handle_coordinator_update(self) -> None:
        self._schedule_end()
        super()._handle_coordinator_update()

    @callback
    def _schedule_end(self) -> None:
        if self._unsub_end:
            self._unsub_end()
            self._unsub_end = None
        until = _parse_time((self._entry() or {}).get("until"))
        if until and until > dt_util.utcnow():
            self._unsub_end = async_track_point_in_utc_time(self.hass, self._window_ended, until)

    @callback
    def _window_ended(self, _now: datetime) -> None:
        self._unsub_end = None
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Stop the end-of-window timer."""
        if self._unsub_end:
            self._unsub_end()
        await super().async_will_remove_from_hass()


class HishoCritical(HishoEntity, BinarySensorEntity):
    """On while a critical needs-you item is open and not acknowledged."""

    _attr_translation_key = "critical_alert"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, "critical_alert")

    def _open(self) -> list[dict[str, Any]]:
        return [a for a in self.coordinator.data.alerts.values()
                if a.get("severity") == "critical" and not a.get("muted") and not a.get("off")]

    @property
    def is_on(self) -> bool:
        """Something critical needs the owner."""
        return bool(self._open())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Which."""
        return {"alerts": [a.get("title") for a in self._open()]}

