# Stockpile

A Home Assistant–native inventory system for chest freezers, garage pantries,
household supplies, and any other storage location — built as a proper custom
integration with a SQLite backend, not one HA entity per item.

Stockpile tracks **individual packages** (partial consumption, brand, image,
location, dates) while presenting **aggregated views** ("Ground Beef — 3
packages, 2.25 lb remaining"). Two bundled Lovelace cards — a modern summary and
a detailed grid — share a location picker and quick consume actions.

> Status: early but usable. Integration + both cards work. See [ROADMAP.md](ROADMAP.md).

---

## Why a database, not entities or JSON

- **Not entities** — HA's state machine isn't built for hundreds of mutable,
  attribute-rich records; it would bloat the registry and recorder.
- **Not flat JSON** — a card can't read `/config/*.json` directly and concurrent
  writes corrupt files. SQLite gives atomic writes (WAL), real queries for
  aggregation/history, and a clean WebSocket channel to the cards.

The only entities created are two derived summary sensors for automations.

---

## Install (HACS — recommended)

Stockpile ships the cards **inside** the integration, so HACS installs and
updates everything together — no copying JS or registering resources.

1. HACS → ⋮ → **Custom repositories**. Add your repo URL, category **Integration**.
2. Install **Stockpile**, then restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Stockpile**.

The DB is created at `config/stockpile.db`, `aiosqlite` is installed
automatically, and the cards register themselves with the frontend. (If a card
doesn't appear, hard-refresh the browser to clear the cached module.)

### Manual install

Copy `custom_components/stockpile/` into `config/custom_components/`, restart,
then add the integration. The card is auto-registered the same way.

### Load test data

Developer Tools → Actions → `stockpile.seed_demo` → Run. This adds a realistic
set across Freezer / Pantry / Garage (including some low-stock items). It's
idempotent — running it again does nothing.

---

## The cards

Add either from the dashboard card picker, or in YAML:

```yaml
# Modern overview, with a Summary <-> Items toggle and location chips
type: custom:stockpile-summary-card
title: Kitchen

# Detailed package grid
type: custom:stockpile-card
title: Freezer
location_id: loc_xxxxxxxx   # optional: pin to one spot, hides the picker
columns: 10                 # optional: fixed column count (e.g. 2 rows of 10)
min_tile: 150               # optional: min tile width when columns is unset
```

- **Location picker** — chips switch the view between Freezer, Pantry, etc.
  Set `location_id` to lock a card to one spot.
- **Summary → Items** — tap a product in the summary to drill into its packages.
- **Arrange** — in the grid, tap *Arrange*, drag tiles into the order that
  matches your physical layout, then *Done*. Order is saved per package and
  survives items coming and going. Use `columns` to control the row width.
  (Drag currently uses HTML5 drag-and-drop; touch dragging is limited — a
  touch-friendly mode is on the roadmap.)

---

## Services

| Service | Purpose |
|---|---|
| `stockpile.add_product` | Create a catalog product. |
| `stockpile.add_package` | Add a package. Pass `product_id`, or `name`/`brand` to find-or-create. |
| `stockpile.consume` | Reduce a package's remaining % by `amount`; logs it. |
| `stockpile.set_remaining` | Set a package's remaining % to an absolute value. |
| `stockpile.remove_package` / `remove_product` | Delete. |
| `stockpile.add_location` | Create a storage location. |
| `stockpile.reorder` | Set manual display order (`package_ids` in order). |
| `stockpile.seed_demo` | Load demo data (idempotent). |
| `stockpile.get_summary` / `list_packages` | Read data (returns response). |

## WebSocket API (used by the cards)

`stockpile/packages` `{location_id?}` · `stockpile/products` · `stockpile/summary`
· `stockpile/locations` · `stockpile/history` `{product_id?, limit?}`
· `stockpile/subscribe` (pushes on every change → live refresh)

## Sensors

- `sensor.stockpile_total_packages`
- `sensor.stockpile_low_stock` — count below threshold; items in the `items`
  attribute (use for shopping reminders).

## Status colors

75–100% full (`--success-color`) · 30–74% medium (`--warning-color`) ·
1–29% low (`--error-color`) · 0% empty (`--disabled-text-color`).

---

## Data model

- **products** — catalog: name, brand, unit, category, image, aliases, threshold.
- **packages** — instances: product, `remaining` %, quantity, location,
  `position`, added/frozen/expires.
- **locations** — freezer baskets, shelves, racks.
- **consumption_log** — who/when/how much.

## License

MIT — see [LICENSE](LICENSE).
