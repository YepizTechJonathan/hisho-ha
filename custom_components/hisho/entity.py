"""Base entities: one for the Hisho hub device, one for a machine's device."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import HishoCoordinator


class HishoEntity(CoordinatorEntity[HishoCoordinator]):
    """An entity on the Hisho hub device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HishoCoordinator, key: str) -> None:
        """Set up the entity."""
        super().__init__(coordinator)
        raw = coordinator.data.raw
        self._attr_unique_id = f"{coordinator.instance_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.instance_id)},
            name=raw.get("name") or "Hisho",
            manufacturer=MANUFACTURER,
            model="Control plane",
            sw_version=raw.get("version"),
            configuration_url=raw.get("dashboard_url"),
            entry_type=DeviceEntryType.SERVICE,
        )


class HishoMachineEntity(CoordinatorEntity[HishoCoordinator]):
    """An entity on one machine's device.

    Available only while Hisho still lists the machine; the platforms also make
    a reading the machine's agent supplies unavailable while it is missing (a
    sleeping machine's memory is unknown, not zero).
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: HishoCoordinator, machine_id: str, key: str) -> None:
        """Set up the entity."""
        super().__init__(coordinator)
        self.machine_id = machine_id
        machine = coordinator.data.machines[machine_id]
        self._attr_unique_id = f"{coordinator.instance_id}_{machine_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={coordinator.machine_identifier(machine_id)},
            name=machine.get("name") or machine_id,
            manufacturer=MANUFACTURER,
            model=machine.get("role") or "Machine",
            sw_version=machine.get("agent_version"),
            via_device=(DOMAIN, coordinator.instance_id),
        )

    @property
    def machine(self) -> dict[str, Any] | None:
        """This machine in the latest snapshot."""
        return self.coordinator.data.machines.get(self.machine_id)

    @property
    def available(self) -> bool:
        """Unavailable once the machine leaves the fleet."""
        return super().available and self.machine is not None
