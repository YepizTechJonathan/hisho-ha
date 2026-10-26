"""Buttons: Hisho's one-press controls (wake, park, restart, add-on buttons, housekeeping).

A button exists only when Hisho lets this Home Assistant do that action. Buttons
that power something off or remove something are created disabled, like the
dashboard keeps them behind a confirm: enabling one is the owner saying yes, so
its press is sent confirmed. Cards ask before they press.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoAddonEntity, HishoBoxEntity, HishoEntity, HishoMachineEntity, machine_items

PARALLEL_UPDATES = 1  # one press at a time: each is a real action on the fleet


@dataclass(frozen=True, kw_only=True)
class HishoButtonDescription(ButtonEntityDescription):
    """A press = one Hisho action."""

    action: str
    danger: bool = False


HUB_BUTTONS = (
    HishoButtonDescription(key="hide_acked", translation_key="hide_acked", action="alerts.hide_acked"),
    HishoButtonDescription(
        key="clear_phone", translation_key="clear_phone", action="push.clear",
        entity_category=EntityCategory.CONFIG,
    ),
    HishoButtonDescription(
        key="reap_jobs", translation_key="reap_jobs", action="jobs.reap", entity_category=EntityCategory.CONFIG
    ),
)
WAKE = HishoButtonDescription(key="wake", translation_key="wake", action="machine.wake")
PARK = HishoButtonDescription(key="park", translation_key="park", action="machine.park", danger=True)


class _Press:
    """Shared press: run the action, confirmed when the button is a danger one (it had to be enabled)."""

    coordinator: HishoCoordinator
    entity_description: HishoButtonDescription
    _target: str
    _args: dict[str, Any]

    async def async_press(self) -> None:
        """Press it."""
        await self.coordinator.async_action(
            self.entity_description.action, self._target, self._args, confirm=self.entity_description.danger, context=self._context)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add buttons as Hisho reports what can be pressed."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        new: list[ButtonEntity] = []

        def add(key: str, entity: ButtonEntity) -> None:
            if key not in known:
                known.add(key)
                new.append(entity)

        for desc in HUB_BUTTONS:
            if data.can(desc.action):
                add(f"hub:{desc.key}", HishoHubButton(coordinator, desc))
        for machine_id in data.machines:
            controls = data.controls.get(machine_id) or {}
            for desc in (WAKE, PARK):
                if desc.key in (controls.get("power") or []) and data.can(desc.action):
                    add(f"machine:{machine_id}:{desc.key}", HishoMachineButton(coordinator, machine_id, desc))
            if data.can("unit.restart"):
                for unit in controls.get("units") or []:
                    if "restart" in (unit.get("actions") or []):
                        add(f"unit:{machine_id}:{unit['unit']}",
                            HishoUnitRestartButton(coordinator, machine_id, unit))
            if data.can("device.press"):
                for item in machine_items(data, machine_id):
                    for act in item.get("actions") or []:
                        add(f"item:{machine_id}:{item['addon']}:{item['entry']}:{item['id']}:{act['id']}",
                            HishoMachineItemButton(coordinator, machine_id, item, act))
        if data.can("device.press"):
            for device_id, box in data.devices.items():
                for item in box.get("items") or []:
                    for act in item.get("actions") or []:
                        add(f"item:{device_id}:{item['addon']}:{item['entry']}:{item['id']}:{act['id']}",
                            HishoBoxItemButton(coordinator, device_id, item, act))
        if data.can("addon.run"):
            for name, addon in data.addons.items():
                for act in addon.get("actions") or []:
                    add(f"addon:{name}:{act['id']}", HishoAddonActionButton(coordinator, name, act))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class HishoHubButton(_Press, HishoEntity, ButtonEntity):
    """Housekeeping for the whole fleet."""

    def __init__(self, coordinator: HishoCoordinator, description: HishoButtonDescription) -> None:
        """Set up the button."""
        super().__init__(coordinator, description.key)
        self.entity_description = description
        self._target, self._args = "", {}


class HishoMachineButton(_Press, HishoMachineEntity, ButtonEntity):
    """Wake or park a reserve machine."""

    def __init__(self, coordinator: HishoCoordinator, machine_id: str, description: HishoButtonDescription) -> None:
        """Set up the button."""
        super().__init__(coordinator, machine_id, description.key)
        self.entity_description = description
        self._attr_entity_registry_enabled_default = not description.danger
        self._target, self._args = machine_id, {}


class HishoUnitRestartButton(_Press, HishoMachineEntity, ButtonEntity):
    """Restart one of the services Hisho lets the dashboard control."""

    def __init__(self, coordinator: HishoCoordinator, machine_id: str, unit: dict[str, Any]) -> None:
        """Set up the button."""
        super().__init__(coordinator, machine_id, f"restart_{unit['unit']}")
        self.entity_description = HishoButtonDescription(
            key=f"restart_{unit['unit']}", translation_key="restart_unit", action="unit.restart"
        )
        self._attr_translation_placeholders = {"unit": unit.get("label") or unit["unit"]}
        self._target, self._args = machine_id, {"unit": unit["unit"]}


def _item_description(item: dict[str, Any], act: dict[str, Any]) -> HishoButtonDescription:
    return HishoButtonDescription(
        key=f"{item['addon']}_{item['entry']}_{item['id']}_{act['id']}",
        translation_key="item_action",
        action="device.press",
        danger=bool(act.get("danger")),
    )


def _item_args(item: dict[str, Any], act: dict[str, Any]) -> dict[str, Any]:
    return {"entry": item["entry"], "item": item["id"], "action": act["id"]}


class HishoMachineItemButton(_Press, HishoMachineEntity, ButtonEntity):
    """An add-on's button about a fleet machine (e.g. reinstall node_exporter)."""

    def __init__(
        self, coordinator: HishoCoordinator, machine_id: str, item: dict[str, Any], act: dict[str, Any]
    ) -> None:
        """Set up the button."""
        desc = _item_description(item, act)
        super().__init__(coordinator, machine_id, desc.key)
        self.entity_description = desc
        self._attr_translation_placeholders = {"item": item.get("name") or item["id"],
                                               "action": act.get("label") or act["id"]}
        self._attr_entity_registry_enabled_default = not desc.danger
        self._target, self._args = item["addon"], _item_args(item, act)


class HishoBoxItemButton(_Press, HishoBoxEntity, ButtonEntity):
    """An add-on's button on a box it knows (e.g. Proxmox: shut down TruenasCE)."""

    def __init__(self, coordinator: HishoCoordinator, device_id: str, item: dict[str, Any], act: dict[str, Any]) -> None:
        """Set up the button."""
        desc = _item_description(item, act)
        super().__init__(coordinator, device_id, desc.key)
        self.entity_description = desc
        self._attr_translation_placeholders = {"item": item.get("name") or item["id"],
                                               "action": act.get("label") or act["id"]}
        self._attr_entity_registry_enabled_default = not desc.danger
        self._target, self._args = item["addon"], _item_args(item, act)


class HishoAddonActionButton(_Press, HishoAddonEntity, ButtonEntity):
    """One of an add-on's declared actions (e.g. Web Factory: deploy dev now)."""

    def __init__(self, coordinator: HishoCoordinator, name: str, act: dict[str, Any]) -> None:
        """Set up the button."""
        desc = HishoButtonDescription(
            key=f"action_{act['id']}", translation_key="addon_action", action="addon.run",
            danger=bool(act.get("danger")),
        )
        super().__init__(coordinator, name, desc.key)
        self.entity_description = desc
        self._attr_translation_placeholders = {"action": act.get("label") or act["id"]}
        self._attr_entity_registry_enabled_default = not desc.danger
        self._target, self._args = name, {"action": act["id"]}
