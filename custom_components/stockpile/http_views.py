"""HTTP views for the Stockpile integration."""
from __future__ import annotations

import io

from aiohttp import web
from homeassistant.components.http import HomeAssistantView


class StockpileQRView(HomeAssistantView):
    """Return an SVG QR code for any URL passed as the ``url`` query parameter.

    The QR image itself contains no inventory data — it just encodes the URL
    the caller supplies. ``requires_auth = False`` lets the card display it in
    an ``<img>`` tag without needing to forward auth headers.
    """

    url = "/api/stockpile/qr"
    name = "api:stockpile:qr"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        target_url = request.query.get("url", "").strip()
        if not target_url:
            return web.Response(status=400, text="url parameter required")

        try:
            import qrcode  # noqa: PLC0415
            import qrcode.image.svg  # noqa: PLC0415

            factory = qrcode.image.svg.SvgPathImage
            img = qrcode.make(
                target_url,
                image_factory=factory,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
            )
            buf = io.BytesIO()
            img.save(buf)
            return web.Response(
                body=buf.getvalue(),
                content_type="image/svg+xml",
                headers={"Cache-Control": "max-age=3600"},
            )
        except ImportError:
            return web.Response(
                status=503,
                text="qrcode library not installed — restart Home Assistant after setup",
            )
        except Exception as exc:  # noqa: BLE001
            return web.Response(status=500, text=str(exc))
