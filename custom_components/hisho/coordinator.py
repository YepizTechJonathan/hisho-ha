"""DataUpdateCoordinator for Hisho."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DATA_FLEET_HEALTH,
    DATA_STATUS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENDPOINT_FLEET_HEALTH,
    ENDPOINT_STATUS,
)

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = 20


class HishoCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch fleet health and system status from the Hisho control plane."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        token: str,
        scan_interval: int = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.host = host
        self.port = port
        self.token = token
        self.base_url = f"http://{host}:{port}"

    def _headers(self) -> dict[str, str]:
        """Build request headers, including auth token if configured."""
        headers: dict[str, str] = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def _async_get(self, session, path: str) -> dict[str, Any]:
        """Fetch one JSON document from the control plane."""
        try:
            async with session.get(
                self.base_url + path, timeout=REQUEST_TIMEOUT, headers=self._headers()
            ) as resp:
                if resp.status != 200:
                    raise UpdateFailed(
                        f"Hisho API {path} returned HTTP {resp.status}"
                    )
                return await resp.json(content_type=None)
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"Error talking to Hisho at {self.base_url}: {err}") from err

    async def _async_update_data(self) -> dict[str, Any]:
        """Poll fleet-health (mandatory) and status (best-effort)."""
        session = async_get_clientsession(self.hass)

        fleet_health = await self._async_get(session, ENDPOINT_FLEET_HEALTH)
        if not isinstance(fleet_health, dict) or "machines" not in fleet_health:
            raise UpdateFailed("Hisho fleet-health payload was not understood")

        try:
            status = await self._async_get(session, ENDPOINT_STATUS)
        except UpdateFailed as err:
            _LOGGER.debug("Hisho status endpoint unavailable: %s", err)
            status = {}

        return {DATA_FLEET_HEALTH: fleet_health, DATA_STATUS: status}
