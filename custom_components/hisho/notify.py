"""Notify "Hisho phone app": a message to the owner's Hisho phone app (``notify.send_message``)."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the phone app target when Hisho lets this Home Assistant send to it."""
    coordinator = entry.runtime_data
    added = False

    @callback
    def _add() -> None:
        nonlocal added
        if not added and coordinator.data.can("notify.send"):
            added = True
            async_add_entities([HishoPhone(coordinator)])

    _add()
    entry.async_on_unload(coordinator.async_add_listener(_add))


class HishoPhone(HishoEntity, NotifyEntity):
    """Sends through Hisho, so it reaches the phones that get Hisho's alerts."""

    _attr_translation_key = "phone"

    def __init__(self, coordinator: HishoCoordinator) -> None:
        """Set up the target."""
        super().__init__(coordinator, "phone")

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        """Send one message."""
        args = {"message": message}
        if title:
            args["title"] = title
        await self.coordinator.async_action("notify.send", "", args, context=self._context)
