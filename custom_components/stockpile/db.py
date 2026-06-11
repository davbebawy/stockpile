"""Async SQLite data layer for the Stockpile integration.

A single long-lived connection is opened at setup. All writes go through an
asyncio.Lock so concurrent service calls / automations can't corrupt state.
WAL mode is enabled so reads don't block on writes.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import aiosqlite

from homeassistant.util import dt as dt_util

SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    parent  TEXT
);

CREATE TABLE IF NOT EXISTS products (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    brand      TEXT,
    unit       TEXT,
    category   TEXT,
    image      TEXT,
    aliases    TEXT,            -- JSON array of strings
    threshold  REAL,            -- low-stock threshold, in whole-package equivalents
    created    TEXT
);

CREATE TABLE IF NOT EXISTS packages (
    id           TEXT PRIMARY KEY,
    product_id   TEXT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    remaining    REAL NOT NULL DEFAULT 100,   -- percent, 0..100
    quantity     REAL DEFAULT 1,              -- e.g. 1.5 (lbs) at purchase
    location_id  TEXT REFERENCES locations(id) ON DELETE SET NULL,
    position     REAL,                        -- manual ordering within a view
    added        TEXT,
    frozen       TEXT,
    expires      TEXT,
    notes        TEXT
);

CREATE TABLE IF NOT EXISTS consumption_log (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    package_id       TEXT,
    product_id       TEXT,
    amount           REAL,        -- percent consumed in this event
    remaining_after  REAL,
    who              TEXT,
    ts               TEXT
);

CREATE INDEX IF NOT EXISTS idx_packages_product  ON packages(product_id);
CREATE INDEX IF NOT EXISTS idx_packages_location ON packages(location_id);
CREATE INDEX IF NOT EXISTS idx_log_product       ON consumption_log(product_id);
"""


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _now() -> str:
    return dt_util.utcnow().isoformat()


class InventoryDB:
    """Owns the SQLite connection and all data operations."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._write_lock = asyncio.Lock()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    async def async_init(self) -> None:
        self._db = await aiosqlite.connect(self._path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(SCHEMA)
        await self._db.commit()
        await self._migrate()

    async def _migrate(self) -> None:
        """Lightweight column migrations for DBs created by older versions."""
        assert self._db is not None
        cols = await self._query("PRAGMA table_info(packages)")
        names = {c["name"] for c in cols}
        if "position" not in names:
            await self._db.execute("ALTER TABLE packages ADD COLUMN position REAL")
            await self._db.execute(
                "UPDATE packages SET position = rowid WHERE position IS NULL"
            )
            await self._db.commit()

    async def async_close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    # ------------------------------------------------------------------ #
    # internal helpers
    # ------------------------------------------------------------------ #
    async def _query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        assert self._db is not None
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def _query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        rows = await self._query(sql, params)
        return rows[0] if rows else None

    async def _write(self, sql: str, params: tuple = ()) -> None:
        assert self._db is not None
        async with self._write_lock:
            await self._db.execute(sql, params)
            await self._db.commit()

    async def _next_position(self) -> float:
        row = await self._query_one(
            "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM packages"
        )
        return float(row["p"]) if row else 1.0

    # ------------------------------------------------------------------ #
    # locations
    # ------------------------------------------------------------------ #
    async def add_location(self, name: str, parent: str | None = None) -> str:
        loc_id = _new_id("loc")
        await self._write(
            "INSERT INTO locations (id, name, parent) VALUES (?, ?, ?)",
            (loc_id, name, parent),
        )
        return loc_id

    async def get_locations(self) -> list[dict[str, Any]]:
        return await self._query("SELECT * FROM locations ORDER BY name")

    async def get_location_by_name(self, name: str) -> dict[str, Any] | None:
        return await self._query_one(
            "SELECT * FROM locations WHERE lower(name) = lower(?)", (name,)
        )

    # ------------------------------------------------------------------ #
    # products (catalog)
    # ------------------------------------------------------------------ #
    async def add_product(
        self,
        name: str,
        brand: str | None = None,
        unit: str | None = None,
        category: str | None = None,
        image: str | None = None,
        aliases: list[str] | None = None,
        threshold: float | None = None,
        product_id: str | None = None,
    ) -> str:
        pid = product_id or _new_id("prod")
        await self._write(
            """INSERT INTO products
               (id, name, brand, unit, category, image, aliases, threshold, created)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, name, brand, unit, category, image,
             json.dumps(aliases or []), threshold, _now()),
        )
        return pid

    async def get_products(self) -> list[dict[str, Any]]:
        rows = await self._query("SELECT * FROM products ORDER BY name")
        for r in rows:
            r["aliases"] = json.loads(r["aliases"] or "[]")
        return rows

    async def get_product(self, product_id: str) -> dict[str, Any] | None:
        row = await self._query_one("SELECT * FROM products WHERE id = ?", (product_id,))
        if row:
            row["aliases"] = json.loads(row["aliases"] or "[]")
        return row

    async def find_product(self, name: str, brand: str | None = None) -> dict[str, Any] | None:
        if brand:
            row = await self._query_one(
                "SELECT * FROM products WHERE lower(name)=lower(?) AND lower(coalesce(brand,''))=lower(?)",
                (name, brand),
            )
        else:
            row = await self._query_one(
                "SELECT * FROM products WHERE lower(name)=lower(?)", (name,)
            )
        if row:
            row["aliases"] = json.loads(row["aliases"] or "[]")
        return row

    async def remove_product(self, product_id: str) -> None:
        await self._write("DELETE FROM products WHERE id = ?", (product_id,))

    # ------------------------------------------------------------------ #
    # packages (instances)
    # ------------------------------------------------------------------ #
    async def add_package(
        self,
        product_id: str,
        remaining: float = 100.0,
        quantity: float = 1.0,
        location_id: str | None = None,
        frozen: str | None = None,
        expires: str | None = None,
        notes: str | None = None,
    ) -> str:
        pkg_id = _new_id("pkg")
        position = await self._next_position()
        await self._write(
            """INSERT INTO packages
               (id, product_id, remaining, quantity, location_id, position, added, frozen, expires, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pkg_id, product_id, remaining, quantity, location_id, position,
             _now(), frozen, expires, notes),
        )
        return pkg_id

    async def get_packages(self, location_id: str | None = None) -> list[dict[str, Any]]:
        """Packages joined with their product, ready for the UI grid."""
        sql = """
            SELECT
                pk.id, pk.product_id, pk.remaining, pk.quantity,
                pk.location_id, pk.position, pk.added, pk.frozen, pk.expires, pk.notes,
                pr.name AS product_name, pr.brand, pr.unit,
                pr.category, pr.image, pr.threshold,
                loc.name AS location_name
            FROM packages pk
            JOIN products pr ON pr.id = pk.product_id
            LEFT JOIN locations loc ON loc.id = pk.location_id
        """
        params: tuple = ()
        if location_id:
            sql += " WHERE pk.location_id = ?"
            params = (location_id,)
        sql += " ORDER BY pk.position IS NULL, pk.position, pr.name, pk.added"
        rows = await self._query(sql, params)
        for r in rows:
            r["status"] = _status(r["remaining"])
        return rows

    async def get_package(self, package_id: str) -> dict[str, Any] | None:
        rows = await self.get_packages()
        return next((r for r in rows if r["id"] == package_id), None)

    async def set_remaining(self, package_id: str, remaining: float, who: str | None = None) -> dict[str, Any] | None:
        remaining = max(0.0, min(100.0, remaining))
        pkg = await self.get_package(package_id)
        if pkg is None:
            return None
        delta = pkg["remaining"] - remaining
        await self._write("UPDATE packages SET remaining = ? WHERE id = ?", (remaining, package_id))
        await self._log(package_id, pkg["product_id"], delta, remaining, who)
        return await self.get_package(package_id)

    async def consume(self, package_id: str, amount: float, who: str | None = None) -> dict[str, Any] | None:
        pkg = await self.get_package(package_id)
        if pkg is None:
            return None
        new_remaining = max(0.0, pkg["remaining"] - amount)
        await self._write("UPDATE packages SET remaining = ? WHERE id = ?", (new_remaining, package_id))
        await self._log(package_id, pkg["product_id"], amount, new_remaining, who)
        return await self.get_package(package_id)

    async def remove_package(self, package_id: str) -> None:
        await self._write("DELETE FROM packages WHERE id = ?", (package_id,))

    async def reorder(self, ids: list[str]) -> None:
        """Reassign ordering among the given packages.

        Preserves the *set of slots* these packages currently occupy, so
        reordering a filtered subset (e.g. just the freezer) doesn't disturb
        the ordering of packages in other locations.
        """
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        rows = await self._query(
            f"SELECT id, position, rowid FROM packages WHERE id IN ({placeholders})", tuple(ids)
        )
        if not rows:
            return
        # Use existing positions as the slots; fall back to rowid if NULL.
        slots = sorted(
            (r["position"] if r["position"] is not None else float(r["rowid"]))
            for r in rows
        )
        async with self._write_lock:
            assert self._db is not None
            for slot, pid in zip(slots, ids):
                await self._db.execute(
                    "UPDATE packages SET position = ? WHERE id = ?", (slot, pid)
                )
            await self._db.commit()

    async def _log(self, package_id, product_id, amount, remaining_after, who) -> None:
        await self._write(
            """INSERT INTO consumption_log
               (package_id, product_id, amount, remaining_after, who, ts)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (package_id, product_id, amount, remaining_after, who, _now()),
        )

    async def get_history(self, product_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if product_id:
            return await self._query(
                "SELECT * FROM consumption_log WHERE product_id = ? ORDER BY ts DESC LIMIT ?",
                (product_id, limit),
            )
        return await self._query("SELECT * FROM consumption_log ORDER BY ts DESC LIMIT ?", (limit,))

    # ------------------------------------------------------------------ #
    # aggregates
    # ------------------------------------------------------------------ #
    async def get_summary(self) -> list[dict[str, Any]]:
        rows = await self._query(
            """
            SELECT
                pr.id AS product_id, pr.name, pr.brand, pr.unit,
                pr.image, pr.category, pr.threshold,
                COUNT(pk.id) AS package_count,
                COALESCE(SUM(pk.remaining), 0) / 100.0 AS equiv_remaining,
                COALESCE(SUM(pk.quantity * pk.remaining / 100.0), 0) AS qty_remaining
            FROM products pr
            LEFT JOIN packages pk ON pk.product_id = pr.id
            GROUP BY pr.id
            ORDER BY pr.name
            """
        )
        for r in rows:
            thr = r.get("threshold")
            r["low_stock"] = thr is not None and r["equiv_remaining"] < thr
        return rows

    async def get_low_stock(self) -> list[dict[str, Any]]:
        return [r for r in await self.get_summary() if r["low_stock"]]

    async def count_packages(self) -> int:
        row = await self._query_one("SELECT COUNT(*) AS c FROM packages")
        return int(row["c"]) if row else 0

    # ------------------------------------------------------------------ #
    # demo / test data
    # ------------------------------------------------------------------ #
    async def seed_demo(self) -> dict[str, int]:
        """Populate realistic test data. Idempotent: skips if already seeded."""
        if await self.get_location_by_name("Freezer Top"):
            return {"skipped": 1}

        locs: dict[str, str] = {}
        for name in ["Freezer Top", "Freezer Bottom", "Pantry", "Garage"]:
            locs[name] = await self.add_location(name)

        # (name, brand, unit, category, threshold, location, [remaining per package])
        demo = [
            ("Ground Beef", "Costco", "lb", "Meat", 2, "Freezer Bottom", [100, 100, 50]),
            ("Chicken Breast", "Kirkland", "lb", "Meat", 3, "Freezer Bottom", [100, 100, 100, 100]),
            ("Salmon Fillet", "Kirkland", "lb", "Seafood", None, "Freezer Top", [100, 100]),
            ("Frozen Peas", "365", "bag", "Vegetables", None, "Freezer Top", [40]),
            ("Ice Cream", "Tillamook", "tub", "Dessert", None, "Freezer Top", [20]),
            ("Pasta", "Barilla", "box", "Dry Goods", 4, "Pantry", [100, 100, 100, 100, 100, 100]),
            ("Marinara Sauce", "Rao's", "jar", "Dry Goods", None, "Pantry", [100, 100, 100, 30]),
            ("Olive Oil", "Kirkland", "bottle", "Dry Goods", None, "Pantry", [65]),
            ("Paper Towels", "Bounty", "roll", "Household", 6, "Garage", [100, 100]),
            ("Toilet Paper", "Charmin", "roll", "Household", 8, "Garage", [100, 100, 100, 100, 100, 100]),
            ("Dish Soap", "Dawn", "bottle", "Household", None, "Garage", [15]),
        ]

        products = packages = 0
        for name, brand, unit, category, threshold, loc_name, remainings in demo:
            pid = await self.add_product(
                name=name, brand=brand, unit=unit, category=category, threshold=threshold
            )
            products += 1
            for rem in remainings:
                await self.add_package(pid, remaining=rem, location_id=locs[loc_name])
                packages += 1

        return {"locations": len(locs), "products": products, "packages": packages}


def _status(remaining: float) -> str:
    if remaining <= 0:
        return "empty"
    if remaining < 30:
        return "low"
    if remaining < 75:
        return "medium"
    return "full"
