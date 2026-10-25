"""Base entities: one for the Hisho hub device, one for a machine's device."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import HishoCoordinator


def hub_device_info(coordinator: HishoCoordinator) -> DeviceInfo:
    """The Hisho hub: the fleet as a whole."""
    raw = coordinator.data.raw
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.instance_id)},
        name=raw.get("name") or "Hisho",
        manufacturer=MANUFACTURER,
        model="Control plane",
        sw_version=raw.get("version"),
        configuration_url=raw.get("dashboard_url"),
        entry_type=DeviceEntryType.SERVICE,
    )


class HishoEntity(CoordinatorEntity[HishoCoordinator]):
    """An entity on the Hisho hub device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HishoCoordinator, key: str) -> None:
        """Set up the entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.instance_id}_{key}"
        self._attr_device_info = hub_device_info(coordinator)


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


class HishoBoxEntity(CoordinatorEntity[HishoCoordinator]):
    """An entity on a box one of Hisho's add-ons knows: a server, a VM, storage, network gear."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HishoCoordinator, device_id: str, key: str) -> None:
        """Set up the entity."""
        super().__init__(coordinator)
        self.device_id = device_id
        box = coordinator.data.devices[device_id]
        self._attr_unique_id = f"{coordinator.instance_id}_device_{device_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={coordinator.device_identifier(device_id)},
            name=box.get("name") or device_id,
            manufacturer=MANUFACTURER,
            model=(box.get("kind") or "device").capitalize(),
            via_device=(DOMAIN, coordinator.instance_id),
        )

    @property
    def box(self) -> dict[str, Any] | None:
        """This box in the latest snapshot."""
        return self.coordinator.data.devices.get(self.device_id)

    @property
    def available(self) -> bool:
        """Unavailable once no add-on reports the box."""
        return super().available and self.box is not None


class HishoAddonEntity(CoordinatorEntity[HishoCoordinator]):
    """An entity on one of Hisho's add-ons (a service device under the hub)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: HishoCoordinator, name: str, key: str) -> None:
        """Set up the entity."""
        super().__init__(coordinator)
        self.addon_name = name
        addon = coordinator.data.addons[name]
        self._attr_unique_id = f"{coordinator.instance_id}_addon_{name}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={coordinator.addon_identifier(name)},
            name=addon.get("title") or name,
            manufacturer=MANUFACTURER,
            model="Add-on",
            sw_version=addon.get("version"),
            via_device=(DOMAIN, coordinator.instance_id),
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def addon(self) -> dict[str, Any] | None:
        """This add-on in the latest snapshot."""
        return self.coordinator.data.addons.get(self.addon_name)

    @property
    def available(self) -> bool:
        """Unavailable once the add-on is removed."""
        return super().available and self.addon is not None


def machine_items(data: Any, machine_id: str) -> list[dict[str, Any]]:
    """What add-ons report about a fleet machine (e.g. Prometheus's node_exporter)."""
    return list((data.controls.get(machine_id) or {}).get("items") or [])
