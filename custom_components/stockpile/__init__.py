"""The Stockpile integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DB_FILENAME, DOMAIN, PLATFORMS
from .db import InventoryDB
from .frontend_register import async_register_frontend
from .http_views import StockpileQRView
from .services import async_register_services
from .websocket import async_register_websocket

# Typed config entry (2024.6+): the DB lives on entry.runtime_data.
type StockpileConfigEntry = ConfigEntry[InventoryDB]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the things that exist once per HA process."""
    if hass.data.get(DOMAIN, {}).get("_registered"):
        return True
    async_register_services(hass)
    async_register_websocket(hass)
    await async_register_frontend(hass)
    hass.http.register_view(StockpileQRView())
    hass.data.setdefault(DOMAIN, {})["_registered"] = True
    return True


async def async_setup_entry(hass: HomeAssistant, entry: StockpileConfigEntry) -> bool:
    """Set up Stockpile from a config entry."""
    db = InventoryDB(hass.config.path(DB_FILENAME))
    await db.async_init()
    entry.runtime_data = db

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: StockpileConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and entry.runtime_data is not None:
        await entry.runtime_data.async_close()
    return unload_ok
