"""Constants for the Stockpile integration."""

DOMAIN = "stockpile"
VERSION = "0.9.1"

DB_FILENAME = "stockpile.db"

# Threshold (in days) below which a package is treated as "expiring soon".
EXPIRING_SOON_DAYS = 7

# Fired on the HA event bus whenever inventory data changes.
# Automations and the frontend card can listen for this.
EVENT_UPDATED = "stockpile_updated"

PLATFORMS = ["sensor"]

# A package is considered "depleted" at this remaining percent.
DEPLETED_AT = 0.0
