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

from .const import FOLDER_STATES, MACHINE_STATES, WORKER_STATES
from .coordinator import HishoConfigEntry, HishoCoordinator, HishoData
from .entity import HishoEntity, HishoMachineEntity

PARALLEL_UPDATES = 0  # the coordinator does all the fetching


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
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class HishoHubSensor(HishoEntity, SensorEntity):
    """A fleet-wide reading."""

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


class HishoMachineSensor(HishoMachineEntity, SensorEntity):
    """A reading of one machine."""

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
