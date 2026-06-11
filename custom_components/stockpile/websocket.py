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
    websocket_api.async_register_command(hass, ws_subscribe)
