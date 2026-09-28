"""Sensor platform for Hisho.

Adapted to the live Hisho fleet-health payload shape:
  counts: {machines, reachable, unreachable}
  machines: [{host, reachable, reason, checked_at}]
  services: [{name, type, unit, active, state}]
  compute_workers: [{worker, ok, blocked, detail}]
  all_machines_reachable, all_services_active
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_FLEET_HEALTH, DATA_STATUS, DOMAIN, MANUFACTURER, MODEL
from .coordinator import HishoCoordinator

FLEET_STATES = ["healthy", "degraded", "error"]
COMPUTE_STATES = ["all_ok", "degraded", "no_workers"]


def _fh(data: dict[str, Any]) -> dict[str, Any]:
    return (data or {}).get(DATA_FLEET_HEALTH) or {}


def _status(data: dict[str, Any]) -> dict[str, Any]:
    return (data or {}).get(DATA_STATUS) or {}


def _fleet_state(data: dict[str, Any]) -> str:
    fh = _fh(data)
    if not fh.get("all_machines_reachable") or not fh.get("all_services_active"):
        unreach = (fh.get("counts") or {}).get("unreachable", 0)
        return "error" if unreach > 1 else "degraded"
    return "healthy"


def _compute_state(data: dict[str, Any]) -> str:
    workers = _fh(data).get("compute_workers") or []
    if not workers:
        return "no_workers"
    return "all_ok" if all(w.get("ok") for w in workers) else "degraded"


@dataclass(frozen=True, kw_only=True)
class HishoSensorDescription(SensorEntityDescription):
    """Describes a Hisho sensor."""
    value_fn: Callable[[dict[str, Any]], Any]


SENSORS: tuple[HishoSensorDescription, ...] = (
    HishoSensorDescription(
        key="fleet_health",
        name="Fleet health",
        device_class=SensorDeviceClass.ENUM,
        options=FLEET_STATES,
        value_fn=_fleet_state,
    ),
    HishoSensorDescription(
        key="machines_reachable",
        name="Machines reachable",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="machines",
        value_fn=lambda d: (_fh(d).get("counts") or {}).get("reachable", 0),
    ),
    HishoSensorDescription(
        key="machines_total",
        name="Machines total",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="machines",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (_fh(d).get("counts") or {}).get("machines", 0),
    ),
    HishoSensorDescription(
        key="services_active",
        name="Services active",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement="services",
        value_fn=lambda d: sum(1 for s in (_fh(d).get("services") or []) if s.get("active")),
    ),
    HishoSensorDescription(
        key="compute_state",
        name="Compute workers",
        device_class=SensorDeviceClass.ENUM,
        options=COMPUTE_STATES,
        value_fn=_compute_state,
    ),
    HishoSensorDescription(
        key="last_sweep",
        name="Last fleet sweep",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _fh(d).get("generated_at"),
    ),
    HishoSensorDescription(
        key="version",
        name="Version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _status(d).get("version"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hisho sensors."""
    coordinator: HishoCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[SensorEntity] = [
        HishoSensor(coordinator, entry, description) for description in SENSORS
    ]

    # Per-machine sensors
    known_machines: set[str] = set()
    for machine in (_fh(coordinator.data) or {}).get("machines", []):
        host = machine.get("host")
        if host:
            entities.append(HishoMachineSensor(coordinator, entry, host))
            known_machines.add(host)

    # Per-compute-worker sensors
    known_workers: set[str] = set()
    for worker in (_fh(coordinator.data) or {}).get("compute_workers", []):
        name = worker.get("worker")
        if name:
            entities.append(HishoComputeWorkerSensor(coordinator, entry, name))
            known_workers.add(name)

    async_add_entities(entities)

    def _add_new() -> None:
        new_entities = []
        for machine in (_fh(coordinator.data) or {}).get("machines", []):
            host = machine.get("host")
            if host and host not in known_machines:
                known_machines.add(host)
                new_entities.append(HishoMachineSensor(coordinator, entry, host))
        for worker in (_fh(coordinator.data) or {}).get("compute_workers", []):
            name = worker.get("worker")
            if name and name not in known_workers:
                known_workers.add(name)
                new_entities.append(HishoComputeWorkerSensor(coordinator, entry, name))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class HishoSensor(CoordinatorEntity[HishoCoordinator], SensorEntity):
    """A fleet-wide Hisho sensor."""

    entity_description: HishoSensorDescription
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "control_plane")},
            "name": "Hisho Control Plane",
            "manufacturer": MANUFACTURER,
            "model": MODEL,
            "configuration_url": self.coordinator.base_url,
        }

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data or {})


class HishoMachineSensor(CoordinatorEntity[HishoCoordinator], SensorEntity):
    """Per-machine reachability sensor (from fleet-health machines array)."""

    _attr_has_entity_name = True
    _attr_name = "Status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["reachable", "unreachable"]

    def __init__(self, coordinator, entry, host: str):
        super().__init__(coordinator)
        self._host = host
        self._attr_unique_id = f"{entry.entry_id}_machine_{host}"

    def _machine(self) -> dict[str, Any]:
        for m in (_fh(self.coordinator.data) or {}).get("machines", []):
            if m.get("host") == self._host:
                return m
        return {}

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, f"machine_{self._host}")},
            "name": self._host.replace("-", " ").title(),
            "manufacturer": MANUFACTURER,
            "model": "Hisho machine",
            "via_device": (DOMAIN, "control_plane"),
        }

    @property
    def native_value(self) -> str:
        return "reachable" if self._machine().get("reachable") else "unreachable"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        m = self._machine()
        return {"host": self._host, "reason": m.get("reason"), "checked_at": m.get("checked_at")}


class HishoComputeWorkerSensor(CoordinatorEntity[HishoCoordinator], SensorEntity):
    """Per-compute-worker status sensor."""

    _attr_has_entity_name = True
    _attr_name = "Compute"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["ok", "error", "unknown"]

    def __init__(self, coordinator, entry, worker: str):
        super().__init__(coordinator)
        self._worker = worker
        self._attr_unique_id = f"{entry.entry_id}_compute_{worker}"

    def _worker_data(self) -> dict[str, Any]:
        for w in (_fh(self.coordinator.data) or {}).get("compute_workers", []):
            if w.get("worker") == self._worker:
                return w
        return {}

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, f"machine_{self._worker}")},
            "name": self._worker.replace("-", " ").title(),
            "manufacturer": MANUFACTURER,
            "model": "Hisho machine",
            "via_device": (DOMAIN, "control_plane"),
        }

    @property
    def native_value(self) -> str:
        w = self._worker_data()
        if w.get("ok"):
            return "ok"
        if w.get("blocked"):
            return "error"
        return "unknown"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        w = self._worker_data()
        return {"worker": self._worker, "detail": w.get("detail"), "blocked": w.get("blocked")}
