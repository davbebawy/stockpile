# Stockpile Roadmap

Legend: ✅ done · 🚧 in progress · ⬜ planned

---

## Phase 0 — Foundation ✅

- ✅ Custom integration (config flow, single instance, `runtime_data`)
- ✅ SQLite data layer (WAL, write lock, async via `aiosqlite`, migrations)
- ✅ Two-level model: products + packages + locations + consumption_log
- ✅ Services: add/consume/set_remaining/remove/reorder/seed_demo/get_summary/list_packages
- ✅ WebSocket API + `stockpile_updated` event
- ✅ Summary sensors: total_packages, low_stock
- ✅ Demo/test data seeding (`stockpile.seed_demo`)

## Phase 1 — UI ✅ / 🚧

- ✅ Grid card: responsive tiles, status level bars, color coding
- ✅ Summary card: modern aggregated overview + stats strip
- ✅ Summary ⇄ Items toggle; drill from a product into its packages
- ✅ Location picker (chips) shared by both cards
- ✅ Detail popup (built-in) with Use 10/25/50/all + custom
- ✅ Drag-to-arrange order + configurable columns (persisted per package)
- ✅ Live refresh via `stockpile/subscribe`
- ✅ Cards bundled in the integration (auto-registered, HACS-updatable)
- 🚧 Touch-friendly arrange (HTML5 DnD is weak on touch)
- ⬜ Visual card config editor (YAML only for now)
- ⬜ Add-package flow from the card (currently via service)

## Phase 2 — Access events & prompts ⬜

- ⬜ Door/cabinet sensor → "review" prompt (debounced; only after open > N s)
- ⬜ Targeted popup on a specific tablet/dashboard
- ⬜ Snooze / "nothing changed" dismissal
- ⬜ Notifications: low stock, expiring soon

## Phase 3 — Expiration & history ⬜

- ⬜ Expiration surfaced in UI; "oldest first" / "expiring soon" views
- ⬜ Consumption history view + usage trends
- ⬜ Per-user attribution (who consumed)

## Phase 4 — Catalog & input acceleration ⬜

- ⬜ Product catalog UI: pick existing product instead of retyping
- ⬜ Aliases / fuzzy matching on add
- ⬜ QR codes per package → scan → consume popup
- ⬜ Barcode (UPC) lookup (spotty for warehouse/Kirkland items)

## Phase 5 — Intelligence & integrations ⬜

- ⬜ Receipt parsing → suggested additions → confirm
- ⬜ Smart suggestions; shopping-list integration on threshold breach
- ⬜ Restock suggestions

## Phase 6 — Distribution ⬜

- ✅ HACS-installable integration with bundled card
- ⬜ Submit to HACS default store
- ⬜ Tests (pytest-homeassistant-custom-component)
- ⬜ Export/import (DB → JSON backup)
- ⬜ Free placement (true x/y grid) as an alternative to ordered layout

---

## Design decisions

- **Layout = ordered list + column count, not x/y coordinates.** Absolute
  positions go stale as packages are added/consumed; an order that the grid
  flows into N columns gives the same look and survives churn.
- **Status color is the one bold visual**; everything else uses HA theme tokens.
- **Aggregation is a computed view**, so individual package records stay
  authoritative.
- **Cards live inside the integration** so one HACS repo updates both.
