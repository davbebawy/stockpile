# Stockpile

Home Assistant inventory management with a SQLite backend. Tracks individual packages (partial consumption, brand, image, location, expiry) and presents aggregated views. Two Lovelace cards share a location picker, floor-plan map, receipt parsing, QR codes, and Open Food Facts lookup — no external dependencies.

> Current version: **1.1.0** — see [ROADMAP.md](ROADMAP.md)

---

## Install

**HACS (recommended):** HACS → Custom repositories → add this repo as Integration → Install → restart HA → Settings → Devices & Services → Add Integration → Stockpile.

**Manual:** copy `custom_components/stockpile/` to `config/custom_components/`, restart, add integration.

The cards register themselves — no resource configuration needed. If a card doesn't appear, hard-refresh the browser.

**Requirements:** Home Assistant 2025.6.0+, Python packages `aiosqlite` and `qrcode` (auto-installed).

**Demo data:** Developer Tools → Actions → `stockpile.seed_demo` → Run. Adds realistic items across Freezer, Pantry, and Garage locations.

---

## Cards

```yaml
type: custom:stockpile-card          # package grid (default view: Items)
title: Freezer
location_id: loc_xxxxxxxx            # optional: pin to one location
columns: 10                          # optional: fixed column count
min_tile: 150                        # optional: min tile width px
show_expiring: true                  # optional: expiry badges and filter

type: custom:stockpile-summary-card  # aggregated overview (default view: Summary)
title: Kitchen
```

Both cards have a visual config editor — YAML is optional.

---

## Views

The **Summary / Items / History / Trends** tabs are in the top-right of every card.

| Tab | What it shows |
|---|---|
| Summary | One row per product — aggregated stock bar, package count, qty remaining. Tap a row to drill into that product in Items. |
| Items | Package grid. Tap a tile to open its detail sheet. |
| History | Last 200 consumption events with local timestamps and user attribution. |
| Trends | 14-day sparkline bar charts per product. |

---

## Location chips

Chips at the top filter the view to one location. The **⋯** button beside each named chip opens a **Location Detail** sheet where you can assign or reconfigure a floor-plan template and jump directly to the floor plan.

---

## Floor plan (Map mode)

Visualise a storage location as a 2D schematic with packages placed at their physical positions.

**Getting there:**
1. Tap a location chip to select it (e.g. "Freezer Top").
2. In the **Items** tab, tap **Map** in the toolbar.
3. If no template is assigned yet, the template picker opens — choose one, adjust parameters, tap **Apply template**. The floor plan popup opens automatically.
4. Next time: Map opens the popup directly.

**Alternative:** tap **⋯** on a location chip → **View Floor Plan →**.

**Using the popup:**
- Tiles without a position appear in the staging tray at the bottom — **drag them onto the canvas** to place.
- Drag a placed tile to reposition it; drag it off the canvas to return it to the tray.
- Tap any tile to open its normal detail sheet.

**Templates** (three included, all use `currentColor` SVG for dark/light themes):

| Template | Projection | Use for |
|---|---|---|
| Chest Freezer | Top-down | Chest freezers, flat bins |
| Wire Rack | Front elevation | Garage shelving, metro racks |
| Refrigerator | Front elevation | Upright fridges, pantry cabinets |

Each template has configurable parameters (shelf count, basket divisions, etc.).

---

## Adding a package

Items tab → **+ Add**. The add form:

- **Name** — autocompletes from your catalog. Selecting a known product fills Brand and Unit.
- **🔍 button** — searches Open Food Facts (see below). Fills image, brand, unit, category automatically.
- **Product cache ›** — manage previously cached OFF lookups.
- Brand, Unit, Location, Remaining %, Quantity, Expires.

---

## Open Food Facts lookup

In the Add form, type a product name and tap **🔍**. Stockpile searches Open Food Facts and returns up to 6 matches showing image, brand, unit, Nutri-Score, and category. Tap a result to auto-fill the form.

Results are **cached in SQLite** — subsequent searches for the same term return instantly without a network call.

**Cache management:** Add form → **Product cache ›** lists every cached search with result count and date. Delete individual entries or **Clear all**. Force a fresh fetch from within automations with `force_refresh: true` on the `stockpile/search_product` WebSocket command.

```yaml
# Remove one cached entry so the next lookup re-fetches from OFF
service: stockpile.clear_product_cache
data:
  key: "pasta"        # omit key to clear everything
```

---

## Receipt parsing

Items tab → **Receipt**. Paste receipt text, tap **Parse**. The server strips prices, extracts product names and quantities, and matches against your catalog. Review the suggestions (edit name, qty, unit; uncheck items to skip), pick a location, and tap **Add checked**.

---

## QR codes

Detail sheet → **QR code** — generates a QR that deep-links to the consume sheet for that package. Print it and stick it on the physical package. Scanning opens HA directly to the consume controls.

---

## Tiles without images

Products with no image get a **deterministic gradient background** (consistent color per product) and a **category emoji** (🥩 meat, 🐟 seafood, 🍝 pasta, 🧻 paper goods, etc.) derived from the product name. Use the Open Food Facts lookup to pull in a real product photo.

---

## Automation actions

| Action | Purpose |
|---|---|
| `stockpile.add_product` | Create a catalog entry. |
| `stockpile.add_package` | Add a package. Pass `product_id` or `name`/`brand` to find-or-create. Accepts `image` and `category` to enrich the product. |
| `stockpile.consume` | Reduce remaining % by `amount`. |
| `stockpile.set_remaining` | Set remaining % to an absolute value. |
| `stockpile.remove_package` / `remove_product` | Delete records. |
| `stockpile.add_location` | Create a storage location. |
| `stockpile.reorder` | Set display order (`package_ids` list). |
| `stockpile.set_package_position` | Set or clear floor-plan `loc_x`/`loc_y` (0–100%). |
| `stockpile.set_location_template` | Assign a schematic template to a location. |
| `stockpile.parse_receipt` | Extract product lines from receipt text. Returns `suggestions`. |
| `stockpile.suggest_restock` | Velocity-based reorder suggestions. |
| `stockpile.push_to_todo` | Add low-stock / expiring items to a `todo` entity. |
| `stockpile.compose_notification` | Build a title + message for low-stock / expiring items. |
| `stockpile.snooze` | Silence alerts for one product (`hours`, `days`, or `until`). |
| `stockpile.acknowledge` | Mark a product as reviewed; clears snooze. |
| `stockpile.clear_product_cache` | Delete one (`key:`) or all OFF cache entries. |
| `stockpile.export` / `import_data` | Full backup and restore (includes snooze state). |
| `stockpile.seed_demo` | Load demo data (idempotent). |

---

## Sensors

| Entity | Value |
|---|---|
| `sensor.stockpile_total_packages` | Total package count |
| `sensor.stockpile_low_stock` | Products below threshold (`items` attribute lists them) |
| `sensor.stockpile_expiring_soon` | Packages expiring or expired (`items` attribute) |

---

## Blueprints

[`blueprints/automation/stockpile/`](blueprints/automation/stockpile) includes:

- **`door_review_prompt.yaml`** — door sensor triggers a low-stock/expiring prompt. Respects quiet hours.
- **`low_stock_daily.yaml`** — daily summary; silent when inventory is healthy.
- **`targeted_popup.yaml`** — popup on a specific device with optional dashboard deep-link.

---

## Backup

`stockpile.export` returns the full dataset (locations, products, packages, consumption log, snooze state) as JSON. `stockpile.import_data` restores it; pass `replace: true` to wipe first.

---

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
pytest
```

```bash
python3 scripts/release.py 1.2.0 "Short description"
```

---

## Data model

| Table | Contents |
|---|---|
| `products` | Catalog: name, brand, unit, category, image, aliases, threshold |
| `packages` | Instances: remaining %, quantity, location, position, `loc_x`/`loc_y`, dates |
| `locations` | Storage locations with `template_id` / `template_config` |
| `consumption_log` | Who consumed what, when, how much |
| `product_state` | Per-product snooze and acknowledgement |
| `off_cache` | Cached Open Food Facts search results |

## License

MIT — see [LICENSE](LICENSE).
