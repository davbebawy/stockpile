"""Shared helpers for the Stockpile integration."""
from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .db import InventoryDB


def get_db(hass: HomeAssistant) -> InventoryDB:
    """Return the single Stockpile database instance.

    Stockpile is a single-instance integration, so the first loaded entry's
    runtime_data is the DB.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        db = getattr(entry, "runtime_data", None)
        if isinstance(db, InventoryDB):
            return db
    raise RuntimeError("Stockpile is not set up")
