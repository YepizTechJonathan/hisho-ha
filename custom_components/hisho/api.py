"""A small client for Hisho Core's Home Assistant routes: pairing, the snapshot, actions."""

from __future__ import annotations

import asyncio
from typing import Any
from urllib.parse import quote, urlencode

import aiohttp

from .const import (
    ACTION_PATH,
    CONVERSATIONS_PATH,
    CONVERSE_PATH,
    CONVERSE_TIMEOUT,
    PAIR_PATH,
    REQUEST_TIMEOUT,
    SNAPSHOT_PATH,
    SUPPORTED_SCHEMA,
)


class HishoError(Exception):
    """Base error talking to Hisho."""


class HishoConnectionError(HishoError):
    """Hisho did not answer, or answered with an error."""


class HishoAuthError(HishoError):
    """Hisho refused the token (revoked, or never paired)."""


class HishoNotHishoError(HishoError):
    """The address answered, but not as Hisho Core (or a Hisho too old for this route)."""


class HishoUnsupportedError(HishoError):
    """Hisho sent a snapshot schema newer than this integration understands."""

    def __init__(self, schema: int) -> None:
        """Keep the schema Hisho sent."""
        super().__init__(f"snapshot schema {schema} is newer than {SUPPORTED_SCHEMA}")
        self.schema = schema


class HishoActionError(HishoError):
    """Hisho refused an action: not allowed for this Home Assistant (403), or it needs confirming (409)."""

    def __init__(self, status: int, detail: str) -> None:
        """Keep Hisho's status and reason."""
        super().__init__(detail)
        self.status = status
        self.detail = detail


class HishoPairingBusyError(HishoError):
    """Hisho is not taking a new pairing request right now (too many, or too soon)."""


class HishoClient:
    """Talks to one Hisho Core."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str | None = None) -> None:
        """Set up the client. No token yet = only pairing works."""
        self._session = session
        self._url = url.rstrip("/")
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = REQUEST_TIMEOUT
    ) -> tuple[int, Any]:
        try:
            async with self._session.request(
                method,
                self._url + path,
                json=body,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                try:
                    data = await resp.json()
                except (aiohttp.ContentTypeError, ValueError):
                    data = None
                return resp.status, data
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HishoConnectionError(str(err) or type(err).__name__) from err

    async def async_get_snapshot(self) -> dict[str, Any]:
        """Fetch and check one snapshot."""
        status, data = await self._request("GET", SNAPSHOT_PATH)
        if status in (401, 403):
            raise HishoAuthError
        if status == 404 or (status == 200 and data is None):
            raise HishoNotHishoError
        if status != 200:
            raise HishoConnectionError(f"HTTP {status}")
        if not isinstance(data, dict) or not data.get("instance_id") or "machines" not in data:
            raise HishoNotHishoError
        schema = data.get("schema")
        if not isinstance(schema, int) or schema > SUPPORTED_SCHEMA:
            raise HishoUnsupportedError(schema if isinstance(schema, int) else -1)
        return data

    async def async_pair_start(self, name: str, ha_id: str) -> dict[str, Any]:
        """Ask Hisho to pair. Returns pair_id (secret), code, instance_id, poll_seconds."""
        status, data = await self._request("POST", PAIR_PATH, {"name": name, "ha_id": ha_id})
        if status == 429:
            raise HishoPairingBusyError((data or {}).get("detail") or "busy")
        if status in (404, 405) or (status == 200 and not isinstance(data, dict)):
            raise HishoNotHishoError
        if status != 200:
            raise HishoConnectionError(f"HTTP {status}")
        if not data.get("pair_id") or not data.get("code"):
            raise HishoNotHishoError
        return data

    async def async_pair_status(self, pair_id: str) -> dict[str, Any]:
        """pending, approved (with the token, once), denied or expired."""
        status, data = await self._request("GET", f"{PAIR_PATH}/{pair_id}")
        if status != 200 or not isinstance(data, dict):
            raise HishoConnectionError(f"HTTP {status}")
        return data

    async def async_action(
        self, action: str, target: str = "", args: dict[str, Any] | None = None, confirm: bool = False
    ) -> Any:
        """Press one of Hisho's controls."""
        body: dict[str, Any] = {"action": action, "target": target, "args": args or {}}
        if confirm:
            body["confirm"] = True
        status, data = await self._request("POST", ACTION_PATH, body)
        if status == 401:
            raise HishoAuthError
        if status != 200:
            detail = data.get("detail") if isinstance(data, dict) else None
            raise HishoActionError(status, str(detail or f"HTTP {status}"))
        return data

    async def async_converse(self, body: dict[str, Any]) -> dict[str, Any]:
        """One turn with Hisho's assistant: {reply, model, ok}. Long thinking can take minutes."""
        status, data = await self._request("POST", CONVERSE_PATH, body, timeout=CONVERSE_TIMEOUT)
        if status == 401:
            raise HishoAuthError
        if status != 200 or not isinstance(data, dict):
            detail = data.get("detail") if isinstance(data, dict) else None
            raise HishoActionError(status, str(detail or f"HTTP {status}"))
        return data


    async def _conversations(self, method: str, path: str) -> dict[str, Any]:
        status, data = await self._request(method, CONVERSATIONS_PATH + path)
        if status == 401:
            raise HishoAuthError
        if status != 200 or not isinstance(data, dict):
            detail = data.get("detail") if isinstance(data, dict) else None
            raise HishoActionError(status, str(detail or f"HTTP {status}"))
        return data

    async def async_conversations(self, person: str | None = None, limit: int = 100) -> dict[str, Any]:
        """The conversations people had with Hisho here, newest first: {conversations, timezone, keep_days}."""
        query = {"limit": limit} | ({"person": person} if person else {})
        return await self._conversations("GET", "?" + urlencode(query))

    async def async_conversation(self, conversation_id: str) -> dict[str, Any]:
        """One conversation's transcript."""
        return await self._conversations("GET", "/" + quote(conversation_id, safe=""))

    async def async_delete_conversation(self, conversation_id: str) -> dict[str, Any]:
        """Delete one conversation (Hisho journals it)."""
        return await self._conversations("DELETE", "/" + quote(conversation_id, safe=""))
