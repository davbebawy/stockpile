"""Derived summary sensors.

These are the only entities the integration creates. They give automations
(shopping reminders, expiration notifications) something to trigger on,
without the anti-pattern of one entity per inventory item.
"""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, EVENT_UPDATED, VERSION
from .db import InventoryDB


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Stockpile",
        manufacturer="Stockpile",
        model="Inventory",
        sw_version=VERSION,
        entry_type=DeviceEntryType.SERVICE,
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    db: InventoryDB = entry.runtime_data
    async_add_entities(
        [
            TotalPackagesSensor(hass, entry, db),
            LowStockSensor(hass, entry, db),
            ExpiringSoonSensor(hass, entry, db),
        ]
    )


class _BaseInventorySensor(SensorEntity):
    """Refreshes itself whenever EVENT_UPDATED fires."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, db: InventoryDB) -> None:
        self.hass = hass
        self._entry = entry
        self._db = db
        self._attr_device_info = _device_info(entry)
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.hass.bus.async_listen(EVENT_UPDATED, self._handle_update)
        )
        await self._refresh()

    @callback
    def _handle_update(self, _event) -> None:
        self.hass.async_create_task(self._refresh())

    async def _refresh(self) -> None:
        raise NotImplementedError


class TotalPackagesSensor(_BaseInventorySensor):
    _attr_translation_key = "stockpile_total_packages"
    _attr_unique_id = "stockpile_total_packages"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "packages"

    async def _refresh(self) -> None:
        self._attr_native_value = await self._db.count_packages()
        self.async_write_ha_state()


class LowStockSensor(_BaseInventorySensor):
    _attr_translation_key = "stockpile_low_stock"
    _attr_unique_id = "stockpile_low_stock"
    _attr_icon = "mdi:cart-alert"
    _attr_native_unit_of_measurement = "items"

    async def _refresh(self) -> None:
        low = await self._db.get_low_stock()
        self._attr_native_value = len(low)
        self._attr_extra_state_attributes = {
            "items": [
                {
                    "name": r["name"],
                    "brand": r["brand"],
                    "equiv_remaining": round(r["equiv_remaining"], 2),
                    "package_count": r["package_count"],
                }
                for r in low
            ]
        }
        self.async_write_ha_state()


class ExpiringSoonSensor(_BaseInventorySensor):
    _attr_translation_key = "stockpile_expiring_soon"
    _attr_unique_id = "stockpile_expiring_soon"
    _attr_icon = "mdi:calendar-clock"
    _attr_native_unit_of_measurement = "items"

    async def _refresh(self) -> None:
        items = await self._db.get_expiring_soon()
        self._attr_native_value = len(items)
        self._attr_extra_state_attributes = {
            "items": [
                {
                    "name": r["product_name"],
                    "brand": r["brand"],
                    "location": r["location_name"],
                    "expires": r["expires"],
                    "expires_in_days": r["expires_in_days"],
                    "expired": r["expired"],
                }
                for r in items
            ]
        }
        self.async_write_ha_state()
