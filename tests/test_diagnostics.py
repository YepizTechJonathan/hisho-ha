"""Diagnostics never include the token."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.hisho.diagnostics import async_get_config_entry_diagnostics

from .conftest import TOKEN, setup_entry


async def test_diagnostics_redact_the_token(
    hass: HomeAssistant, mock_hisho: AiohttpClientMocker, config_entry: MockConfigEntry
) -> None:
    """The snapshot is there for debugging; the token is not."""
    await setup_entry(hass, config_entry)
    result = await async_get_config_entry_diagnostics(hass, config_entry)
    assert result["entry"]["data"]["token"] == "**REDACTED**"
    assert TOKEN not in str(result)
    assert result["snapshot"]["instance_id"] == config_entry.unique_id
    assert result["last_update_success"] is True
