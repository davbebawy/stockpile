"""Services for the Stockpile integration.

Each mutating service fires EVENT_UPDATED on the bus so sensors, automations,
and the frontend card can react. Service calls that need to return data
(e.g. the new package id) use SupportsResponse.
"""
from __future__ import annotations

import json
import re
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

# --------------------------------------------------------------------------- #
# Receipt parser (pure-Python, no external deps)
# --------------------------------------------------------------------------- #
_PRICE_RE   = re.compile(r'\$?\s*\d+[.,]\d{2}(?:\s*[A-Z])?\s*$')
_QTY_X_RE   = re.compile(r'^(\d+(?:\.\d+)?)\s*[xX]\s+')
_AT_RE      = re.compile(r'\b\d+(?:\.\d+)?\s*(?:lb|lbs?|oz|kg|g)\s*@\s*\$?\d+[.,]\d+/\w+', re.I)
_WEIGHT_RE  = re.compile(r'\b(\d+(?:\.\d+)?)\s*(lb|lbs?|oz|kg|gal|ct|pk|pack|ea|each)\b', re.I)
_JUNK_RE    = re.compile(r'\b\d{5,}\b|#\d+|/[Ll][Bb]')
_SKIP_WORDS = frozenset([
    'subtotal', 'total', 'tax', 'change', 'balance', 'cash', 'credit', 'debit',
    'visa', 'mastercard', 'amex', 'discover', 'thank', 'receipt', 'store',
    'cashier', 'transaction', 'payment', 'savings', 'discount', 'coupon',
    'manager', 'special', 'sale', 'tel', 'phone', 'fax', 'address', 'www',
    'http', 'member', 'loyalty', 'reward', 'points',
])
_UNIT_NORM = {'lbs': 'lb', 'ozs': 'oz', 'kgs': 'kg', 'packs': 'pack', 'pks': 'pack'}


def _parse_receipt_text(text: str) -> list[dict]:
    """Extract product candidates from raw receipt text."""
    out: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        line = re.sub(r'[\x00-\x1f\x7f]', ' ', line).strip()
        if len(line) < 3:
            continue
        ll = line.lower()
        if any(re.search(r'\b' + re.escape(w) + r'\b', ll) for w in _SKIP_WORDS):
            continue
        line = _PRICE_RE.sub('', line).strip()
        if not line:
            continue
        line = _AT_RE.sub('', line).strip()
        qty = 1.0
        unit: str | None = None
        m = _QTY_X_RE.match(line)
        if m:
            qty = float(m.group(1))
            line = line[m.end():]
        wm = _WEIGHT_RE.search(line)
        if wm:
            qty = float(wm.group(1))
            u = _UNIT_NORM.get(wm.group(2).lower(), wm.group(2).lower().rstrip('s'))
            unit = 'each' if u in ('ea', 'each') else u
            line = (line[:wm.start()] + line[wm.end():])
        line = _JUNK_RE.sub('', line).strip(' -.,:/\\')
        line = re.sub(r'\s+', ' ', line).strip()
        if len(line) < 2:
            continue
        name = ' '.join(w.capitalize() for w in line.split())
        out.append({'name': name, 'qty': round(qty, 3), 'unit': unit})
    return out


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
    "suggest_restock",
    "parse_receipt",
    "set_package_position",
    "set_location_template",
    "clear_product_cache",
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
        # Product metadata — used when creating; fills missing fields on existing products.
        vol.Optional("image"): cv.string,
        vol.Optional("category"): cv.string,
        vol.Optional("remaining", default=100.0): vol.All(vol.Coerce(float), vol.Range(0, 100)),
        vol.Optional("quantity", default=1.0): vol.Coerce(float),
        vol.Optional("location_id"): cv.string,
        vol.Optional("frozen"): cv.string,
        vol.Optional("expires"): cv.string,
        vol.Optional("notes"): cv.string,
    }
)

CLEAR_PRODUCT_CACHE_SCHEMA = vol.Schema(
    {vol.Optional("key"): cv.string}
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

PARSE_RECEIPT_SCHEMA = vol.Schema({vol.Required("text"): cv.string})

SET_PACKAGE_POSITION_SCHEMA = vol.Schema(
    {
        vol.Required("package_id"): cv.string,
        vol.Optional("loc_x"): vol.Any(None, vol.Coerce(float)),
        vol.Optional("loc_y"): vol.Any(None, vol.Coerce(float)),
    }
)
SET_LOCATION_TEMPLATE_SCHEMA = vol.Schema(
    {
        vol.Required("location_id"): cv.string,
        vol.Optional("template_id"): vol.Any(None, cv.string),
        # Accept dict (from YAML/scripts) or JSON string; handler normalises to str.
        vol.Optional("template_config"): vol.Any(None, dict, cv.string),
    }
)

SUGGEST_RESTOCK_SCHEMA = vol.Schema(
    {
        vol.Optional("velocity_days", default=30): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
        vol.Optional("horizon_days", default=14): vol.All(vol.Coerce(int), vol.Range(min=1, max=365)),
    }
)

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
        image = call.data.get("image")
        category = call.data.get("category")
        if not product_id:
            name = call.data.get("name")
            if not name:
                raise vol.Invalid("Provide either product_id or name")
            brand = call.data.get("brand")
            existing = await db.find_product(name, brand)
            if existing:
                product_id = existing["id"]
                # Fill in missing metadata without overwriting user's existing data.
                if image or category:
                    await db.update_product_metadata(
                        product_id, image=image, category=category
                    )
            else:
                product_id = await db.add_product(
                    name=name, brand=brand, unit=call.data.get("unit"),
                    image=image, category=category,
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
        low_lines = []
        for r in low[:limit]:
            brand = f" ({r['brand']})" if r.get("brand") else ""
            low_lines.append(
                f"{r['name']}{brand}"
                f" — {r['package_count']} pkg, {round(r['equiv_remaining'], 2)} left"
            )
        exp_lines = []
        for r in expiring[:limit]:
            brand = f" ({r['brand']})" if r.get("brand") else ""
            days = r["expires_in_days"]
            exp_str = f"expired {abs(days)}d ago" if r["expired"] else f"expires in {days}d"
            exp_lines.append(f"{r['product_name']}{brand} — {exp_str}")
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

    async def suggest_restock(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        suggestions = await db.suggest_restock(
            velocity_days=call.data["velocity_days"],
            horizon_days=call.data["horizon_days"],
        )
        return {"suggestions": suggestions}

    hass.services.async_register(
        DOMAIN, "suggest_restock", suggest_restock, SUGGEST_RESTOCK_SCHEMA, SupportsResponse.ONLY
    )

    async def parse_receipt(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        raw = _parse_receipt_text(call.data["text"])
        suggestions = []
        for item in raw:
            match = await db.find_product(item["name"])
            suggestions.append({
                "name": match["name"] if match else item["name"],
                "qty": item["qty"],
                "unit": item["unit"] or (match.get("unit") if match else None),
                "matched": match is not None,
                "product_id": match["id"] if match else None,
                "brand": match.get("brand") if match else None,
            })
        return {"suggestions": suggestions, "total": len(suggestions)}

    hass.services.async_register(
        DOMAIN, "parse_receipt", parse_receipt, PARSE_RECEIPT_SCHEMA, SupportsResponse.ONLY
    )

    async def set_package_position(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        loc_x = call.data.get("loc_x")
        loc_y = call.data.get("loc_y")
        pkg = await db.set_package_position(call.data["package_id"], loc_x, loc_y)
        notify()
        return {"package": pkg}

    hass.services.async_register(
        DOMAIN, "set_package_position", set_package_position,
        SET_PACKAGE_POSITION_SCHEMA, SupportsResponse.OPTIONAL,
    )

    async def set_location_template(call: ServiceCall) -> None:
        db = get_db(hass)
        config = call.data.get("template_config")
        if isinstance(config, dict):
            config = json.dumps(config)
        await db.set_location_template(
            call.data["location_id"],
            call.data.get("template_id"),
            config,
        )
        notify()

    hass.services.async_register(
        DOMAIN, "set_location_template", set_location_template,
        SET_LOCATION_TEMPLATE_SCHEMA,
    )

    async def clear_product_cache(call: ServiceCall) -> ServiceResponse:
        db = get_db(hass)
        key = call.data.get("key")
        if key:
            await db.delete_off_cache(key)
            return {"deleted": 1, "key": key}
        count = await db.clear_off_cache()
        return {"deleted": count}

    hass.services.async_register(
        DOMAIN, "clear_product_cache", clear_product_cache,
        CLEAR_PRODUCT_CACHE_SCHEMA, SupportsResponse.OPTIONAL,
    )


def async_unregister_services(hass: HomeAssistant) -> None:
    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)
