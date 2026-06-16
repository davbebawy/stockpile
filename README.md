# Stockpile

A Home Assistant native inventory system for chest freezers, garage pantries,
household supplies, and any other storage location — built as a proper custom
integration with a SQLite backend, not one HA entity per item.

Stockpile tracks **individual packages** (partial consumption, brand, image,
location, dates) while presenting **aggregated views** ("Ground Beef — 3
packages, 2.25 lb remaining"). Two bundled Lovelace cards — a summary and a
detailed grid — share a location picker, quick consume actions, an in-card
add flow, a floor-plan map mode, and a visual configuration editor.

> Status: feature-complete. Integration and both cards work end-to-end.
> See [ROADMAP.md](ROADMAP.md) for what shipped.

---

## Why a database, not entities or JSON

- **Not entities.** Home Assistant's state machine is not built for hundreds of
  mutable, attribute-rich records; it would bloat the entity registry and the
  recorder.
- **Not flat JSON.** A card cannot read `/config/*.json` directly, and
  concurrent writes corrupt the file. SQLite gives atomic writes (WAL), real
  queries for aggregation and history, and a clean WebSocket channel to the
  cards.

The only entities the integration creates are three derived summary sensors
intended for automations.

---

## Requirements

- Home Assistant **2025.6.0** or newer
- `aiosqlite>=0.20.0` and `qrcode>=7.4.2` (installed automatically)

## Install (HACS)

Stockpile ships the cards **inside** the integration, so HACS installs and
updates everything together — no copying JavaScript or registering resources.

1. HACS → menu → **Custom repositories**. Add the repository URL with the
   category **Integration**.
2. Install **Stockpile**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Stockpile**.

The database is created at `config/stockpile.db` and the cards register
themselves with the frontend. If a card does not appear, hard-refresh the
browser to clear the cached module.

### Manual install

Copy `custom_components/stockpile/` into `config/custom_components/`, restart
Home Assistant, then add the integration. The cards are auto-registered the
same way.

### Load demo data

Developer Tools → Actions → `stockpile.seed_demo` → Run. This adds a realistic
set of items across Freezer, Pantry, and Garage locations, including some that
are below their low-stock threshold. The action is idempotent — running it
again does nothing.

---

## The cards

Add either card from the dashboard card picker, or define it in YAML. Both
cards expose a visual configuration editor, so YAML is optional.

```yaml
# Aggregated overview, with a Summary / Items toggle and location chips
type: custom:stockpile-summary-card
title: Kitchen

# Detailed package grid
type: custom:stockpile-card
title: Freezer
location_id: loc_xxxxxxxx   # optional: pin to one spot, hides the picker
columns: 10                 # optional: fixed column count (e.g. 2 rows of 10)
min_tile: 150               # optional: minimum tile width when columns is unset
show_expiring: true         # optional: surface expiring/expired items
```

---

## Feature guide

### Location picker

Chips at the top of the card filter the current view to a single storage
location. Tap a chip to switch; the active chip is highlighted.

Each named location chip has a **⋯** button on its right edge. Tapping ⋯
opens the **Location Detail** sheet for that location — see below.

### Views (Summary / Items / History / Trends)

The segment control in the top-right corner switches between four views.

| View | What it shows |
|---|---|
| **Summary** | One row per product with an aggregated progress bar, package count, and remaining quantity. Tap a row to drill into that product's packages in the grid. |
| **Items** | The package grid. Tap any tile to open its detail sheet. |
| **History** | Up to 200 most recent consumption events across all visible packages, with timestamps and user attribution. |
| **Trends** | 14-day sparkline bar charts per product — shows which items are being consumed most actively. |

### Detail sheet

Tap any tile in the Items grid to open the package detail sheet. It shows:

- Remaining percentage (color-coded), quantity, location, and dates
- **Usage velocity** — 30-day consumption rate and a "runs out in N days"
  estimate when there is enough data
- **Recent activity** — last 8 consumption events for this product
- **Use 10 / 25 / 50 / all** quick-consume buttons
- **Custom slider** — precise consume amount
- **Snooze 1d / 7d** — suppresses low-stock alerts for this product
- **Acknowledge** — marks the product as reviewed
- **QR code** — see below
- **Remove** — deletes the package

### Adding a package

In the Items view, tap **+ Add** in the toolbar. The form opens inline:

- The **Name** field autocompletes from your existing product catalog; selecting
  a known product auto-fills Brand and Unit.
- Choose a location from the dropdown.
- Set remaining %, quantity, and optional expiration date.
- Tap **Add**.

### Arrange mode

In the Items view, tap **Arrange** in the toolbar to drag tiles into the
physical order that matches your shelf layout. Use **+ cols / − cols** to
control the grid width. Tap **Done** when finished. Order is saved per package
and survives items coming and going.

Arrange uses Pointer Events and works on phones and tablets.

### Expiring items

Packages with an expiration date display a corner badge when approaching
expiry. Expired packages get a red outline. Enable with `show_expiring: true`
(the default). An **Expiring** filter chip narrows either view to only
expiring/expired items.

### Velocity

The detail sheet shows a 30-day usage rate (per week) and a "runs out in N
days" estimate for products with recent consumption activity.

### Snooze / Acknowledge

Per-product buttons in the detail sheet silence alerts for 1 or 7 days, or
mark a product as reviewed. All notifications, sensors, and the Expiring filter
chip respect snooze.

---

## Floor plan (Map mode)

Map mode lets you place packages at their actual physical position on a
schematic of the storage location.

### Quick start

1. **Select a location** using the chip at the top of the card (e.g. "Freezer").
2. In the Items view, tap **Map** in the toolbar.
3. If the location has no floor plan yet, you will be prompted to choose a
   template (see below). Otherwise the floor plan opens immediately.
4. **Drag** any tile from the staging tray at the bottom onto the canvas to
   place it. Drag a placed tile to reposition it. Drag a placed tile off the
   canvas to return it to the staging tray.
5. Tap any placed tile to open its normal detail sheet.

### Location Detail sheet (⋯ button)

Every location chip has a **⋯** button. Tapping it opens the Location Detail
sheet, which is the primary way to manage a location's floor plan:

- If no template is assigned, the template picker is shown directly.
- If a template is already assigned, a preview is shown with two buttons:
  - **Reconfigure** — change the template or its parameters.
  - **View Floor Plan →** — jump directly to the floor plan for this location
    (selects the location and switches to Map mode automatically).

### Templates

Three schematic templates are included. Each uses context-appropriate
projection so the layout makes visual sense:

| Template | Projection | Best for |
|---|---|---|
| **Chest Freezer** | Top-down | Chest freezers, under-bed storage, flat bins |
| **Wire Rack** | Front elevation | Garage shelving, wire metro racks |
| **Refrigerator** | Front elevation | Upright fridges, pantry cabinets |

Each template has configurable parameters (shelf count, basket sections, etc.)
that appear after you select a template thumbnail. Adjust the parameters and
tap **Apply template** to save.

Templates use `currentColor` SVG so they automatically adapt to HA's dark and
light themes.

---

## QR codes

Every package can generate a QR code that deep-links directly to its consume
sheet.

1. Tap a package tile to open its detail sheet.
2. Tap **QR code** in the action row.
3. Print or display the QR code on the physical package.
4. Scanning the QR with any phone camera opens HA and jumps straight to the
   consume sheet for that package.

The QR endpoint (`/api/stockpile/qr`) is same-origin only — it refuses to
generate codes for external URLs.

---

## Receipt parsing

Paste text from a shopping receipt to bulk-add items to your inventory.

1. In the Items view, tap **Receipt** in the toolbar.
2. Paste the receipt text (copy from email, PDF, or camera OCR).
3. Tap **Parse** — the server extracts product names, quantities, and units,
   and matches them against your existing catalog.
4. A review screen shows each detected item with a checkbox, editable name,
   quantity, and unit. Items matched to existing catalog products are labelled
   **Catalog**; new products are labelled **New**.
5. Optionally select a location from the dropdown.
6. Tap **Add checked** to bulk-add all ticked items.

The parser strips prices, barcodes, and store codes; it handles common
multi-quantity patterns ("PASTA 12OZ x4", "3LB GROUND BEEF").

---

## Actions

| Action | Purpose |
|---|---|
| `stockpile.add_product` | Create a catalog product. |
| `stockpile.add_package` | Add a package. Pass `product_id`, or `name` / `brand` to find-or-create. |
| `stockpile.consume` | Reduce a package's remaining percentage by `amount`; logs the event. |
| `stockpile.set_remaining` | Set a package's remaining percentage to an absolute value. |
| `stockpile.remove_package` / `remove_product` | Delete records. |
| `stockpile.add_location` | Create a storage location. |
| `stockpile.reorder` | Set manual display order (`package_ids` in the desired order). |
| `stockpile.set_package_position` | Set or clear a package's floor-plan coordinates (`loc_x`, `loc_y` as 0–100 percentages, or omit both to unstage). |
| `stockpile.set_location_template` | Assign a schematic template to a location. Pass `template_id` and optional `template_config` JSON. |
| `stockpile.parse_receipt` | Extract product lines from receipt text. Returns `suggestions` list with name, qty, unit, and matched product. |
| `stockpile.seed_demo` | Load demo data (idempotent). |
| `stockpile.get_summary` / `list_packages` | Read data (returns a response). |
| `stockpile.export` | Dump the full dataset as a JSON-safe object (returns a response). |
| `stockpile.import_data` | Restore from an `export` payload. Set `replace: true` to wipe first. |
| `stockpile.compose_notification` | Build a short title / message summary of low-stock and expiring items. |
| `stockpile.snooze` | Suppress alerts for one product (`hours`, `days`, or `until`). |
| `stockpile.acknowledge` | Mark a product as reviewed; clears any active snooze. |
| `stockpile.push_to_todo` | Add low-stock / expiring items to a Home Assistant `todo` entity. |
| `stockpile.suggest_restock` | Velocity-based reorder suggestions with quantities. |

## WebSocket API

Used by the cards; also available to other front-end code.

| Message | Parameters | Returns |
|---|---|---|
| `stockpile/packages` | `location_id?` | `packages` list |
| `stockpile/products` | — | `products` list |
| `stockpile/summary` | — | aggregated product rows |
| `stockpile/locations` | — | `locations` list (includes `template_id`) |
| `stockpile/history` | `product_id?`, `limit?` | `history` list |
| `stockpile/velocity` | `product_id`, `days?` | velocity object |
| `stockpile/trends` | `days?` | per-product daily consumption map |
| `stockpile/templates` | `location_id?` | template list; when `location_id` is given, also returns `location_svg` (rendered SVG for that location) |
| `stockpile/subscribe` | — | pushes on every data change (drives live refresh) |

## Sensors

- `sensor.stockpile_total_packages` — count of packages currently tracked.
- `sensor.stockpile_low_stock` — number of products below their threshold;
  items are listed in the `items` attribute (useful for shopping reminders).
- `sensor.stockpile_expiring_soon` — number of packages within the
  expiration warning window or already past expiry; items are listed in the
  `items` attribute.

## Status colors

| Range | Token |
|---|---|
| 75 – 100 % | `--success-color` |
| 30 – 74 % | `--warning-color` |
| 1 – 29 % | `--error-color` |
| 0 % | `--disabled-text-color` |

Status color is the one bold visual; every other surface uses Home Assistant
theme tokens so the card adapts to your chosen palette.

---

## Blueprints

Three ready-made automation blueprints live in
[`blueprints/automation/stockpile/`](blueprints/automation/stockpile):

- **`door_review_prompt.yaml`** — when a freezer or cabinet door stays open
  for a few seconds, send a Stockpile review prompt (low stock and expiring
  soon) to a chosen notification target. Respects quiet hours.
- **`low_stock_daily.yaml`** — once a day at a chosen time, send a summary of
  low-stock and expiring items. Sends nothing when inventory is healthy.
- **`targeted_popup.yaml`** — send a popup to a specific device; accepts an
  optional dashboard URL that opens Stockpile directly in the companion app.

All three rely on `stockpile.compose_notification` and work with any
`notify.*` service.

## Backup and restore

`stockpile.export` returns the whole dataset (locations, products, packages,
consumption log) as JSON. Capture it with `response_variable` and store it
wherever you keep HA backups. `stockpile.import_data` round-trips the result
back into the database; pass `replace: true` to start from a clean slate.

## Development

Stockpile ships a pytest suite covering the data layer: alias matching,
expiration enrichment, snooze filtering, velocity windows, suggest_restock
logic, export / import round-trips, and ordering preservation.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
pytest
```

To release a new version:

```bash
python3 scripts/release.py 0.10.0 "Short release description"
```

This bumps the version in `manifest.json`, `const.py`, and the card JS,
commits, creates an annotated tag, pushes, and opens a GitHub release.

## Data model

- **products** — catalog: name, brand, unit, category, image, aliases, threshold.
- **packages** — instances: product, `remaining` percentage, quantity,
  location, `position`, `loc_x`/`loc_y` (floor-plan coordinates, 0–100%),
  added / frozen / expires dates.
- **locations** — freezer baskets, shelves, racks; stores `template_id` and
  `template_config` for floor-plan rendering.
- **consumption_log** — who / when / how much.
- **product_state** — per-product snooze and acknowledgement.

## License

MIT — see [LICENSE](LICENSE).
