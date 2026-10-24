"""A small client for the one Hisho Core route this integration reads."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT, SNAPSHOT_PATH, SUPPORTED_SCHEMA


class HishoError(Exception):
    """Base error talking to Hisho."""


class HishoConnectionError(HishoError):
    """Hisho did not answer, or answered with an error."""


class HishoAuthError(HishoError):
    """Hisho refused the token."""


class HishoNotHishoError(HishoError):
    """The address answered, but not with a Hisho snapshot."""


class HishoUnsupportedError(HishoError):
    """Hisho sent a snapshot schema newer than this integration understands."""

    def __init__(self, schema: int) -> None:
        """Keep the schema Hisho sent."""
        super().__init__(f"snapshot schema {schema} is newer than {SUPPORTED_SCHEMA}")
        self.schema = schema


class HishoClient:
    """Reads ``GET /api/ha/snapshot`` with the HA read token."""

    def __init__(self, session: aiohttp.ClientSession, url: str, token: str) -> None:
        """Set up the client."""
        self._session = session
        self._url = url.rstrip("/")
        self._token = token

    async def async_get_snapshot(self) -> dict[str, Any]:
        """Fetch and check one snapshot."""
        try:
            async with self._session.get(
                self._url + SNAPSHOT_PATH,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status in (401, 403):
                    raise HishoAuthError
                if resp.status == 404:
                    raise HishoNotHishoError
                if resp.status != 200:
                    raise HishoConnectionError(f"HTTP {resp.status}")
                try:
                    data = await resp.json()
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise HishoNotHishoError from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HishoConnectionError(str(err) or type(err).__name__) from err
        if not isinstance(data, dict) or not data.get("instance_id") or "machines" not in data:
            raise HishoNotHishoError
        schema = data.get("schema")
        if not isinstance(schema, int) or schema > SUPPORTED_SCHEMA:
            raise HishoUnsupportedError(schema if isinstance(schema, int) else -1)
        return data
