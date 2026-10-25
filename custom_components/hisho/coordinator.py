"""Polls Hisho Core, keeps the device registry in step with the fleet, and runs Hisho's controls."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    HishoActionError,
    HishoAuthError,
    HishoClient,
    HishoError,
    HishoUnsupportedError,
)
from .const import CONF_SHOW_NETWORK, DOMAIN, UPDATE_INTERVAL

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
    alerts: dict[str, dict[str, Any]] = field(default_factory=dict)
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    addons: dict[str, dict[str, Any]] = field(default_factory=dict)
    controls: dict[str, dict[str, Any]] = field(default_factory=dict)
    allowed: frozenset[str] = frozenset()

    @classmethod
    def from_snapshot(cls, raw: dict[str, Any], show_network: bool = False) -> HishoData:
        """Index a snapshot by id. Network gear (switches, cameras) only on request: HA's own
        integrations for it (UniFi...) usually show it already."""
        sync = raw.get("sync") or {}
        machines = {m["id"]: m for m in raw.get("machines") or [] if m.get("id")}
        return cls(
            raw=raw,
            machines=machines,
            folders={f["id"]: f for f in sync.get("folders") or [] if f.get("id")},
            peers={p["id"]: p for p in sync.get("peers") or [] if p.get("id")},
            workers={w["id"]: w for w in raw.get("compute_workers") or [] if w.get("id")},
            alerts={a["id"]: a for a in raw.get("alerts") or [] if a.get("id")},
            # A box that is a fleet machine is that machine's device, not a second one.
            devices={
                d["id"]: d
                for d in raw.get("devices") or []
                if d.get("id") and d["id"] not in machines and (show_network or d.get("kind") != "network")
            },
            addons={a["name"]: a for a in raw.get("addons") or [] if a.get("name")},
            controls=raw.get("controls") or {},
            allowed=frozenset(raw.get("allowed") or ()),
        )

    @property
    def sync(self) -> dict[str, Any]:
        """The hub's sync block."""
        return self.raw.get("sync") or {}

    @property
    def maintenance(self) -> dict[str, Any]:
        """Fleet and per-machine maintenance."""
        return self.raw.get("maintenance") or {}

    def can(self, action: str) -> bool:
        """Whether Hisho lets this Home Assistant do that (its scopes, kept by Hisho)."""
        return action in self.allowed


class HishoCoordinator(DataUpdateCoordinator[HishoData]):
    """Fetches the snapshot every minute; presses Hisho's buttons."""

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
        data = HishoData.from_snapshot(raw, self.config_entry.options.get(CONF_SHOW_NETWORK, False))
        self._remove_departed(data)
        return data

    async def async_action(
        self, action: str, target: str = "", args: dict[str, Any] | None = None, confirm: bool = False
    ) -> Any:
        """Press one of Hisho's controls, then refresh so the result shows at once."""
        try:
            result = await self.client.async_action(action, target, args, confirm)
        except HishoAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="invalid_auth"
            ) from err
        except HishoActionError as err:
            key = {403: "action_not_allowed", 409: "action_needs_confirm"}.get(err.status, "action_failed")
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=key,
                translation_placeholders={"action": action, "error": err.detail},
            ) from err
        except HishoError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"error": str(err) or type(err).__name__},
            ) from err
        await self.async_request_refresh()
        return result

    def machine_identifier(self, machine_id: str) -> tuple[str, str]:
        """Device registry identifier of one machine."""
        return (DOMAIN, f"{self.instance_id}_{machine_id}")

    def device_identifier(self, device_id: str) -> tuple[str, str]:
        """Device registry identifier of a box an add-on knows."""
        return (DOMAIN, f"{self.instance_id}_device_{device_id}")

    def addon_identifier(self, name: str) -> tuple[str, str]:
        """Device registry identifier of an add-on."""
        return (DOMAIN, f"{self.instance_id}_addon_{name}")

    def find_device(self, identifier: tuple[str, str]) -> dr.DeviceEntry | None:
        """This entry's device with that identifier."""
        registry = dr.async_get(self.hass)
        if hasattr(registry, "async_get_device_by_identifier"):  # HA 2026.10+
            return registry.async_get_device_by_identifier(identifier, self.config_entry.entry_id)
        return registry.async_get_device(identifiers={identifier})

    def current_identifiers(self, data: HishoData) -> set[tuple[str, str]]:
        """Every device Hisho reports now, the hub included."""
        return (
            {(DOMAIN, self.instance_id)}
            | {self.machine_identifier(m) for m in data.machines}
            | {self.device_identifier(d) for d in data.devices}
            | {self.addon_identifier(a) for a in data.addons}
        )

    def _remove_departed(self, data: HishoData) -> None:
        """A machine retired, a box an add-on no longer sees, an add-on removed: its device goes."""
        if self.data is None:
            return
        gone = self.current_identifiers(self.data) - self.current_identifiers(data)
        for identifier in gone:
            device = self.find_device(identifier)
            if device is not None:
                dr.async_get(self.hass).async_update_device(
                    device.id, remove_config_entry_id=self.config_entry.entry_id
                )
