"""Config flow for Hisho: type Hisho's address, approve the request in Hisho. No tokens.

Home Assistant asks Hisho Core to pair and shows a short code. Hisho puts the
request in Needs you (and on the owner's phone) with the same code; once the owner
approves it, this flow receives its own token and Hisho keeps only its hash. What
this Home Assistant may do is set in Hisho. Re-authenticating is pairing again.

A token made with ``hisho home-assistant token`` still works (the optional field):
that is the CLI fallback, read-only.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
import logging
import time
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_RECONFIGURE,
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.components import persistent_notification
from homeassistant.core import callback
from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.helpers import instance_id
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
    HishoError,
    HishoNotHishoError,
    HishoPairingBusyError,
    HishoUnsupportedError,
)
from .const import CONF_SHOW_NETWORK, DOMAIN, PAIR_TIMEOUT_SECONDS

_LOGGER = logging.getLogger(__name__)

URL_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.URL))
TOKEN_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
EXAMPLE_URL = "https://core.your-tailnet.ts.net"


def _connection_schema(defaults: Mapping[str, Any], with_token: bool = True) -> vol.Schema:
    fields: dict[Any, Any] = {
        vol.Required(CONF_URL, default=defaults.get(CONF_URL, "")): URL_SELECTOR,
        vol.Optional(CONF_VERIFY_SSL, default=defaults.get(CONF_VERIFY_SSL, True)): bool,
    }
    if with_token:
        fields[vol.Optional(CONF_TOKEN)] = TOKEN_SELECTOR
    return vol.Schema(fields)


class HishoConfigFlow(ConfigFlow, domain=DOMAIN):
    """Pair with a Hisho; re-pair (reauth), or point at its new address (reconfigure)."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> HishoOptionsFlow:
        """What to show."""
        return HishoOptionsFlow()

    def __init__(self) -> None:
        """Start empty."""
        self._url = ""
        self._verify_ssl = True
        self._pairing: dict[str, Any] = {}
        self._pair_task: asyncio.Task[dict[str, Any]] | None = None
        self._result: dict[str, Any] = {}

    @property
    def _notification_id(self) -> str:
        return f"{DOMAIN}_pairing_{self.flow_id}"

    def async_remove(self) -> None:
        """Closed or aborted while waiting: stop polling and take the notification down."""
        if self._pair_task is not None:
            self._pair_task.cancel()
        persistent_notification.async_dismiss(self.hass, self._notification_id)

    def _client(self, token: str | None = None) -> HishoClient:
        return HishoClient(async_get_clientsession(self.hass, self._verify_ssl), self._url, token)

    async def _async_check_token(
        self, token: str
    ) -> tuple[dict[str, Any] | None, dict[str, str], dict[str, str]]:
        """The CLI fallback: a token made by hand. Returns (snapshot, errors, placeholders)."""
        try:
            return await self._client(token).async_get_snapshot(), {}, {}
        except HishoAuthError:
            return None, {"base": "invalid_auth"}, {}
        except HishoNotHishoError:
            return None, {"base": "not_hisho"}, {}
        except HishoUnsupportedError as err:
            return None, {"base": "unsupported_schema"}, {"schema": str(err.schema)}
        except HishoConnectionError as err:
            # Say why on the form and in the log: "cannot resolve host" and "certificate
            # does not match" need different fixes, and both read "failed to connect".
            _LOGGER.warning("Cannot reach Hisho at %s: %s", self._url, err)
            return None, {"base": "cannot_connect"}, {"error": str(err)}
        except Exception:
            _LOGGER.exception("Unexpected error talking to Hisho at %s", self._url)
            return None, {"base": "unknown"}, {}

    async def _async_start_pairing(self) -> tuple[dict[str, str], dict[str, str]]:
        """Ask Hisho to pair. Returns (errors, placeholders); no errors = started."""
        try:
            self._pairing = await self._client().async_pair_start(
                self.hass.config.location_name or "Home Assistant",
                await instance_id.async_get(self.hass),
            )
        except HishoPairingBusyError as err:
            return {"base": "pair_busy"}, {"error": str(err)}
        except HishoNotHishoError:
            return {"base": "not_hisho"}, {}
        except HishoConnectionError as err:
            _LOGGER.warning("Cannot reach Hisho at %s: %s", self._url, err)
            return {"base": "cannot_connect"}, {"error": str(err)}
        except Exception:
            _LOGGER.exception("Unexpected error asking Hisho at %s to pair", self._url)
            return {"base": "unknown"}, {}
        return {}, {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Hisho's address (and, only for the CLI fallback, a token)."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"example_url": EXAMPLE_URL}
        if user_input is not None:
            self._url = user_input[CONF_URL].strip().rstrip("/")
            self._verify_ssl = user_input[CONF_VERIFY_SSL]
            if token := (user_input.get(CONF_TOKEN) or "").strip():
                snapshot, errors, extra = await self._async_check_token(token)
                placeholders |= extra
                if snapshot is not None:
                    await self.async_set_unique_id(snapshot["instance_id"])
                    self._abort_if_unique_id_configured(updates={CONF_URL: self._url})
                    return self._create(snapshot.get("name") or "Hisho", token)
            else:
                errors, extra = await self._async_start_pairing()
                placeholders |= extra
                if not errors:
                    await self.async_set_unique_id(self._pairing["instance_id"])
                    self._abort_if_unique_id_configured(updates={CONF_URL: self._url})
                    return await self.async_step_pair()
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(_connection_schema({}), user_input or {}),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def _async_wait_for_approval(self) -> dict[str, Any]:
        """Poll Hisho until the owner answers, the request expires, or we give up."""
        client = self._client()
        deadline = time.monotonic() + PAIR_TIMEOUT_SECONDS
        delay = max(1.0, float(self._pairing.get("poll_seconds") or 3))
        while time.monotonic() < deadline:
            await asyncio.sleep(delay)
            try:
                status = await client.async_pair_status(self._pairing["pair_id"])
            except HishoError:
                continue  # a blip: keep waiting
            if status.get("state") != "pending":
                return status
        return {"state": "expired"}

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Waiting for the owner to approve in Hisho, with the code to compare."""
        if self._pair_task is None:
            self._pair_task = self.hass.async_create_task(self._async_wait_for_approval())
            # The code also as a notification: the dialog's text comes from the browser's copy of this
            # integration's translations, and a browser still holding an older copy showed only a spinner.
            persistent_notification.async_create(
                self.hass,
                f"In Hisho, open **Needs you** (or the notification on your phone) and approve Home Assistant "
                f"if it shows the code **{self._pairing.get('code', '')}**.",
                title="Approve Home Assistant in Hisho",
                notification_id=self._notification_id,
            )
        if not self._pair_task.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="wait_for_approval",
                description_placeholders={"code": self._pairing.get("code", ""), "url": self._url},
                progress_task=self._pair_task,
            )
        try:
            self._result = self._pair_task.result()
        except Exception:
            _LOGGER.exception("Pairing with Hisho at %s failed", self._url)
            self._result = {"state": "expired"}
        self._pair_task = None
        persistent_notification.async_dismiss(self.hass, self._notification_id)
        state = self._result.get("state")
        if state == "approved" and self._result.get("token"):
            return self.async_show_progress_done(next_step_id="paired")
        return self.async_show_progress_done(next_step_id="denied" if state == "denied" else "expired")

    async def async_step_paired(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Approved: save the token (a new entry, or the entry being re-paired)."""
        token = self._result["token"]
        if self.source in (SOURCE_REAUTH, SOURCE_RECONFIGURE):
            entry = (
                self._get_reauth_entry() if self.source == SOURCE_REAUTH else self._get_reconfigure_entry()
            )
            return self.async_update_reload_and_abort(
                entry,
                data_updates={CONF_URL: self._url, CONF_TOKEN: token, CONF_VERIFY_SSL: self._verify_ssl},
            )
        return self._create("Hisho", token)

    async def async_step_denied(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """The owner said no in Hisho."""
        return self.async_abort(reason="pair_denied")

    async def async_step_expired(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Nobody answered in time."""
        return self.async_abort(reason="pair_expired")

    def _create(self, title: str, token: str) -> ConfigFlowResult:
        return self.async_create_entry(
            title=title,
            data={CONF_URL: self._url, CONF_TOKEN: token, CONF_VERIFY_SSL: self._verify_ssl},
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Hisho no longer accepts this Home Assistant (revoked, or Hisho reset): pair again."""
        self._url = entry_data[CONF_URL]
        self._verify_ssl = entry_data.get(CONF_VERIFY_SSL, True)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Say what happens, then ask Hisho again."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"url": self._url}
        if user_input is not None:
            errors, extra = await self._async_start_pairing()
            placeholders |= extra
            if not errors:
                await self.async_set_unique_id(self._pairing["instance_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_hisho")
                return await self.async_step_pair()
        return self.async_show_form(
            step_id="reauth_confirm", errors=errors, description_placeholders=placeholders
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Hisho Core moved: its new address, then approve again in Hisho."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"example_url": EXAMPLE_URL}
        if user_input is not None:
            self._url = user_input[CONF_URL].strip().rstrip("/")
            self._verify_ssl = user_input[CONF_VERIFY_SSL]
            errors, extra = await self._async_start_pairing()
            placeholders |= extra
            if not errors:
                await self.async_set_unique_id(self._pairing["instance_id"])
                self._abort_if_unique_id_mismatch(reason="wrong_hisho")
                return await self.async_step_pair()
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(user_input or entry.data, with_token=False),
            errors=errors,
            description_placeholders=placeholders,
        )


class HishoOptionsFlow(OptionsFlowWithReload):
    """What to show from Hisho."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Network gear on or off."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_SHOW_NETWORK, default=self.config_entry.options.get(CONF_SHOW_NETWORK, False)
                    ): bool
                }
            ),
        )

