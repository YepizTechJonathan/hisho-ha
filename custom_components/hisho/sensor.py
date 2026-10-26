"""Sensors: fleet counts and sync on the hub, readings on each machine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .const import DEVICE_STATES, FOLDER_STATES, HEALTH_STATES, MACHINE_STATES, WORKER_STATES
from .coordinator import HishoConfigEntry, HishoCoordinator, HishoData
from .entity import HishoAddonEntity, HishoBoxEntity, HishoEntity, HishoMachineEntity

PARALLEL_UPDATES = 0  # the coordinator does all the fetching


def _waiting_on_owner(project: dict[str, Any]) -> bool:
    """Waiting on a person (not on Claude or Hisho), or active with open asks."""
    waiting = str(project.get("waiting_on") or "").lower()
    return (waiting not in ("", "claude", "hisho")) or bool(project.get("asks") and project.get("status") == "active")


def _timestamp(value: Any) -> datetime | None:
    return dt_util.parse_datetime(value) if isinstance(value, str) else None


@dataclass(frozen=True, kw_only=True)
class HishoHubSensorDescription(SensorEntityDescription):
    """A reading of the whole fleet."""

    value_fn: Callable[[HishoData], StateType]


@dataclass(frozen=True, kw_only=True)
class HishoMachineSensorDescription(SensorEntityDescription):
    """A reading of one machine."""

    value_fn: Callable[[dict[str, Any]], StateType | datetime]
    # Whether this machine gets the entity at all (no GPU, no GPU sensor).
    exists_fn: Callable[[dict[str, Any]], bool] = lambda _: True
    # Unavailable while the value is missing: the machine is asleep or agentless.
    from_agent: bool = True


HUB_SENSORS: tuple[HishoHubSensorDescription, ...] = (
    HishoHubSensorDescription(
        key="machines_online",
        translation_key="machines_online",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.raw.get("counts") or {}).get("reachable"),
    ),
    HishoHubSensorDescription(
        key="machines_faulted",
        translation_key="machines_faulted",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: (d.raw.get("counts") or {}).get("faulted"),
    ),
    HishoHubSensorDescription(
        key="needs_you",
        translation_key="needs_you_count",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for a in d.alerts.values() if not a.get("muted") and not a.get("off")),
    ),
    HishoHubSensorDescription(
        key="jobs_active",
        translation_key="jobs_active",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for j in d.raw.get("jobs") or [] if j.get("status") in ("queued", "routed", "running")),
    ),
    HishoHubSensorDescription(
        key="jobs_failed",
        translation_key="jobs_failed",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for j in d.raw.get("jobs") or [] if j.get("status") == "failed"),
    ),
    HishoHubSensorDescription(
        key="projects_active",
        translation_key="projects_active",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for p in d.raw.get("projects") or [] if p.get("status") == "active")
        if "projects" in d.raw else None,
    ),
    HishoHubSensorDescription(
        key="projects_waiting",
        translation_key="projects_waiting",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: sum(1 for p in d.raw.get("projects") or [] if _waiting_on_owner(p))
        if "projects" in d.raw else None,
    ),
    HishoHubSensorDescription(
        key="addon_updates",
        translation_key="addon_updates",
        value_fn=lambda d: sum(1 for a in d.raw.get("store") or [] if a.get("installed") and a.get("update_available"))
        if "store" in d.raw else None,
    ),
    HishoHubSensorDescription(
        key="actions_allowed",
        translation_key="actions_allowed",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: len(d.allowed),
    ),
    HishoHubSensorDescription(
        key="sync_conflicts",
        translation_key="sync_conflicts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.sync.get("conflicts"),
    ),
)

MACHINE_SENSORS: tuple[HishoMachineSensorDescription, ...] = (
    HishoMachineSensorDescription(
        key="state",
        translation_key="machine_state",
        device_class=SensorDeviceClass.ENUM,
        options=MACHINE_STATES,
        value_fn=lambda m: m.get("state"),
        from_agent=False,
    ),
    HishoMachineSensorDescription(
        key="memory_usage",
        translation_key="memory_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: m.get("memory_percent"),
    ),
    HishoMachineSensorDescription(
        key="disk_usage",
        translation_key="disk_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: m.get("disk_percent"),
    ),
    HishoMachineSensorDescription(
        key="gpu_usage",
        translation_key="gpu_usage",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda m: m.get("gpu_percent"),
        exists_fn=lambda m: m.get("gpu_name") is not None,
    ),
    HishoMachineSensorDescription(
        key="jobs_running",
        translation_key="jobs_running",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.get("jobs_running"),
    ),
    HishoMachineSensorDescription(
        key="jobs_queued",
        translation_key="jobs_queued",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda m: m.get("jobs_queued"),
    ),
    HishoMachineSensorDescription(
        key="last_boot",
        translation_key="last_boot",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: _timestamp(m.get("booted_at")),
    ),
    HishoMachineSensorDescription(
        key="containers_running",
        translation_key="containers_running",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda m: m.get("containers_running"),
    ),
    HishoMachineSensorDescription(
        key="heartbeat_age",
        translation_key="heartbeat_age",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Changes on every poll; worth having, not worth recording by default.
        entity_registry_enabled_default=False,
        value_fn=lambda m: m.get("heartbeat_age_seconds"),
    ),
)

FOLDER_SENSOR = SensorEntityDescription(
    key="sync_folder",
    translation_key="sync_folder",
    device_class=SensorDeviceClass.ENUM,
    options=FOLDER_STATES,
)
WORKER_SENSOR = SensorEntityDescription(
    key="compute_worker",
    translation_key="compute_worker",
    device_class=SensorDeviceClass.ENUM,
    options=WORKER_STATES,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the hub sensors, then each machine's, folder's and worker's as they appear."""
    coordinator = entry.runtime_data
    async_add_entities(HishoHubSensor(coordinator, d) for d in HUB_SENSORS)
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        new: list[SensorEntity] = []
        for machine_id, machine in data.machines.items():
            for desc in MACHINE_SENSORS:
                key = f"machine:{machine_id}:{desc.key}"
                if key not in known and desc.exists_fn(machine):
                    known.add(key)
                    new.append(HishoMachineSensor(coordinator, machine_id, desc))
        for folder_id in data.folders:
            if (key := f"folder:{folder_id}") not in known:
                known.add(key)
                new.append(HishoFolderSensor(coordinator, folder_id))
        for worker_id in data.workers:
            if (key := f"worker:{worker_id}") not in known:
                known.add(key)
                new.append(HishoWorkerSensor(coordinator, worker_id))
        for machine_id in data.machines:
            if (data.controls.get(machine_id) or {}).get("power") and (key := f"awake:{machine_id}") not in known:
                known.add(key)
                new.append(HishoAwakeSensor(coordinator, machine_id))
        for device_id, box in data.devices.items():
            for kind in ("state", "health"):
                if (key := f"box:{device_id}:{kind}") not in known:
                    known.add(key)
                    new.append(HishoBoxSensor(coordinator, device_id, kind))
            for item in box.get("items") or []:
                if (key := f"box:{device_id}:item:{item['addon']}:{item['entry']}:{item['id']}") not in known:
                    known.add(key)
                    new.append(HishoBoxItemSensor(coordinator, device_id, item))
        for name in data.addons:
            if (key := f"addon:{name}") not in known:
                known.add(key)
                new.append(HishoAddonStatus(coordinator, name))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class HishoHubSensor(HishoEntity, SensorEntity):
    """A fleet-wide reading."""

    # Lists for cards: shown, not kept in history (they would fill the recorder).
    _unrecorded_attributes = frozenset({"jobs", "projects", "alerts", "allowed", "store", "repositories"})

    entity_description: HishoHubSensorDescription

    def __init__(self, coordinator: HishoCoordinator, description: HishoHubSensorDescription) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Unavailable while Hisho cannot give the number."""
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> StateType:
        """The reading."""
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """For the counts that list things: which."""
        data = self.coordinator.data
        key = self.entity_description.key
        if key == "actions_allowed":
            return {"allowed": sorted(data.allowed)}
        if key == "needs_you":
            return {"alerts": [{k: a.get(k) for k in ("id", "severity", "title")}
                               for a in data.alerts.values() if not a.get("muted") and not a.get("off")]}
        if key in ("jobs_active", "jobs_failed"):
            return {"jobs": [{k: j.get(k) for k in ("id", "kind", "status", "worker", "updated_at", "reason")}
                             for j in data.raw.get("jobs") or []]} if key == "jobs_active" else None
        if key == "addon_updates":
            return {"store": data.raw.get("store") or [], "repositories": data.raw.get("repositories") or []}
        if key == "projects_active":
            return {"projects": [{k: p.get(k) for k in ("name", "title", "company", "status", "waiting_on", "next", "asks")}
                                 for p in data.raw.get("projects") or []]}
        if key == "projects_waiting":
            return {"projects": [{"name": p.get("title") or p.get("name"), "asks": p.get("asks")}
                                 for p in data.raw.get("projects") or []
                                 if _waiting_on_owner(p)]}
        return None


class HishoMachineSensor(HishoMachineEntity, SensorEntity):
    """A reading of one machine. Its State also carries what cards need to act on it."""

    _unrecorded_attributes = frozenset({"items", "power"})

    entity_description: HishoMachineSensorDescription

    def __init__(
        self, coordinator: HishoCoordinator, machine_id: str, description: HishoMachineSensorDescription
    ) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, machine_id, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """A reading the agent supplies is unavailable while it is missing."""
        if not super().available:
            return False
        return not self.entity_description.from_agent or self.native_value is not None

    @property
    def native_value(self) -> StateType | datetime:
        """The reading."""
        machine = self.machine
        return self.entity_description.value_fn(machine) if machine is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """On State only: the machine's id, why it is off, and what add-ons offer for it (cards use these)."""
        if self.entity_description.key != "state":
            return None
        machine = self.machine or {}
        controls = self.coordinator.data.controls.get(self.machine_id) or {}
        return {"machine": self.machine_id, "reason": machine.get("reason"), "role": machine.get("role"),
                "power": controls.get("power") or [], "items": controls.get("items") or []}


class HishoFolderSensor(HishoEntity, SensorEntity):
    """How one synced folder is doing."""

    entity_description = FOLDER_SENSOR

    def __init__(self, coordinator: HishoCoordinator, folder_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, f"sync_folder_{folder_id}")
        self.folder_id = folder_id
        folder = coordinator.data.folders[folder_id]
        self._attr_translation_placeholders = {"folder": folder.get("name") or folder_id}

    @property
    def available(self) -> bool:
        """Unavailable once the folder is no longer synced."""
        return super().available and self.folder_id in self.coordinator.data.folders

    @property
    def native_value(self) -> str | None:
        """ok, info, warn, error or unknown."""
        folder = self.coordinator.data.folders.get(self.folder_id) or {}
        status = folder.get("status")
        return status if status in FOLDER_STATES else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What Syncthing says about it."""
        folder = self.coordinator.data.folders.get(self.folder_id) or {}
        return {
            "verdict": folder.get("verdict"),
            "out_of_sync_files": folder.get("out_of_sync_files"),
            "last_sync": folder.get("last_sync_at"),
        }


class HishoWorkerSensor(HishoEntity, SensorEntity):
    """Whether one compute worker can take jobs."""

    entity_description = WORKER_SENSOR

    def __init__(self, coordinator: HishoCoordinator, worker_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, f"compute_worker_{worker_id}")
        self.worker_id = worker_id
        self._attr_translation_placeholders = {"worker": worker_id}

    @property
    def available(self) -> bool:
        """Unavailable once the worker is gone."""
        return super().available and self.worker_id in self.coordinator.data.workers

    @property
    def native_value(self) -> str | None:
        """running, blocked or unknown."""
        state = (self.coordinator.data.workers.get(self.worker_id) or {}).get("state")
        return state if state in WORKER_STATES else "unknown"


class HishoAwakeSensor(HishoMachineEntity, SensorEntity):
    """Why a reserve machine is up and when it parks (Hisho's "awake" line)."""

    _attr_translation_key = "awake"

    def __init__(self, coordinator: HishoCoordinator, machine_id: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, machine_id, "awake")

    def _awake(self) -> dict[str, Any]:
        return (self.coordinator.data.controls.get(self.machine_id) or {}).get("awake") or {}

    @property
    def native_value(self) -> str | None:
        """Short: "parks in 16m", "deep sleep"."""
        return self._awake().get("short")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The full sentence and when it parks."""
        awake = self._awake()
        return {"summary": awake.get("summary"), "parks_at": awake.get("parks_at"),
                "server_asleep_since": ((self.coordinator.data.controls.get(self.machine_id) or {})
                                        .get("deep_sleep") or {}).get("since")}


class HishoBoxSensor(HishoBoxEntity, SensorEntity):
    """A box's power state or health, as the add-ons see it."""

    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: HishoCoordinator, device_id: str, kind: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, device_id, kind)
        self._kind = kind
        self._attr_translation_key = f"box_{kind}"
        self._attr_options = DEVICE_STATES if kind == "state" else HEALTH_STATES

    @property
    def native_value(self) -> str | None:
        """on / off / asleep / offline, or ok / warn / problem."""
        value = (self.box or {}).get(self._kind)
        return value if value in self._attr_options else "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The add-ons' one-line summary and which add-ons see it."""
        if self._kind != "health":
            return {}
        box = self.box or {}
        return {"summary": box.get("summary"), "kind": box.get("kind"), "addons": box.get("addons")}


class HishoBoxItemSensor(HishoBoxEntity, SensorEntity):
    """One thing an add-on watches on a box (a pool, a VM, updates)."""

    _unrecorded_attributes = frozenset({"facts", "actions"})

    _attr_translation_key = "box_item"

    def __init__(self, coordinator: HishoCoordinator, device_id: str, item: dict[str, Any]) -> None:
        """Set up the sensor."""
        key = f"item_{item['addon']}_{item['entry']}_{item['id']}"
        super().__init__(coordinator, device_id, key)
        self._ref = (item["addon"], item["entry"], item["id"])
        self._attr_translation_placeholders = {"item": item.get("name") or item["id"]}

    def _item(self) -> dict[str, Any] | None:
        return next((i for i in (self.box or {}).get("items") or []
                     if (i.get("addon"), i.get("entry"), i.get("id")) == self._ref), None)

    @property
    def available(self) -> bool:
        """Unavailable once the add-on stops reporting it."""
        return super().available and self._item() is not None

    @property
    def native_value(self) -> str | None:
        """up / warn / down / off."""
        return (self._item() or {}).get("state")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """What the add-on says about it."""
        item = self._item() or {}
        return {"facts": item.get("facts") or [], "addon": item.get("addon"), "entry": item.get("entry"),
                "item_id": item.get("id"), "actions": item.get("actions") or []}


class HishoAddonStatus(HishoAddonEntity, SensorEntity):
    """How an add-on is doing: ok, needs a look, problem, needs setup, off."""

    _unrecorded_attributes = frozenset({"actions", "problems", "settings"})

    _attr_translation_key = "addon_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["ok", "warn", "down", "needs_setup", "off"]

    def __init__(self, coordinator: HishoCoordinator, name: str) -> None:
        """Set up the sensor."""
        super().__init__(coordinator, name, "status")

    @property
    def native_value(self) -> str | None:
        """Its state, as the dashboard's add-on card shows it."""
        addon = self.addon or {}
        if not addon.get("enabled"):
            return "off"
        if addon.get("problems"):
            return "needs_setup"
        state = addon.get("state")
        return state if state in ("warn", "down") else "ok"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Problems, version, where it runs."""
        addon = self.addon or {}
        return {"problems": addon.get("problems") or [], "version": addon.get("version"),
                "host": addon.get("host"), "kind": addon.get("kind"), "addon": self.addon_name,
                "actions": addon.get("actions") or [], "settings": addon.get("settings") or {}}

