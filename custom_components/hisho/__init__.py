"""The Hisho integration: a Hisho fleet as Home Assistant devices, with Hisho's controls.

Home Assistant pairs with Hisho Core once (the owner approves it in Hisho), then
polls ``GET /api/ha/snapshot`` and presses Hisho's controls through
``POST /api/ha/action``. What this Home Assistant may do is kept by Hisho.
"""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import hub_device_info
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.EVENT,
    Platform.NOTIFY,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TODO,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register Hisho's services (once, for every entry)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: HishoConfigEntry) -> bool:
    """Set up Hisho from a config entry."""
    coordinator = HishoCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    # The hub first, so machines, boxes and add-ons can name it as the device they hang off.
    hub = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **hub_device_info(coordinator))
    coordinator.hub_device_id = hub.id
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: HishoConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: HishoConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Let a device be deleted by hand once Hisho no longer reports it (never the hub)."""
    coordinator = entry.runtime_data
    return not device.identifiers & coordinator.current_identifiers(coordinator.data)
