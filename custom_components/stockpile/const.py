"""Constants for the Stockpile integration."""

DOMAIN = "stockpile"
VERSION = "0.2.0"

DB_FILENAME = "stockpile.db"

# Fired on the HA event bus whenever inventory data changes.
# Automations and the frontend card can listen for this.
EVENT_UPDATED = "stockpile_updated"

PLATFORMS = ["sensor"]

# A package is considered "depleted" at this remaining percent.
DEPLETED_AT = 0.0
