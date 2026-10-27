# Copyright (c) 2026 Dhruvin Dave
# SPDX-License-Identifier: AGPL-3.0-only
"""Per-thread persistent SQLite connections (performance fix): no deadlock, cross-thread visibility, clean close."""
import os, tempfile, threading

from src.twin.models import Zone
from src.twin.store import TwinStore


def _zone(i):
    return Zone(zone_id=f"z{i}", name=f"Z{i}", zone_type="workspace", security_level=1)


def _run_in_thread(fn, timeout=10):
    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", fn()), daemon=True)
    t.start(); t.join(timeout)
    assert not t.is_alive(), "store call hung (deadlock)"
    return out.get("r")


def test_first_call_on_a_new_thread_can_be_a_write():
    store = TwinStore(os.path.join(tempfile.mkdtemp(), "c.db"))
    _run_in_thread(lambda: store.insert_zone(_zone(1)))
    assert [z.zone_id for z in store.get_zones()] == ["z1"]
    store.close()


def test_writes_from_one_thread_are_visible_to_another_and_close_releases_files():
    path = os.path.join(tempfile.mkdtemp(), "c.db")
    store = TwinStore(path)
    assert store.get_zones() == []              # main thread opens its connection first
    _run_in_thread(lambda: store.insert_zone(_zone(2)))
    assert [z.zone_id for z in store.get_zones()] == ["z2"]
    assert [z.zone_id for z in _run_in_thread(store.get_zones)] == ["z2"]
    store.close()
    os.remove(path)                              # Windows: fails if any connection is still open
