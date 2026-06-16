"""WebSocket API for the Inventory integration.

This is the data channel the custom Lovelace card uses instead of fetching a
JSON file. Read commands return data; `stockpile/subscribe` pushes a message
every time EVENT_UPDATED fires so the card refreshes live.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote_plus

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import EVENT_UPDATED
from .helpers import get_db as _db

# --------------------------------------------------------------------------- #
# Open Food Facts helpers
# --------------------------------------------------------------------------- #
_OFF_UNIT_RE = re.compile(
    r'\b(\d+(?:\.\d+)?)\s*'
    r'(fl\.?\s*oz|oz|lbs?|lb|kgs?|kg|g|ml|l|ct|count|pk|pack(?:s)?|each|ea)\b',
    re.I,
)
_OFF_CAT_MAP: dict[str, str] = {
    "en:meats": "Meat", "en:meat": "Meat", "en:beef": "Meat",
    "en:poultry": "Meat", "en:chicken": "Meat", "en:pork": "Meat",
    "en:seafood": "Seafood", "en:fish": "Seafood", "en:salmon": "Seafood",
    "en:vegetables": "Vegetables", "en:frozen-vegetables": "Vegetables",
    "en:dairy-products": "Dairy", "en:dairies": "Dairy",
    "en:cheeses": "Dairy", "en:cheese": "Dairy",
    "en:yogurts": "Dairy", "en:milks": "Dairy", "en:butter": "Dairy",
    "en:pastas": "Dry Goods", "en:pasta": "Dry Goods",
    "en:rice": "Dry Goods", "en:dry-goods": "Dry Goods",
    "en:cereals-and-potatoes": "Dry Goods", "en:cereals": "Dry Goods",
    "en:sauces": "Dry Goods", "en:condiments": "Dry Goods",
    "en:breads": "Bakery", "en:bread": "Bakery", "en:bakery": "Bakery",
    "en:frozen-foods": "Frozen", "en:frozen-meals": "Frozen",
    "en:ice-creams": "Dessert", "en:desserts": "Dessert",
    "en:beverages": "Beverages", "en:drinks": "Beverages",
    "en:snacks": "Snacks", "en:salty-snacks": "Snacks", "en:chips": "Snacks",
    "en:cleaning-products": "Household", "en:household-supplies": "Household",
    "en:paper-products": "Household", "en:laundry-products": "Household",
}


def _off_unit(qty: str | None) -> str | None:
    if not qty:
        return None
    m = _OFF_UNIT_RE.search(qty)
    if not m:
        return None
    raw = m.group(2).lower().replace(" ", "").replace(".", "")
    return {
        "lbs": "lb", "ozs": "oz", "kgs": "kg", "floz": "fl oz",
        "each": "ea", "count": "ct", "packs": "pack", "pk": "pack",
    }.get(raw, raw)


def _off_category(tags: list | None) -> str | None:
    for tag in (tags or []):
        cat = _OFF_CAT_MAP.get(tag.lower())
        if cat:
            return cat
    return None


def _normalize_off(p: dict) -> dict | None:
    name = (p.get("product_name_en") or p.get("product_name") or "").strip()
    if not name:
        return None
    brand_raw = p.get("brands") or ""
    brand = brand_raw.split(",")[0].strip() or None
    qty_str = p.get("quantity") or ""
    return {
        "name": name,
        "brand": brand,
        "image_url": p.get("image_front_url") or p.get("image_url") or None,
        "unit": _off_unit(qty_str),
        "category": _off_category(p.get("categories_tags")),
        "quantity_str": qty_str,
        "barcode": p.get("code") or None,
        "ingredients": (p.get("ingredients_text_en") or p.get("ingredients_text") or "")[:300] or None,
        "nutriscore": p.get("nutriscore_grade") or None,
        "ecoscore": p.get("ecoscore_grade") or None,
        "labels": [t.replace("en:", "").replace("-", " ").title() for t in (p.get("labels_tags") or []) if t.startswith("en:")][:5],
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): "stockpile/packages",
        vol.Optional("location_id"): str,
    }
)
@websocket_api.async_response
async def ws_packages(hass, connection, msg):
    packages = await _db(hass).get_packages(msg.get("location_id"))
    connection.send_result(msg["id"], {"packages": packages})


@websocket_api.websocket_command({vol.Required("type"): "stockpile/products"})
@websocket_api.async_response
async def ws_products(hass, connection, msg):
    connection.send_result(msg["id"], {"products": await _db(hass).get_products()})


@websocket_api.websocket_command({vol.Required("type"): "stockpile/summary"})
@websocket_api.async_response
async def ws_summary(hass, connection, msg):
    connection.send_result(msg["id"], {"summary": await _db(hass).get_summary()})


@websocket_api.websocket_command({vol.Required("type"): "stockpile/locations"})
@websocket_api.async_response
async def ws_locations(hass, connection, msg):
    connection.send_result(msg["id"], {"locations": await _db(hass).get_locations()})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "stockpile/history",
        vol.Optional("product_id"): str,
        vol.Optional("limit", default=100): int,
    }
)
@websocket_api.async_response
async def ws_history(hass, connection, msg):
    history = await _db(hass).get_history(msg.get("product_id"), msg["limit"])
    connection.send_result(msg["id"], {"history": history})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "stockpile/velocity",
        vol.Required("product_id"): str,
        vol.Optional("days", default=30): int,
    }
)
@websocket_api.async_response
async def ws_velocity(hass, connection, msg):
    velocity = await _db(hass).get_velocity(msg["product_id"], msg["days"])
    connection.send_result(msg["id"], {"velocity": velocity})


@websocket_api.websocket_command(
    {
        vol.Required("type"): "stockpile/templates",
        vol.Optional("location_id"): str,
    }
)
@websocket_api.async_response
async def ws_templates(hass, connection, msg):
    from .location_templates import get_template_list, render_for_location  # noqa: PLC0415

    templates = get_template_list()
    payload: dict = {"templates": templates}

    location_id = msg.get("location_id")
    if location_id:
        loc = next(
            (l for l in await _db(hass).get_locations() if l["id"] == location_id),
            None,
        )
        if loc and loc.get("template_id"):
            try:
                svg = render_for_location(loc["template_id"], loc.get("template_config"))
                payload["location_svg"] = svg
                payload["location_template_id"] = loc["template_id"]
            except ValueError:
                pass

    connection.send_result(msg["id"], payload)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "stockpile/trends",
        vol.Optional("days", default=14): int,
    }
)
@websocket_api.async_response
async def ws_trends(hass, connection, msg):
    trends = await _db(hass).get_trends(msg["days"])
    connection.send_result(msg["id"], {"trends": trends})


@websocket_api.websocket_command({vol.Required("type"): "stockpile/subscribe"})
@callback
def ws_subscribe(hass, connection, msg):
    """Push a notification to the card whenever inventory changes."""

    @callback
    def _forward(event) -> None:
        connection.send_message(
            websocket_api.event_message(msg["id"], {"event": "updated"})
        )

    remove = hass.bus.async_listen(EVENT_UPDATED, _forward)
    connection.subscriptions[msg["id"]] = remove
    connection.send_result(msg["id"])


_OFF_FIELDS = (
    "product_name,product_name_en,brands,image_front_url,quantity,"
    "categories_tags,labels_tags,code,ingredients_text_en,ingredients_text,"
    "nutriscore_grade,ecoscore_grade"
)
_OFF_SEARCH = (
    "https://world.openfoodfacts.org/cgi/search.pl"
    "?search_terms={q}&search_simple=1&action=process&json=1&page_size=6"
    f"&fields={_OFF_FIELDS}"
)
_OFF_BARCODE = (
    "https://world.openfoodfacts.org/api/v2/product/{{barcode}}.json"
    f"?fields={_OFF_FIELDS}"
)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "stockpile/search_product",
        vol.Optional("query"): str,
        vol.Optional("barcode"): str,
        vol.Optional("force_refresh", default=False): bool,
    }
)
@websocket_api.async_response
async def ws_search_product(hass, connection, msg):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession  # noqa: PLC0415
    import aiohttp  # noqa: PLC0415

    query = (msg.get("query") or "").strip()
    barcode = (msg.get("barcode") or "").strip()
    force = msg.get("force_refresh", False)

    if not query and not barcode:
        connection.send_error(msg["id"], "invalid_input", "Provide query or barcode")
        return

    db = _db(hass)
    cache_key = barcode if barcode else query.lower()

    if not force:
        cached = await db.get_off_cache(cache_key)
        if cached:
            try:
                results = json.loads(cached["results"] or "[]")
            except Exception:
                results = []
            connection.send_result(msg["id"], {
                "results": results,
                "cached": True,
                "fetched_at": cached["fetched_at"],
            })
            return

    try:
        session = async_get_clientsession(hass)
        timeout = aiohttp.ClientTimeout(total=12)
        results: list[dict] = []

        if barcode:
            url = f"https://world.openfoodfacts.org/api/v2/product/{barcode}.json?fields={_OFF_FIELDS}"
            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    prod = data.get("product")
                    if prod:
                        item = _normalize_off(prod)
                        if item:
                            results = [item]
        else:
            url = _OFF_SEARCH.format(q=quote_plus(query))
            async with session.get(url, timeout=timeout) as resp:
                if resp.status == 200:
                    data = await resp.json(content_type=None)
                    raw = data.get("products") or []
                    results = [r for r in (_normalize_off(p) for p in raw) if r]

        await db.upsert_off_cache(cache_key, query or barcode, json.dumps(results))
        connection.send_result(msg["id"], {"results": results, "cached": False})

    except Exception as exc:  # noqa: BLE001
        connection.send_error(msg["id"], "off_fetch_error", str(exc))


@websocket_api.websocket_command({vol.Required("type"): "stockpile/off_cache"})
@websocket_api.async_response
async def ws_off_cache(hass, connection, msg):
    entries = await _db(hass).list_off_cache()
    connection.send_result(msg["id"], {"entries": entries})


def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_packages)
    websocket_api.async_register_command(hass, ws_products)
    websocket_api.async_register_command(hass, ws_summary)
    websocket_api.async_register_command(hass, ws_locations)
    websocket_api.async_register_command(hass, ws_history)
    websocket_api.async_register_command(hass, ws_velocity)
    websocket_api.async_register_command(hass, ws_trends)
    websocket_api.async_register_command(hass, ws_templates)
    websocket_api.async_register_command(hass, ws_search_product)
    websocket_api.async_register_command(hass, ws_off_cache)
    websocket_api.async_register_command(hass, ws_subscribe)
