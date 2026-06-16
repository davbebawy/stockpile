/*
 * Stockpile cards for Home Assistant.
 *
 * Registers two card types (same code, different default view):
 *   custom:stockpile-summary-card   aggregated overview (default)
 *   custom:stockpile-card           detailed package grid (default)
 *
 * Both share a header with a Summary / Items toggle and a location picker,
 * so either is an entry point and the views switch live. Tapping a product
 * in the summary drills into that product's packages in the grid.
 *
 * Talks to the Stockpile integration over WebSocket (stockpile/packages,
 * stockpile/locations, stockpile/history, stockpile/subscribe) and calls
 * stockpile.consume / set_remaining / remove_package / reorder / add_package.
 * No external dependencies.
 *
 * Config:
 *   type: custom:stockpile-summary-card
 *   title: Kitchen
 *   location_id: loc_xxxxxxxx   # optional: pin to one spot, hides the picker
 *   columns: 10                 # optional (grid view): fixed column count
 *   min_tile: 150               # optional (grid view): min tile width px
 *   show_expiring: true         # optional: highlight expiring items in views
 */

const VERSION = "0.9.1";

const STATUS_VAR = {
  full: "var(--success-color, #2e7d32)",
  medium: "var(--warning-color, #ed6c02)",
  low: "var(--error-color, #c62828)",
  empty: "var(--disabled-text-color, #9e9e9e)",
};

const EXPIRING_LABEL = (days) => {
  if (days == null) return "";
  if (days < 0) return `Expired ${Math.abs(days)}d ago`;
  if (days === 0) return "Expires today";
  if (days === 1) return "Expires tomorrow";
  return `Expires in ${days}d`;
};

const _statusOf = (avg) => {
  if (avg <= 0) return "empty";
  if (avg < 30) return "low";
  if (avg < 75) return "medium";
  return "full";
};

class StockpileCard extends HTMLElement {
  get _defaultView() {
    return "grid";
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._packages = [];
    this._locations = [];
    this._products = [];
    this._started = false;
    this._arrange = false;
    this._selected = null;
    this._productFilter = null;
    this._expFilter = false;
    this._history = [];
    this._dragId = null;
    this._dragPlaceholder = null;
    this._addOpen = false;
    this._historyView = null;
    this._trends = null;
    this._deepLinkHandled = false;
    this._gridMode = "grid";
    this._templates = [];
    this._locationSvg = null;
    this._locationSvgId = null;
    this._pendingTmplId = null;
    this._onKeydown = null;
    this._mapGhost = null;
  }

  setConfig(config) {
    this._config = { min_tile: 150, show_expiring: true, ...config };
    this._view = config.view || this._defaultView;
    this._columns = config.columns || null;
    this._locationFilter = config.location_id || null;
    this._locked = !!config.location_id;
    this._buildShell();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._started) {
      this._started = true;
      this._connect();
    }
  }

  getCardSize() {
    return this._view === "summary" ? 5 : 7;
  }

  // HA 2025+ section dashboards honor this for column placement.
  getLayoutOptions() {
    return { grid_columns: 4, grid_min_columns: 2, grid_rows: "auto" };
  }

  static getStubConfig() {
    return { title: "Stockpile" };
  }

  static getConfigElement() {
    return document.createElement("stockpile-card-editor");
  }

  // ------------------------------------------------------------------ //
  // data
  // ------------------------------------------------------------------ //
  async _connect() {
    try {
      const locs = await this._hass.connection.sendMessagePromise({ type: "stockpile/locations" });
      this._locations = locs.locations || [];
    } catch (e) {
      /* picker just won't show */
    }
    try {
      const prods = await this._hass.connection.sendMessagePromise({ type: "stockpile/products" });
      this._products = prods.products || [];
    } catch (e) {
      /* autocomplete just won't suggest */
    }
    try {
      const tmplRes = await this._hass.connection.sendMessagePromise({ type: "stockpile/templates" });
      this._templates = tmplRes.templates || [];
    } catch (e) {
      /* floor plan just won't show template picker */
    }
    await this._fetch();
    try {
      this._unsub = await this._hass.connection.subscribeMessage(
        () => this._fetch(),
        { type: "stockpile/subscribe" }
      );
    } catch (e) {
      console.warn("stockpile: subscribe failed", e);
    }
  }

  async _fetch() {
    if (this._arrange) return;
    try {
      const msg = { type: "stockpile/packages" };
      if (this._locationFilter) msg.location_id = this._locationFilter;
      const [res, prods] = await Promise.all([
        this._hass.connection.sendMessagePromise(msg),
        this._hass.connection.sendMessagePromise({ type: "stockpile/products" }).catch(() => ({ products: this._products })),
      ]);
      this._packages = res.packages || [];
      this._products = prods.products || [];
      if (this._view === "history") await this._loadFullHistory();
      else if (this._view === "trends") await this._loadTrends();
      this._render();
      if (this._selected) this._refreshDetail();
      if (!this._deepLinkHandled) {
        const deepPkg = new URLSearchParams(window.location.search).get("stockpile");
        if (deepPkg && this._packages.find((p) => p.id === deepPkg)) {
          this._deepLinkHandled = true;
          this._openDetail(deepPkg);
        }
      }
    } catch (e) {
      this._renderError(e);
    }
  }

  async _fetchHistory(productId) {
    try {
      const res = await this._hass.connection.sendMessagePromise({
        type: "stockpile/history",
        product_id: productId,
        limit: 8,
      });
      this._history = res.history || [];
    } catch (e) {
      this._history = [];
    }
  }

  disconnectedCallback() {
    if (this._onKeydown) document.removeEventListener("keydown", this._onKeydown);
    if (this._mapGhost) { this._mapGhost.remove(); this._mapGhost = null; }
    if (this._unsub) {
      try { this._unsub(); } catch (e) { /* noop */ }
    }
  }

  // ------------------------------------------------------------------ //
  // shell
  // ------------------------------------------------------------------ //
  _buildShell() {
    this.shadowRoot.innerHTML = `
      <style>${this._css()}</style>
      <ha-card>
        <div class="head"></div>
        <div class="body"></div>
      </ha-card>
      <div class="overlay" role="dialog" aria-modal="true">
        <div class="sheet"></div>
      </div>
      <div class="overlay add-overlay" role="dialog" aria-modal="true">
        <div class="sheet add-sheet"></div>
      </div>
    `;
    this._head = this.shadowRoot.querySelector(".head");
    this._body = this.shadowRoot.querySelector(".body");
    this._overlay = this.shadowRoot.querySelector(".overlay:not(.add-overlay)");
    this._sheet = this._overlay.querySelector(".sheet");
    this._addOverlay = this.shadowRoot.querySelector(".add-overlay");
    this._addSheet = this._addOverlay.querySelector(".sheet");

    this._overlay.addEventListener("click", (e) => {
      if (e.target === this._overlay) this._closeDetail();
    });
    this._addOverlay.addEventListener("click", (e) => {
      if (e.target === this._addOverlay) this._closeAdd();
    });
    if (this._onKeydown) document.removeEventListener("keydown", this._onKeydown);
    this._onKeydown = (e) => {
      if (e.key === "Escape") {
        if (this._selected) this._closeDetail();
        if (this._addOpen) this._closeAdd();
      }
    };
    document.addEventListener("keydown", this._onKeydown);
  }

  // ------------------------------------------------------------------ //
  // render
  // ------------------------------------------------------------------ //
  _render() {
    this._renderHead();
    if (this._view === "summary") this._renderSummary();
    else if (this._view === "history") this._renderHistory();
    else if (this._view === "trends") this._renderTrends();
    else this._renderGrid();
  }

  _renderHead() {
    const title = this._config.title || "";
    const chips = this._locked
      ? ""
      : `<div class="chips" role="tablist" aria-label="Locations">
           <button class="chip ${!this._locationFilter ? "on" : ""}" data-loc="" role="tab" aria-selected="${!this._locationFilter}">All</button>
           ${this._locations
             .map(
               (l) =>
                 `<button class="chip ${this._locationFilter === l.id ? "on" : ""}" data-loc="${l.id}" role="tab" aria-selected="${this._locationFilter === l.id}">${this._esc(l.name)}</button>`
             )
             .join("")}
         </div>`;

    const expFilterBtn = this._config.show_expiring
      ? `<button class="chip filter ${this._expFilter ? "on" : ""}" data-expfilter="1" title="Show only expiring or expired">Expiring</button>`
      : "";

    this._head.innerHTML = `
      <div class="head-row">
        <div class="title">${this._esc(title)}</div>
        <div class="seg" role="tablist" aria-label="View">
          <button class="seg-btn ${this._view === "summary" ? "on" : ""}" data-view="summary" role="tab" aria-selected="${this._view === "summary"}">Summary</button>
          <button class="seg-btn ${this._view === "grid" ? "on" : ""}" data-view="grid" role="tab" aria-selected="${this._view === "grid"}">Items</button>
          <button class="seg-btn ${this._view === "history" ? "on" : ""}" data-view="history" role="tab" aria-selected="${this._view === "history"}">History</button>
          <button class="seg-btn ${this._view === "trends" ? "on" : ""}" data-view="trends" role="tab" aria-selected="${this._view === "trends"}">Trends</button>
        </div>
      </div>
      ${chips}
      ${expFilterBtn ? `<div class="filters">${expFilterBtn}</div>` : ""}
    `;

    this._head.querySelectorAll("[data-view]").forEach((b) =>
      b.addEventListener("click", () => {
        this._view = b.dataset.view;
        this._productFilter = null;
        this._arrange = false;
        if (this._view === "history") this._loadFullHistory().then(() => this._render());
        else if (this._view === "trends") this._loadTrends().then(() => this._render());
        else this._render();
      })
    );
    this._head.querySelectorAll("[data-loc]").forEach((b) =>
      b.addEventListener("click", () => {
        this._locationFilter = b.dataset.loc || null;
        this._productFilter = null;
        this._fetch();
      })
    );
    const expBtn = this._head.querySelector("[data-expfilter]");
    if (expBtn) {
      expBtn.addEventListener("click", () => {
        this._expFilter = !this._expFilter;
        this._render();
      });
    }
  }

  // ----- summary view ----------------------------------------------- //
  _aggregate(packages) {
    const groups = {};
    for (const p of packages) {
      const g = (groups[p.product_id] ||= {
        product_id: p.product_id,
        name: p.product_name,
        brand: p.brand,
        image: p.image,
        unit: p.unit,
        threshold: p.threshold,
        count: 0,
        sumRemaining: 0,
        qtyRemaining: 0,
        expiringCount: 0,
        expiredCount: 0,
        soonestDays: null,
      });
      g.count += 1;
      g.sumRemaining += p.remaining;
      g.qtyRemaining += (p.quantity || 1) * (p.remaining / 100);
      if (p.expired) g.expiredCount += 1;
      else if (p.expiring_soon) g.expiringCount += 1;
      if (p.expires_in_days != null) {
        g.soonestDays = g.soonestDays == null ? p.expires_in_days : Math.min(g.soonestDays, p.expires_in_days);
      }
    }
    return Object.values(groups)
      .map((g) => {
        g.equiv = g.sumRemaining / 100;
        g.avg = g.count ? g.sumRemaining / g.count : 0;
        g.low = g.threshold != null && g.equiv < g.threshold;
        g.status = _statusOf(g.avg);
        return g;
      })
      .sort((a, b) => (a.low === b.low ? a.name.localeCompare(b.name) : a.low ? -1 : 1));
  }

  _renderSummary() {
    let pkgs = this._packages;
    if (this._expFilter) pkgs = pkgs.filter((p) => p.expiring_soon || p.expired);
    const groups = this._aggregate(pkgs);
    if (!groups.length) {
      this._body.innerHTML = this._empty();
      return;
    }
    const lowCount = groups.filter((g) => g.low).length;
    const expiringCount = this._packages.filter((p) => p.expiring_soon || p.expired).length;
    const totalPkgs = pkgs.length;

    this._body.innerHTML = `
      <div class="stats">
        <div class="stat"><span class="num">${groups.length}</span><span class="lbl">Products</span></div>
        <div class="stat"><span class="num">${totalPkgs}</span><span class="lbl">Packages</span></div>
        <div class="stat ${lowCount ? "warn" : ""}"><span class="num">${lowCount}</span><span class="lbl">Low</span></div>
        ${this._config.show_expiring ? `<div class="stat ${expiringCount ? "warn" : ""}"><span class="num">${expiringCount}</span><span class="lbl">Expiring</span></div>` : ""}
      </div>
      <div class="rows">
        ${groups
          .map((g) => {
            const color = STATUS_VAR[g.status];
            const qty = g.unit ? ` · ${this._round(g.qtyRemaining)} ${this._esc(g.unit)}` : "";
            const thumb = g.image
              ? `style="background-image:url('${this._esc(g.image)}')"`
              : "";
            const initial = g.image ? "" : `<span class="ini">${this._esc(g.name[0].toUpperCase())}</span>`;
            const tags = [
              g.low ? `<span class="tag tag-warn">Restock</span>` : "",
              g.expiredCount ? `<span class="tag tag-err">${g.expiredCount} expired</span>` : "",
              !g.expiredCount && g.expiringCount ? `<span class="tag tag-warn">${g.expiringCount} expiring</span>` : "",
            ].filter(Boolean).join("");
            return `
              <button class="row" data-product="${g.product_id}">
                <div class="r-thumb" ${thumb}>${initial}</div>
                <div class="r-main">
                  <div class="r-name">${this._esc(g.name)} ${tags}</div>
                  <div class="r-sub">${this._esc(g.brand || "")}${g.brand ? " · " : ""}${g.count} package${g.count !== 1 ? "s" : ""}${qty}</div>
                  <div class="r-bar"><span style="width:${Math.round(g.avg)}%;background:${color}"></span></div>
                </div>
                <div class="r-chev" aria-hidden="true">›</div>
              </button>`;
          })
          .join("")}
      </div>
    `;
    this._body.querySelectorAll("[data-product]").forEach((b) =>
      b.addEventListener("click", () => {
        this._productFilter = b.dataset.product;
        this._view = "grid";
        this._render();
      })
    );
  }

  // ----- grid view -------------------------------------------------- //
  _renderGrid() {
    if (this._gridMode === "map") { this._renderMapMode(); return; }
    let pkgs = this._packages;
    let backBtn = "";
    if (this._productFilter) {
      pkgs = pkgs.filter((p) => p.product_id === this._productFilter);
      backBtn = `<button class="link" data-back="1">‹ All items</button>`;
    }
    if (this._expFilter) {
      pkgs = pkgs.filter((p) => p.expiring_soon || p.expired);
    }

    const tmpl = this._columns
      ? `repeat(${this._columns}, 1fr)`
      : `repeat(auto-fill, minmax(${this._config.min_tile}px, 1fr))`;

    const controls = `
      <div class="grid-bar">
        ${backBtn}
        <span class="spacer"></span>
        ${
          this._arrange
            ? `<button class="link" data-cols="-1" aria-label="Fewer columns">− cols</button>
               <span class="cols" aria-live="polite">${this._columns || "auto"}</span>
               <button class="link" data-cols="1" aria-label="More columns">+ cols</button>
               <button class="btn-primary" data-arrange="0">Done</button>`
            : `<button class="link" data-add="1">+ Add</button>
               <button class="link" data-receipt="1">Receipt</button>
               <button class="link ${this._gridMode === "map" ? "on" : ""}" data-mapmode="1" title="Toggle floor-plan map">Map</button>
               <button class="link" data-arrange="1">Arrange</button>`
        }
      </div>`;

    if (!pkgs.length) {
      this._body.innerHTML = controls + this._empty();
      this._wireGridBar();
      return;
    }

    const counts = {};
    for (const p of this._packages) counts[p.product_id] = (counts[p.product_id] || 0) + 1;

    this._body.innerHTML =
      controls +
      `<div class="grid ${this._arrange ? "arranging" : ""}" style="grid-template-columns:${tmpl}">
        ${pkgs.map((p) => this._tile(p, counts)).join("")}
      </div>`;

    this._wireGridBar();
    const grid = this._body.querySelector(".grid");

    if (!this._arrange) {
      grid.addEventListener("click", (e) => {
        const t = e.target.closest(".tile");
        if (t) this._openDetail(t.dataset.id);
      });
      grid.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          const t = e.target.closest(".tile");
          if (t) { e.preventDefault(); this._openDetail(t.dataset.id); }
        }
      });
    } else {
      this._wirePointerDrag(grid);
    }
  }

  _tile(p, counts) {
    const color = STATUS_VAR[p.status] || STATUS_VAR.medium;
    const pct = Math.round(p.remaining);
    const img = p.image ? `style="background-image:url('${this._esc(p.image)}')"` : "";
    const initial = p.image ? "" : `<span class="ini">${this._esc((p.product_name || "?")[0].toUpperCase())}</span>`;
    const badge = counts[p.product_id] > 1 ? `<span class="count">${counts[p.product_id]}</span>` : "";
    const grip = this._arrange ? `<span class="grip" aria-hidden="true">⋮⋮</span>` : "";
    const expFlag = p.expired
      ? `<span class="exp-flag err" title="Expired">!</span>`
      : (this._config.show_expiring && p.expiring_soon
          ? `<span class="exp-flag warn" title="${EXPIRING_LABEL(p.expires_in_days)}">!</span>`
          : "");
    return `
      <div class="tile ${p.status}${p.expired ? " expired" : ""}" data-id="${p.id}" ${this._arrange ? "" : 'tabindex="0" role="button"'}
           aria-label="${this._esc(p.product_name)}, ${pct}%${p.expired ? ", expired" : (p.expiring_soon ? ", expiring soon" : "")}">
        <div class="thumb" ${img}>
          ${initial}${badge}${grip}${expFlag}
          <div class="level" style="width:${pct}%;background:${color}"></div>
        </div>
        <div class="meta">
          <div class="name">${this._esc(p.product_name)}</div>
          <div class="brand">${this._esc(p.brand || "")}</div>
          <div class="pct" style="color:${color}">${pct}%</div>
        </div>
      </div>`;
  }

  _wireGridBar() {
    this._body.querySelectorAll("[data-arrange]").forEach((b) =>
      b.addEventListener("click", () => {
        this._arrange = b.dataset.arrange === "1";
        this._render();
        if (!this._arrange) this._fetch();
      })
    );
    const back = this._body.querySelector("[data-back]");
    if (back) back.addEventListener("click", () => { this._productFilter = null; this._render(); });
    this._body.querySelectorAll("[data-cols]").forEach((b) =>
      b.addEventListener("click", () => {
        const cur = this._columns || 4;
        this._columns = Math.max(1, cur + Number(b.dataset.cols));
        this._render();
      })
    );
    const add = this._body.querySelector("[data-add]");
    if (add) add.addEventListener("click", () => this._openAdd());
    const rcpt = this._body.querySelector("[data-receipt]");
    if (rcpt) rcpt.addEventListener("click", () => this._openReceipt());
    const mapBtn = this._body.querySelector("[data-mapmode]");
    if (mapBtn) mapBtn.addEventListener("click", () => {
      this._gridMode = this._gridMode === "map" ? "grid" : "map";
      this._locationSvg = null;
      this._locationSvgId = null;
      this._render();
    });
  }

  // ----- pointer-based drag to arrange (touch-friendly) ------------- //
  _wirePointerDrag(grid) {
    let active = null;
    let placeholder = null;
    let offsetX = 0;
    let offsetY = 0;
    let started = false;

    const onDown = (e) => {
      const t = e.target.closest(".tile");
      if (!t) return;
      e.preventDefault();
      active = t;
      const box = t.getBoundingClientRect();
      offsetX = e.clientX - box.left;
      offsetY = e.clientY - box.top;
      started = false;
      t.setPointerCapture && t.setPointerCapture(e.pointerId);
    };

    const begin = () => {
      if (!active || started) return;
      started = true;
      const box = active.getBoundingClientRect();
      placeholder = document.createElement("div");
      placeholder.className = "drag-ph";
      placeholder.style.height = `${box.height}px`;
      placeholder.style.width = `${box.width}px`;
      active.parentNode.insertBefore(placeholder, active);
      active.classList.add("dragging");
      active.style.position = "fixed";
      active.style.zIndex = "20";
      active.style.width = `${box.width}px`;
      active.style.left = `${box.left}px`;
      active.style.top = `${box.top}px`;
      active.style.pointerEvents = "none";
    };

    const onMove = (e) => {
      if (!active) return;
      const dx = Math.abs(e.clientX - (active.getBoundingClientRect().left + offsetX));
      const dy = Math.abs(e.clientY - (active.getBoundingClientRect().top + offsetY));
      if (!started && Math.hypot(dx, dy) < 5) return;
      begin();
      active.style.left = `${e.clientX - offsetX}px`;
      active.style.top = `${e.clientY - offsetY}px`;
      const after = this._tileAfterPointer(grid, e.clientX, e.clientY);
      if (placeholder && after !== placeholder) {
        if (after == null) grid.appendChild(placeholder);
        else grid.insertBefore(placeholder, after);
      }
    };

    const onUp = (e) => {
      if (!active) return;
      if (started && placeholder) {
        grid.insertBefore(active, placeholder);
        placeholder.remove();
        active.classList.remove("dragging");
        active.style.cssText = "";
        this._persistOrder(grid);
      }
      active = null;
      placeholder = null;
      started = false;
    };

    grid.addEventListener("pointerdown", onDown);
    grid.addEventListener("pointermove", onMove);
    grid.addEventListener("pointerup", onUp);
    grid.addEventListener("pointercancel", onUp);
  }

  _tileAfterPointer(grid, x, y) {
    const tiles = [...grid.querySelectorAll(".tile:not(.dragging), .drag-ph")];
    let closest = { dist: Infinity, el: null };
    for (const el of tiles) {
      const box = el.getBoundingClientRect();
      const cx = box.left + box.width / 2;
      const cy = box.top + box.height / 2;
      if (y < box.bottom && x < cx) {
        const dist = Math.hypot(cx - x, cy - y);
        if (dist < closest.dist) closest = { dist, el };
      }
    }
    return closest.el;
  }

  async _persistOrder(grid) {
    const ids = [...grid.querySelectorAll(".tile")].map((t) => t.dataset.id);
    try {
      await this._hass.callService("stockpile", "reorder", { package_ids: ids });
    } catch (e) {
      console.error("stockpile: reorder failed", e);
    }
  }

  // ----- history view ----------------------------------------------- //
  async _loadFullHistory() {
    try {
      const res = await this._hass.connection.sendMessagePromise({
        type: "stockpile/history",
        limit: 200,
      });
      this._historyView = res.history || [];
    } catch (e) {
      this._historyView = [];
    }
  }

  _renderHistory() {
    if (this._historyView == null) {
      this._body.innerHTML = `<div class="empty">Loading history…</div>`;
      return;
    }
    if (!this._historyView.length) {
      this._body.innerHTML = `<div class="empty">No activity yet.</div>`;
      return;
    }
    const products = Object.fromEntries((this._products || []).map((p) => [p.id, p]));
    const rows = this._historyView.map((h) => {
      const p = products[h.product_id];
      const name = p ? p.name : h.product_id;
      const brand = p && p.brand ? p.brand : "";
      const when = h.ts ? h.ts.replace("T", " ").slice(0, 16) : "";
      const amount = h.amount != null ? `${this._round(h.amount)}%` : "";
      const remaining = h.remaining_after != null ? `→ ${Math.round(h.remaining_after)}%` : "";
      const who = h.who ? ` · ${this._esc(h.who)}` : "";
      return `
        <li class="h-row">
          <div class="h-left">
            <div class="h-name">${this._esc(name)}</div>
            <div class="h-meta">${this._esc(brand)}${brand ? " · " : ""}${this._esc(when)}${who}</div>
          </div>
          <div class="h-right">
            <span class="h-amount">${this._esc(amount)}</span>
            <span class="h-rem">${this._esc(remaining)}</span>
          </div>
        </li>`;
    }).join("");

    this._body.innerHTML = `
      <div class="h-bar">
        <span class="h-count">${this._historyView.length} event${this._historyView.length === 1 ? "" : "s"}</span>
        <span class="spacer"></span>
        <button class="link" data-h-refresh="1">Refresh</button>
      </div>
      <ul class="h-list">${rows}</ul>
    `;
    const btn = this._body.querySelector("[data-h-refresh]");
    if (btn) btn.addEventListener("click", async () => {
      await this._loadFullHistory();
      this._render();
    });
  }

  // ----- detail overlay --------------------------------------------- //
  async _openDetail(id) {
    this._selected = id;
    const p = this._packages.find((x) => x.id === id);
    this._velocity = null;
    if (p) {
      await Promise.all([
        this._fetchHistory(p.product_id),
        this._fetchVelocity(p.product_id),
      ]);
    }
    this._refreshDetail();
    this._overlay.classList.add("open");
  }
  _closeDetail() {
    this._selected = null;
    this._history = [];
    this._velocity = null;
    this._overlay.classList.remove("open");
  }

  async _fetchVelocity(productId) {
    try {
      const res = await this._hass.connection.sendMessagePromise({
        type: "stockpile/velocity",
        product_id: productId,
        days: 30,
      });
      this._velocity = res.velocity || null;
    } catch (e) {
      this._velocity = null;
    }
  }
  _refreshDetail() {
    const p = this._packages.find((x) => x.id === this._selected);
    if (!p) { this._closeDetail(); return; }
    const color = STATUS_VAR[p.status] || STATUS_VAR.medium;
    const pct = Math.round(p.remaining);
    const img = p.image ? `style="background-image:url('${this._esc(p.image)}')"` : "";
    const initial = p.image ? "" : `<span class="ini big">${this._esc((p.product_name || "?")[0].toUpperCase())}</span>`;
    const d = (s) => (s ? s.slice(0, 10) : "—");
    const expRow = p.expires
      ? `<dt>Expires</dt><dd>${d(p.expires)}${p.expires_in_days != null ? ` <span class="hint">(${this._esc(EXPIRING_LABEL(p.expires_in_days))})</span>` : ""}</dd>`
      : "";

    const historyHtml = this._history.length
      ? `<div class="hist">
           <div class="hist-title">Recent activity</div>
           <ul>
             ${this._history.map((h) => {
               const when = h.ts ? h.ts.slice(0, 10) : "";
               const amount = h.amount != null ? `${this._round(h.amount)}%` : "";
               return `<li><span class="hist-amt">${this._esc(amount)}</span><span class="hist-when">${this._esc(when)}</span></li>`;
             }).join("")}
           </ul>
         </div>`
      : "";

    const velocity = this._velocity;
    let velRow = "";
    let runOutRow = "";
    if (velocity && velocity.per_day > 0) {
      const perWeek = velocity.per_day * 7;
      velRow = `<dt>Usage</dt><dd>${this._round(perWeek)}/wk <span class="hint">(${velocity.days}d window)</span></dd>`;
      // Total equiv-remaining for the product across all its packages
      const totalEquiv = this._packages
        .filter((x) => x.product_id === p.product_id)
        .reduce((s, x) => s + x.remaining / 100, 0);
      const daysLeft = Math.round(totalEquiv / velocity.per_day);
      if (Number.isFinite(daysLeft) && daysLeft >= 0 && daysLeft <= 365 * 3) {
        runOutRow = `<dt>Runs out</dt><dd>~${daysLeft}d <span class="hint">at current pace</span></dd>`;
      }
    }

    this._sheet.innerHTML = `
      <div class="d-thumb" ${img}>${initial}</div>
      <h2>${this._esc(p.product_name)}</h2>
      <div class="d-brand">${this._esc(p.brand || "")}</div>
      <dl class="facts">
        <dt>Remaining</dt><dd style="color:${color};font-weight:600">${pct}%</dd>
        <dt>Quantity</dt><dd>${this._esc(String(p.quantity))}${p.unit ? " " + this._esc(p.unit) : ""}</dd>
        <dt>Location</dt><dd>${this._esc(p.location_name || "—")}</dd>
        <dt>Added</dt><dd>${d(p.added)}</dd>
        ${expRow}
        ${velRow}
        ${runOutRow}
      </dl>
      <div class="actions">
        <button class="act" data-use="10">Use 10%</button>
        <button class="act" data-use="25">Use 25%</button>
        <button class="act" data-use="50">Use 50%</button>
        <button class="act" data-use="100">Use all</button>
      </div>
      <div class="custom-row">
        <input type="range" min="1" max="100" value="20" aria-label="Custom amount" />
        <span class="val" aria-live="polite">20</span>
        <button class="apply">Use</button>
      </div>
      ${historyHtml}
      <div class="quiet-row">
        <button class="link" data-snooze="24">Snooze 1d</button>
        <button class="link" data-snooze="168">Snooze 7d</button>
        <button class="link" data-ack="1">Acknowledge</button>
        <button class="link" data-qr="1" title="Show QR code to scan and consume">QR code</button>
      </div>
      <div class="close-row">
        <button class="act danger" data-remove="1">Remove</button>
        <button class="close">Close</button>
      </div>`;

    this._sheet.querySelectorAll("[data-use]").forEach((b) =>
      b.addEventListener("click", () => this._consume(p.id, Number(b.dataset.use)))
    );
    const range = this._sheet.querySelector("input[type=range]");
    const val = this._sheet.querySelector(".val");
    range.addEventListener("input", () => (val.textContent = range.value));
    this._sheet.querySelector(".apply").addEventListener("click", () => this._consume(p.id, Number(range.value)));
    this._sheet.querySelector("[data-remove]").addEventListener("click", () => this._remove(p.id));
    this._sheet.querySelector(".close").addEventListener("click", () => this._closeDetail());
    this._sheet.querySelectorAll("[data-snooze]").forEach((b) =>
      b.addEventListener("click", () => this._snooze(p.product_id, Number(b.dataset.snooze)))
    );
    const ack = this._sheet.querySelector("[data-ack]");
    if (ack) ack.addEventListener("click", () => this._acknowledge(p.product_id));
    const qrBtn = this._sheet.querySelector("[data-qr]");
    if (qrBtn) qrBtn.addEventListener("click", () => this._showQR(p));
  }

  async _snooze(productId, hours) {
    try {
      await this._hass.callService("stockpile", "snooze", { product_id: productId, hours });
      await this._fetch();
    } catch (e) { console.error("stockpile: snooze failed", e); }
  }
  async _acknowledge(productId) {
    try {
      await this._hass.callService("stockpile", "acknowledge", { product_id: productId });
      await this._fetch();
    } catch (e) { console.error("stockpile: acknowledge failed", e); }
  }

  // ----- QR code overlay -------------------------------------------- //
  _showQR(pkg) {
    const deepLink = `${window.location.origin}/?stockpile=${pkg.id}`;
    const qrSrc = `/api/stockpile/qr?url=${encodeURIComponent(deepLink)}`;

    let overlay = this.shadowRoot.querySelector(".qr-overlay");
    if (!overlay) {
      overlay = document.createElement("div");
      overlay.className = "qr-overlay";
      overlay.innerHTML = `
        <div class="qr-sheet">
          <div class="qr-title">Scan to consume</div>
          <div class="qr-pkg-name"></div>
          <img class="qr-img" alt="QR code" />
          <div class="qr-hint">Scan with your phone camera to open the consume sheet directly.</div>
          <div class="qr-id-row"><code class="qr-id"></code></div>
          <button class="close qr-close">Close</button>
        </div>`;
      overlay.addEventListener("click", (e) => {
        if (e.target === overlay) overlay.classList.remove("open");
      });
      overlay.querySelector(".qr-close").addEventListener("click", () =>
        overlay.classList.remove("open")
      );
      this.shadowRoot.appendChild(overlay);
    }

    overlay.querySelector(".qr-pkg-name").textContent = pkg.product_name + (pkg.brand ? ` · ${pkg.brand}` : "");
    overlay.querySelector(".qr-id").textContent = pkg.id;
    overlay.querySelector(".qr-img").src = qrSrc;
    overlay.classList.add("open");
  }

  // ----- trends view ------------------------------------------------ //
  async _loadTrends() {
    try {
      const res = await this._hass.connection.sendMessagePromise({
        type: "stockpile/trends",
        days: 14,
      });
      this._trends = res.trends || [];
    } catch (e) {
      this._trends = [];
    }
  }

  _renderTrends() {
    if (this._trends == null) {
      this._body.innerHTML = `<div class="empty">Loading trends…</div>`;
      return;
    }
    if (!this._trends.length) {
      this._body.innerHTML = `<div class="empty">No consumption data in the last 14 days.</div>`;
      return;
    }

    const today = new Date();
    const dateKeys = [];
    for (let i = 13; i >= 0; i--) {
      const d = new Date(today);
      d.setDate(d.getDate() - i);
      // Local date string so sparkline columns align with the user's timezone,
      // not UTC (which can shift the "today" bucket for non-UTC users).
      const y = d.getFullYear();
      const mo = String(d.getMonth() + 1).padStart(2, "0");
      const dy = String(d.getDate()).padStart(2, "0");
      dateKeys.push(`${y}-${mo}-${dy}`);
    }

    const maxVal = Math.max(
      1e-9,
      ...this._trends.flatMap((t) => dateKeys.map((dk) => t.daily[dk] || 0))
    );

    const rows = this._trends.map((t) => {
      const spark = this._sparkline(t.daily, dateKeys, maxVal);
      const qty = t.unit
        ? ` <span class="tr-unit">${this._esc(t.unit)}</span>`
        : "";
      return `
        <div class="tr-row">
          <div class="tr-name">${this._esc(t.name)}</div>
          <div class="tr-total">${this._round(t.total_equiv)}${qty}</div>
          <div class="tr-spark">${spark}</div>
        </div>`;
    }).join("");

    const oldest = dateKeys[0].slice(5);
    const newest = dateKeys[dateKeys.length - 1].slice(5);
    this._body.innerHTML = `
      <div class="tr-head">
        <span class="tr-h-name">Product</span>
        <span class="tr-h-total">14d used</span>
        <div class="tr-h-spark">
          <span>${oldest}</span>
          <span>today</span>
        </div>
      </div>
      <div class="tr-list">${rows}</div>
      <div class="tr-foot">
        <button class="link" data-tr-refresh="1">Refresh</button>
      </div>`;

    const btn = this._body.querySelector("[data-tr-refresh]");
    if (btn) btn.addEventListener("click", async () => {
      this._trends = null;
      this._render();
      await this._loadTrends();
      this._render();
    });
  }

  _sparkline(daily, dateKeys, maxVal) {
    const BAR_W = 7;
    const GAP = 1;
    const H = 28;
    const totalW = dateKeys.length * (BAR_W + GAP) - GAP;
    const bars = dateKeys.map((dk, i) => {
      const v = daily[dk] || 0;
      const h = v > 0 ? Math.max(3, Math.round((v / maxVal) * H)) : 1;
      const fill = v > 0 ? "var(--sp-primary)" : "var(--sp-divider)";
      const x = i * (BAR_W + GAP);
      return `<rect x="${x}" y="${H - h}" width="${BAR_W}" height="${h}" rx="1.5" fill="${fill}"/>`;
    }).join("");
    return `<svg width="${totalW}" height="${H}" viewBox="0 0 ${totalW} ${H}" aria-hidden="true">${bars}</svg>`;
  }

  async _consume(id, amount) {
    try {
      await this._hass.callService("stockpile", "consume", { package_id: id, amount });
      await this._fetch();
      const p = this._packages.find((x) => x.id === id);
      if (p) await this._fetchHistory(p.product_id);
      this._refreshDetail();
    } catch (e) { console.error("stockpile: consume failed", e); }
  }
  async _remove(id) {
    try {
      await this._hass.callService("stockpile", "remove_package", { package_id: id });
      this._closeDetail();
      await this._fetch();
    } catch (e) { console.error("stockpile: remove failed", e); }
  }

  // ----- add overlay ------------------------------------------------ //
  _openAdd() {
    this._addOpen = true;
    this._renderAdd();
    this._addOverlay.classList.add("open");
  }
  _closeAdd() {
    this._addOpen = false;
    this._addOverlay.classList.remove("open");
  }
  _renderAdd() {
    const locs = this._locations.length
      ? `<label>Location
           <select name="location_id">
             <option value="">—</option>
             ${this._locations.map((l) => `<option value="${l.id}" ${this._locationFilter === l.id ? "selected" : ""}>${this._esc(l.name)}</option>`).join("")}
           </select>
         </label>`
      : "";

    const productOptions = (this._products || [])
      .map((p) => `<option value="${this._esc(p.name)}"></option>`)
      .join("");

    this._addSheet.innerHTML = `
      <h2>Add package</h2>
      <form class="add-form" autocomplete="off">
        <label>Name
          <input name="name" type="text" required placeholder="e.g. Ground Beef" list="stockpile-products" />
          <datalist id="stockpile-products">${productOptions}</datalist>
          <span class="hint" data-suggest hidden></span>
        </label>
        <label>Brand
          <input name="brand" type="text" placeholder="optional" />
        </label>
        <label>Unit
          <input name="unit" type="text" placeholder="lb, jar, box…" />
        </label>
        ${locs}
        <label>Remaining
          <div class="row-inline">
            <input name="remaining" type="number" min="0" max="100" step="1" value="100" />
            <span class="hint">%</span>
          </div>
        </label>
        <label>Quantity
          <input name="quantity" type="number" min="0" step="0.1" value="1" />
        </label>
        <label>Expires
          <input name="expires" type="date" />
        </label>
        <div class="close-row">
          <button type="button" class="close">Cancel</button>
          <button type="submit" class="btn-primary">Add</button>
        </div>
        <div class="add-error" hidden></div>
      </form>`;

    const form = this._addSheet.querySelector("form");
    const nameInput = form.querySelector("input[name=name]");
    const brandInput = form.querySelector("input[name=brand]");
    const unitInput = form.querySelector("input[name=unit]");
    const suggestHint = form.querySelector("[data-suggest]");

    const tryMatch = () => {
      const v = (nameInput.value || "").trim().toLowerCase();
      if (!v) { suggestHint.hidden = true; return; }
      const match = (this._products || []).find(
        (p) => (p.name || "").toLowerCase() === v
            || ((p.aliases || []).map((a) => a.toLowerCase())).includes(v)
      );
      if (match) {
        if (!brandInput.value && match.brand) brandInput.value = match.brand;
        if (!unitInput.value && match.unit) unitInput.value = match.unit;
        suggestHint.hidden = false;
        suggestHint.textContent = `Matches existing product · ${match.brand || ""}${match.brand && match.unit ? " · " : ""}${match.unit || ""}`.trim();
      } else {
        suggestHint.hidden = false;
        suggestHint.textContent = "Will create a new product";
      }
    };
    nameInput.addEventListener("change", tryMatch);
    nameInput.addEventListener("input", tryMatch);

    this._addSheet.querySelector(".close").addEventListener("click", () => this._closeAdd());
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const data = new FormData(form);
      const name = (data.get("name") || "").toString().trim();
      if (!name) return;
      const payload = {
        name,
        brand: (data.get("brand") || "").toString().trim() || undefined,
        unit: (data.get("unit") || "").toString().trim() || undefined,
        location_id: (data.get("location_id") || "").toString() || undefined,
        remaining: Number(data.get("remaining") || 100),
        quantity: Number(data.get("quantity") || 1),
        expires: (data.get("expires") || "").toString() || undefined,
      };
      Object.keys(payload).forEach((k) => payload[k] === undefined && delete payload[k]);
      try {
        await this._hass.callService("stockpile", "add_package", payload);
        this._closeAdd();
        await this._fetch();
      } catch (err) {
        const box = this._addSheet.querySelector(".add-error");
        box.hidden = false;
        box.textContent = `Could not add: ${String(err.message || err)}`;
      }
    });
  }

  // ----- floor plan (map mode) -------------------------------------- //
  _renderMapMode() {
    const controls = `
      <div class="grid-bar">
        <span class="spacer"></span>
        <button class="link ${this._gridMode === "map" ? "on" : ""}" data-mapmode="1" title="Toggle floor-plan map">Map</button>
        <button class="link" data-arrange="0" style="display:none"></button>
      </div>`;

    if (!this._locationFilter) {
      this._body.innerHTML = controls + `
        <div class="map-hint">
          <div class="map-hint-icon">🗺</div>
          <div>Select a <strong>location</strong> above to view its floor plan.</div>
        </div>`;
      this._wireGridBar();
      return;
    }

    const loc = this._locations.find((l) => l.id === this._locationFilter);
    if (!loc) { this._renderGrid(); return; }

    if (!loc.template_id) {
      this._body.innerHTML = controls + this._renderTemplateSetup(loc);
      this._wireGridBar();
      this._wireTemplateSetup(loc);
      return;
    }

    // Need to load the SVG from the server if we don't have it cached
    if (this._locationSvgId !== this._locationFilter || !this._locationSvg) {
      this._body.innerHTML = controls + `<div class="empty">Loading map…</div>`;
      this._wireGridBar();
      this._hass.connection.sendMessagePromise({
        type: "stockpile/templates",
        location_id: this._locationFilter,
      }).then((res) => {
        this._locationSvg = res.location_svg || null;
        this._locationSvgId = this._locationFilter;
        this._renderMapMode();
      }).catch(() => {
        this._body.innerHTML = controls + `<div class="empty">Could not load floor plan.</div>`;
      });
      return;
    }

    const pkgs = this._packages;
    const placed = pkgs.filter((p) => p.loc_x != null && p.loc_y != null);
    const staged = pkgs.filter((p) => p.loc_x == null || p.loc_y == null);

    // Derive aspect ratio from the template viewBox
    const tmpl = this._templates.find((t) => t.id === loc.template_id);
    const vb = tmpl ? tmpl.viewBox.split(" ") : ["0","0","200","130"];
    const canvasW = parseFloat(vb[2]) || 200;
    const canvasH = parseFloat(vb[3]) || 130;
    const aspect = canvasW / canvasH;

    const placedTiles = placed.map((p) => {
      const color = STATUS_VAR[p.status] || STATUS_VAR.medium;
      const pct = Math.round(p.remaining);
      const img = p.image ? `style="background-image:url('${this._esc(p.image)}')"` : "";
      const initial = p.image ? "" : `<span class="ini">${this._esc((p.product_name||"?")[0].toUpperCase())}</span>`;
      return `
        <div class="map-tile ${p.status}" data-id="${p.id}"
             style="left:${p.loc_x}%;top:${p.loc_y}%"
             tabindex="0" role="button"
             aria-label="${this._esc(p.product_name)}, ${pct}%">
          <div class="map-tile-thumb" ${img}>
            ${initial}
            <div class="map-level" style="width:${pct}%;background:${color}"></div>
          </div>
          <div class="map-tile-pct" style="color:${color}">${pct}%</div>
        </div>`;
    }).join("");

    const stagedTiles = staged.map((p) => {
      const color = STATUS_VAR[p.status] || STATUS_VAR.medium;
      const pct = Math.round(p.remaining);
      const img = p.image ? `style="background-image:url('${this._esc(p.image)}')"` : "";
      const initial = p.image ? "" : `<span class="ini">${this._esc((p.product_name||"?")[0].toUpperCase())}</span>`;
      return `
        <div class="map-tile staged ${p.status}" data-id="${p.id}"
             tabindex="0" role="button"
             aria-label="${this._esc(p.product_name)}, ${pct}% (unpositioned)">
          <div class="map-tile-thumb" ${img}>
            ${initial}
            <div class="map-level" style="width:${pct}%;background:${color}"></div>
          </div>
          <div class="map-tile-pct" style="color:${color}">${pct}%</div>
        </div>`;
    }).join("");

    this._body.innerHTML = `
      ${controls}
      <div class="map-canvas" style="aspect-ratio:${aspect.toFixed(4)}">
        <div class="map-bg">${this._locationSvg}</div>
        ${placedTiles}
      </div>
      ${staged.length ? `
        <div class="map-tray">
          <div class="map-tray-label">Drag onto the map to position</div>
          <div class="map-tray-items">${stagedTiles}</div>
        </div>` : ""}
    `;

    this._wireGridBar();

    const canvas = this._body.querySelector(".map-canvas");
    const tray = this._body.querySelector(".map-tray-items");

    // Click to open detail sheet
    [canvas, tray].forEach((el) => el && el.addEventListener("click", (e) => {
      const t = e.target.closest(".map-tile");
      if (t && !this._mapDragMoved) this._openDetail(t.dataset.id);
    }));

    this._wireMapDrag(canvas, tray);
  }

  _wireMapDrag(canvas, tray) {
    let dragging = null;
    let ghost = null;
    this._mapDragMoved = false;

    const DRAG_THRESHOLD = 6;

    const startDrag = (tile, e) => {
      e.preventDefault();
      this._mapDragMoved = false;
      const box = tile.getBoundingClientRect();
      dragging = {
        id: tile.dataset.id,
        fromTray: tile.classList.contains("staged"),
        startX: e.clientX,
        startY: e.clientY,
      };
      ghost = tile.cloneNode(true);
      ghost.classList.add("map-ghost");
      // Inline positional styles so Shadow DOM stylesheet can apply the rest.
      // position:fixed inside Shadow DOM is viewport-relative (no transform on host).
      ghost.style.cssText = `position:fixed;width:${box.width}px;left:${box.left}px;top:${box.top}px;`;
      this._mapGhost = ghost;
      this.shadowRoot.appendChild(ghost);
      tile.style.opacity = "0.25";
      tile.setPointerCapture && tile.setPointerCapture(e.pointerId);
      dragging.tile = tile;
    };

    const onDown = (e) => {
      const t = e.target.closest(".map-tile");
      if (!t) return;
      startDrag(t, e);
    };

    const onMove = (e) => {
      if (!dragging) return;
      const dx = e.clientX - dragging.startX;
      const dy = e.clientY - dragging.startY;
      if (!this._mapDragMoved && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
      this._mapDragMoved = true;
      if (ghost) {
        ghost.style.left = (parseFloat(ghost.style.left) + e.movementX) + "px";
        ghost.style.top = (parseFloat(ghost.style.top) + e.movementY) + "px";
      }
    };

    const onUp = async (e) => {
      if (!dragging) return;
      if (dragging.tile) dragging.tile.style.opacity = "";
      if (ghost) { ghost.remove(); ghost = null; this._mapGhost = null; }

      if (this._mapDragMoved && canvas) {
        const canvasRect = canvas.getBoundingClientRect();
        const overCanvas = (
          e.clientX >= canvasRect.left && e.clientX <= canvasRect.right &&
          e.clientY >= canvasRect.top  && e.clientY <= canvasRect.bottom
        );

        const id = dragging.id;
        if (overCanvas) {
          const loc_x = Math.max(2, Math.min(98, ((e.clientX - canvasRect.left) / canvasRect.width) * 100));
          const loc_y = Math.max(2, Math.min(98, ((e.clientY - canvasRect.top) / canvasRect.height) * 100));
          await this._persistMapPosition(id, loc_x, loc_y);
        } else if (!dragging.fromTray) {
          // Dragged off canvas → unstage
          await this._persistMapPosition(id, null, null);
        }
      }
      dragging = null;
    };

    if (canvas) {
      canvas.addEventListener("pointerdown", onDown);
      canvas.addEventListener("pointermove", onMove);
      canvas.addEventListener("pointerup", onUp);
      canvas.addEventListener("pointercancel", onUp);
    }
    if (tray) {
      tray.addEventListener("pointerdown", onDown);
      tray.addEventListener("pointermove", onMove);
      tray.addEventListener("pointerup", onUp);
      tray.addEventListener("pointercancel", onUp);
    }
  }

  async _persistMapPosition(id, locX, locY) {
    try {
      const payload = { package_id: id };
      if (locX != null) { payload.loc_x = locX; payload.loc_y = locY; }
      await this._hass.callService("stockpile", "set_package_position", payload);
      await this._fetch();
    } catch (e) {
      console.error("stockpile: map position failed", e);
    }
  }

  _renderTemplateSetup(loc) {
    const thumbs = this._templates.map((t) => `
      <button class="tmpl-thumb ${this._pendingTmplId === t.id ? "on" : ""}" data-tmpl="${t.id}">
        <div class="tmpl-svg">${t.default_svg}</div>
        <div class="tmpl-label">${this._esc(t.label)}</div>
      </button>`).join("");

    const pending = this._templates.find((t) => t.id === this._pendingTmplId);
    const configFields = pending
      ? pending.config_schema.map((f) => {
          const inputEl = f.type === "bool"
            ? `<label class="tmpl-bool">
                 <input type="checkbox" data-cfg="${f.key}" ${f.default ? "checked" : ""} />
                 ${this._esc(f.label)}
               </label>`
            : `<label class="tmpl-field">
                 ${this._esc(f.label)}
                 <input type="number" data-cfg="${f.key}"
                        min="${f.min}" max="${f.max}" value="${f.default}" />
               </label>`;
          return inputEl;
        }).join("")
      : "";

    return `
      <div class="tmpl-setup">
        <div class="tmpl-setup-title">Set up floor plan for <strong>${this._esc(loc.name)}</strong></div>
        <div class="tmpl-thumbs">${thumbs}</div>
        ${pending ? `
          <div class="tmpl-config">${configFields}</div>
          <div class="tmpl-apply-row">
            <button class="btn-primary" data-apply-tmpl="1">Apply template</button>
          </div>` : ""}
      </div>`;
  }

  _wireTemplateSetup(loc) {
    this._body.querySelectorAll("[data-tmpl]").forEach((b) =>
      b.addEventListener("click", () => {
        this._pendingTmplId = b.dataset.tmpl;
        this._render();
      })
    );
    const applyBtn = this._body.querySelector("[data-apply-tmpl]");
    if (applyBtn) applyBtn.addEventListener("click", () => this._applyTemplate(loc.id));
  }

  async _applyTemplate(locationId) {
    const tmpl = this._templates.find((t) => t.id === this._pendingTmplId);
    if (!tmpl) return;
    const config = {};
    this._body.querySelectorAll("[data-cfg]").forEach((el) => {
      const key = el.dataset.cfg;
      config[key] = el.type === "checkbox" ? el.checked : Number(el.value);
    });
    try {
      await this._hass.callService("stockpile", "set_location_template", {
        location_id: locationId,
        template_id: this._pendingTmplId,
        template_config: JSON.stringify(config),
      });
      this._pendingTmplId = null;
      this._locationSvg = null;
      this._locationSvgId = null;
      const locs = await this._hass.connection.sendMessagePromise({ type: "stockpile/locations" });
      this._locations = locs.locations || [];
      this._render();
    } catch (e) {
      console.error("stockpile: set_location_template failed", e);
    }
  }

  // ----- receipt overlay -------------------------------------------- //
  _openReceipt() {
    let ov = this.shadowRoot.querySelector(".rx-overlay");
    if (!ov) {
      ov = document.createElement("div");
      ov.className = "rx-overlay";
      ov.addEventListener("click", (e) => { if (e.target === ov) ov.classList.remove("open"); });
      this.shadowRoot.appendChild(ov);
    }
    this._rxOv = ov;
    this._renderReceiptInput();
    ov.classList.add("open");
  }

  _renderReceiptInput() {
    this._rxOv.innerHTML = `
      <div class="rx-sheet">
        <h2>Parse receipt</h2>
        <p class="rx-hint">Paste receipt text below. The parser strips prices, extracts names and quantities, then matches against your catalog.</p>
        <textarea class="rx-ta" rows="9" placeholder="GROUND BEEF 80/20 3LB    $8.99&#10;KIRKLAND PASTA 12OZ x4    $7.96&#10;PAPER TOWELS 6PK    $14.99&#10;..."></textarea>
        <div class="rx-foot">
          <button class="close rx-cancel">Cancel</button>
          <button class="btn-primary rx-parse">Parse</button>
        </div>
      </div>`;
    this._rxOv.querySelector(".rx-cancel").addEventListener("click", () => this._rxOv.classList.remove("open"));
    this._rxOv.querySelector(".rx-parse").addEventListener("click", () => this._runReceiptParse());
  }

  async _runReceiptParse() {
    const text = this._rxOv.querySelector(".rx-ta").value.trim();
    if (!text) return;
    const btn = this._rxOv.querySelector(".rx-parse");
    btn.disabled = true;
    btn.textContent = "Parsing…";
    try {
      // hass.callService(domain, service, data, target, notifyOnError, returnResponse)
      const result = await this._hass.callService(
        "stockpile", "parse_receipt", { text },
        undefined, false, true,
      );
      const suggestions = result?.response?.suggestions ?? result?.suggestions ?? [];
      this._rxSugg = suggestions;
      this._renderReceiptReview(suggestions);
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "Parse";
      const err = this._rxOv.querySelector(".rx-err") || (() => {
        const d = document.createElement("div");
        d.className = "rx-err";
        this._rxOv.querySelector(".rx-sheet").appendChild(d);
        return d;
      })();
      err.textContent = `Error: ${e.message || String(e)}`;
    }
  }

  _renderReceiptReview(suggestions) {
    if (!suggestions.length) {
      this._rxOv.innerHTML = `
        <div class="rx-sheet">
          <h2>No products found</h2>
          <p class="rx-hint">Could not extract any product lines. Try pasting a different section of the receipt.</p>
          <div class="rx-foot"><button class="btn-primary rx-back">Back</button></div>
        </div>`;
      this._rxOv.querySelector(".rx-back").addEventListener("click", () => this._renderReceiptInput());
      return;
    }

    const locOpts = this._locations
      .map((l) => `<option value="${l.id}">${this._esc(l.name)}</option>`)
      .join("");
    const locPicker = this._locations.length
      ? `<label class="rx-loc-label">Add to location
           <select class="rx-loc"><option value="">— none —</option>${locOpts}</select>
         </label>`
      : "";

    const rows = suggestions.map((s, i) => {
      const badge = s.matched
        ? `<span class="rx-badge match">Catalog</span>`
        : `<span class="rx-badge new">New</span>`;
      return `
        <div class="rx-item" data-i="${i}">
          <input type="checkbox" class="rx-cb" checked />
          <div class="rx-item-info">
            <input class="rx-name" type="text" value="${this._esc(s.name)}" />
            <div class="rx-item-sub">${badge}${s.brand ? ` <span class="rx-brand">${this._esc(s.brand)}</span>` : ""}</div>
          </div>
          <div class="rx-item-nums">
            <input class="rx-qty" type="number" min="0.1" step="0.1" value="${s.qty || 1}" title="Quantity" />
            <input class="rx-unit" type="text" value="${this._esc(s.unit || "")}" placeholder="unit" title="Unit" />
          </div>
        </div>`;
    }).join("");

    this._rxOv.innerHTML = `
      <div class="rx-sheet rx-review">
        <div class="rx-rev-head">
          <h2>Review items</h2>
          <button class="link rx-selall" title="Toggle all">All</button>
        </div>
        <div class="rx-list">${rows}</div>
        <div class="rx-rev-foot">
          ${locPicker}
          <div class="rx-foot">
            <button class="close rx-back">Back</button>
            <button class="btn-primary rx-confirm">Add checked</button>
          </div>
        </div>
      </div>`;

    const selAll = this._rxOv.querySelector(".rx-selall");
    selAll.addEventListener("click", () => {
      const cbs = [...this._rxOv.querySelectorAll(".rx-cb")];
      const allOn = cbs.every((c) => c.checked);
      cbs.forEach((c) => (c.checked = !allOn));
    });
    this._rxOv.querySelector(".rx-back").addEventListener("click", () => this._renderReceiptInput());
    this._rxOv.querySelector(".rx-confirm").addEventListener("click", () => this._addReceiptItems());
  }

  async _addReceiptItems() {
    const locEl = this._rxOv.querySelector(".rx-loc");
    const locId = locEl ? locEl.value || undefined : undefined;
    const toAdd = [];

    for (const row of this._rxOv.querySelectorAll(".rx-item")) {
      if (!row.querySelector(".rx-cb").checked) continue;
      const i = Number(row.dataset.i);
      const s = this._rxSugg[i];
      const name = row.querySelector(".rx-name").value.trim() || s.name;
      const qty = Number(row.querySelector(".rx-qty").value) || 1;
      const unit = row.querySelector(".rx-unit").value.trim() || s.unit || undefined;
      const payload = { remaining: 100, quantity: qty };
      if (s.product_id) {
        payload.product_id = s.product_id;
      } else {
        payload.name = name;
        if (unit) payload.unit = unit;
      }
      if (locId) payload.location_id = locId;
      toAdd.push(payload);
    }

    if (!toAdd.length) { this._rxOv.classList.remove("open"); return; }

    const btn = this._rxOv.querySelector(".rx-confirm");
    btn.disabled = true;
    btn.textContent = `Adding ${toAdd.length}…`;

    for (const payload of toAdd) {
      try {
        await this._hass.callService("stockpile", "add_package", payload);
      } catch (e) {
        console.error("stockpile: failed to add receipt item", payload, e);
      }
    }

    this._rxOv.classList.remove("open");
    await this._fetch();
  }

  // ------------------------------------------------------------------ //
  _empty() {
    return `<div class="empty">Nothing here yet. Add a package, or call <code>stockpile.seed_demo</code> for test data.</div>`;
  }
  _renderError(e) {
    if (this._body) this._body.innerHTML = `<div class="empty">Could not load Stockpile (${this._esc(String(e))}).</div>`;
  }
  _round(n) { return Math.round(n * 10) / 10; }
  _esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  _css() {
    return `
      :host {
        --sp-radius-card: var(--ha-card-border-radius, 16px);
        --sp-radius-md: 14px;
        --sp-radius-sm: 10px;
        --sp-radius-pill: 999px;
        --sp-gap: 12px;
        --sp-elevation: 0 1px 2px rgba(0,0,0,.06), 0 4px 16px rgba(0,0,0,.10);
        --sp-elevation-strong: 0 8px 28px rgba(0,0,0,.22);
        --sp-divider: var(--divider-color, rgba(127,127,127,.18));
        --sp-surface: var(--secondary-background-color);
        --sp-surface-2: var(--card-background-color, #fff);
        --sp-fg: var(--primary-text-color);
        --sp-fg-dim: var(--secondary-text-color);
        --sp-primary: var(--primary-color, #3f7afe);
        font-family: var(--ha-font-family-body, var(--paper-font-body1_-_font-family, "Roboto", "Helvetica Neue", sans-serif));
      }

      ha-card {
        padding: 16px 16px 18px;
        border-radius: var(--sp-radius-card);
      }

      .head { margin-bottom: 14px; }
      .head-row { display:flex; align-items:center; gap:12px; }
      .title {
        font-size: 1.18rem;
        font-weight: 600;
        letter-spacing: -0.005em;
        flex: 1;
        color: var(--sp-fg);
      }

      .seg {
        display: inline-flex;
        background: var(--sp-surface);
        border-radius: var(--sp-radius-pill);
        padding: 3px;
        box-shadow: inset 0 0 0 1px var(--sp-divider);
      }
      .seg-btn {
        font: inherit;
        font-size: .82rem;
        font-weight: 600;
        border: none;
        background: none;
        cursor: pointer;
        color: var(--sp-fg-dim);
        padding: 6px 14px;
        border-radius: var(--sp-radius-pill);
        transition: background-color .15s ease, color .15s ease;
      }
      .seg-btn.on {
        background: var(--sp-surface-2);
        color: var(--sp-fg);
        box-shadow: 0 1px 2px rgba(0,0,0,.10), 0 0 0 1px var(--sp-divider);
      }
      .seg-btn:focus-visible {
        outline: 2px solid var(--sp-primary);
        outline-offset: 2px;
      }

      .chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        margin-top: 12px;
      }
      .chip {
        font: inherit;
        font-size: .8rem;
        font-weight: 500;
        cursor: pointer;
        padding: 6px 13px;
        min-height: 32px;
        border-radius: var(--sp-radius-pill);
        border: 1px solid var(--sp-divider);
        background: transparent;
        color: var(--sp-fg);
        transition: background-color .15s ease, border-color .15s ease, color .15s ease;
      }
      .chip:hover { background: var(--sp-surface); }
      .chip.on {
        background: var(--sp-primary);
        border-color: var(--sp-primary);
        color: #fff;
      }
      .chip:focus-visible { outline: 2px solid var(--sp-primary); outline-offset: 2px; }
      .filters { margin-top: 8px; display: flex; gap: 6px; }
      .chip.filter { font-size: .76rem; }

      /* Summary */
      .stats {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(76px, 1fr));
        gap: 10px;
        margin-bottom: 14px;
      }
      .stat {
        background: var(--sp-surface);
        border-radius: var(--sp-radius-md);
        padding: 12px 8px;
        text-align: center;
      }
      .stat .num {
        display: block;
        font-size: 1.55rem;
        font-weight: 700;
        line-height: 1.05;
        letter-spacing: -0.01em;
        color: var(--sp-fg);
      }
      .stat .lbl {
        font-size: .7rem;
        color: var(--sp-fg-dim);
        text-transform: uppercase;
        letter-spacing: .06em;
        font-weight: 600;
      }
      .stat.warn .num { color: var(--error-color); }

      .rows { display: flex; flex-direction: column; }
      .row {
        display: flex;
        align-items: center;
        gap: 12px;
        width: 100%;
        text-align: left;
        cursor: pointer;
        font: inherit;
        background: none;
        border: none;
        padding: 10px 8px;
        border-bottom: 1px solid var(--sp-divider);
        border-radius: var(--sp-radius-sm);
        transition: background-color .15s ease;
      }
      .row:hover, .row:focus-visible {
        background: var(--sp-surface);
        outline: none;
      }
      .r-thumb {
        width: 48px;
        height: 48px;
        flex: 0 0 48px;
        border-radius: var(--sp-radius-sm);
        background-size: cover;
        background-position: center;
        background-color: var(--sp-surface);
        display: flex;
        align-items: center;
        justify-content: center;
      }
      .r-main { flex: 1; min-width: 0; }
      .r-name {
        font-weight: 600;
        color: var(--sp-fg);
        display: flex;
        align-items: center;
        gap: 8px;
        flex-wrap: wrap;
      }
      .r-sub {
        font-size: .8rem;
        color: var(--sp-fg-dim);
        margin: 2px 0 7px;
      }
      .r-bar {
        height: 5px;
        border-radius: var(--sp-radius-pill);
        background: var(--sp-divider);
        overflow: hidden;
      }
      .r-bar span {
        display: block;
        height: 100%;
        border-radius: var(--sp-radius-pill);
        transition: width .3s ease;
      }
      .r-chev {
        color: var(--sp-fg-dim);
        font-size: 1.3rem;
        line-height: 1;
      }
      .tag {
        font-size: .62rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .05em;
        border-radius: var(--sp-radius-pill);
        padding: 2px 8px;
        border: 1px solid currentColor;
      }
      .tag-warn { color: var(--warning-color, #ed6c02); }
      .tag-err { color: var(--error-color, #c62828); }
      .ini { font-size: 1.2rem; font-weight: 700; color: var(--sp-fg-dim); opacity: .55; }
      .ini.big { font-size: 2.4rem; }

      /* Grid */
      .grid-bar {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 10px;
        min-height: 36px;
      }
      .grid-bar .spacer { flex: 1; }
      .link {
        font: inherit;
        font-size: .86rem;
        font-weight: 600;
        background: none;
        border: none;
        cursor: pointer;
        color: var(--sp-primary);
        padding: 6px 10px;
        border-radius: var(--sp-radius-sm);
        transition: background-color .15s ease;
      }
      .link:hover { background: var(--sp-surface); }
      .link:focus-visible { outline: 2px solid var(--sp-primary); outline-offset: 2px; }
      .link.on { color: var(--sp-primary); background: color-mix(in srgb, var(--sp-primary) 10%, transparent); }
      .cols { font-size: .82rem; color: var(--sp-fg-dim); min-width: 3ch; text-align: center; }
      .btn-primary {
        font: inherit;
        font-weight: 600;
        cursor: pointer;
        border: none;
        background: var(--sp-primary);
        color: #fff;
        padding: 8px 16px;
        border-radius: var(--sp-radius-sm);
        transition: filter .15s ease;
      }
      .btn-primary:hover { filter: brightness(1.05); }
      .btn-primary:focus-visible { outline: 2px solid var(--sp-primary); outline-offset: 2px; }

      .grid {
        display: grid;
        gap: 10px;
      }
      .grid.arranging .tile { cursor: grab; touch-action: none; }
      .grid.arranging .tile.dragging { cursor: grabbing; box-shadow: var(--sp-elevation-strong); }

      .tile {
        position: relative;
        cursor: pointer;
        border-radius: var(--sp-radius-md);
        overflow: hidden;
        background: var(--sp-surface);
        border: 1px solid var(--sp-divider);
        transition: transform .14s cubic-bezier(.2,.7,.2,1), box-shadow .14s ease, border-color .14s ease;
      }
      .tile:hover, .tile:focus-visible {
        transform: translateY(-2px);
        box-shadow: var(--sp-elevation);
        outline: none;
        border-color: var(--sp-divider);
      }
      .tile:focus-visible { border-color: var(--sp-primary); }
      .tile.empty { opacity: .55; }
      .tile.expired { box-shadow: inset 0 0 0 2px var(--error-color); }

      .thumb {
        position: relative;
        aspect-ratio: 1/1;
        width: 100%;
        background-size: cover;
        background-position: center;
        display: flex;
        align-items: center;
        justify-content: center;
        background-color: var(--card-background-color);
      }
      .level {
        position: absolute;
        left: 0;
        bottom: 0;
        height: 6px;
        transition: width .3s ease;
      }
      .count {
        position: absolute;
        top: 6px;
        right: 6px;
        font-size: .7rem;
        font-weight: 700;
        line-height: 1;
        padding: 3px 7px;
        border-radius: var(--sp-radius-pill);
        background: rgba(0,0,0,.55);
        color: #fff;
      }
      .grip {
        position: absolute;
        top: 6px;
        left: 6px;
        font-size: .9rem;
        line-height: 1;
        color: #fff;
        background: rgba(0,0,0,.5);
        border-radius: 7px;
        padding: 2px 6px;
        letter-spacing: -2px;
      }
      .exp-flag {
        position: absolute;
        bottom: 10px;
        right: 6px;
        width: 18px;
        height: 18px;
        border-radius: 50%;
        font-size: .7rem;
        font-weight: 800;
        color: #fff;
        display: flex;
        align-items: center;
        justify-content: center;
        line-height: 1;
        box-shadow: 0 1px 3px rgba(0,0,0,.3);
      }
      .exp-flag.warn { background: var(--warning-color, #ed6c02); }
      .exp-flag.err { background: var(--error-color, #c62828); }

      .meta { padding: 9px 11px 11px; }
      .name {
        font-weight: 600;
        font-size: .93rem;
        color: var(--sp-fg);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .brand {
        font-size: .78rem;
        color: var(--sp-fg-dim);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .pct { font-size: .8rem; font-weight: 600; margin-top: 3px; }

      /* History view */
      .h-bar {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 8px;
        min-height: 30px;
      }
      .h-bar .spacer { flex: 1; }
      .h-count {
        font-size: .76rem;
        font-weight: 600;
        color: var(--sp-fg-dim);
        text-transform: uppercase;
        letter-spacing: .04em;
      }
      .h-list {
        list-style: none;
        padding: 0;
        margin: 0;
        max-height: 480px;
        overflow: auto;
      }
      .h-row {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px 6px;
        border-bottom: 1px solid var(--sp-divider);
      }
      .h-row:last-child { border-bottom: none; }
      .h-left { flex: 1; min-width: 0; }
      .h-name {
        font-weight: 600;
        color: var(--sp-fg);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .h-meta {
        font-size: .76rem;
        color: var(--sp-fg-dim);
        margin-top: 2px;
      }
      .h-right {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 2px;
        text-align: right;
      }
      .h-amount {
        font-weight: 700;
        color: var(--sp-fg);
      }
      .h-rem {
        font-size: .76rem;
        color: var(--sp-fg-dim);
      }

      .drag-ph {
        border: 2px dashed var(--sp-primary);
        border-radius: var(--sp-radius-md);
        background: color-mix(in srgb, var(--sp-primary) 10%, transparent);
      }

      .empty {
        text-align: center;
        color: var(--sp-fg-dim);
        padding: 32px 14px;
      }
      .empty code {
        background: var(--sp-surface);
        padding: 1px 6px;
        border-radius: 6px;
      }

      /* Overlays */
      .overlay {
        position: fixed;
        inset: 0;
        z-index: 9;
        display: none;
        align-items: center;
        justify-content: center;
        background: rgba(0,0,0,.5);
        backdrop-filter: blur(4px);
        -webkit-backdrop-filter: blur(4px);
        animation: sp-fade .15s ease;
      }
      .overlay.open { display: flex; }
      .sheet {
        width: min(440px, 92vw);
        max-height: 88vh;
        overflow: auto;
        background: var(--sp-surface-2);
        color: var(--sp-fg);
        border-radius: 20px;
        padding: 20px;
        box-shadow: var(--sp-elevation-strong);
        animation: sp-pop .18s cubic-bezier(.2,.8,.2,1);
      }
      .sheet .d-thumb {
        width: 100%;
        aspect-ratio: 16/9;
        border-radius: var(--sp-radius-md);
        background-size: cover;
        background-position: center;
        background-color: var(--sp-surface);
        display: flex;
        align-items: center;
        justify-content: center;
        margin-bottom: 14px;
      }
      .sheet h2 {
        margin: 0 0 2px;
        font-size: 1.22rem;
        font-weight: 600;
        letter-spacing: -0.01em;
      }
      .sheet .d-brand {
        color: var(--sp-fg-dim);
        margin-bottom: 14px;
      }
      .facts {
        display: grid;
        grid-template-columns: auto 1fr;
        gap: 6px 14px;
        font-size: .88rem;
        margin-bottom: 18px;
      }
      .facts dt { color: var(--sp-fg-dim); }
      .facts dd { margin: 0; text-align: right; }
      .facts dd .hint { color: var(--sp-fg-dim); font-weight: 400; }

      .actions {
        display: grid;
        grid-template-columns: repeat(4, 1fr);
        gap: 8px;
      }
      button.act {
        font: inherit;
        cursor: pointer;
        padding: 10px 0;
        border-radius: var(--sp-radius-sm);
        border: 1px solid var(--sp-divider);
        background: var(--sp-surface);
        color: var(--sp-fg);
        font-weight: 600;
        transition: border-color .15s ease, background-color .15s ease;
      }
      button.act:hover { border-color: var(--sp-primary); background: var(--sp-surface-2); }
      button.act:focus-visible { outline: 2px solid var(--sp-primary); outline-offset: 2px; }
      button.act.danger { color: var(--error-color); }

      .custom-row {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-top: 14px;
      }
      .custom-row input[type=range] {
        flex: 1;
        accent-color: var(--sp-primary);
      }
      .custom-row .val {
        width: 3ch;
        text-align: right;
        font-weight: 600;
      }
      button.apply {
        font: inherit;
        cursor: pointer;
        padding: 9px 16px;
        border-radius: var(--sp-radius-sm);
        border: none;
        background: var(--sp-primary);
        color: #fff;
        font-weight: 600;
      }
      button.apply:focus-visible { outline: 2px solid var(--sp-primary); outline-offset: 2px; }

      .hist {
        margin-top: 16px;
        border-top: 1px solid var(--sp-divider);
        padding-top: 12px;
      }
      .hist-title {
        font-size: .72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .06em;
        color: var(--sp-fg-dim);
        margin-bottom: 6px;
      }
      .hist ul { list-style: none; padding: 0; margin: 0; }
      .hist li {
        display: flex;
        justify-content: space-between;
        font-size: .85rem;
        padding: 4px 0;
        color: var(--sp-fg);
      }
      .hist-when { color: var(--sp-fg-dim); }

      .quiet-row {
        display: flex;
        gap: 4px;
        margin-top: 14px;
        padding-top: 12px;
        border-top: 1px solid var(--sp-divider);
        flex-wrap: wrap;
      }

      .close-row {
        display: flex;
        justify-content: space-between;
        margin-top: 16px;
        gap: 10px;
      }
      button.close {
        font: inherit;
        cursor: pointer;
        background: none;
        border: none;
        color: var(--sp-fg-dim);
        padding: 8px 12px;
        border-radius: var(--sp-radius-sm);
      }
      button.close:hover { background: var(--sp-surface); }

      /* Add form */
      .add-sheet { width: min(440px, 92vw); }
      .add-form { display: grid; gap: 12px; margin-top: 4px; }
      .add-form label {
        display: grid;
        gap: 4px;
        font-size: .78rem;
        font-weight: 600;
        color: var(--sp-fg-dim);
        text-transform: uppercase;
        letter-spacing: .04em;
      }
      .add-form input, .add-form select {
        font: inherit;
        font-size: .95rem;
        font-weight: 400;
        color: var(--sp-fg);
        text-transform: none;
        letter-spacing: normal;
        background: var(--sp-surface);
        border: 1px solid var(--sp-divider);
        border-radius: var(--sp-radius-sm);
        padding: 9px 10px;
        outline: none;
        transition: border-color .15s ease, box-shadow .15s ease;
      }
      .add-form input:focus, .add-form select:focus {
        border-color: var(--sp-primary);
        box-shadow: 0 0 0 3px color-mix(in srgb, var(--sp-primary) 25%, transparent);
      }
      .row-inline { display: flex; align-items: center; gap: 8px; }
      .row-inline .hint { font-size: .8rem; color: var(--sp-fg-dim); }
      .add-error {
        color: var(--error-color);
        font-size: .85rem;
        font-weight: 500;
      }

      @keyframes sp-fade { from { opacity: 0 } to { opacity: 1 } }
      @keyframes sp-pop  { from { transform: translateY(8px) scale(.98); opacity: 0 } to { transform: none; opacity: 1 } }

      /* Trends view */
      .tr-head {
        display: grid;
        grid-template-columns: 1fr 5rem 113px;
        gap: 8px;
        align-items: center;
        padding: 4px 6px 8px;
        border-bottom: 1px solid var(--sp-divider);
        font-size: .72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .05em;
        color: var(--sp-fg-dim);
      }
      .tr-h-spark {
        display: flex;
        justify-content: space-between;
        font-size: .65rem;
      }
      .tr-list { display: flex; flex-direction: column; }
      .tr-row {
        display: grid;
        grid-template-columns: 1fr 5rem 113px;
        gap: 8px;
        align-items: center;
        padding: 8px 6px;
        border-bottom: 1px solid var(--sp-divider);
      }
      .tr-row:last-child { border-bottom: none; }
      .tr-name {
        font-weight: 600;
        font-size: .9rem;
        color: var(--sp-fg);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }
      .tr-total {
        font-size: .85rem;
        font-weight: 600;
        color: var(--sp-fg);
        text-align: right;
        white-space: nowrap;
      }
      .tr-unit { font-weight: 400; color: var(--sp-fg-dim); }
      .tr-spark { display: flex; align-items: flex-end; }
      .tr-foot {
        display: flex;
        justify-content: flex-end;
        padding: 6px 0 0;
      }

      /* Floor plan / map mode */
      .map-hint {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 10px;
        padding: 40px 20px;
        color: var(--sp-fg-dim);
        text-align: center;
        font-size: .9rem;
      }
      .map-hint-icon { font-size: 2.4rem; }

      .map-canvas {
        position: relative;
        width: 100%;
        overflow: hidden;
        border-radius: var(--sp-radius-md);
        border: 1px solid var(--sp-divider);
        background: var(--sp-surface);
      }
      .map-bg {
        position: absolute;
        inset: 0;
        pointer-events: none;
        color: var(--sp-fg-dim);
      }
      .map-bg svg { width: 100%; height: 100%; display: block; }

      .map-tile {
        position: absolute;
        width: 58px;
        transform: translate(-50%, -50%);
        cursor: pointer;
        border-radius: var(--sp-radius-sm);
        overflow: hidden;
        background: var(--sp-surface-2);
        border: 1px solid var(--sp-divider);
        box-shadow: var(--sp-elevation);
        transition: transform .12s ease, box-shadow .12s ease;
        z-index: 2;
        user-select: none;
      }
      .map-tile:hover { transform: translate(-50%,-50%) scale(1.08); box-shadow: var(--sp-elevation-strong); }
      .map-tile:focus-visible { outline: 2px solid var(--sp-primary); outline-offset: 2px; }
      .map-tile.staged { position: static; transform: none; flex: 0 0 58px; }
      .map-tile.staged:hover { transform: scale(1.05); }
      .map-tile-thumb {
        width: 100%;
        aspect-ratio: 1/1;
        background-size: cover;
        background-position: center;
        display: flex;
        align-items: center;
        justify-content: center;
        position: relative;
        background-color: var(--card-background-color);
      }
      .map-level {
        position: absolute;
        bottom: 0;
        left: 0;
        height: 4px;
      }
      .map-tile-pct {
        font-size: .7rem;
        font-weight: 700;
        text-align: center;
        padding: 2px 0;
        line-height: 1;
      }
      .map-ghost {
        position: fixed;
        width: 58px !important;
        z-index: 9999;
        opacity: .85;
        pointer-events: none;
        border-radius: var(--sp-radius-sm);
        overflow: hidden;
        background: var(--sp-surface-2);
        border: 2px solid var(--sp-primary);
        box-shadow: var(--sp-elevation-strong);
      }

      .map-tray {
        margin-top: 12px;
        border-top: 1px dashed var(--sp-divider);
        padding-top: 10px;
      }
      .map-tray-label {
        font-size: .7rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .05em;
        color: var(--sp-fg-dim);
        margin-bottom: 8px;
      }
      .map-tray-items {
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
      }

      /* Template setup */
      .tmpl-setup {
        padding: 4px 0;
      }
      .tmpl-setup-title {
        font-size: .9rem;
        color: var(--sp-fg-dim);
        margin-bottom: 14px;
      }
      .tmpl-thumbs {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        margin-bottom: 16px;
      }
      .tmpl-thumb {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 6px;
        padding: 10px 12px;
        border: 2px solid var(--sp-divider);
        border-radius: var(--sp-radius-md);
        background: var(--sp-surface);
        cursor: pointer;
        font: inherit;
        color: var(--sp-fg);
        transition: border-color .15s ease;
        width: 100px;
      }
      .tmpl-thumb:hover { border-color: var(--sp-primary); }
      .tmpl-thumb.on { border-color: var(--sp-primary); background: color-mix(in srgb, var(--sp-primary) 8%, transparent); }
      .tmpl-svg {
        width: 70px;
        height: 54px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: var(--sp-fg-dim);
      }
      .tmpl-svg svg { max-width: 70px; max-height: 54px; }
      .tmpl-label { font-size: .7rem; font-weight: 600; text-align: center; color: var(--sp-fg-dim); }
      .tmpl-config {
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        margin-bottom: 14px;
      }
      .tmpl-field {
        display: grid;
        gap: 4px;
        font-size: .76rem;
        font-weight: 600;
        color: var(--sp-fg-dim);
        text-transform: uppercase;
        letter-spacing: .04em;
      }
      .tmpl-field input {
        font: inherit;
        font-size: .9rem;
        font-weight: 400;
        text-transform: none;
        letter-spacing: normal;
        color: var(--sp-fg);
        background: var(--sp-surface);
        border: 1px solid var(--sp-divider);
        border-radius: var(--sp-radius-sm);
        padding: 7px 10px;
        width: 70px;
        outline: none;
      }
      .tmpl-field input:focus { border-color: var(--sp-primary); }
      .tmpl-bool {
        display: flex;
        align-items: center;
        gap: 8px;
        font-size: .88rem;
        font-weight: 600;
        color: var(--sp-fg);
        cursor: pointer;
        align-self: flex-end;
        padding-bottom: 8px;
      }
      .tmpl-bool input { width: 18px; height: 18px; accent-color: var(--sp-primary); }
      .tmpl-apply-row { display: flex; justify-content: flex-end; }

      /* Receipt overlay */
      .rx-overlay {
        position: fixed;
        inset: 0;
        z-index: 13;
        display: none;
        align-items: center;
        justify-content: center;
        background: rgba(0,0,0,.55);
        backdrop-filter: blur(4px);
        -webkit-backdrop-filter: blur(4px);
        animation: sp-fade .15s ease;
      }
      .rx-overlay.open { display: flex; }
      .rx-sheet {
        background: var(--sp-surface-2);
        color: var(--sp-fg);
        border-radius: 20px;
        padding: 20px;
        box-shadow: var(--sp-elevation-strong);
        animation: sp-pop .18s cubic-bezier(.2,.8,.2,1);
        width: min(520px, 94vw);
        max-height: 88vh;
        overflow: hidden;
        display: flex;
        flex-direction: column;
        gap: 12px;
      }
      .rx-sheet h2 { margin: 0; font-size: 1.12rem; font-weight: 700; }
      .rx-hint { margin: 0; font-size: .82rem; color: var(--sp-fg-dim); line-height: 1.5; }
      .rx-ta {
        font: inherit;
        font-size: .88rem;
        resize: vertical;
        background: var(--sp-surface);
        border: 1px solid var(--sp-divider);
        border-radius: var(--sp-radius-sm);
        color: var(--sp-fg);
        padding: 10px;
        outline: none;
        transition: border-color .15s ease;
      }
      .rx-ta:focus { border-color: var(--sp-primary); }
      .rx-foot {
        display: flex;
        justify-content: flex-end;
        gap: 8px;
        margin-top: 4px;
      }
      .rx-err { font-size: .82rem; color: var(--error-color); }

      .rx-review { max-height: 88vh; }
      .rx-rev-head {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .rx-list {
        flex: 1;
        overflow-y: auto;
        display: flex;
        flex-direction: column;
        gap: 6px;
        padding-right: 2px;
      }
      .rx-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 10px;
        background: var(--sp-surface);
        border-radius: var(--sp-radius-sm);
        border: 1px solid var(--sp-divider);
      }
      .rx-cb { flex: 0 0 18px; width: 18px; height: 18px; accent-color: var(--sp-primary); cursor: pointer; }
      .rx-item-info { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
      .rx-name {
        font: inherit;
        font-size: .9rem;
        font-weight: 600;
        background: none;
        border: none;
        border-bottom: 1px solid transparent;
        color: var(--sp-fg);
        padding: 0;
        width: 100%;
        outline: none;
        transition: border-color .15s;
      }
      .rx-name:focus { border-bottom-color: var(--sp-primary); }
      .rx-item-sub { display: flex; align-items: center; gap: 6px; }
      .rx-badge {
        font-size: .62rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .05em;
        padding: 1px 6px;
        border-radius: var(--sp-radius-pill);
        border: 1px solid currentColor;
      }
      .rx-badge.match { color: var(--success-color, #2e7d32); }
      .rx-badge.new { color: var(--sp-fg-dim); }
      .rx-brand { font-size: .72rem; color: var(--sp-fg-dim); }
      .rx-item-nums { display: flex; gap: 6px; flex: 0 0 auto; }
      .rx-qty, .rx-unit {
        font: inherit;
        font-size: .82rem;
        background: var(--sp-surface-2);
        border: 1px solid var(--sp-divider);
        border-radius: 6px;
        color: var(--sp-fg);
        padding: 4px 6px;
        outline: none;
        transition: border-color .15s;
      }
      .rx-qty { width: 58px; }
      .rx-unit { width: 54px; }
      .rx-qty:focus, .rx-unit:focus { border-color: var(--sp-primary); }
      .rx-rev-foot { display: flex; flex-direction: column; gap: 8px; }
      .rx-loc-label {
        display: grid;
        gap: 4px;
        font-size: .72rem;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .04em;
        color: var(--sp-fg-dim);
      }
      .rx-loc {
        font: inherit;
        font-size: .9rem;
        background: var(--sp-surface);
        border: 1px solid var(--sp-divider);
        border-radius: var(--sp-radius-sm);
        color: var(--sp-fg);
        padding: 8px 10px;
        outline: none;
      }

      /* QR overlay */
      .qr-overlay {
        position: fixed;
        inset: 0;
        z-index: 12;
        display: none;
        align-items: center;
        justify-content: center;
        background: rgba(0,0,0,.55);
        backdrop-filter: blur(4px);
        -webkit-backdrop-filter: blur(4px);
        animation: sp-fade .15s ease;
      }
      .qr-overlay.open { display: flex; }
      .qr-sheet {
        background: var(--sp-surface-2);
        color: var(--sp-fg);
        border-radius: 20px;
        padding: 24px 20px 20px;
        box-shadow: var(--sp-elevation-strong);
        animation: sp-pop .18s cubic-bezier(.2,.8,.2,1);
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 10px;
        width: min(320px, 88vw);
      }
      .qr-title {
        font-size: 1rem;
        font-weight: 700;
        letter-spacing: -.01em;
        color: var(--sp-fg);
      }
      .qr-pkg-name {
        font-size: .85rem;
        color: var(--sp-fg-dim);
        text-align: center;
      }
      .qr-img {
        width: 200px;
        height: 200px;
        border-radius: 12px;
        background: #fff;
        padding: 8px;
      }
      .qr-hint {
        font-size: .78rem;
        color: var(--sp-fg-dim);
        text-align: center;
        max-width: 240px;
        line-height: 1.5;
      }
      .qr-id-row { margin-top: -4px; }
      .qr-id {
        font-size: .72rem;
        background: var(--sp-surface);
        padding: 2px 8px;
        border-radius: 6px;
        color: var(--sp-fg-dim);
      }

      @media (prefers-reduced-motion: reduce) {
        .tile, .level, .r-bar span, .overlay, .sheet { transition: none; animation: none; }
      }
    `;
  }
}

class StockpileSummaryCard extends StockpileCard {
  get _defaultView() {
    return "summary";
  }
  static getConfigElement() {
    return document.createElement("stockpile-summary-card-editor");
  }
}

// ---------------------------------------------------------------------- //
// Visual editor (vanilla custom element, no Lit dependency)
// ---------------------------------------------------------------------- //
class StockpileCardEditorBase extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._locations = [];
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = { ...config };
    this._render();
  }

  set hass(hass) {
    if (!hass) return;
    this._hass = hass;
    if (!this._locsLoaded) {
      this._locsLoaded = true;
      hass.connection
        .sendMessagePromise({ type: "stockpile/locations" })
        .then((r) => { this._locations = r.locations || []; this._render(); })
        .catch(() => {});
    }
  }

  _typeName() { return "stockpile-card"; }

  _emit(patch) {
    this._config = { ...this._config, ...patch };
    Object.keys(this._config).forEach((k) => {
      const v = this._config[k];
      if (v === "" || v == null) delete this._config[k];
    });
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail: { config: { type: `custom:${this._typeName()}`, ...this._config } },
      bubbles: true,
      composed: true,
    }));
  }

  _render() {
    const c = this._config;
    const isGrid = this._typeName() === "stockpile-card";
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; font-family: var(--ha-font-family-body, "Roboto", sans-serif); }
        .ed { display: grid; gap: 12px; }
        label { display: grid; gap: 4px; font-size: .78rem; font-weight: 600;
                color: var(--secondary-text-color); text-transform: uppercase; letter-spacing: .04em; }
        input, select {
          font: inherit; font-size: .95rem; font-weight: 400; text-transform: none; letter-spacing: normal;
          color: var(--primary-text-color);
          background: var(--secondary-background-color);
          border: 1px solid var(--divider-color, rgba(127,127,127,.18));
          border-radius: 10px; padding: 9px 10px; outline: none;
        }
        input:focus, select:focus {
          border-color: var(--primary-color);
          box-shadow: 0 0 0 3px color-mix(in srgb, var(--primary-color) 25%, transparent);
        }
        .checkbox { display: flex; align-items: center; gap: 8px;
                    font-weight: 600; color: var(--primary-text-color);
                    text-transform: none; letter-spacing: normal; font-size: .92rem; }
        .checkbox input { width: 18px; height: 18px; }
        .hint { font-size: .78rem; color: var(--secondary-text-color);
                font-weight: 400; text-transform: none; letter-spacing: normal; }
      </style>
      <div class="ed">
        <label>Title
          <input data-key="title" type="text" value="${this._esc(c.title || "")}" placeholder="Stockpile" />
        </label>
        <label>Location
          <select data-key="location_id">
            <option value="">All locations</option>
            ${this._locations.map((l) =>
              `<option value="${l.id}" ${c.location_id === l.id ? "selected" : ""}>${this._esc(l.name)}</option>`
            ).join("")}
          </select>
          <span class="hint">Pin the card to one location; hides the picker.</span>
        </label>
        ${isGrid ? `
          <label>Columns
            <input data-key="columns" type="number" min="1" max="20" value="${c.columns || ""}" placeholder="auto" />
            <span class="hint">Fixed column count. Leave empty for responsive auto layout.</span>
          </label>
          <label>Minimum tile width (px)
            <input data-key="min_tile" type="number" min="80" step="10" value="${c.min_tile || ""}" placeholder="150" />
          </label>
        ` : ""}
        <label class="checkbox">
          <input data-key="show_expiring" type="checkbox" ${c.show_expiring !== false ? "checked" : ""} />
          Highlight expiring items
        </label>
      </div>
    `;

    this.shadowRoot.querySelectorAll("[data-key]").forEach((el) => {
      el.addEventListener("change", () => {
        const key = el.dataset.key;
        let v;
        if (el.type === "checkbox") v = el.checked;
        else if (el.type === "number") v = el.value === "" ? "" : Number(el.value);
        else v = el.value;
        this._emit({ [key]: v });
      });
      if (el.type === "text") {
        el.addEventListener("input", () => this._emit({ [el.dataset.key]: el.value }));
      }
    });
  }

  _esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }
}

class StockpileCardEditor extends StockpileCardEditorBase {
  _typeName() { return "stockpile-card"; }
}
class StockpileSummaryCardEditor extends StockpileCardEditorBase {
  _typeName() { return "stockpile-summary-card"; }
}

customElements.define("stockpile-card", StockpileCard);
customElements.define("stockpile-summary-card", StockpileSummaryCard);
customElements.define("stockpile-card-editor", StockpileCardEditor);
customElements.define("stockpile-summary-card-editor", StockpileSummaryCardEditor);

window.customCards = window.customCards || [];
window.customCards.push(
  {
    type: "stockpile-summary-card",
    name: "Stockpile Summary",
    description: "Aggregated inventory overview with a location picker.",
    preview: false,
    documentationURL: "https://github.com/davbebawwy/stockpile",
  },
  {
    type: "stockpile-card",
    name: "Stockpile Grid",
    description: "Visual package grid with quick consume actions and drag-to-arrange.",
    preview: false,
    documentationURL: "https://github.com/davbebawwy/stockpile",
  }
);

console.info(
  `%c STOCKPILE %c ${VERSION} `,
  "background:#1f6feb;color:#fff;padding:2px 6px;border-radius:4px 0 0 4px;font-weight:600",
  "background:#1e293b;color:#fff;padding:2px 6px;border-radius:0 4px 4px 0"
);
