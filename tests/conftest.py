"""Fixtures for the Hisho integration tests."""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
from typing import Any

import pytest

from homeassistant.const import CONF_TOKEN, CONF_URL, CONF_VERIFY_SSL
from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hisho.const import DOMAIN, SNAPSHOT_PATH

URL = "https://kihon.example.ts.net"
TOKEN = "read-token"
SNAPSHOT_URL = URL + SNAPSHOT_PATH


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> Generator[None]:
    """Load custom_components/ in every test."""
    yield


@pytest.fixture
def snapshot_data() -> dict[str, Any]:
    """A fresh copy of a real snapshot, safe to edit per test."""
    return json.loads((Path(__file__).parent / "fixtures" / "snapshot.json").read_text())


@pytest.fixture
def mock_hisho(aioclient_mock: AiohttpClientMocker, snapshot_data: dict[str, Any]) -> AiohttpClientMocker:
    """Hisho Core answering with the snapshot."""
    aioclient_mock.get(SNAPSHOT_URL, json=snapshot_data)
    return aioclient_mock


@pytest.fixture
def config_entry(snapshot_data: dict[str, Any]) -> MockConfigEntry:
    """A Hisho entry as the config flow makes it."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Hisho",
        unique_id=snapshot_data["instance_id"],
        data={CONF_URL: URL, CONF_TOKEN: TOKEN, CONF_VERIFY_SSL: True},
    )


async def setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Add the entry and wait for it to load."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
