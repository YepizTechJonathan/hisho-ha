"""Config flow for Hisho: address and read token, checked against Core before saving."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    HishoAuthError,
    HishoClient,
    HishoConnectionError,
    HishoNotHishoError,
    HishoUnsupportedError,
)
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
EXAMPLE_URL = "https://core.your-tailnet.ts.net"


def _connection_schema(defaults: Mapping[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): URL_SELECTOR,
            vol.Required(CONF_TOKEN): TOKEN_SELECTOR,
            vol.Optional(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)): bool,
        }
    )


class HishoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add, re-authenticate or reconfigure a Hisho."""

    VERSION = 1

    async def _async_try(
        self, url: str, token: str, verify_ssl: bool
    ) -> tuple[dict[str, Any] | None, dict[str, str], dict[str, str]]:
        """Fetch one snapshot. Returns (snapshot, errors, placeholders)."""
        client = HishoClient(async_get_clientsession(self.hass, verify_ssl), url, token)
        try:
            return await client.async_get_snapshot(), {}, {}
        except HishoAuthError:
            return None, {"base": "invalid_auth"}, {}
        except HishoNotHishoError:
            return None, {"base": "not_hisho"}, {}
        except HishoUnsupportedError as err:
            return None, {"base": "unsupported_schema"}, {"schema": str(err.schema)}
        except HishoConnectionError as err:
            # Say why on the form and in the log: "cannot resolve host" and "certificate
            # does not match" need different fixes, and both read "failed to connect".
            _LOGGER.warning("Cannot reach Hisho at %s: %s", url, err)
            return None, {"base": "cannot_connect"}, {"error": str(err)}
        except Exception:
            _LOGGER.exception("Unexpected error talking to Hisho at %s", url)
            return None, {"base": "unknown"}, {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for Hisho's address and the read token."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"example_url": EXAMPLE_URL}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            snapshot, errors, extra = await self._async_try(
                url, user_input[CONF_TOKEN].strip(), user_input[CONF_VERIFY_SSL]
            )
            placeholders |= extra
            if snapshot is not None:
                await self.async_set_unique_id(snapshot["instance_id"])
                self._abort_if_unique_id_configured(updates={CONF_URL: url})
                return self.async_create_entry(
                    title=snapshot.get("name") or "Hisho",
                    data={**user_input, CONF_URL: url, CONF_TOKEN: user_input[CONF_TOKEN].strip()},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _connection_schema({}), user_input or {}
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Hisho refused the token: it was rotated or revoked."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new read token."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"url": entry.data[CONF_URL]}
        if user_input is not None:
            token = user_input[CONF_TOKEN].strip()
            snapshot, errors, extra = await self._async_try(
                entry.data[CONF_URL], token, entry.data.get(CONF_VERIFY_SSL, True)
            )
            placeholders |= extra
            if snapshot is not None:
                await self.async_set_unique_id(snapshot["instance_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_hisho")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_TOKEN: token}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_TOKEN): TOKEN_SELECTOR}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the address (Core moved) or the token."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            token = user_input[CONF_TOKEN].strip()
            snapshot, errors, placeholders = await self._async_try(
                url, token, user_input[CONF_VERIFY_SSL]
            )
            if snapshot is not None:
                await self.async_set_unique_id(snapshot["instance_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_hisho")
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_URL: url,
                        CONF_TOKEN: token,
                        CONF_VERIFY_SSL: user_input[CONF_VERIFY_SSL],
                    },
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or entry.data),
            errors=errors,
            description_placeholders=placeholders,
        )
