"""WebSocket API for the Inventory integration.

This is the data channel the custom Lovelace card uses instead of fetching a
JSON file. Read commands return data; `stockpile/subscribe` pushes a message
every time EVENT_UPDATED fires so the card refreshes live.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import EVENT_UPDATED
from .helpers import get_db as _db


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


def async_register_websocket(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_packages)
    websocket_api.async_register_command(hass, ws_products)
    websocket_api.async_register_command(hass, ws_summary)
    websocket_api.async_register_command(hass, ws_locations)
    websocket_api.async_register_command(hass, ws_history)
    websocket_api.async_register_command(hass, ws_velocity)
    websocket_api.async_register_command(hass, ws_trends)
    websocket_api.async_register_command(hass, ws_templates)
    websocket_api.async_register_command(hass, ws_subscribe)
