"""The "Hisho" theme for Home Assistant's frontend, and its fonts.

Hisho's look (the secretary's brief): warm black, crimson, Shippori Mincho headings, Inter Tight text, IBM Plex Mono
for numbers and machine names, hairline rules instead of card boxes. Any dashboard (and the profile's theme picker)
can use it: Profile › Theme › Hisho, or ``theme: Hisho`` on a view. The Hisho dashboard sets it on its own views.

Home Assistant reads themes only from configuration.yaml (``frontend: themes:``), so the integration adds this one to
the frontend's theme list itself, and adds it back whenever ``frontend.reload_themes`` replaces that list. A theme of
the same name in configuration.yaml wins. The fonts (SIL Open Font License, latin subsets plus the kanji the look
uses) are served with the dashboard's pictures and loaded with the frontend, so the theme works offline.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.const import EVENT_THEMES_UPDATED
from homeassistant.core import Event, HomeAssistant, callback

_LOGGER = logging.getLogger(__name__)

THEME_NAME = "Hisho"
FONTS_JS = "/hisho_static/hisho-fonts.js"

# The frontend's own hass.data keys (homeassistant/components/frontend/helpers.py).
DATA_THEMES = "frontend_themes"
DATA_THEMES_STORE = "frontend_themes_store"
DATA_DEFAULT_THEME = "frontend_default_theme"
DATA_DEFAULT_DARK_THEME = "frontend_default_dark_theme"

BG = "#0B0A09"
SURFACE = "#151311"  # dialogs and menus: a step up from the page so they read as on top of it
TEXT = "#ECE6DC"
MUTED = "#8C857B"
FAINT = "#4A4640"
LINE = "#26231F"
RED = "#DC143C"
CRIT = "#E8364F"
WARN = "#E0A030"
OK = "#3FB97A"

SERIF = '"Shippori Mincho", "Noto Serif JP", Georgia, serif'
UI = '"Inter Tight", system-ui, sans-serif'
MONO = '"IBM Plex Mono", ui-monospace, monospace'

# card-mod (when installed) gives Home Assistant's own cards the hairline instead of a box, and its headings the serif.
CARD_MOD_CARD = f"""
ha-card {{ border-top: 1px solid {LINE} !important; }}
ha-card.type-heading, ha-card.type-custom-hisho-control-center-card {{ border-top: none !important; }}
ha-card.type-markdown {{ font-family: {UI}; }}
ha-card.type-markdown h1, ha-card.type-markdown h2, ha-card.type-markdown h3 {{ font-family: {SERIF}; font-weight: 600; }}
"""
CARD_MOD_ROOT = f"""
.header, .toolbar {{ border-bottom: 1px solid {LINE}; }}
"""

THEME: dict[str, Any] = {
    "modes": {
        "dark": {
            # the page
            "primary-background-color": BG,
            "secondary-background-color": BG,
            "lovelace-background": BG,
            "clear-background-color": BG,
            "primary-text-color": TEXT,
            "secondary-text-color": MUTED,
            "disabled-text-color": FAINT,
            "text-primary-color": "#FFFFFF",
            "divider-color": LINE,
            "outline-color": LINE,
            # cards: no box, no shadow; the hairline comes from card-mod (or the cards' own rules)
            "card-background-color": SURFACE,
            "ha-card-background": "transparent",
            "ha-card-border-width": "0px",
            "ha-card-border-color": LINE,
            "ha-card-border-radius": "0px",
            "ha-card-box-shadow": "none",
            "ha-dialog-border-radius": "6px",
            "mdc-theme-surface": SURFACE,
            "mdc-theme-on-surface": TEXT,
            "dialog-backdrop-filter": "none",
            "input-fill-color": "transparent",
            "input-disabled-fill-color": "transparent",
            "input-ink-color": TEXT,
            "input-label-ink-color": MUTED,
            "input-idle-line-color": FAINT,
            "input-hover-line-color": MUTED,
            # accents
            "primary-color": RED,
            "dark-primary-color": "#A80F2E",
            "light-primary-color": "#F26A82",
            "accent-color": RED,
            "rgb-primary-color": "220, 20, 60",
            "rgb-accent-color": "220, 20, 60",
            "mdc-theme-primary": RED,
            "mdc-theme-secondary": RED,
            "switch-checked-color": RED,
            "switch-checked-button-color": RED,
            "switch-checked-track-color": RED,
            "slider-color": RED,
            "paper-slider-knob-color": RED,
            "success-color": OK,
            "warning-color": WARN,
            "error-color": CRIT,
            "info-color": MUTED,
            # states: crimson for on and active, grey for off
            "state-icon-color": MUTED,
            "state-active-color": RED,
            "state-inactive-color": FAINT,
            "state-unavailable-color": FAINT,
            "state-lock-locked-color": OK,
            "state-lock-unlocked-color": CRIT,
            "state-lock-jammed-color": CRIT,
            "state-alarm_control_panel-armed_away-color": OK,
            "state-alarm_control_panel-armed_home-color": OK,
            "state-alarm_control_panel-triggered-color": CRIT,
            "state-binary_sensor-active-color": WARN,
            "state-climate-heat-color": RED,
            "state-climate-cool-color": "#6C9CF0",
            "history-unavailable-color": LINE,
            # the top bar and the sidebar: the page's own black with a hairline
            "app-header-background-color": BG,
            "app-header-text-color": TEXT,
            "app-header-selection-bar-color": RED,
            "app-header-border-bottom": f"1px solid {LINE}",
            "app-header-edit-background-color": SURFACE,
            "app-header-edit-text-color": TEXT,
            "sidebar-background-color": BG,
            "sidebar-menu-button-background-color": BG,
            "sidebar-text-color": MUTED,
            "sidebar-icon-color": MUTED,
            "sidebar-selected-text-color": TEXT,
            "sidebar-selected-icon-color": RED,
            "sidebar-border-color": LINE,
            # type
            "ha-font-family-body": UI,
            "ha-font-family-heading": SERIF,
            "ha-font-family-longform": SERIF,
            "ha-font-family-code": MONO,
            "code-font-family": MONO,
            "paper-font-common-base_-_font-family": UI,
            "mdc-typography-font-family": UI,
            "primary-font-family": UI,
            # the Hisho cards' own variables, so a card on any dashboard with this theme matches
            "hisho-theme-accent": RED,
            "hisho-theme-glass": "transparent",
            # card-mod
            "card-mod-theme": THEME_NAME,
            "card-mod-card": CARD_MOD_CARD,
            "card-mod-root": CARD_MOD_ROOT,
        }
    }
}


@callback
def _add_theme(hass: HomeAssistant) -> bool:
    """Put the Hisho theme in the frontend's list; True when it was missing. Brings back the default theme choice
    the frontend dropped at startup because the theme didn't exist yet."""
    themes = hass.data.get(DATA_THEMES)
    if themes is None:  # no frontend (a headless Home Assistant)
        return False
    if THEME_NAME in themes:
        return False
    themes[THEME_NAME] = THEME
    return True


async def _restore_defaults(hass: HomeAssistant) -> None:
    """At startup the frontend resets a saved default theme it doesn't know to "default"; if that was Hisho, put it back."""
    store = hass.data.get(DATA_THEMES_STORE)
    if store is None:
        return
    try:
        saved = await store.async_load() or {}
    except Exception:  # noqa: BLE001 - a broken store only means no saved choice
        return
    if not isinstance(saved, dict):
        return
    if saved.get(DATA_DEFAULT_THEME) == THEME_NAME:
        hass.data[DATA_DEFAULT_THEME] = THEME_NAME
    if saved.get(DATA_DEFAULT_DARK_THEME) == THEME_NAME:
        hass.data[DATA_DEFAULT_DARK_THEME] = THEME_NAME


async def async_setup_theme(hass: HomeAssistant) -> bool:
    """Add the Hisho theme, keep it through theme reloads, and load its fonts with the frontend. False without a frontend."""
    if DATA_THEMES not in hass.data:
        _LOGGER.debug("Frontend not loaded; no Hisho theme")
        return False
    _add_theme(hass)
    await _restore_defaults(hass)
    hass.bus.async_fire(EVENT_THEMES_UPDATED)

    @callback
    def _reloaded(_event: Event) -> None:
        # frontend.reload_themes replaced the list with configuration.yaml's: add Hisho again and tell the browsers.
        if _add_theme(hass):
            hass.async_create_task(_readd_defaults(hass))

    async def _readd_defaults(hass: HomeAssistant) -> None:
        await _restore_defaults(hass)
        hass.bus.async_fire(EVENT_THEMES_UPDATED)

    hass.bus.async_listen(EVENT_THEMES_UPDATED, _reloaded)
    try:
        from homeassistant.components.frontend import add_extra_js_url  # noqa: PLC0415

        add_extra_js_url(hass, FONTS_JS)
    except (ImportError, KeyError):
        _LOGGER.debug("Frontend not loaded; the Hisho fonts load with the cards")
    return True
