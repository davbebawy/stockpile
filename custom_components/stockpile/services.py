"""Services for the Stockpile integration.

Each mutating service fires EVENT_UPDATED on the bus so sensors, automations,
and the frontend card can react. Service calls that need to return data
(e.g. the new package id) use SupportsResponse.
"""
from __future__ import annotations

from datetime import timedelta

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.util import dt as dt_util

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
    "export",
    "import_data",
    "compose_notification",
    "snooze",
    "acknowledge",
    "push_to_todo",
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

EXPORT_SCHEMA = vol.Schema({})
IMPORT_SCHEMA = vol.Schema(
    {
        vol.Required("data"): dict,
        vol.Optional("replace", default=False): cv.boolean,
    }
)
COMPOSE_NOTIFICATION_SCHEMA = vol.Schema(
    {
        vol.Optional("kind", default="all"): vol.In(["all", "low", "expiring"]),
        vol.Optional("limit", default=20): vol.All(vol.Coerce(int), vol.Range(min=1, max=200)),
    }
)

SNOOZE_SCHEMA = vol.Schema(
    {
        vol.Required("product_id"): cv.string,
        vol.Exclusive("hours", "duration"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Exclusive("days", "duration"): vol.All(vol.Coerce(float), vol.Range(min=0)),
        vol.Exclusive("until", "duration"): cv.string,
    }
)
ACKNOWLEDGE_SCHEMA = vol.Schema({vol.Required("product_id"): cv.string})

PUSH_TO_TODO_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("kind", default="low"): vol.In(["low", "expiring", "all"]),
        vol.Optional("dedupe", default=True): cv.boolean,
    }
)


def async_register_services(hass: HomeAssistant) -> None:
    def notify() -> None:
        hass.bus.async_fire(EVENT_UPDATED, {})

    async def _resolve_who(call: ServiceCall) -> str | None:
        """Use the supplied `who`, otherwise the calling user's friendly name."""
        explicit = call.data.get("who")
        if explicit:
            return explicit
        user_id = getattr(call.context, "user_id", None)
        if not user_id:
            return None
        user = await hass.auth.async_get_user(user_id)
        return user.name if user else None

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
        who = await _resolve_who(call)
        pkg = await db.consume(call.data["package_id"], call.data["amount"], who)
        notify()
        return {"package": pkg}

    async def set_remaining(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        who = await _resolve_who(call)
        pkg = await db.set_remaining(call.data["package_id"], call.data["remaining"], who)
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

    async def export_data(call: ServiceCall) -> ServiceResponse:
        return await get_db(hass).export_all()

    async def import_data(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        counts = await db.import_all(call.data["data"], replace=call.data["replace"])
        notify()
        return {"imported": counts}

    async def snooze(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        if "until" in call.data:
            until_iso = call.data["until"]
        else:
            hours = call.data.get("hours")
            days = call.data.get("days")
            if hours is None and days is None:
                raise vol.Invalid("Provide one of: hours, days, until")
            delta = timedelta(hours=hours or 0, days=days or 0)
            until_iso = (dt_util.utcnow() + delta).isoformat()
        await db.snooze_product(call.data["product_id"], until_iso)
        notify()
        return {"product_id": call.data["product_id"], "snoozed_until": until_iso}

    async def acknowledge(call: ServiceCall) -> None:
        db = get_db(hass)
        await db.acknowledge_product(call.data["product_id"])
        notify()

    async def push_to_todo(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        entity_id = call.data["entity_id"]
        kind = call.data["kind"]
        dedupe = call.data["dedupe"]

        items: list[str] = []
        if kind in ("all", "low"):
            for r in await db.get_low_stock():
                label = r["name"]
                if r.get("brand"):
                    label = f"{label} ({r['brand']})"
                items.append(label)
        if kind in ("all", "expiring"):
            for r in await db.get_expiring_soon():
                label = r["product_name"]
                if r.get("brand"):
                    label = f"{label} ({r['brand']})"
                items.append(f"{label} - use soon")

        existing: set[str] = set()
        if dedupe:
            try:
                resp = await hass.services.async_call(
                    "todo", "get_items", {"entity_id": entity_id},
                    blocking=True, return_response=True,
                )
                for entry in (resp or {}).get(entity_id, {}).get("items", []):
                    summary = entry.get("summary") or entry.get("name")
                    if summary:
                        existing.add(summary)
            except Exception:
                existing = set()

        added = 0
        for label in items:
            if dedupe and label in existing:
                continue
            await hass.services.async_call(
                "todo", "add_item", {"entity_id": entity_id, "item": label},
                blocking=True,
            )
            added += 1
        return {"added": added, "considered": len(items), "entity_id": entity_id}

    async def compose_notification(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        kind = call.data["kind"]
        limit = call.data["limit"]
        low = await db.get_low_stock() if kind in ("all", "low") else []
        expiring = await db.get_expiring_soon() if kind in ("all", "expiring") else []
        low_lines = [
            f"{r['name']}{f' ({r['brand']})' if r.get('brand') else ''}"
            f" — {r['package_count']} pkg, {round(r['equiv_remaining'], 2)} left"
            for r in low[:limit]
        ]
        exp_lines = [
            f"{r['product_name']}{f' ({r['brand']})' if r.get('brand') else ''}"
            f" — {('expired ' + str(abs(r['expires_in_days'])) + 'd ago') if r['expired'] else ('expires in ' + str(r['expires_in_days']) + 'd')}"
            for r in expiring[:limit]
        ]
        parts: list[str] = []
        if low_lines:
            parts.append("Low stock:\n  " + "\n  ".join(low_lines))
        if exp_lines:
            parts.append("Expiring soon:\n  " + "\n  ".join(exp_lines))
        message = "\n\n".join(parts) or "Inventory is healthy."
        return {
            "title": "Stockpile",
            "message": message,
            "low_count": len(low),
            "expiring_count": len(expiring),
        }

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
    hass.services.async_register(
        DOMAIN, "export", export_data, EXPORT_SCHEMA, SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN, "import_data", import_data, IMPORT_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN,
        "compose_notification",
        compose_notification,
        COMPOSE_NOTIFICATION_SCHEMA,
        SupportsResponse.ONLY,
    )
    hass.services.async_register(
        DOMAIN, "snooze", snooze, SNOOZE_SCHEMA, SupportsResponse.OPTIONAL
    )
    hass.services.async_register(DOMAIN, "acknowledge", acknowledge, ACKNOWLEDGE_SCHEMA)
    hass.services.async_register(
        DOMAIN, "push_to_todo", push_to_todo, PUSH_TO_TODO_SCHEMA, SupportsResponse.OPTIONAL
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)
