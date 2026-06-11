"""Services for the Stockpile integration.

Each mutating service fires EVENT_UPDATED on the bus so sensors, automations,
and the frontend card can react. Service calls that need to return data
(e.g. the new package id) use SupportsResponse.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
import homeassistant.helpers.config_validation as cv

from .const import DOMAIN, EVENT_UPDATED
from .helpers import get_db

SERVICES = [
    "add_product",
    "add_package",
    "consume",
    "set_remaining",
    "remove_package",
    "remove_product",
    "add_location",
    "reorder",
    "seed_demo",
    "get_summary",
    "list_packages",
]

GET_SUMMARY_SCHEMA = vol.Schema({})
LIST_PACKAGES_SCHEMA = vol.Schema({vol.Optional("location_id"): cv.string})
REORDER_SCHEMA = vol.Schema(
    {vol.Required("package_ids"): vol.All(cv.ensure_list, [cv.string])}
)
SEED_DEMO_SCHEMA = vol.Schema({})

ADD_PRODUCT_SCHEMA = vol.Schema(
    {
        vol.Required("name"): cv.string,
        vol.Optional("brand"): cv.string,
        vol.Optional("unit"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("image"): cv.string,
        vol.Optional("aliases"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("threshold"): vol.Coerce(float),
    }
)

ADD_PACKAGE_SCHEMA = vol.Schema(
    {
        # Either reference an existing product...
        vol.Optional("product_id"): cv.string,
        # ...or pass name/brand and we'll find-or-create the product.
        vol.Optional("name"): cv.string,
        vol.Optional("brand"): cv.string,
        vol.Optional("unit"): cv.string,
        vol.Optional("remaining", default=100.0): vol.All(vol.Coerce(float), vol.Range(0, 100)),
        vol.Optional("quantity", default=1.0): vol.Coerce(float),
        vol.Optional("location_id"): cv.string,
        vol.Optional("frozen"): cv.string,
        vol.Optional("expires"): cv.string,
        vol.Optional("notes"): cv.string,
    }
)

CONSUME_SCHEMA = vol.Schema(
    {
        vol.Required("package_id"): cv.string,
        vol.Required("amount"): vol.All(vol.Coerce(float), vol.Range(0, 100)),
        vol.Optional("who"): cv.string,
    }
)

SET_REMAINING_SCHEMA = vol.Schema(
    {
        vol.Required("package_id"): cv.string,
        vol.Required("remaining"): vol.All(vol.Coerce(float), vol.Range(0, 100)),
        vol.Optional("who"): cv.string,
    }
)

REMOVE_PACKAGE_SCHEMA = vol.Schema({vol.Required("package_id"): cv.string})
REMOVE_PRODUCT_SCHEMA = vol.Schema({vol.Required("product_id"): cv.string})
ADD_LOCATION_SCHEMA = vol.Schema(
    {vol.Required("name"): cv.string, vol.Optional("parent"): cv.string}
)


def async_register_services(hass: HomeAssistant) -> None:
    def notify() -> None:
        hass.bus.async_fire(EVENT_UPDATED, {})

    async def add_product(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        pid = await db.add_product(
            name=call.data["name"],
            brand=call.data.get("brand"),
            unit=call.data.get("unit"),
            category=call.data.get("category"),
            image=call.data.get("image"),
            aliases=call.data.get("aliases"),
            threshold=call.data.get("threshold"),
        )
        notify()
        return {"product_id": pid}

    async def add_package(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        product_id = call.data.get("product_id")
        if not product_id:
            name = call.data.get("name")
            if not name:
                raise vol.Invalid("Provide either product_id or name")
            brand = call.data.get("brand")
            existing = await db.find_product(name, brand)
            if existing:
                product_id = existing["id"]
            else:
                product_id = await db.add_product(
                    name=name, brand=brand, unit=call.data.get("unit")
                )
        pkg_id = await db.add_package(
            product_id=product_id,
            remaining=call.data["remaining"],
            quantity=call.data["quantity"],
            location_id=call.data.get("location_id"),
            frozen=call.data.get("frozen"),
            expires=call.data.get("expires"),
            notes=call.data.get("notes"),
        )
        notify()
        return {"package_id": pkg_id, "product_id": product_id}

    async def consume(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        pkg = await db.consume(
            call.data["package_id"], call.data["amount"], call.data.get("who")
        )
        notify()
        return {"package": pkg}

    async def set_remaining(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        pkg = await db.set_remaining(
            call.data["package_id"], call.data["remaining"], call.data.get("who")
        )
        notify()
        return {"package": pkg}

    async def remove_package(call: ServiceCall) -> None:
        db = get_db(hass)
        await db.remove_package(call.data["package_id"])
        notify()

    async def remove_product(call: ServiceCall) -> None:
        db = get_db(hass)
        await db.remove_product(call.data["product_id"])
        notify()

    async def get_summary(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        return {"summary": await db.get_summary()}

    async def list_packages(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        return {"packages": await db.get_packages(call.data.get("location_id"))}

    async def reorder(call: ServiceCall) -> None:
        db = get_db(hass)
        await db.reorder(call.data["package_ids"])
        notify()

    async def seed_demo(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        result = await db.seed_demo()
        notify()
        return result

    async def add_location(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        loc_id = await db.add_location(call.data["name"], call.data.get("parent"))
        notify()
        return {"location_id": loc_id}

    hass.services.async_register(
        DOMAIN, "add_product", add_product, ADD_PRODUCT_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "add_package", add_package, ADD_PACKAGE_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "consume", consume, CONSUME_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "set_remaining", set_remaining, SET_REMAINING_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "remove_package", remove_package, REMOVE_PACKAGE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "remove_product", remove_product, REMOVE_PRODUCT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "add_location", add_location, ADD_LOCATION_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "reorder", reorder, REORDER_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, "seed_demo", seed_demo, SEED_DEMO_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "get_summary", get_summary, GET_SUMMARY_SCHEMA, SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN, "list_packages", list_packages, LIST_PACKAGES_SCHEMA, SupportsResponse.ONLY
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)
