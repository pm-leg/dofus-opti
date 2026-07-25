from __future__ import annotations

import sqlite3

from dofus_opti.ingest.db import load_condition, write_database
from dofus_opti.ingest.normalize import IngestReport, normalize_items, normalize_sets
from dofus_opti.model.stats import StatKey


def _build(tmp_path, raw_items, raw_sets):
    report = IngestReport()
    items = normalize_items(raw_items, report)
    sets = normalize_sets(raw_sets, report)
    path = tmp_path / "test.db"
    write_database(path, items, sets, meta={"source": "fixture", "game_version": "test"})
    return path, items, sets


def test_roundtrip_counts(tmp_path, raw_items, raw_sets):
    path, items, sets = _build(tmp_path, raw_items, raw_sets)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM item").fetchone()[0] == len(items)
        assert conn.execute("SELECT COUNT(*) FROM item_set").fetchone()[0] == len(sets)

        expected_stats = sum(len(i.stats) for i in items)
        assert conn.execute("SELECT COUNT(*) FROM item_stat").fetchone()[0] == expected_stats

        expected_hits = sum(len(i.weapon_hits) for i in items)
        assert conn.execute("SELECT COUNT(*) FROM item_weapon_hit").fetchone()[0] == expected_hits
    finally:
        conn.close()


def test_meta_is_recorded(tmp_path, raw_items, raw_sets):
    path, _, _ = _build(tmp_path, raw_items, raw_sets)
    conn = sqlite3.connect(path)
    try:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        assert meta["source"] == "fixture"
        assert meta["game_version"] == "test"
        assert "built_at" in meta
    finally:
        conn.close()


def test_stat_values_survive_the_roundtrip(tmp_path, raw_items, raw_sets):
    path, _, _ = _build(tmp_path, raw_items, raw_sets)
    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            """SELECT s.min, s.max FROM item_stat s
               JOIN item i ON i.ankama_id = s.item_id
               WHERE i.name = ? AND s.stat = ?""",
            ("Cape Fulgurante", StatKey.VITALITE.value),
        ).fetchone()
        assert row == (21, 25)

        # Le malus de PA du Kaiser doit rester négatif en base.
        row = conn.execute(
            """SELECT s.max FROM item_stat s
               JOIN item i ON i.ankama_id = s.item_id
               WHERE i.name = ? AND s.stat = ?""",
            ("Kaiser", StatKey.PA.value),
        ).fetchone()
        assert row[0] == -1
    finally:
        conn.close()


def test_conditions_are_reloadable(tmp_path, raw_items, raw_sets):
    path, items, _ = _build(tmp_path, raw_items, raw_sets)
    by_id = {i.ankama_id: i for i in items}
    conn = sqlite3.connect(path)
    try:
        for item_id, item in by_id.items():
            assert load_condition(conn, item_id) == item.condition
    finally:
        conn.close()


def test_rebuild_is_idempotent(tmp_path, raw_items, raw_sets):
    path, items, sets = _build(tmp_path, raw_items, raw_sets)
    write_database(path, items, sets, meta={"source": "fixture", "game_version": "test"})
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM item").fetchone()[0] == len(items)
    finally:
        conn.close()
