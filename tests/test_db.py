"""Unit tests for the Stockpile data layer.

These tests cover behavior that's easy to break in refactors: alias-aware
product lookup, position-preserving reorder, expiration enrichment, snooze
filtering, velocity windows, and export/import round-tripping.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util


pytestmark = pytest.mark.asyncio


async def _seed_minimal(db):
    loc = await db.add_location("Pantry")
    pid = await db.add_product(name="Pasta", brand="Barilla", unit="box", threshold=2)
    pkg = await db.add_package(product_id=pid, remaining=100, location_id=loc)
    return loc, pid, pkg


async def test_add_and_find_product_by_name(db):
    pid = await db.add_product(name="Ground Beef", brand="Costco")
    found = await db.find_product("ground beef")
    assert found is not None and found["id"] == pid


async def test_find_product_matches_alias(db):
    await db.add_product(name="Ground Beef", aliases=["beef", "hamburger meat"])
    found = await db.find_product("HamBurger Meat")
    assert found is not None and found["name"] == "Ground Beef"


async def test_find_product_returns_none_for_unknown(db):
    await db.add_product(name="Pasta")
    assert await db.find_product("nothing") is None


async def test_consume_clamps_to_zero(db):
    _loc, pid, pkg = await _seed_minimal(db)
    result = await db.consume(pkg, 150)
    assert result["remaining"] == 0


async def test_consume_logs_event(db):
    _loc, pid, pkg = await _seed_minimal(db)
    await db.consume(pkg, 10, who="alice")
    history = await db.get_history()
    assert len(history) == 1
    assert history[0]["who"] == "alice"
    assert history[0]["amount"] == 10


async def test_set_remaining_logs_delta(db):
    _loc, pid, pkg = await _seed_minimal(db)
    await db.set_remaining(pkg, 40)
    history = await db.get_history()
    # remaining went 100 -> 40, so delta logged is 60
    assert history[0]["amount"] == 60
    assert history[0]["remaining_after"] == 40


async def test_get_packages_enriches_status(db):
    _loc, pid, full = await _seed_minimal(db)
    half = await db.add_package(pid, remaining=50)
    low = await db.add_package(pid, remaining=10)
    empty = await db.add_package(pid, remaining=0)
    by_id = {p["id"]: p for p in await db.get_packages()}
    assert by_id[full]["status"] == "full"
    assert by_id[half]["status"] == "medium"
    assert by_id[low]["status"] == "low"
    assert by_id[empty]["status"] == "empty"


async def test_get_packages_marks_expired_and_expiring(db):
    _loc, pid, _pkg = await _seed_minimal(db)
    soon = (dt_util.utcnow() + timedelta(days=3)).isoformat()
    far = (dt_util.utcnow() + timedelta(days=60)).isoformat()
    past = (dt_util.utcnow() - timedelta(days=2)).isoformat()
    soon_id = await db.add_package(pid, remaining=100, expires=soon)
    far_id = await db.add_package(pid, remaining=100, expires=far)
    past_id = await db.add_package(pid, remaining=100, expires=past)
    by_id = {p["id"]: p for p in await db.get_packages()}
    assert by_id[soon_id]["expiring_soon"] is True
    assert by_id[soon_id]["expired"] is False
    assert by_id[far_id]["expiring_soon"] is False
    assert by_id[past_id]["expired"] is True


async def test_summary_low_stock_uses_threshold(db):
    loc = await db.add_location("Pantry")
    high_pid = await db.add_product(name="Rice", threshold=1)
    low_pid = await db.add_product(name="Pasta", threshold=4)
    await db.add_package(high_pid, remaining=100, location_id=loc)
    await db.add_package(low_pid, remaining=100, location_id=loc)
    low = await db.get_low_stock()
    names = {r["name"] for r in low}
    assert "Pasta" in names
    assert "Rice" not in names


async def test_snooze_hides_from_low_stock(db):
    loc = await db.add_location("Pantry")
    pid = await db.add_product(name="Pasta", threshold=4)
    await db.add_package(pid, remaining=100, location_id=loc)
    assert any(r["name"] == "Pasta" for r in await db.get_low_stock())
    future = (dt_util.utcnow() + timedelta(hours=1)).isoformat()
    await db.snooze_product(pid, future)
    assert all(r["name"] != "Pasta" for r in await db.get_low_stock())
    assert any(r["name"] == "Pasta" for r in await db.get_low_stock(include_snoozed=True))


async def test_acknowledge_clears_snooze(db):
    loc = await db.add_location("Pantry")
    pid = await db.add_product(name="Pasta", threshold=4)
    await db.add_package(pid, remaining=100, location_id=loc)
    await db.snooze_product(pid, (dt_util.utcnow() + timedelta(hours=24)).isoformat())
    assert all(r["name"] != "Pasta" for r in await db.get_low_stock())
    await db.acknowledge_product(pid)
    assert any(r["name"] == "Pasta" for r in await db.get_low_stock())


async def test_reorder_preserves_slots_of_subset(db):
    loc_a = await db.add_location("A")
    loc_b = await db.add_location("B")
    pid = await db.add_product(name="X")
    a1 = await db.add_package(pid, location_id=loc_a)
    b1 = await db.add_package(pid, location_id=loc_b)
    a2 = await db.add_package(pid, location_id=loc_a)
    # Reorder only loc_a's packages: their positions should stay within their existing slots.
    a_packs = [p for p in await db.get_packages(loc_a)]
    before = [p["position"] for p in a_packs]
    ids_reversed = [p["id"] for p in reversed(a_packs)]
    await db.reorder(ids_reversed)
    a_packs_after = await db.get_packages(loc_a)
    after = [p["position"] for p in a_packs_after]
    # Same slot positions, just used by different ids.
    assert sorted(before) == sorted(after)
    # The reversed order is reflected in the resulting ids.
    assert [p["id"] for p in a_packs_after] == ids_reversed


async def test_velocity_aggregates_recent_consumption(db):
    _loc, pid, pkg = await _seed_minimal(db)
    await db.consume(pkg, 25)
    await db.consume(pkg, 25)
    v = await db.get_velocity(pid, days=30)
    assert v["events"] == 2
    # 50% total = 0.5 packages-equivalent consumed
    assert v["consumed_equiv"] == pytest.approx(0.5, rel=1e-3)
    assert v["per_day"] > 0


async def test_export_import_roundtrip_replace(db, tmp_path):
    from custom_components.stockpile.db import InventoryDB

    loc = await db.add_location("Pantry")
    pid = await db.add_product(name="Pasta", brand="Barilla", aliases=["spaghetti"])
    pkg = await db.add_package(pid, remaining=75, location_id=loc)
    await db.consume(pkg, 10)

    dump = await db.export_all()
    assert dump["version"] == 1
    assert len(dump["products"]) == 1
    assert len(dump["packages"]) == 1
    assert len(dump["consumption_log"]) == 1

    # Fresh DB, import with replace
    other_path = tmp_path / "other.db"
    other = InventoryDB(str(other_path))
    await other.async_init()
    try:
        counts = await other.import_all(dump, replace=True)
        assert counts == {"locations": 1, "products": 1, "packages": 1, "log": 1}
        products = await other.get_products()
        assert products[0]["aliases"] == ["spaghetti"]
        packages = await other.get_packages()
        # Consumed 10 from 75 before export, so the round-tripped value is 65.
        assert packages[0]["remaining"] == 65
    finally:
        await other.async_close()


async def test_suggest_restock_flags_low_stock(db):
    loc = await db.add_location("Pantry")
    # threshold=4 but only 1 package-equivalent remaining -> low stock
    pid = await db.add_product(name="Pasta", brand="Barilla", unit="box", threshold=4)
    await db.add_package(pid, remaining=100, location_id=loc)
    suggestions = await db.suggest_restock()
    names = {s["name"] for s in suggestions}
    assert "Pasta" in names
    pasta = next(s for s in suggestions if s["name"] == "Pasta")
    assert pasta["low_stock"] is True
    assert pasta["suggested_qty"] >= 1


async def test_suggest_restock_uses_velocity(db):
    loc = await db.add_location("Pantry")
    # No threshold set; rely on velocity alone
    pid = await db.add_product(name="Rice", unit="bag")
    pkg = await db.add_package(pid, remaining=100, location_id=loc)
    # Consume heavily so velocity implies it will run out within horizon
    await db.consume(pkg, 90)
    suggestions = await db.suggest_restock(velocity_days=30, horizon_days=60)
    names = {s["name"] for s in suggestions}
    assert "Rice" in names


async def test_suggest_restock_excludes_well_stocked(db):
    loc = await db.add_location("Pantry")
    pid = await db.add_product(name="Salt", threshold=1)
    await db.add_package(pid, remaining=100, location_id=loc)
    await db.add_package(pid, remaining=100, location_id=loc)
    await db.add_package(pid, remaining=100, location_id=loc)
    # 3 packages well above threshold of 1, no consumption
    suggestions = await db.suggest_restock()
    assert all(s["name"] != "Salt" for s in suggestions)
