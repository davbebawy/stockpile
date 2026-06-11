"""Serve the bundled Lovelace card and register it with the frontend.

This is what lets the card ship *inside* the integration and update via HACS,
with no manual resource setup. The JS is served from the integration folder and
added as an extra frontend module so the custom card elements get defined.
"""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant

from .const import DOMAIN, VERSION

_LOGGER = logging.getLogger(__name__)

CARD_FILENAME = "stockpile-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"


async def async_register_frontend(hass: HomeAssistant) -> None:
    """Register the static path and add the card module to the frontend."""
    card_path = Path(__file__).parent / "frontend" / CARD_FILENAME
    await hass.http.async_register_static_paths(
        [StaticPathConfig(CARD_URL, str(card_path), cache_headers=False)]
    )
    # Version query busts the browser cache when the integration updates.
    add_extra_js_url(hass, f"{CARD_URL}?v={VERSION}")
    _LOGGER.debug("Stockpile card registered at %s", CARD_URL)
