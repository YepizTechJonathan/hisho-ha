"""The Hisho integration: a Hisho fleet as Home Assistant devices.

Home Assistant polls one Hisho Core route, ``GET /api/ha/snapshot``, with the
read-only token ``hisho home-assistant token`` makes. Each machine becomes a
device under a Hisho hub device.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .coordinator import HishoConfigEntry, HishoCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: HishoConfigEntry) -> bool:
    """Set up Hisho from a config entry."""
    coordinator = HishoCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HishoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: HishoConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Let a device be deleted by hand once Hisho no longer reports it."""
    coordinator = entry.runtime_data
    if (dr_id := next(iter(device.identifiers), None)) is None:
        return True
    if dr_id[1] == coordinator.instance_id:
        return False  # the hub stays while the entry does
    return not any(
        coordinator.machine_identifier(machine_id) in device.identifiers
        for machine_id in coordinator.data.machines
    )
