"""Polls Hisho Core and keeps the device registry in step with the fleet."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import HishoAuthError, HishoClient, HishoError, HishoUnsupportedError
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type HishoConfigEntry = ConfigEntry[HishoCoordinator]


@dataclass
class HishoData:
    """One snapshot, indexed the way the entities look things up."""

    raw: dict[str, Any]
    machines: dict[str, dict[str, Any]] = field(default_factory=dict)
    folders: dict[str, dict[str, Any]] = field(default_factory=dict)
    peers: dict[str, dict[str, Any]] = field(default_factory=dict)
    workers: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_snapshot(cls, raw: dict[str, Any]) -> HishoData:
        """Index a snapshot by id."""
        sync = raw.get("sync") or {}
        return cls(
            raw=raw,
            machines={m["id"]: m for m in raw.get("machines") or [] if m.get("id")},
            folders={f["id"]: f for f in sync.get("folders") or [] if f.get("id")},
            peers={p["id"]: p for p in sync.get("peers") or [] if p.get("id")},
            workers={w["id"]: w for w in raw.get("compute_workers") or [] if w.get("id")},
        )

    @property
    def sync(self) -> dict[str, Any]:
        """The hub's sync block."""
        return self.raw.get("sync") or {}


class HishoCoordinator(DataUpdateCoordinator[HishoData]):
    """Fetches the snapshot every minute."""

    config_entry: HishoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: HishoConfigEntry) -> None:
        """Set up the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = HishoClient(
            async_get_clientsession(hass, entry.data.get(CONF_VERIFY_SSL, True)),
            entry.data[CONF_URL],
            entry.data[CONF_TOKEN],
        )

    @property
    def instance_id(self) -> str:
        """The Hisho this entry belongs to."""
        return self.config_entry.unique_id or self.config_entry.entry_id

    async def _async_update_data(self) -> HishoData:
        try:
            raw = await self.client.async_get_snapshot()
        except HishoAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except HishoUnsupportedError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="unsupported_schema",
                translation_placeholders={"schema": str(err.schema)},
            ) from err
        except HishoError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err) or type(err).__name__},
            ) from err
        data = HishoData.from_snapshot(raw)
        self._remove_departed_machines(data)
        return data

    def machine_identifier(self, machine_id: str) -> tuple[str, str]:
        """Device registry identifier of one machine."""
        return (DOMAIN, f"{self.instance_id}_{machine_id}")

    def find_device(self, identifier: tuple[str, str]) -> dr.DeviceEntry | None:
        """This entry's device with that identifier."""
        registry = dr.async_get(self.hass)
        if hasattr(registry, "async_get_device_by_identifier"):  # HA 2026.10+
            return registry.async_get_device_by_identifier(identifier, self.config_entry.entry_id)
        return registry.async_get_device(identifiers={identifier})

    def _remove_departed_machines(self, data: HishoData) -> None:
        """A machine retired from the fleet takes its device with it."""
        if self.data is None:
            return
        gone = set(self.data.machines) - set(data.machines)
        if not gone:
            return
        for machine_id in gone:
            device = self.find_device(self.machine_identifier(machine_id))
            if device is not None:
                dr.async_get(self.hass).async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )
