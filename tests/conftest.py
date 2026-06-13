"""Test fixtures for Stockpile.

The DB layer is exercised in isolation against a real on-disk SQLite file in
a temporary directory. Home Assistant is imported only for `dt_util`, which
is a lightweight datetime helper — no full HA runtime is required.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest

# Make the integration importable from the repo root without an install.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def db(tmp_path):
    """Fresh on-disk DB per test."""
    from custom_components.stockpile.db import InventoryDB

    path = tmp_path / "stockpile.db"
    inst = InventoryDB(str(path))
    await inst.async_init()
    try:
        yield inst
    finally:
        await inst.async_close()
        # WAL sidecars
        for ext in ("", "-wal", "-shm"):
            f = Path(str(path) + ext)
            if f.exists():
                os.unlink(f)
