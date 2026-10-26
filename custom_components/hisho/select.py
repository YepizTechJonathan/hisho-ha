"""Select "Phone alerts": which of Hisho's alerts reach the Hisho phone app."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoEntity

PARALLEL_UPDATES = 1
LEVELS = ["all", "problems", "important", "off"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the phone-alerts select when Hisho lets this Home Assistant change it."""
    coordinator = entry.runtime_data
    if coordinator.data.can("notify.channels") and "phone_levels" in coordinator.data.raw:
        async_add_entities([HishoPhoneAlerts(coordinator)])


class HishoPhoneAlerts(HishoEntity, SelectEntity):
    """Everything, problems, important (critical and decisions), or nothing."""

    _attr_translation_key = "phone_alerts"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = LEVELS

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the select."""
        super().__init__(coordinator, "phone_alerts")

    @property
    def current_option(self) -> str | None:
        """The app channel's level."""
        level = (self.coordinator.data.raw.get("phone") or {}).get("channel")
        return level if level in LEVELS else None

    async def async_select_option(self, option: str) -> None:
        """Set it in Hisho."""
        await self.coordinator.async_action("notify.channels", "", {"app": option}, context=self._context)
