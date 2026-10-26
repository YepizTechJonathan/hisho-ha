"""Updates: each store add-on Hisho has installed, in Home Assistant's Updates page."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import HishoConfigEntry, HishoCoordinator
from .entity import HishoAddonEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HishoConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One update entity per installed store add-on that also is an add-on device."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        if not data.can("store.update"):
            return
        new = []
        for item in data.raw.get("store") or []:
            name = item.get("name")
            if item.get("installed") and name in data.addons and name not in known:
                known.add(name)
                new.append(HishoAddonUpdate(coordinator, name))
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class HishoAddonUpdate(HishoAddonEntity, UpdateEntity):
    """Installed vs. the store's version; Install updates it on its machine (Hisho keeps the settings)."""

    _attr_translation_key = "addon_update"
    _attr_supported_features = UpdateEntityFeature.INSTALL

    def __init__(self, coordinator: HishoCoordinator, name: str) -> None:
        """Set up the entity."""
        super().__init__(coordinator, name, "update")

    def _item(self) -> dict[str, Any]:
        return next((i for i in self.coordinator.data.raw.get("store") or [] if i.get("name") == self.addon_name), {})

    @property
    def installed_version(self) -> str | None:
        """What runs now."""
        return self._item().get("installed_version")

    @property
    def latest_version(self) -> str | None:
        """What the store offers (the installed one when there is nothing newer)."""
        item = self._item()
        return item.get("version") if item.get("update_available") else item.get("installed_version")

    @property
    def title(self) -> str | None:
        """The add-on's name."""
        return self._item().get("title")

    @property
    def release_summary(self) -> str | None:
        """What it does."""
        return (self._item().get("description") or "")[:255] or None

    async def async_install(self, version: str | None, backup: bool, **kwargs: Any) -> None:
        """Update the add-on through Hisho's store."""
        await self.coordinator.async_action(
            "store.update", self.addon_name, {"host": self._item().get("host") or ""}, context=self._context
        )
