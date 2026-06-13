# Stockpile

A Home Assistant native inventory system for chest freezers, garage pantries,
household supplies, and any other storage location — built as a proper custom
integration with a SQLite backend, not one HA entity per item.

Stockpile tracks **individual packages** (partial consumption, brand, image,
location, dates) while presenting **aggregated views** ("Ground Beef — 3
packages, 2.25 lb remaining"). Two bundled Lovelace cards — a summary and a
detailed grid — share a location picker, quick consume actions, an in-card
add flow, and a visual configuration editor.

> Status: usable. Integration and both cards work end-to-end. See
> [ROADMAP.md](ROADMAP.md) for what is shipping next.

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
- HACS (recommended) for installation and updates

## Install (HACS)

Stockpile ships the cards **inside** the integration, so HACS installs and
updates everything together — no copying JavaScript or registering resources.

1. HACS → menu → **Custom repositories**. Add the repository URL with the
   category **Integration**.
2. Install **Stockpile**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Stockpile**.

The database is created at `config/stockpile.db`, `aiosqlite` is installed
automatically, and the cards register themselves with the frontend. If a card
does not appear, hard-refresh the browser to clear the cached module.

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

- **Location picker.** Chips switch the view between Freezer, Pantry, and so
  on. Set `location_id` to lock the card to a single location.
- **Summary / Items / History.** Three tabs share the same data. Summary
  aggregates per product; Items shows the package grid; History shows the
  most recent consumption events across all products.
- **Drill-down.** Tap a product in the summary to filter the grid to that
  product's packages.
- **Arrange.** In the grid, tap *Arrange*, drag tiles into the order that
  matches your physical layout, then tap *Done*. Order is saved per package
  and survives items coming and going. Use `columns` to control the row
  width. Arrange uses Pointer Events, so dragging works on phones and
  tablets.
- **Add.** The grid bar includes an **+ Add** button that opens an in-card
  sheet to add a new package. The name field autocompletes from the existing
  catalog, and the brand / unit auto-fill when an existing product is
  selected.
- **Expiring.** Packages with an expiration date inside the warning window
  display a corner badge; expired packages get a red outline. The Summary
  shows a count, and an **Expiring** filter chip narrows either view.
- **Attribution.** Consume / set-remaining calls record the calling Home
  Assistant user's name automatically, unless `who` is supplied explicitly.
- **Velocity.** The detail sheet shows a 30-day usage rate and a "runs out in
  N days" estimate for products with recent activity.
- **Snooze / Acknowledge.** Per-product buttons in the detail sheet silence
  alerts for 1 or 7 days, or mark a product as reviewed. Notifications,
  sensors, and the **Expiring** filter all respect snooze.

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
| `stockpile.seed_demo` | Load demo data (idempotent). |
| `stockpile.get_summary` / `list_packages` | Read data (returns a response). |
| `stockpile.export` | Dump the full dataset as a JSON-safe object (returns a response). |
| `stockpile.import_data` | Restore from an `export` payload. Set `replace: true` to wipe first. |
| `stockpile.compose_notification` | Build a short title / message summary of low-stock and expiring items. |
| `stockpile.snooze` | Suppress alerts for one product (`hours`, `days`, or `until`). |
| `stockpile.acknowledge` | Mark a product as reviewed; clears any active snooze. |
| `stockpile.push_to_todo` | Add low-stock / expiring items to a Home Assistant `todo` entity. |

## WebSocket API

Used by the cards; also available to other front-end code.

- `stockpile/packages` `{location_id?}`
- `stockpile/products`
- `stockpile/summary`
- `stockpile/locations`
- `stockpile/history` `{product_id?, limit?}`
- `stockpile/velocity` `{product_id, days?}`
- `stockpile/subscribe` (pushes on every change — drives live refresh)

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

Two ready-made automation blueprints live in
[`blueprints/automation/stockpile/`](blueprints/automation/stockpile):

- **`door_review_prompt.yaml`** — when a freezer or cabinet door stays open
  for a few seconds, send a Stockpile review prompt (low stock and expiring
  soon) to a chosen notification target. Respects quiet hours.
- **`low_stock_daily.yaml`** — once a day at a chosen time, send a summary of
  low-stock and expiring items. Sends nothing when inventory is healthy.

Both rely on `stockpile.compose_notification`, so they work with any
`notify.*` service.

## Backup and restore

`stockpile.export` returns the whole dataset (locations, products, packages,
consumption log) as JSON. Capture it with `response_variable` and store it
wherever you keep HA backups. `stockpile.import_data` round-trips the result
back into the database; pass `replace: true` to start from a clean slate.

## Development

Stockpile ships a small pytest suite covering the data layer (alias matching,
expiration enrichment, snooze filtering, velocity windows, export / import
round-trips, and ordering preservation).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements_test.txt
pytest
```

## Data model

- **products** — catalog: name, brand, unit, category, image, aliases, threshold.
- **packages** — instances: product, `remaining` percentage, quantity,
  location, `position`, added / frozen / expires dates.
- **locations** — freezer baskets, shelves, racks.
- **consumption_log** — who / when / how much.
- **product_state** — per-product snooze and acknowledgement.

## License

MIT — see [LICENSE](LICENSE).
