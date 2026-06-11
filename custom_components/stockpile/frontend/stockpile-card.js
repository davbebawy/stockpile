/*
 * Stockpile cards for Home Assistant.
 *
 * Registers two card types (same code, different default view):
 *   custom:stockpile-summary-card   modern aggregated overview (default)
 *   custom:stockpile-card           detailed package grid (default)
 *
 * Both share a header with a Summary <-> Items toggle and a location picker,
 * so either is an entry point and you can switch live. Tapping a product in
 * the summary drills into that product's packages in the grid.
 *
 * Talks to the Stockpile integration over WebSocket (stockpile/packages,
 * stockpile/locations, stockpile/subscribe) and calls stockpile.consume /
 * remove_package / reorder. No external dependencies.
 *
 * Config:
 *   type: custom:stockpile-summary-card
 *   title: Kitchen
 *   location_id: loc_xxxxxxxx   # optional: pin to one spot, hides the picker
 *   columns: 10                 # optional (grid view): fixed column count
 *   min_tile: 150               # optional (grid view): min tile width px
 */

const STATUS_VAR = {
  full: "var(--success-color, #43a047)",
  medium: "var(--warning-color, #ffa600)",
  low: "var(--error-color, #db4437)",
  empty: "var(--disabled-text-color, #9e9e9e)",
};
const VERSION = "0.2.0";

class StockpileCard extends HTMLElement {
  get _defaultView() {
    return "grid";
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._packages = [];
    this._locations = [];
    this._started = false;
    this._arrange = false;
    this._selected = null;
    this._productFilter = null;
    this._dragId = null;
  }

  setConfig(config) {
    this._config = { min_tile: 150, ...config };
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
  static getStubConfig() {
    return { title: "Stockpile" };
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
    if (this._arrange) return; // don't yank the grid out from under a drag
    try {
      const msg = { type: "stockpile/packages" };
      if (this._locationFilter) msg.location_id = this._locationFilter;
      const res = await this._hass.connection.sendMessagePromise(msg);
      this._packages = res.packages || [];
      this._render();
      if (this._selected) this._refreshDetail();
    } catch (e) {
      this._renderError(e);
    }
  }

  disconnectedCallback() {
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
      <div class="overlay"><div class="sheet"></div></div>
    `;
    this._head = this.shadowRoot.querySelector(".head");
    this._body = this.shadowRoot.querySelector(".body");
    this._overlay = this.shadowRoot.querySelector(".overlay");
    this._sheet = this.shadowRoot.querySelector(".sheet");
    this._overlay.addEventListener("click", (e) => {
      if (e.target === this._overlay) this._closeDetail();
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this._selected) this._closeDetail();
    });
  }

  // ------------------------------------------------------------------ //
  // render
  // ------------------------------------------------------------------ //
  _render() {
    this._renderHead();
    if (this._view === "summary") this._renderSummary();
    else this._renderGrid();
  }

  _renderHead() {
    const title = this._config.title || "";
    const chips = this._locked
      ? ""
      : `<div class="chips">
           <button class="chip ${!this._locationFilter ? "on" : ""}" data-loc="">All</button>
           ${this._locations
             .map(
               (l) =>
                 `<button class="chip ${this._locationFilter === l.id ? "on" : ""}" data-loc="${l.id}">${this._esc(l.name)}</button>`
             )
             .join("")}
         </div>`;

    this._head.innerHTML = `
      <div class="head-row">
        <div class="title">${this._esc(title)}</div>
        <div class="seg">
          <button class="seg-btn ${this._view === "summary" ? "on" : ""}" data-view="summary">Summary</button>
          <button class="seg-btn ${this._view === "grid" ? "on" : ""}" data-view="grid">Items</button>
        </div>
      </div>
      ${chips}
    `;

    this._head.querySelectorAll("[data-view]").forEach((b) =>
      b.addEventListener("click", () => {
        this._view = b.dataset.view;
        this._productFilter = null;
        this._arrange = false;
        this._render();
      })
    );
    this._head.querySelectorAll("[data-loc]").forEach((b) =>
      b.addEventListener("click", () => {
        this._locationFilter = b.dataset.loc || null;
        this._productFilter = null;
        this._fetch();
      })
    );
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
      });
      g.count += 1;
      g.sumRemaining += p.remaining;
      g.qtyRemaining += (p.quantity || 1) * (p.remaining / 100);
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
    const groups = this._aggregate(this._packages);
    if (!groups.length) {
      this._body.innerHTML = this._empty();
      return;
    }
    const lowCount = groups.filter((g) => g.low).length;
    const totalPkgs = this._packages.length;

    this._body.innerHTML = `
      <div class="stats">
        <div class="stat"><span class="num">${groups.length}</span><span class="lbl">products</span></div>
        <div class="stat"><span class="num">${totalPkgs}</span><span class="lbl">packages</span></div>
        <div class="stat ${lowCount ? "warn" : ""}"><span class="num">${lowCount}</span><span class="lbl">low</span></div>
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
            return `
              <button class="row" data-product="${g.product_id}">
                <div class="r-thumb" ${thumb}>${initial}</div>
                <div class="r-main">
                  <div class="r-name">${this._esc(g.name)} ${g.low ? '<span class="tag">restock</span>' : ""}</div>
                  <div class="r-sub">${this._esc(g.brand || "")}${g.brand ? " · " : ""}${g.count} package${g.count !== 1 ? "s" : ""}${qty}</div>
                  <div class="r-bar"><span style="width:${Math.round(g.avg)}%;background:${color}"></span></div>
                </div>
                <div class="r-chev">›</div>
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
    let pkgs = this._packages;
    let backBtn = "";
    if (this._productFilter) {
      pkgs = pkgs.filter((p) => p.product_id === this._productFilter);
      backBtn = `<button class="link" data-back="1">‹ All items</button>`;
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
            ? `<button class="link" data-cols="-1">− cols</button>
               <span class="cols">${this._columns || "auto"}</span>
               <button class="link" data-cols="1">+ cols</button>
               <button class="btn-primary" data-arrange="0">Done</button>`
            : `<button class="link" data-arrange="1">Arrange</button>`
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
      this._wireDrag(grid);
    }
  }

  _tile(p, counts) {
    const color = STATUS_VAR[p.status] || STATUS_VAR.medium;
    const pct = Math.round(p.remaining);
    const img = p.image ? `style="background-image:url('${this._esc(p.image)}')"` : "";
    const initial = p.image ? "" : `<span class="ini">${this._esc((p.product_name || "?")[0].toUpperCase())}</span>`;
    const badge = counts[p.product_id] > 1 ? `<span class="count">${counts[p.product_id]}</span>` : "";
    const grip = this._arrange ? `<span class="grip">⠿</span>` : "";
    return `
      <div class="tile ${p.status}" data-id="${p.id}" ${this._arrange ? 'draggable="true"' : 'tabindex="0" role="button"'}
           aria-label="${this._esc(p.product_name)}, ${pct}%">
        <div class="thumb" ${img}>
          ${initial}${badge}${grip}
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
  }

  // ----- drag to arrange -------------------------------------------- //
  _wireDrag(grid) {
    grid.addEventListener("dragstart", (e) => {
      const t = e.target.closest(".tile");
      if (!t) return;
      this._dragId = t.dataset.id;
      t.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });
    grid.addEventListener("dragend", (e) => {
      const t = e.target.closest(".tile");
      if (t) t.classList.remove("dragging");
      this._persistOrder(grid);
    });
    grid.addEventListener("dragover", (e) => {
      e.preventDefault();
      const dragging = grid.querySelector(".dragging");
      if (!dragging) return;
      const after = this._tileAfter(grid, e.clientX, e.clientY);
      if (after == null) grid.appendChild(dragging);
      else grid.insertBefore(dragging, after);
    });
  }

  _tileAfter(grid, x, y) {
    const tiles = [...grid.querySelectorAll(".tile:not(.dragging)")];
    let closest = { dist: Infinity, el: null };
    for (const el of tiles) {
      const box = el.getBoundingClientRect();
      const cx = box.left + box.width / 2;
      const cy = box.top + box.height / 2;
      // place before the first tile whose center is to the right/below the cursor
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

  // ----- detail overlay --------------------------------------------- //
  _openDetail(id) {
    this._selected = id;
    this._refreshDetail();
    this._overlay.classList.add("open");
  }
  _closeDetail() {
    this._selected = null;
    this._overlay.classList.remove("open");
  }
  _refreshDetail() {
    const p = this._packages.find((x) => x.id === this._selected);
    if (!p) { this._closeDetail(); return; }
    const color = STATUS_VAR[p.status] || STATUS_VAR.medium;
    const pct = Math.round(p.remaining);
    const img = p.image ? `style="background-image:url('${this._esc(p.image)}')"` : "";
    const initial = p.image ? "" : `<span class="ini big">${this._esc((p.product_name || "?")[0].toUpperCase())}</span>`;
    const d = (s) => (s ? s.slice(0, 10) : "—");

    this._sheet.innerHTML = `
      <div class="d-thumb" ${img}>${initial}</div>
      <h2>${this._esc(p.product_name)}</h2>
      <div class="d-brand">${this._esc(p.brand || "")}</div>
      <dl class="facts">
        <dt>Remaining</dt><dd style="color:${color};font-weight:600">${pct}%</dd>
        <dt>Quantity</dt><dd>${this._esc(String(p.quantity))}${p.unit ? " " + this._esc(p.unit) : ""}</dd>
        <dt>Location</dt><dd>${this._esc(p.location_name || "—")}</dd>
        <dt>Added</dt><dd>${d(p.added)}</dd>
        ${p.expires ? `<dt>Expires</dt><dd>${d(p.expires)}</dd>` : ""}
      </dl>
      <div class="actions">
        <button class="act" data-use="10">Use 10%</button>
        <button class="act" data-use="25">Use 25%</button>
        <button class="act" data-use="50">Use 50%</button>
        <button class="act" data-use="100">Use all</button>
      </div>
      <div class="custom-row">
        <input type="range" min="1" max="100" value="20" />
        <span class="val">20</span>
        <button class="apply">Use</button>
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
  }

  async _consume(id, amount) {
    try {
      await this._hass.callService("stockpile", "consume", { package_id: id, amount });
      await this._fetch();
    } catch (e) { console.error("stockpile: consume failed", e); }
  }
  async _remove(id) {
    try {
      await this._hass.callService("stockpile", "remove_package", { package_id: id });
      this._closeDetail();
      await this._fetch();
    } catch (e) { console.error("stockpile: remove failed", e); }
  }

  // ------------------------------------------------------------------ //
  _empty() {
    return `<div class="empty">Nothing here yet. Add a package, or call <code>stockpile.seed_demo</code> for test data.</div>`;
  }
  _renderError(e) {
    if (this._body) this._body.innerHTML = `<div class="empty">Couldn't load Stockpile (${this._esc(String(e))}).</div>`;
  }
  _round(n) { return Math.round(n * 10) / 10; }
  _esc(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  _css() {
    return `
      ha-card { padding: 14px 14px 18px; }
      .head { margin-bottom: 12px; }
      .head-row { display:flex; align-items:center; gap:12px; }
      .title { font-size:1.15rem; font-weight:700; flex:1; color:var(--primary-text-color); }
      .seg { display:inline-flex; background:var(--secondary-background-color); border-radius:999px; padding:3px; }
      .seg-btn { font:inherit; font-size:.82rem; font-weight:600; border:none; background:none; cursor:pointer;
                 color:var(--secondary-text-color); padding:5px 12px; border-radius:999px; }
      .seg-btn.on { background:var(--card-background-color); color:var(--primary-text-color); box-shadow:0 1px 3px rgba(0,0,0,.18); }
      .chips { display:flex; flex-wrap:wrap; gap:6px; margin-top:12px; }
      .chip { font:inherit; font-size:.8rem; cursor:pointer; padding:5px 12px; border-radius:999px;
              border:1px solid var(--divider-color, rgba(0,0,0,.12)); background:transparent; color:var(--primary-text-color); }
      .chip.on { background:var(--primary-color); border-color:var(--primary-color); color:#fff; }

      /* summary */
      .stats { display:flex; gap:10px; margin-bottom:14px; }
      .stat { flex:1; background:var(--secondary-background-color); border-radius:14px; padding:12px; text-align:center; }
      .stat .num { display:block; font-size:1.6rem; font-weight:700; line-height:1; color:var(--primary-text-color); }
      .stat .lbl { font-size:.72rem; color:var(--secondary-text-color); text-transform:uppercase; letter-spacing:.04em; }
      .stat.warn .num { color:var(--error-color); }
      .rows { display:flex; flex-direction:column; }
      .row { display:flex; align-items:center; gap:12px; width:100%; text-align:left; cursor:pointer;
             font:inherit; background:none; border:none; padding:10px 6px; border-bottom:1px solid var(--divider-color, rgba(0,0,0,.08)); }
      .row:hover { background:var(--secondary-background-color); border-radius:10px; }
      .r-thumb { width:46px; height:46px; flex:0 0 46px; border-radius:11px; background-size:cover; background-position:center;
                 background-color:var(--secondary-background-color); display:flex; align-items:center; justify-content:center; }
      .r-main { flex:1; min-width:0; }
      .r-name { font-weight:600; color:var(--primary-text-color); display:flex; align-items:center; gap:8px; }
      .r-sub { font-size:.8rem; color:var(--secondary-text-color); margin:1px 0 6px; }
      .r-bar { height:5px; border-radius:999px; background:var(--divider-color, rgba(0,0,0,.1)); overflow:hidden; }
      .r-bar span { display:block; height:100%; border-radius:999px; }
      .r-chev { color:var(--secondary-text-color); font-size:1.3rem; }
      .tag { font-size:.62rem; font-weight:700; text-transform:uppercase; letter-spacing:.04em;
             color:var(--error-color); border:1px solid var(--error-color); border-radius:999px; padding:1px 7px; }
      .ini { font-size:1.2rem; font-weight:700; color:var(--secondary-text-color); opacity:.6; }
      .ini.big { font-size:2.4rem; }

      /* grid */
      .grid-bar { display:flex; align-items:center; gap:10px; margin-bottom:10px; min-height:30px; }
      .grid-bar .spacer { flex:1; }
      .link { font:inherit; font-size:.85rem; font-weight:600; background:none; border:none; cursor:pointer; color:var(--primary-color); padding:4px 6px; }
      .cols { font-size:.82rem; color:var(--secondary-text-color); min-width:3ch; text-align:center; }
      .btn-primary { font:inherit; font-weight:600; cursor:pointer; border:none; background:var(--primary-color); color:#fff; padding:6px 14px; border-radius:9px; }
      .grid { display:grid; gap:10px; }
      .grid.arranging .tile { cursor:grab; }
      .tile { position:relative; cursor:pointer; border-radius:12px; overflow:hidden;
              background:var(--secondary-background-color); border:1px solid var(--divider-color, rgba(0,0,0,.12));
              transition:transform .12s ease, box-shadow .12s ease; }
      .tile:hover, .tile:focus-visible { transform:translateY(-2px); box-shadow:0 4px 14px rgba(0,0,0,.18); outline:none; }
      .tile:focus-visible { border-color:var(--primary-color); }
      .tile.empty { opacity:.55; }
      .tile.dragging { opacity:.4; }
      .thumb { position:relative; aspect-ratio:1/1; width:100%; background-size:cover; background-position:center;
               display:flex; align-items:center; justify-content:center; }
      .level { position:absolute; left:0; bottom:0; height:6px; transition:width .3s ease; }
      .count { position:absolute; top:6px; right:6px; font-size:.7rem; font-weight:700; line-height:1; padding:3px 7px; border-radius:999px; background:rgba(0,0,0,.55); color:#fff; }
      .grip { position:absolute; top:6px; left:6px; font-size:.95rem; color:#fff; background:rgba(0,0,0,.45); border-radius:6px; padding:1px 5px; }
      .meta { padding:8px 10px 10px; }
      .name { font-weight:600; font-size:.92rem; color:var(--primary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .brand { font-size:.78rem; color:var(--secondary-text-color); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }
      .pct { font-size:.8rem; font-weight:600; margin-top:2px; }
      .empty { text-align:center; color:var(--secondary-text-color); padding:30px 12px; }
      .empty code { background:var(--secondary-background-color); padding:1px 6px; border-radius:6px; }

      /* detail overlay */
      .overlay { position:fixed; inset:0; z-index:9; display:none; align-items:center; justify-content:center; background:rgba(0,0,0,.5); }
      .overlay.open { display:flex; }
      .sheet { width:min(420px,92vw); max-height:88vh; overflow:auto; background:var(--card-background-color,#fff);
               color:var(--primary-text-color); border-radius:16px; padding:18px; box-shadow:0 12px 40px rgba(0,0,0,.4); }
      .sheet .d-thumb { width:100%; aspect-ratio:16/9; border-radius:12px; background-size:cover; background-position:center;
                        background-color:var(--secondary-background-color); display:flex; align-items:center; justify-content:center; margin-bottom:14px; }
      .sheet h2 { margin:0 0 2px; font-size:1.2rem; }
      .sheet .d-brand { color:var(--secondary-text-color); margin-bottom:12px; }
      .facts { display:grid; grid-template-columns:auto 1fr; gap:4px 14px; font-size:.88rem; margin-bottom:16px; }
      .facts dt { color:var(--secondary-text-color); }
      .facts dd { margin:0; text-align:right; }
      .actions { display:grid; grid-template-columns:repeat(4,1fr); gap:8px; }
      button.act { font:inherit; cursor:pointer; padding:10px 0; border-radius:10px; border:1px solid var(--divider-color, rgba(0,0,0,.15));
                   background:var(--secondary-background-color); color:var(--primary-text-color); font-weight:600; }
      button.act:hover { border-color:var(--primary-color); }
      button.act.danger { color:var(--error-color); }
      .custom-row { display:flex; align-items:center; gap:10px; margin-top:14px; }
      .custom-row input[type=range] { flex:1; accent-color:var(--primary-color); }
      .custom-row .val { width:3ch; text-align:right; font-weight:600; }
      button.apply { font:inherit; cursor:pointer; padding:8px 14px; border-radius:10px; border:none; background:var(--primary-color); color:#fff; font-weight:600; }
      .close-row { display:flex; justify-content:space-between; margin-top:16px; }
      button.close { font:inherit; cursor:pointer; background:none; border:none; color:var(--secondary-text-color); padding:6px 8px; }
      @media (prefers-reduced-motion: reduce) { .tile, .level { transition:none; } }
    `;
  }
}

class StockpileSummaryCard extends StockpileCard {
  get _defaultView() {
    return "summary";
  }
}

customElements.define("stockpile-card", StockpileCard);
customElements.define("stockpile-summary-card", StockpileSummaryCard);

window.customCards = window.customCards || [];
window.customCards.push(
  { type: "stockpile-summary-card", name: "Stockpile Summary", description: "Modern at-a-glance inventory overview with a location picker." },
  { type: "stockpile-card", name: "Stockpile Grid", description: "Visual package grid with quick consume actions and drag-to-arrange." }
);

console.info(`%c STOCKPILE %c ${VERSION} `, "background:#43a047;color:#fff;border-radius:3px 0 0 3px", "background:#333;color:#fff;border-radius:0 3px 3px 0");
