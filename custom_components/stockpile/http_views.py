"""HTTP views for the Stockpile integration."""
from __future__ import annotations

import io
import logging
from urllib.parse import urlparse

from aiohttp import web
from homeassistant.components.http import HomeAssistantView

_LOGGER = logging.getLogger(__name__)


class StockpileQRView(HomeAssistantView):
    """Return an SVG QR code for a same-origin URL.

    ``requires_auth = False`` lets the card display it in an ``<img>`` tag.
    To prevent the endpoint being used as an open QR generator for arbitrary
    external URLs (phishing), only same-origin absolute URLs and relative paths
    are accepted.
    """

    url = "/api/stockpile/qr"
    name = "api:stockpile:qr"
    requires_auth = False

    async def get(self, request: web.Request) -> web.Response:
        target_url = request.query.get("url", "").strip()
        if not target_url:
            return web.Response(status=400, text="url parameter required")

        parsed = urlparse(target_url)
        if parsed.scheme or parsed.netloc:
            if parsed.scheme not in ("http", "https"):
                return web.Response(status=400, text="invalid URL scheme")
            if parsed.netloc != request.host:
                return web.Response(status=400, text="cross-origin URLs not permitted")

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
            _LOGGER.warning("QR generation failed for %r: %s", target_url, exc)
            return web.Response(status=500, text="QR generation failed")
