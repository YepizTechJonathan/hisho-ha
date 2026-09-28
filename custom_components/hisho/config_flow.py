"""Config flow for Hisho."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_TOKEN,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENDPOINT_FLEET_HEALTH,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): vol.Coerce(int),
        vol.Optional(CONF_TOKEN, default=""): str,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
            vol.Coerce(int), vol.Range(min=10, max=600)
        ),
    }
)


async def _probe(hass, host: str, port: int, token: str) -> str | None:
    """Return a machine-count string on success, or None if the API is unusable."""
    session = async_get_clientsession(hass)
    url = f"http://{host}:{port}{ENDPOINT_FLEET_HEALTH}"
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        async with session.get(url, timeout=15, headers=headers) as resp:
            if resp.status != 200:
                return None
            payload = await resp.json(content_type=None)
    except Exception:
        return None
    if not isinstance(payload, dict) or "machines" not in payload:
        return None
    return str(len(payload.get("machines") or []))


class HishoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Hisho."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            token = user_input.get(CONF_TOKEN, "").strip()

            machines = await _probe(self.hass, host, port, token)
            if machines is None:
                errors["base"] = "cannot_connect"
            else:
                await self.async_set_unique_id(f"{host}:{port}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=f"Hisho ({host})",
                    data={CONF_HOST: host, CONF_PORT: port, CONF_TOKEN: token},
                    options={
                        CONF_SCAN_INTERVAL: user_input.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        )
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow handler."""
        return HishoOptionsFlow()


class HishoOptionsFlow(OptionsFlow):
    """Allow tuning the poll interval after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_SCAN_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=10, max=600)
                    )
                }
            ),
        )
