"""Built-in location templates for Stockpile.

Each template produces schematic SVG line-art that uses ``currentColor`` so it
adapts automatically to the Home Assistant theme.  The calling code sets
``color: var(--sp-fg-dim)`` on the containing element; the SVG inherits it.

Adding or updating a template only requires changing this file — the JS card
receives the rendered SVG via the ``stockpile/templates`` WebSocket command and
has no built-in renderers of its own.
"""
from __future__ import annotations

import json
from typing import Any


# ---------------------------------------------------------------------------
# Render functions
# ---------------------------------------------------------------------------

def _chest_freezer(cfg: dict[str, Any]) -> str:
    """Top-down (birds-eye) view of a chest / deep freezer."""
    baskets: int = max(0, min(4, int(cfg.get("baskets", 2))))
    dividers: int = max(0, min(3, int(cfg.get("dividers", 1))))

    W, H = 200, 130
    INNER_X, INNER_Y = 12, 12
    INNER_W, INNER_H = W - 24, H - 24
    BASKET_H = INNER_H * 0.38  # fraction of inner height for the basket zone

    parts: list[str] = [
        # Outer shell
        f'<rect x="3" y="3" width="{W-6}" height="{H-6}" rx="10"'
        f' fill="none" stroke="currentColor" stroke-width="3" stroke-opacity="0.28"/>',
        # Hinge bumps at back
        f'<rect x="34" y="0" width="14" height="7" rx="3"'
        f' fill="currentColor" fill-opacity="0.18"/>',
        f'<rect x="{W-48}" y="0" width="14" height="7" rx="3"'
        f' fill="currentColor" fill-opacity="0.18"/>',
        # Inner gasket line
        f'<rect x="{INNER_X}" y="{INNER_Y}" width="{INNER_W}" height="{INNER_H}" rx="5"'
        f' fill="none" stroke="currentColor" stroke-width="1"'
        f' stroke-opacity="0.15" stroke-dasharray="3,2"/>',
        # Handle at front
        f'<rect x="{W//2-28}" y="{H-8}" width="56" height="5" rx="2.5"'
        f' fill="currentColor" fill-opacity="0.16"/>',
    ]

    basket_bottom = INNER_Y + BASKET_H
    main_top = basket_bottom + 1

    if baskets > 0:
        # Horizontal separator between basket zone and main zone
        parts.append(
            f'<line x1="{INNER_X}" y1="{basket_bottom:.1f}"'
            f' x2="{INNER_X+INNER_W}" y2="{basket_bottom:.1f}"'
            f' stroke="currentColor" stroke-width="1.5" stroke-opacity="0.22"/>'
        )
        slot_w = INNER_W / baskets
        for i in range(1, baskets):
            bx = INNER_X + slot_w * i
            parts.append(
                f'<line x1="{bx:.1f}" y1="{INNER_Y}"'
                f' x2="{bx:.1f}" y2="{basket_bottom:.1f}"'
                f' stroke="currentColor" stroke-width="1" stroke-opacity="0.18"/>'
            )
        for i in range(baskets):
            lx = INNER_X + slot_w * (i + 0.5)
            ly = INNER_Y + BASKET_H / 2 + 4
            label = f"Basket {chr(65+i)}" if baskets > 1 else "Baskets"
            parts.append(
                f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle"'
                f' font-size="8" font-family="sans-serif"'
                f' fill="currentColor" fill-opacity="0.28">{label}</text>'
            )
    else:
        main_top = INNER_Y

    # Main zone vertical dividers
    main_h = (INNER_Y + INNER_H) - main_top
    if dividers > 0:
        slot_w = INNER_W / (dividers + 1)
        for i in range(1, dividers + 1):
            dx = INNER_X + slot_w * i
            parts.append(
                f'<line x1="{dx:.1f}" y1="{main_top:.1f}"'
                f' x2="{dx:.1f}" y2="{INNER_Y+INNER_H:.1f}"'
                f' stroke="currentColor" stroke-width="1"'
                f' stroke-opacity="0.16" stroke-dasharray="3,2"/>'
            )

    # Main zone label
    parts.append(
        f'<text x="{INNER_X + INNER_W/2:.1f}" y="{main_top + main_h/2 + 4:.1f}"'
        f' text-anchor="middle" font-size="8" font-family="sans-serif"'
        f' fill="currentColor" fill-opacity="0.25">Main</text>'
    )

    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">{"".join(parts)}</svg>'


def _wire_rack(cfg: dict[str, Any]) -> str:
    """Front-elevation view of a wire shelving rack."""
    shelves: int = max(2, min(8, int(cfg.get("shelves", 4))))
    wheels: bool = bool(cfg.get("wheels", True))

    W, H = 160, 220
    POST_W = 8
    TOP_Y = 10
    FOOT_H = 14
    BOTTOM_Y = H - FOOT_H - 6

    usable = BOTTOM_Y - TOP_Y
    shelf_gap = usable / (shelves - 1)

    parts: list[str] = [
        # Left vertical post
        f'<rect x="5" y="{TOP_Y}" width="{POST_W}" height="{BOTTOM_Y-TOP_Y}" rx="4"'
        f' fill="currentColor" fill-opacity="0.16"/>',
        # Right vertical post
        f'<rect x="{W-13}" y="{TOP_Y}" width="{POST_W}" height="{BOTTOM_Y-TOP_Y}" rx="4"'
        f' fill="currentColor" fill-opacity="0.16"/>',
    ]

    for i in range(shelves):
        sy = TOP_Y + i * shelf_gap
        # Shelf beam
        parts.append(
            f'<rect x="5" y="{sy:.1f}" width="{W-10}" height="5" rx="2.5"'
            f' fill="currentColor" fill-opacity="0.28"/>'
        )
        # Sparse wire hints in bay below this shelf
        if i < shelves - 1:
            next_sy = sy + shelf_gap
            for j in range(1, 4):
                wx = 5 + (W - 10) * j / 4
                parts.append(
                    f'<line x1="{wx:.1f}" y1="{sy+5:.1f}" x2="{wx:.1f}" y2="{next_sy:.1f}"'
                    f' stroke="currentColor" stroke-width="0.6" stroke-opacity="0.1"/>'
                )

    # Feet or wheels
    if wheels:
        for fx in [5 + POST_W / 2, W - 13 + POST_W / 2]:
            parts.append(
                f'<circle cx="{fx:.1f}" cy="{BOTTOM_Y+8}" r="6"'
                f' fill="none" stroke="currentColor" stroke-width="1.5" stroke-opacity="0.28"/>'
            )
            parts.append(
                f'<circle cx="{fx:.1f}" cy="{BOTTOM_Y+8}" r="2"'
                f' fill="currentColor" fill-opacity="0.18"/>'
            )
    else:
        for fx in [3, W - 15]:
            parts.append(
                f'<rect x="{fx}" y="{BOTTOM_Y}" width="12" height="{FOOT_H}" rx="3"'
                f' fill="currentColor" fill-opacity="0.2"/>'
            )

    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">{"".join(parts)}</svg>'


def _refrigerator(cfg: dict[str, Any]) -> str:
    """Front-elevation view of a bottom-freezer refrigerator."""
    fridge_shelves: int = max(2, min(5, int(cfg.get("fridge_shelves", 3))))
    door_pockets: int = max(0, min(5, int(cfg.get("door_pockets", 3))))

    W, H = 140, 280
    R = 8
    DOOR_W = 26  # door-pocket strip width on right side of fridge section

    FRIDGE_TOP = 8
    FRIDGE_H = int(H * 0.65)
    FREEZER_TOP = FRIDGE_TOP + FRIDGE_H + 5
    FREEZER_H = H - FREEZER_TOP - 8

    parts: list[str] = [
        # Outer shell
        f'<rect x="3" y="3" width="{W-6}" height="{H-6}" rx="{R}"'
        f' fill="none" stroke="currentColor" stroke-width="2.5" stroke-opacity="0.28"/>',
        # Horizontal split line (fridge / freezer divider)
        f'<line x1="3" y1="{FREEZER_TOP-2}" x2="{W-3}" y2="{FREEZER_TOP-2}"'
        f' stroke="currentColor" stroke-width="2" stroke-opacity="0.22"/>',
        # Fridge body outline
        f'<rect x="8" y="{FRIDGE_TOP+4}" width="{W-DOOR_W-16}" height="{FRIDGE_H-6}" rx="4"'
        f' fill="none" stroke="currentColor" stroke-width="0.8" stroke-opacity="0.14"/>',
        # Freezer body outline
        f'<rect x="8" y="{FREEZER_TOP+4}" width="{W-16}" height="{FREEZER_H-6}" rx="4"'
        f' fill="none" stroke="currentColor" stroke-width="0.8" stroke-opacity="0.14"/>',
    ]

    # Fridge shelves
    inner_top = FRIDGE_TOP + 18
    # Leave 22 px at the bottom for crisper drawers
    inner_h = FRIDGE_H - 18 - 22
    if fridge_shelves > 1:
        gap = inner_h / fridge_shelves
        for i in range(1, fridge_shelves):
            sy = inner_top + i * gap
            parts.append(
                f'<line x1="8" y1="{sy:.1f}" x2="{W-DOOR_W-8}" y2="{sy:.1f}"'
                f' stroke="currentColor" stroke-width="1" stroke-opacity="0.18"/>'
            )

    # Crisper drawers
    crisp_y = FRIDGE_TOP + FRIDGE_H - 20
    crisp_w = (W - DOOR_W - 20) / 2 - 2
    for ci in range(2):
        cx = 10 + ci * (crisp_w + 4)
        parts += [
            f'<rect x="{cx:.1f}" y="{crisp_y}" width="{crisp_w:.1f}" height="16" rx="3"'
            f' fill="currentColor" fill-opacity="0.07"'
            f' stroke="currentColor" stroke-width="1" stroke-opacity="0.18"/>',
            f'<text x="{cx+crisp_w/2:.1f}" y="{crisp_y+10}" text-anchor="middle"'
            f' font-size="6" font-family="sans-serif"'
            f' fill="currentColor" fill-opacity="0.28">{"Veg" if ci==0 else "Fruit"}</text>',
        ]

    # Door pocket strip
    if door_pockets > 0:
        door_x = W - DOOR_W - 8
        parts.append(
            f'<line x1="{door_x}" y1="{FRIDGE_TOP+8}" x2="{door_x}" y2="{FRIDGE_TOP+FRIDGE_H-6}"'
            f' stroke="currentColor" stroke-width="1" stroke-opacity="0.16" stroke-dasharray="2,2"/>'
        )
        pocket_gap = (FRIDGE_H - 14) / (door_pockets + 1)
        for i in range(1, door_pockets + 1):
            py = FRIDGE_TOP + 7 + i * pocket_gap
            parts.append(
                f'<line x1="{door_x+2}" y1="{py:.1f}" x2="{W-10}" y2="{py:.1f}"'
                f' stroke="currentColor" stroke-width="1" stroke-opacity="0.16"/>'
            )
        # "Door" rotated label
        mid_x = door_x + (W - door_x) / 2
        mid_y = FRIDGE_TOP + FRIDGE_H / 2
        parts.append(
            f'<text x="{mid_x:.1f}" y="{mid_y:.1f}" text-anchor="middle"'
            f' font-size="6" font-family="sans-serif" fill="currentColor" fill-opacity="0.25"'
            f' transform="rotate(-90 {mid_x:.1f} {mid_y:.1f})">Door</text>'
        )

    # Door handles
    handle_x = W // 2 - 12
    parts += [
        f'<rect x="{handle_x}" y="{FRIDGE_TOP+FRIDGE_H//2-18}" width="4" height="36" rx="2"'
        f' fill="currentColor" fill-opacity="0.14"/>',
        f'<rect x="{handle_x}" y="{FREEZER_TOP+FREEZER_H//2-12}" width="4" height="24" rx="2"'
        f' fill="currentColor" fill-opacity="0.14"/>',
    ]

    # Zone labels
    parts += [
        f'<text x="{(W-DOOR_W)/2:.1f}" y="{FRIDGE_TOP+FRIDGE_H//2:.1f}" text-anchor="middle"'
        f' font-size="9" font-family="sans-serif" fill="currentColor" fill-opacity="0.2">Fridge</text>',
        f'<text x="{W//2}" y="{FREEZER_TOP+FREEZER_H//2+4}" text-anchor="middle"'
        f' font-size="9" font-family="sans-serif" fill="currentColor" fill-opacity="0.2">Freezer</text>',
    ]

    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}">{"".join(parts)}</svg>'


# ---------------------------------------------------------------------------
# Template registry
# ---------------------------------------------------------------------------

TEMPLATES: dict[str, dict[str, Any]] = {
    "chest_freezer": {
        "id": "chest_freezer",
        "label": "Chest / Deep Freezer",
        "viewBox": "0 0 200 130",
        "projection": "top_down",
        "default_config": {"baskets": 2, "dividers": 1},
        "config_schema": [
            {"key": "baskets",  "label": "Basket sections", "type": "int",  "min": 0, "max": 4, "default": 2},
            {"key": "dividers", "label": "Main dividers",   "type": "int",  "min": 0, "max": 3, "default": 1},
        ],
        "_render": _chest_freezer,
    },
    "wire_rack": {
        "id": "wire_rack",
        "label": "Wire Shelving Rack",
        "viewBox": "0 0 160 220",
        "projection": "front_elevation",
        "default_config": {"shelves": 4, "wheels": True},
        "config_schema": [
            {"key": "shelves", "label": "Shelves",     "type": "int",  "min": 2, "max": 8, "default": 4},
            {"key": "wheels",  "label": "Has wheels",  "type": "bool",                      "default": True},
        ],
        "_render": _wire_rack,
    },
    "refrigerator": {
        "id": "refrigerator",
        "label": "Refrigerator",
        "viewBox": "0 0 140 280",
        "projection": "front_elevation",
        "default_config": {"fridge_shelves": 3, "door_pockets": 3},
        "config_schema": [
            {"key": "fridge_shelves", "label": "Fridge shelves",      "type": "int", "min": 2, "max": 5, "default": 3},
            {"key": "door_pockets",   "label": "Door pocket shelves", "type": "int", "min": 0, "max": 5, "default": 3},
        ],
        "_render": _refrigerator,
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_template_list() -> list[dict[str, Any]]:
    """Return all templates with metadata and default-config SVG.

    The ``_render`` callable is excluded from the returned dicts.
    """
    result = []
    for tmpl in TEMPLATES.values():
        entry = {k: v for k, v in tmpl.items() if not k.startswith("_")}
        entry["default_svg"] = tmpl["_render"](tmpl["default_config"])
        result.append(entry)
    return result


def render_for_location(template_id: str, template_config_json: str | None) -> str:
    """Render the SVG for a specific location's template + stored config.

    Raises ``ValueError`` if *template_id* is unknown.
    """
    tmpl = TEMPLATES.get(template_id)
    if tmpl is None:
        raise ValueError(f"Unknown template: {template_id!r}")
    cfg: dict[str, Any] = {**tmpl["default_config"]}
    if template_config_json:
        try:
            cfg.update(json.loads(template_config_json))
        except (ValueError, TypeError):
            pass
    return tmpl["_render"](cfg)
