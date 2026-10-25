"""Switches: maintenance (fleet, machine, box, add-on), services on/off, add-ons on/off.

Maintenance on holds alerts, auto-park and jobs for what it covers; for one fleet
machine it also shuts that machine's VM down (keep it running with the
``hisho.maintenance`` service and ``keep_vms``). Turning a switch is deliberate,
so it is sent confirmed, as the dashboard's own toggle is after its question.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoAddonEntity, HishoBoxEntity, HishoEntity, HishoMachineEntity

PARALLEL_UPDATES = 1

FLEET = "fleet"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add switches as Hisho reports what can be switched."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        new: list[SwitchEntity] = []

        def add(key: str, entity: SwitchEntity) -> None:
            if key not in known:
                known.add(key)
                new.append(entity)

        maint = data.can("maintenance.on") and data.can("maintenance.off")
        if maint:
            add("fleet", HishoFleetMaintenance(coordinator))
        for machine_id in data.machines:
            if maint:
                add(f"machine:{machine_id}", HishoMachineMaintenance(coordinator, machine_id))
            if data.can("unit.start") and data.can("unit.stop"):
                for unit in (data.controls.get(machine_id) or {}).get("units") or []:
                    if {"start", "stop"} <= set(unit.get("actions") or []):
                        add(f"unit:{machine_id}:{unit['unit']}", HishoUnitSwitch(coordinator, machine_id, unit))
        for device_id in data.devices:
            if maint:
                add(f"box:{device_id}", HishoBoxMaintenance(coordinator, device_id))
        for name in data.addons:
            if maint:
                add(f"addon-maint:{name}", HishoAddonMaintenance(coordinator, name))
            if data.can("addon.enable") and data.can("addon.disable"):
                add(f"addon:{name}", HishoAddonEnabled(coordinator, name))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class _Maintenance:
    """Maintenance for one target."""

    coordinator: HishoCoordinator
    _target: str
    _attr_translation_key = "maintenance"

    def _entry(self) -> dict[str, Any] | None:
        maint = self.coordinator.data.maintenance
        if self._target == FLEET:
            return maint.get("fleet")
        return (maint.get("machines") or {}).get(self._target)

    @property
    def is_on(self) -> bool:
        """In maintenance now."""
        return self._entry() is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Why, since when, until when, and whether it was switched off from Hisho."""
        return self._entry() or {}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start maintenance."""
        await self.coordinator.async_action(
            "maintenance.on", self._target, {"reason": "from Home Assistant"}, confirm=True
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """End maintenance."""
        await self.coordinator.async_action("maintenance.off", self._target)


class HishoFleetMaintenance(_Maintenance, HishoEntity, SwitchEntity):
    """The whole fleet in maintenance."""

    _attr_translation_key = "fleet_maintenance"

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the switch."""
        super().__init__(coordinator, "fleet_maintenance")
        self._target = FLEET


class HishoMachineMaintenance(_Maintenance, HishoMachineEntity, SwitchEntity):
    """One machine in maintenance."""

    def __init__(self, coordinator: HishoCoordinator, machine_id: str) -> None:
        """Set up the switch."""
        super().__init__(coordinator, machine_id, "maintenance")
        self._target = machine_id


class HishoBoxMaintenance(_Maintenance, HishoBoxEntity, SwitchEntity):
    """A box in maintenance, so automation leaves it alone (e.g. TrueNAS while it cools)."""

    def __init__(self, coordinator: HishoCoordinator, device_id: str) -> None:
        """Set up the switch."""
        super().__init__(coordinator, device_id, "maintenance")
        self._target = device_id


class HishoAddonMaintenance(_Maintenance, HishoAddonEntity, SwitchEntity):
    """An add-on in maintenance: its alerts are held."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HishoCoordinator, name: str) -> None:
        """Set up the switch."""
        super().__init__(coordinator, name, "maintenance")
        self._target = f"addon:{name}"


class HishoUnitSwitch(HishoMachineEntity, SwitchEntity):
    """A service Hisho lets the dashboard start and stop (e.g. Ollama)."""

    _attr_translation_key = "unit"

    def __init__(self, coordinator: HishoCoordinator, machine_id: str, unit: dict[str, Any]) -> None:
        """Set up the switch."""
        super().__init__(coordinator, machine_id, f"unit_{unit['unit']}")
        self._unit = unit["unit"]
        self._attr_translation_placeholders = {"unit": unit.get("label") or unit["unit"]}

    def _state(self) -> dict[str, Any] | None:
        units = (self.coordinator.data.controls.get(self.machine_id) or {}).get("units") or []
        return next((u for u in units if u.get("unit") == self._unit), None)

    @property
    def available(self) -> bool:
        """Unavailable while its machine cannot say (asleep, unreachable)."""
        unit = self._state()
        return super().available and unit is not None and unit.get("state") not in (None, "asleep", "unknown")

    @property
    def is_on(self) -> bool:
        """Running."""
        return (self._state() or {}).get("state") == "active"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """The systemd unit and its state."""
        return {"unit": self._unit, "state": (self._state() or {}).get("state")}

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Start it."""
        await self.coordinator.async_action("unit.start", self.machine_id, {"unit": self._unit})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Stop it."""
        await self.coordinator.async_action("unit.stop", self.machine_id, {"unit": self._unit}, confirm=True)


class HishoAddonEnabled(HishoAddonEntity, SwitchEntity):
    """An add-on turned on or off (changes Hisho's setup)."""

    _attr_translation_key = "addon_enabled"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: HishoCoordinator, name: str) -> None:
        """Set up the switch."""
        super().__init__(coordinator, name, "enabled")

    @property
    def is_on(self) -> bool:
        """Turned on."""
        return bool((self.addon or {}).get("enabled"))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the add-on on."""
        await self.coordinator.async_action("addon.enable", self.addon_name, {"host": (self.addon or {}).get("host") or ""})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the add-on off."""
        await self.coordinator.async_action("addon.disable", self.addon_name, confirm=True)
