# Stockpile Roadmap

Status legend: **Done** · **In progress** · **Planned**

---

## Phase 0 — Foundation

- **Done** — Custom integration (config flow, single instance, `runtime_data`)
- **Done** — SQLite data layer (WAL, write lock, async via `aiosqlite`, migrations)
- **Done** — Two-level model: products + packages + locations + consumption_log
- **Done** — Services: add / consume / set_remaining / remove / reorder / seed_demo / get_summary / list_packages
- **Done** — WebSocket API and `stockpile_updated` event
- **Done** — Summary sensors: total_packages, low_stock
- **Done** — Demo data seeding (`stockpile.seed_demo`)

## Phase 1 — Interface

- **Done** — Grid card: responsive tiles, status level bars, status colors
- **Done** — Summary card: aggregated overview with stats strip
- **Done** — Summary / Items toggle; drill from a product into its packages
- **Done** — Location picker (chips) shared by both cards
- **Done** — Detail sheet (built-in) with Use 10 / 25 / 50 / all plus a custom slider
- **Done** — Drag-to-arrange order and configurable columns (persisted per package)
- **Done** — Live refresh via `stockpile/subscribe`
- **Done** — Cards bundled with the integration (auto-registered, HACS-updatable)
- **Done** — Touch-friendly arrange mode (Pointer Events)
- **Done** — Visual card configuration editor (per card)
- **Done** — Add-package flow directly from the card

## Phase 2 — Access events and prompts

- **Done** — Door / cabinet sensor triggers a "review" prompt (blueprint: `door_review_prompt.yaml`)
- **Done** — Daily low-stock + expiring summary (blueprint: `low_stock_daily.yaml`)
- **Done** — `stockpile.compose_notification` action for any notify service
- **Done** — Per-product snooze (`stockpile.snooze`) and acknowledge (`stockpile.acknowledge`); notifications respect them
- **Done** — Targeted popup on a specific device (blueprint: `targeted_popup.yaml`; optional dashboard URL deeplink)

## Phase 3 — Expiration and history

- **Done** — Expiration surfaced in the UI; "expiring soon" filter chip on both cards
- **Done** — `sensor.stockpile_expiring_soon` for automations
- **Done** — Recent consumption activity shown inside the detail sheet
- **Done** — Dedicated History tab on the cards (last 200 events)
- **Done** — Per-user attribution: services infer `who` from the calling HA user
- **Done** — Per-product velocity (30-day window) surfaced in the detail sheet with a "runs out in N days" estimate
- **Done** — Cross-product trend dashboard (14-day sparklines per product in the Trends tab)

## Phase 4 — Catalog and input acceleration

- **Done** — Product catalog autocomplete in the in-card Add form
- **Done** — Alias-aware product matching in `find_product` and the Add form
- **Done** — QR codes per package — "QR code" button in the detail sheet; scan to open the consume sheet via deep link
- **Planned** — Barcode (UPC) lookup (spotty coverage for warehouse-brand items)

## Phase 5 — Intelligence and integrations

- **Done** — Shopping-list integration: `stockpile.push_to_todo` adds low / expiring items to any HA todo entity (dedupes by default)
- **Done** — `stockpile.suggest_restock` returns velocity-based reorder suggestions with quantities
- **Planned** — Receipt parsing to suggested additions, confirmed by the user

## Phase 6 — Distribution

- **Done** — HACS-installable integration with bundled card
- **Done** — Export / import via `stockpile.export` and `stockpile.import_data`
- **Done** — Pytest suite (17 tests, runs with `pytest`)
- **Done** — `scripts/release.py` release helper: bumps version files, commits, tags, and creates a GitHub release in one step
- **Planned** — Submit to the HACS default store
- **Planned** — Free placement (true x/y grid) as an alternative to ordered layout

---

## Design decisions

- **Layout is an ordered list plus column count, not x/y coordinates.** Absolute
  positions go stale as packages are added and consumed; an order that the grid
  flows into N columns gives the same look and survives churn.
- **Status color is the one bold visual** — every other surface uses HA theme
  tokens so the card adapts to the user's chosen palette.
- **Aggregation is a computed view**, so individual package records stay
  authoritative.
- **The cards live inside the integration** so a single HACS repository updates
  both at once.
