"""The Hisho integration: a Hisho fleet as Home Assistant devices, with Hisho's controls.

Home Assistant pairs with Hisho Core once (the owner approves it in Hisho), then
polls ``GET /api/ha/snapshot`` and presses Hisho's controls through
``POST /api/ha/action``. What this Home Assistant may do is kept by Hisho.
"""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import hub_device_info
from .services import async_setup_services
from .websocket import async_setup_websocket

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CALENDAR,
    Platform.CONVERSATION,
    Platform.EVENT,
    Platform.NOTIFY,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TODO,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_LOGGER = logging.getLogger(__name__)

# The Hisho cards (HACS dashboard add-on hisho-ha-cards) register Hisho's icons (hisho:kanji). Home Assistant draws
# the sidebar before it loads dashboard resources, so the Hisho dashboard's sidebar icon would be blank: load the
# cards with the frontend itself when they are installed. Loading them twice (here and as a resource) is harmless.
CARDS_FILE = ("www", "community", "hisho-ha-cards", "hisho-ha-cards.js")
CARDS_URL = "/hacsfiles/hisho-ha-cards/hisho-ha-cards.js"

# Pictures for the Hisho dashboard (its background), served at /hisho_static/... The cards are a separate HACS
# download that ships only their script, so the pictures come with the integration.
STATIC_URL = "/hisho_static"
STATIC_DIR = Path(__file__).parent / "www"


async def async_serve_static(hass: HomeAssistant) -> bool:
    """Serve the dashboard's pictures; False when there is no web server (a headless Home Assistant)."""
    if getattr(hass, "http", None) is None:
        return False
    from homeassistant.components.http import StaticPathConfig  # noqa: PLC0415

    await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(STATIC_DIR), True)])
    return True


async def async_load_cards_early(hass: HomeAssistant) -> str | None:
    """Have the frontend load the Hisho cards at startup; the URL, or None when they aren't installed."""
    path = Path(hass.config.path(*CARDS_FILE))
    mtime = await hass.async_add_executor_job(lambda: path.stat().st_mtime if path.is_file() else None)
    if mtime is None:
        return None
    url = f"{CARDS_URL}?v={int(mtime)}"  # a new version after an update, not the browser's cached one
    try:
        from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415

        add_extra_js_url(hass, url)
    except (ImportError, KeyError):  # no frontend (a headless Home Assistant): nothing to draw anyway
        _LOGGER.debug("Frontend not loaded; the Hisho cards load with the dashboard")
        return None
    return url


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register Hisho's services and admin websocket commands (once, for every entry), serve the dashboard's
    pictures, load the cards early."""
    async_setup_services(hass)
    async_setup_websocket(hass)
    await async_serve_static(hass)
    await async_load_cards_early(hass)
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
