"""Button platform for Hisho — IPMI and compute power actions.

All actions are gated by the API token and the execute=1 safety pattern.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DATA_FLEET_HEALTH, DOMAIN, ENDPOINT_COMPUTE, ENDPOINT_IPMI, MANUFACTURER
from .coordinator import HishoCoordinator

_LOGGER = logging.getLogger(__name__)


def _fh(data: dict[str, Any]) -> dict[str, Any]:
    return (data or {}).get(DATA_FLEET_HEALTH) or {}


# IPMI actions for each target (proxmox2 is the known target)
IPMI_ACTIONS = ["power-status", "power-on", "power-off", "power-cycle"]

# Compute actions for each worker
COMPUTE_ACTIONS = ["check", "wake", "sleep"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Hisho button entities."""
    coordinator: HishoCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[ButtonEntity] = []

    # Compute worker buttons
    for worker in _fh(coordinator.data).get("compute_workers", []):
        worker_name = worker.get("worker")
        if worker_name:
            for action in COMPUTE_ACTIONS:
                entities.append(
                    HishoComputeButton(coordinator, entry, worker_name, action)
                )

    # IPMI buttons — we know proxmox2 is the IPMI target from the registry
    for action in IPMI_ACTIONS:
        entities.append(HishoIpmiButton(coordinator, entry, "proxmox2", action))

    async_add_entities(entities)


class HishoComputeButton(CoordinatorEntity[HishoCoordinator], ButtonEntity):
    """A button to trigger a compute action (check/wake/sleep) on a worker."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, worker: str, action: str):
        super().__init__(coordinator)
        self._worker = worker
        self._action = action
        self._attr_name = f"Compute {action.title()}"
        self._attr_unique_id = f"{entry.entry_id}_compute_{worker}_{action}"
        self._attr_icon = {
            "check": "mdi:magnify",
            "wake": "mdi:power",
            "sleep": "mdi:sleep",
        }.get(action, "mdi:cog")

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, f"machine_{self._worker}")},
            "name": self._worker.replace("-", " ").title(),
            "manufacturer": MANUFACTURER,
            "model": "Hisho machine",
            "via_device": (DOMAIN, "control_plane"),
        }

    async def async_press(self) -> None:
        """Execute the compute action via the Hisho API."""
        session = async_get_clientsession(self.hass)
        url = f"{self.coordinator.base_url}{ENDPOINT_COMPUTE}/{self._worker}/{self._action}"
        params = {"execute": "1"}
        headers = self.coordinator._headers()
        try:
            async with session.post(url, params=params, headers=headers, timeout=30) as resp:
                body = await resp.json(content_type=None)
                _LOGGER.info("Hisho compute %s %s: HTTP %s — %s", self._worker, self._action, resp.status, body)
        except Exception:
            _LOGGER.exception("Failed to execute compute %s on %s", self._action, self._worker)
            raise


class HishoIpmiButton(CoordinatorEntity[HishoCoordinator], ButtonEntity):
    """A button to trigger an IPMI action on a target."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, target: str, action: str):
        super().__init__(coordinator)
        self._target = target
        self._action = action
        self._attr_name = f"IPMI {action.replace('-', ' ').title()}"
        self._attr_unique_id = f"{entry.entry_id}_ipmi_{target}_{action}"
        self._attr_icon = {
            "power-status": "mdi:information-outline",
            "power-on": "mdi:power",
            "power-off": "mdi:power-off",
            "power-cycle": "mdi:restart",
        }.get(action, "mdi:server")

    @property
    def device_info(self) -> dict[str, Any]:
        return {
            "identifiers": {(DOMAIN, f"ipmi_{self._target}")},
            "name": f"IPMI {self._target.title()}",
            "manufacturer": MANUFACTURER,
            "model": "IPMI BMC",
            "via_device": (DOMAIN, "control_plane"),
        }

    async def async_press(self) -> None:
        """Execute the IPMI action via the Hisho API."""
        session = async_get_clientsession(self.hass)
        url = f"{self.coordinator.base_url}{ENDPOINT_IPMI}/{self._target}/{self._action}"
        params = {"execute": "1"}
        headers = self.coordinator._headers()
        try:
            async with session.post(url, params=params, headers=headers, timeout=30) as resp:
                body = await resp.json(content_type=None)
                _LOGGER.info("Hisho IPMI %s %s: HTTP %s — %s", self._target, self._action, resp.status, body)
        except Exception:
            _LOGGER.exception("Failed to execute IPMI %s on %s", self._action, self._target)
            raise
