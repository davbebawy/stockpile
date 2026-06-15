"""Async SQLite data layer for the Stockpile integration.

A single long-lived connection is opened at setup. All writes go through an
asyncio.Lock so concurrent service calls / automations can't corrupt state.
WAL mode is enabled so reads don't block on writes.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta as _timedelta
from typing import Any

import aiosqlite

from homeassistant.util import dt as dt_util

from .const import EXPIRING_SOON_DAYS

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

CREATE TABLE IF NOT EXISTS product_state (
    product_id      TEXT PRIMARY KEY REFERENCES products(id) ON DELETE CASCADE,
    snoozed_until   TEXT,
    acknowledged_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_packages_product  ON packages(product_id);
CREATE INDEX IF NOT EXISTS idx_packages_location ON packages(location_id);
CREATE INDEX IF NOT EXISTS idx_log_product       ON consumption_log(product_id);
CREATE INDEX IF NOT EXISTS idx_log_ts            ON consumption_log(ts);
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
        """Resolve a product by name (or one of its aliases), optionally scoped to a brand.

        Exact name match wins. Otherwise an alias match wins. Comparison is
        case-insensitive and tolerant of leading/trailing whitespace.
        """
        needle = (name or "").strip().lower()
        if not needle:
            return None

        if brand:
            row = await self._query_one(
                "SELECT * FROM products WHERE lower(trim(name))=? AND lower(coalesce(brand,''))=lower(?)",
                (needle, brand),
            )
        else:
            row = await self._query_one(
                "SELECT * FROM products WHERE lower(trim(name))=?", (needle,)
            )

        if not row:
            # Fall back to alias search. SQLite JSON1 is widely available,
            # but be defensive — load all products and scan their alias lists.
            for candidate in await self.get_products():
                if brand and (candidate.get("brand") or "").strip().lower() != brand.strip().lower():
                    continue
                aliases = [a.strip().lower() for a in (candidate.get("aliases") or [])]
                if needle in aliases:
                    return candidate
            return None

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
        now = dt_util.utcnow()
        for r in rows:
            r["status"] = _status(r["remaining"])
            r["expires_in_days"] = _days_until(r.get("expires"), now)
            r["expiring_soon"] = (
                r["expires_in_days"] is not None and 0 <= r["expires_in_days"] <= EXPIRING_SOON_DAYS
            )
            r["expired"] = r["expires_in_days"] is not None and r["expires_in_days"] < 0
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
                COALESCE(SUM(pk.quantity * pk.remaining / 100.0), 0) AS qty_remaining,
                ps.snoozed_until, ps.acknowledged_at
            FROM products pr
            LEFT JOIN packages pk ON pk.product_id = pr.id
            LEFT JOIN product_state ps ON ps.product_id = pr.id
            GROUP BY pr.id
            ORDER BY pr.name
            """
        )
        now = dt_util.utcnow()
        for r in rows:
            thr = r.get("threshold")
            r["low_stock"] = thr is not None and r["equiv_remaining"] < thr
            r["snoozed"] = _is_future(r.get("snoozed_until"), now)
        return rows

    async def get_low_stock(self, *, include_snoozed: bool = False) -> list[dict[str, Any]]:
        return [
            r for r in await self.get_summary()
            if r["low_stock"] and (include_snoozed or not r["snoozed"])
        ]

    async def get_expiring_soon(self, *, include_snoozed: bool = False) -> list[dict[str, Any]]:
        """Packages with an expiration date within EXPIRING_SOON_DAYS (or already past).

        Honors per-product snooze unless `include_snoozed` is set.
        """
        snoozed_ids: set[str] = set()
        if not include_snoozed:
            now = dt_util.utcnow()
            for r in await self._query("SELECT product_id, snoozed_until FROM product_state"):
                if _is_future(r.get("snoozed_until"), now):
                    snoozed_ids.add(r["product_id"])

        return [
            p for p in await self.get_packages()
            if (p.get("expiring_soon") or p.get("expired"))
            and (p["product_id"] not in snoozed_ids)
        ]

    # ------------------------------------------------------------------ #
    # snooze / acknowledge
    # ------------------------------------------------------------------ #
    async def snooze_product(self, product_id: str, until_iso: str) -> None:
        await self._write(
            """INSERT INTO product_state (product_id, snoozed_until)
               VALUES (?, ?)
               ON CONFLICT(product_id) DO UPDATE SET snoozed_until = excluded.snoozed_until""",
            (product_id, until_iso),
        )

    async def acknowledge_product(self, product_id: str) -> None:
        await self._write(
            """INSERT INTO product_state (product_id, acknowledged_at, snoozed_until)
               VALUES (?, ?, NULL)
               ON CONFLICT(product_id) DO UPDATE
               SET acknowledged_at = excluded.acknowledged_at,
                   snoozed_until = NULL""",
            (product_id, _now()),
        )

    async def clear_snooze(self, product_id: str) -> None:
        await self._write(
            "UPDATE product_state SET snoozed_until = NULL WHERE product_id = ?",
            (product_id,),
        )

    # ------------------------------------------------------------------ #
    # velocity
    # ------------------------------------------------------------------ #
    async def get_velocity(self, product_id: str, days: int = 30) -> dict[str, Any]:
        """Average consumption velocity for a product over the last `days`.

        Returns equivalent packages consumed per day. "Equivalent package" is
        the sum of `amount` percentages divided by 100.
        """
        days = max(1, int(days))
        cutoff = (dt_util.utcnow() - _timedelta(days=days)).isoformat()
        rows = await self._query(
            """SELECT COUNT(*) AS events, COALESCE(SUM(amount), 0) AS total_amount
               FROM consumption_log
               WHERE product_id = ? AND ts >= ?""",
            (product_id, cutoff),
        )
        if not rows:
            return {"events": 0, "consumed_equiv": 0.0, "per_day": 0.0, "days": days}
        row = rows[0]
        equiv = float(row["total_amount"] or 0) / 100.0
        return {
            "events": int(row["events"]),
            "consumed_equiv": round(equiv, 3),
            "per_day": round(equiv / days, 4),
            "days": days,
        }

    async def get_all_velocities(self, days: int = 30) -> list[dict[str, Any]]:
        days = max(1, int(days))
        cutoff = (dt_util.utcnow() - _timedelta(days=days)).isoformat()
        rows = await self._query(
            """SELECT product_id,
                      COUNT(*) AS events,
                      COALESCE(SUM(amount), 0) AS total_amount
               FROM consumption_log
               WHERE ts >= ?
               GROUP BY product_id""",
            (cutoff,),
        )
        return [
            {
                "product_id": r["product_id"],
                "events": int(r["events"]),
                "consumed_equiv": round(float(r["total_amount"] or 0) / 100.0, 3),
                "per_day": round(float(r["total_amount"] or 0) / 100.0 / days, 4),
                "days": days,
            }
            for r in rows
        ]

    async def suggest_restock(
        self,
        velocity_days: int = 30,
        horizon_days: int = 14,
    ) -> list[dict[str, Any]]:
        """Products that will run out within `horizon_days` at current velocity,
        or that are already below their low-stock threshold.

        Returns one entry per qualifying product with:
          - name, brand, unit, threshold
          - equiv_remaining (packages-equivalent remaining across all packages)
          - per_day velocity
          - days_left estimate (None when no velocity data)
          - suggested_qty: whole packages to bring stock back to 2× the threshold
            (or 2× the 30-day consumption if no threshold is set)
        """
        summary = {r["product_id"]: r for r in await self.get_summary()}
        velocities = {v["product_id"]: v for v in await self.get_all_velocities(velocity_days)}

        suggestions: list[dict[str, Any]] = []
        for pid, row in summary.items():
            equiv = float(row["equiv_remaining"])
            vel = velocities.get(pid)
            per_day = vel["per_day"] if vel else 0.0

            # Days until empty, capped to avoid huge numbers
            if per_day > 0:
                days_left: int | None = int(equiv / per_day)
            else:
                days_left = None

            is_low = bool(row["low_stock"])
            runs_out_soon = days_left is not None and days_left <= horizon_days

            if not (is_low or runs_out_soon):
                continue

            # Target stock: 2× threshold or 2× 30-day consumption, at least 1
            thr = row.get("threshold")
            target = float(thr) * 2 if thr else max(1.0, per_day * 30 * 2)
            suggested_qty = max(1, round(target - equiv))

            suggestions.append({
                "product_id": pid,
                "name": row["name"],
                "brand": row["brand"],
                "unit": row["unit"],
                "equiv_remaining": round(equiv, 2),
                "per_day": round(per_day, 4),
                "days_left": days_left,
                "low_stock": is_low,
                "suggested_qty": suggested_qty,
            })

        suggestions.sort(key=lambda x: (x["days_left"] if x["days_left"] is not None else 999, x["name"]))
        return suggestions

    async def count_packages(self) -> int:
        row = await self._query_one("SELECT COUNT(*) AS c FROM packages")
        return int(row["c"]) if row else 0

    async def get_trends(self, days: int = 14) -> list[dict[str, Any]]:
        """Daily consumption per product for the last N days.

        Returns products sorted by total consumption descending. Each entry
        has a ``daily`` dict mapping ISO date strings (YYYY-MM-DD) to the
        number of equivalent packages consumed that day.
        """
        days = max(1, min(90, int(days)))
        cutoff = (dt_util.utcnow() - _timedelta(days=days)).isoformat()
        rows = await self._query(
            """SELECT cl.product_id, pr.name, pr.unit,
                      substr(cl.ts, 1, 10) AS day,
                      SUM(cl.amount) AS total_amount
               FROM consumption_log cl
               JOIN products pr ON pr.id = cl.product_id
               WHERE cl.ts >= ?
               GROUP BY cl.product_id, day
               ORDER BY cl.product_id, day""",
            (cutoff,),
        )
        groups: dict[str, dict[str, Any]] = {}
        for r in rows:
            pid = r["product_id"]
            if pid not in groups:
                groups[pid] = {
                    "product_id": pid,
                    "name": r["name"],
                    "unit": r["unit"],
                    "total_equiv": 0.0,
                    "daily": {},
                }
            equiv = float(r["total_amount"] or 0) / 100.0
            groups[pid]["daily"][r["day"]] = round(equiv, 3)
            groups[pid]["total_equiv"] += equiv

        result = list(groups.values())
        for r in result:
            r["total_equiv"] = round(r["total_equiv"], 3)
        result.sort(key=lambda x: -x["total_equiv"])
        return result

    # ------------------------------------------------------------------ #
    # export / import (JSON backup)
    # ------------------------------------------------------------------ #
    async def export_all(self) -> dict[str, Any]:
        """Dump the full DB as a JSON-serializable dict."""
        locations = await self._query("SELECT * FROM locations")
        products = [
            {**p, "aliases": json.loads(p["aliases"] or "[]")}
            for p in await self._query("SELECT * FROM products")
        ]
        packages = await self._query(
            "SELECT id, product_id, remaining, quantity, location_id, position, "
            "added, frozen, expires, notes FROM packages"
        )
        log = await self._query("SELECT * FROM consumption_log")
        return {
            "version": 1,
            "exported_at": _now(),
            "locations": locations,
            "products": products,
            "packages": packages,
            "consumption_log": log,
        }

    async def import_all(self, payload: dict[str, Any], *, replace: bool = False) -> dict[str, int]:
        """Restore from an `export_all` payload.

        With `replace=True`, the existing tables are cleared first. Otherwise
        rows are upserted by primary key.
        """
        assert self._db is not None
        if payload.get("version") != 1:
            raise ValueError(f"unsupported export version: {payload.get('version')}")

        async with self._write_lock:
            if replace:
                for tbl in ("consumption_log", "packages", "products", "locations"):
                    await self._db.execute(f"DELETE FROM {tbl}")

            counts = {"locations": 0, "products": 0, "packages": 0, "log": 0}

            for loc in payload.get("locations", []):
                await self._db.execute(
                    "INSERT OR REPLACE INTO locations (id, name, parent) VALUES (?, ?, ?)",
                    (loc["id"], loc["name"], loc.get("parent")),
                )
                counts["locations"] += 1

            for pr in payload.get("products", []):
                aliases = pr.get("aliases") or []
                if isinstance(aliases, str):
                    try:
                        aliases = json.loads(aliases)
                    except ValueError:
                        aliases = []
                await self._db.execute(
                    "INSERT OR REPLACE INTO products "
                    "(id, name, brand, unit, category, image, aliases, threshold, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        pr["id"], pr["name"], pr.get("brand"), pr.get("unit"),
                        pr.get("category"), pr.get("image"),
                        json.dumps(aliases), pr.get("threshold"),
                        pr.get("created") or _now(),
                    ),
                )
                counts["products"] += 1

            for pk in payload.get("packages", []):
                await self._db.execute(
                    "INSERT OR REPLACE INTO packages "
                    "(id, product_id, remaining, quantity, location_id, position, added, frozen, expires, notes) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        pk["id"], pk["product_id"], pk.get("remaining", 100.0),
                        pk.get("quantity", 1.0), pk.get("location_id"),
                        pk.get("position"), pk.get("added") or _now(),
                        pk.get("frozen"), pk.get("expires"), pk.get("notes"),
                    ),
                )
                counts["packages"] += 1

            for ev in payload.get("consumption_log", []):
                await self._db.execute(
                    "INSERT INTO consumption_log "
                    "(package_id, product_id, amount, remaining_after, who, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        ev.get("package_id"), ev.get("product_id"),
                        ev.get("amount"), ev.get("remaining_after"),
                        ev.get("who"), ev.get("ts") or _now(),
                    ),
                )
                counts["log"] += 1

            await self._db.commit()
            return counts

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


def _is_future(iso: str | None, now) -> bool:
    if not iso:
        return False
    try:
        target = dt_util.parse_datetime(iso) or dt_util.parse_datetime(f"{iso}T00:00:00+00:00")
        return target is not None and target > now
    except (TypeError, ValueError):
        return False


def _days_until(iso: str | None, now) -> int | None:
    """Days from `now` until the given ISO date. Negative if past. None if absent."""
    if not iso:
        return None
    try:
        target = dt_util.parse_datetime(iso) or dt_util.parse_datetime(f"{iso}T00:00:00+00:00")
        if target is None:
            return None
        delta = target - now
        return int(delta.total_seconds() // 86400)
    except (TypeError, ValueError):
        return None
