"""The Hisho theme: in the frontend's list, back after a theme reload, a configuration.yaml theme of that name wins.

The frontend itself needs the hass_frontend package, which the tests don't install, so its theme data is set up the
way frontend.async_setup and frontend.reload_themes leave it."""

from __future__ import annotations

from typing import Any

from homeassistant.const import EVENT_THEMES_UPDATED
from homeassistant.core import HomeAssistant

from custom_components.hisho.theme import (
    DATA_DEFAULT_THEME,
    DATA_THEMES,
    DATA_THEMES_STORE,
    THEME_NAME,
    async_setup_theme,
)


class FakeStore:
    def __init__(self, data: dict[str, Any] | None) -> None:
        self.data = data

    async def async_load(self) -> dict[str, Any] | None:
        return self.data


def _frontend(hass: HomeAssistant, themes: dict[str, Any], saved_default: str | None = None) -> None:
    """The frontend at startup: themes from configuration.yaml, a saved default it reset when it didn't know it."""
    hass.data[DATA_THEMES] = themes
    hass.data[DATA_THEMES_STORE] = FakeStore({DATA_DEFAULT_THEME: saved_default} if saved_default else None)
    hass.data[DATA_DEFAULT_THEME] = saved_default if saved_default in themes else "default"


async def test_theme_added_and_kept_through_reload(hass: HomeAssistant) -> None:
    """Listed at setup; the owner's saved choice of it comes back; a theme reload doesn't drop either."""
    _frontend(hass, {}, saved_default=THEME_NAME)
    assert await async_setup_theme(hass)
    dark = hass.data[DATA_THEMES][THEME_NAME]["modes"]["dark"]
    assert dark["primary-color"] == "#DC143C"
    assert dark["primary-background-color"] == "#0B0A09"
    assert hass.data[DATA_DEFAULT_THEME] == THEME_NAME

    # frontend.reload_themes: the list from configuration.yaml again, the default reset, then its event
    hass.data[DATA_THEMES] = {}
    hass.data[DATA_DEFAULT_THEME] = "default"
    hass.bus.async_fire(EVENT_THEMES_UPDATED)
    await hass.async_block_till_done()
    assert THEME_NAME in hass.data[DATA_THEMES]
    assert hass.data[DATA_DEFAULT_THEME] == THEME_NAME


async def test_configuration_yaml_theme_wins(hass: HomeAssistant) -> None:
    """A theme called Hisho in configuration.yaml is left as the owner wrote it."""
    own = {"primary-color": "#123456"}
    _frontend(hass, {THEME_NAME: own})
    assert await async_setup_theme(hass)
    assert hass.data[DATA_THEMES][THEME_NAME] == own


async def test_no_frontend(hass: HomeAssistant) -> None:
    """A headless Home Assistant: nothing to add, no error."""
    assert not await async_setup_theme(hass)
