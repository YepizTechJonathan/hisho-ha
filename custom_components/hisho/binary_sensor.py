"""Binary sensor platform for Hisho."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_FLEET_HEALTH, DOMAIN, MANUFACTURER
from .coordinator import HishoCoordinator


def _fh(data: dict[str, Any]) -> dict[str, Any]:
    return (data or {}).get(DATA_FLEET_HEALTH) or {}


@dataclass(frozen=True, kw_only=True)
class HishoBinaryDescription(BinarySensorEntityDescription):
    """Describes a fleet-wide Hisho binary sensor."""
    value_fn: Callable[[dict[str, Any]], bool]


BINARY_SENSORS: tuple[HishoBinaryDescription, ...] = (
    HishoBinaryDescription(
        key="fleet_ok",
        name="Fleet OK",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: not bool(_fh(d).get("all_machines_reachable")),
    ),
    HishoBinaryDescription(
        key="services_ok",
        name="Services OK",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: not bool(_fh(d).get("all_services_active")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hisho binary sensors."""
    coordinator: HishoCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[BinarySensorEntity] = [
        HishoBinarySensor(coordinator, entry, description)
        for description in BINARY_SENSORS
    ]

    # Per-machine reachability binary sensors
    known_machines: set[str] = set()
    for machine in _fh(coordinator.data).get("machines", []):
        host = machine.get("host")
        if host:
            entities.append(HishoMachineReachability(coordinator, entry, host))
            known_machines.add(host)

    # Per-service active binary sensors
    known_services: set[str] = set()
    for service in _fh(coordinator.data).get("services", []):
        name = service.get("name")
        if name:
            entities.append(HishoServiceActive(coordinator, entry, name))
            known_services.add(name)

    async_add_entities(entities)

    def _add_new() -> None:
        new_entities = []
        for machine in _fh(coordinator.data).get("machines", []):
            host = machine.get("host")
            if host and host not in known_machines:
                known_machines.add(host)
                new_entities.append(HishoMachineReachability(coordinator, entry, host))
        for service in _fh(coordinator.data).get("services", []):
            name = service.get("name")
            if name and name not in known_services:
                known_services.add(name)
                new_entities.append(HishoServiceActive(coordinator, entry, name))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class HishoBinarySensor(CoordinatorEntity[HishoCoordinator], BinarySensorEntity):
    """A fleet-wide problem binary sensor."""

    entity_description: HishoBinaryDescription
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
            "configuration_url": self.coordinator.base_url,
        }

    @property
    def is_on(self) -> bool:
        return bool(self.entity_description.value_fn(self.coordinator.data or {}))


class HishoMachineReachability(CoordinatorEntity[HishoCoordinator], BinarySensorEntity):
    """Whether a machine is reachable."""

    _attr_has_entity_name = True
    _attr_name = "Reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, coordinator, entry, host: str):
        super().__init__(coordinator)
        self._host = host
        self._attr_unique_id = f"{entry.entry_id}_machine_{host}_reachable"

    def _machine(self) -> dict[str, Any]:
        for m in _fh(self.coordinator.data).get("machines", []):
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
    def is_on(self) -> bool:
        return bool(self._machine().get("reachable"))


class HishoServiceActive(CoordinatorEntity[HishoCoordinator], BinarySensorEntity):
    """Whether a service is active."""

    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.RUNNING

    def __init__(self, coordinator, entry, service_name: str):
        super().__init__(coordinator)
        self._service_name = service_name
        self._attr_name = service_name.replace("-", " ").replace("_", " ").title()
        self._attr_unique_id = f"{entry.entry_id}_service_{service_name}"

    def _service(self) -> dict[str, Any]:
        for s in _fh(self.coordinator.data).get("services", []):
            if s.get("name") == self._service_name:
                return s
        return {}

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, "control_plane")},
            "name": "Hisho Control Plane",
            "manufacturer": MANUFACTURER,
            "configuration_url": self.coordinator.base_url,
        }

    @property
    def is_on(self) -> bool:
        return bool(self._service().get("active"))
