"""Persistance SQLite du catalogue normalisé.

Les stats sont stockées en lignes (`item_stat`) plutôt qu'en colonnes : le jeu de
caractéristiques bouge à chaque extension, et le solveur charge de toute façon
tout en mémoire.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from ..model.items import Item, ItemSet
from ..model.monsters import Monster
from ..model.spells import Breed, ClassSpell
from .conditions import condition_from_dict, condition_to_dict, condition_to_text

SCHEMA = """
PRAGMA journal_mode = WAL;

CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE item (
    ankama_id         INTEGER PRIMARY KEY,
    name              TEXT    NOT NULL,
    slot              TEXT    NOT NULL,
    type_id           INTEGER NOT NULL,
    type_name         TEXT    NOT NULL,
    level             INTEGER NOT NULL,
    is_weapon         INTEGER NOT NULL,
    pods              INTEGER NOT NULL,
    set_id            INTEGER,
    ap_cost           INTEGER,
    crit_probability  INTEGER,
    crit_bonus        INTEGER,
    max_cast_per_turn INTEGER,
    range_min         INTEGER,
    range_max         INTEGER,
    condition_json    TEXT,
    condition_text    TEXT,
    -- provenance : -1 signifie « information non ingérée »
    drop_count        INTEGER NOT NULL DEFAULT -1,
    has_recipe        INTEGER NOT NULL DEFAULT -1,
    bound             INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_item_slot_level ON item(slot, level);
CREATE INDEX idx_item_set        ON item(set_id);

CREATE TABLE item_stat (
    item_id INTEGER NOT NULL REFERENCES item(ankama_id),
    stat    TEXT    NOT NULL,
    min     INTEGER NOT NULL,
    max     INTEGER NOT NULL,
    PRIMARY KEY (item_id, stat)
);
CREATE INDEX idx_item_stat_stat ON item_stat(stat);

CREATE TABLE item_weapon_hit (
    item_id INTEGER NOT NULL REFERENCES item(ankama_id),
    kind    TEXT    NOT NULL,
    element TEXT    NOT NULL,
    min     INTEGER NOT NULL,
    max     INTEGER NOT NULL
);
CREATE INDEX idx_weapon_hit_item ON item_weapon_hit(item_id);

CREATE TABLE item_spell_modifier (
    item_id   INTEGER NOT NULL REFERENCES item(ankama_id),
    effect_id INTEGER NOT NULL,
    raw       TEXT    NOT NULL
);
CREATE INDEX idx_spell_mod_item ON item_spell_modifier(item_id);

CREATE TABLE item_special_effect (
    item_id INTEGER NOT NULL REFERENCES item(ankama_id),
    raw     TEXT    NOT NULL
);
CREATE INDEX idx_special_item ON item_special_effect(item_id);

CREATE TABLE item_set (
    ankama_id INTEGER PRIMARY KEY,
    name      TEXT    NOT NULL,
    level     INTEGER NOT NULL,
    n_items   INTEGER NOT NULL
);

CREATE TABLE set_bonus (
    set_id     INTEGER NOT NULL REFERENCES item_set(ankama_id),
    item_count INTEGER NOT NULL,
    stat       TEXT    NOT NULL,
    min        INTEGER NOT NULL,
    max        INTEGER NOT NULL,
    PRIMARY KEY (set_id, item_count, stat)
);

CREATE TABLE set_bonus_raw (
    set_id     INTEGER NOT NULL REFERENCES item_set(ankama_id),
    item_count INTEGER NOT NULL,
    raw        TEXT    NOT NULL
);

CREATE TABLE breed (
    breed_id INTEGER PRIMARY KEY,
    name     TEXT NOT NULL
);

CREATE TABLE breed_stat_cost (
    breed_id  INTEGER NOT NULL REFERENCES breed(breed_id),
    stat      TEXT    NOT NULL,
    threshold INTEGER NOT NULL,
    cost      INTEGER NOT NULL,
    PRIMARY KEY (breed_id, stat, threshold)
);

CREATE TABLE spell (
    spell_id INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    breed_id INTEGER NOT NULL REFERENCES breed(breed_id)
);
CREATE INDEX idx_spell_breed ON spell(breed_id);

CREATE TABLE spell_level (
    spell_id            INTEGER NOT NULL REFERENCES spell(spell_id),
    grade               INTEGER NOT NULL,
    ap_cost             INTEGER NOT NULL,
    crit_probability    INTEGER NOT NULL,
    range_min           INTEGER NOT NULL,
    range_max           INTEGER NOT NULL,
    max_cast_per_turn   INTEGER NOT NULL,
    max_cast_per_target INTEGER NOT NULL,
    min_player_level    INTEGER NOT NULL,
    cast_in_line        INTEGER NOT NULL,
    needs_line_of_sight INTEGER NOT NULL,
    range_can_be_boosted INTEGER NOT NULL,
    max_stack           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (spell_id, grade)
);

-- « +N dégâts de base au sort cible » : le mécanisme des sorts à charges
CREATE TABLE spell_base_boost (
    target_spell_id INTEGER NOT NULL,
    source_spell_id INTEGER NOT NULL,
    source_grade    INTEGER NOT NULL,
    boost           INTEGER NOT NULL,
    PRIMARY KEY (target_spell_id, source_spell_id, source_grade)
);
CREATE INDEX idx_spell_level_lvl ON spell_level(min_player_level);

CREATE TABLE monster (
    monster_id INTEGER PRIMARY KEY,
    name       TEXT NOT NULL
);
CREATE INDEX idx_monster_name ON monster(name);

CREATE TABLE monster_grade (
    monster_id      INTEGER NOT NULL REFERENCES monster(monster_id),
    grade           INTEGER NOT NULL,
    level           INTEGER NOT NULL,
    life_points     INTEGER NOT NULL,
    action_points   INTEGER NOT NULL,
    movement_points INTEGER NOT NULL,
    PRIMARY KEY (monster_id, grade)
);
CREATE INDEX idx_monster_grade_level ON monster_grade(level);

CREATE TABLE monster_resistance (
    monster_id INTEGER NOT NULL,
    grade      INTEGER NOT NULL,
    element    TEXT    NOT NULL,
    res_pct    INTEGER NOT NULL,
    PRIMARY KEY (monster_id, grade, element)
);

CREATE TABLE spell_roll (
    spell_id  INTEGER NOT NULL,
    grade     INTEGER NOT NULL,
    over_time INTEGER NOT NULL,
    element   TEXT    NOT NULL,
    base_min  INTEGER NOT NULL,
    base_max  INTEGER NOT NULL,
    crit_min  INTEGER NOT NULL,
    crit_max  INTEGER NOT NULL,
    PRIMARY KEY (spell_id, grade, over_time, element)
);
"""


def write_database(
    path: Path,
    items: list[Item],
    sets: list[ItemSet],
    meta: dict[str, str],
    breeds: dict[int, Breed] | None = None,
    spells: list[ClassSpell] | None = None,
    monsters: list[Monster] | None = None,
    sources: dict[int, tuple[int, bool]] | None = None,
    base_boosts: list[tuple[int, int, int, int]] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    for suffix in ("-wal", "-shm"):
        stale = path.with_name(path.name + suffix)
        if stale.exists():
            stale.unlink()

    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)

        conn.executemany(
            "INSERT INTO meta(key, value) VALUES (?, ?)",
            [*meta.items(), ("built_at", datetime.now(UTC).isoformat(timespec="seconds"))],
        )

        conn.executemany(
            """INSERT INTO item (ankama_id, name, slot, type_id, type_name, level,
                                 is_weapon, pods, set_id, ap_cost, crit_probability,
                                 crit_bonus, max_cast_per_turn, range_min, range_max,
                                 condition_json, condition_text, bound)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    i.ankama_id, i.name, i.slot.value, i.type_id, i.type_name, i.level,
                    int(i.is_weapon), i.pods, i.set_id, i.ap_cost, i.crit_probability,
                    i.crit_bonus, i.max_cast_per_turn, i.range_min, i.range_max,
                    json.dumps(condition_to_dict(i.condition), ensure_ascii=False)
                    if i.condition else None,
                    condition_to_text(i.condition) or None,
                    int(i.bound_to_character),
                )
                for i in items
            ],
        )

        if sources:
            conn.executemany(
                "UPDATE item SET drop_count = ?, has_recipe = ? WHERE ankama_id = ?",
                [
                    (drops, int(recipe), item_id)
                    for item_id, (drops, recipe) in sources.items()
                ],
            )

        conn.executemany(
            "INSERT INTO item_stat(item_id, stat, min, max) VALUES (?,?,?,?)",
            [
                (i.ankama_id, k.value, r.minimum, r.maximum)
                for i in items
                for k, r in i.stats.items()
            ],
        )

        conn.executemany(
            "INSERT INTO item_weapon_hit(item_id, kind, element, min, max) VALUES (?,?,?,?,?)",
            [
                (i.ankama_id, h.kind, h.element, h.minimum, h.maximum)
                for i in items
                for h in i.weapon_hits
            ],
        )

        conn.executemany(
            "INSERT INTO item_spell_modifier(item_id, effect_id, raw) VALUES (?,?,?)",
            [(i.ankama_id, m.effect_id, m.raw) for i in items for m in i.spell_modifiers],
        )

        conn.executemany(
            "INSERT INTO item_special_effect(item_id, raw) VALUES (?,?)",
            [(i.ankama_id, s) for i in items for s in i.special_effects],
        )

        conn.executemany(
            "INSERT INTO item_set(ankama_id, name, level, n_items) VALUES (?,?,?,?)",
            [(s.ankama_id, s.name, s.level, s.n_items) for s in sets],
        )

        conn.executemany(
            "INSERT INTO set_bonus(set_id, item_count, stat, min, max) VALUES (?,?,?,?,?)",
            [
                (s.ankama_id, n, k.value, r.minimum, r.maximum)
                for s in sets
                for n, stats in s.bonuses.items()
                for k, r in stats.items()
            ],
        )

        conn.executemany(
            "INSERT INTO set_bonus_raw(set_id, item_count, raw) VALUES (?,?,?)",
            [
                (s.ankama_id, n, raw)
                for s in sets
                for n, raws in s.raw_bonuses.items()
                for raw in raws
            ],
        )

        if breeds:
            conn.executemany(
                "INSERT INTO breed(breed_id, name) VALUES (?,?)",
                [(b.breed_id, b.name) for b in breeds.values()],
            )
            conn.executemany(
                "INSERT INTO breed_stat_cost(breed_id, stat, threshold, cost) "
                "VALUES (?,?,?,?)",
                [
                    (b.breed_id, stat, threshold, cost)
                    for b in breeds.values()
                    for stat, tiers in b.stat_costs.items()
                    for threshold, cost in tiers
                ],
            )

        if spells:
            conn.executemany(
                "INSERT INTO spell(spell_id, name, breed_id) VALUES (?,?,?)",
                [(s.spell_id, s.name, s.breed_id) for s in spells],
            )
            conn.executemany(
                """INSERT INTO spell_level (spell_id, grade, ap_cost, crit_probability,
                                            range_min, range_max, max_cast_per_turn,
                                            max_cast_per_target, min_player_level,
                                            cast_in_line, needs_line_of_sight,
                                            range_can_be_boosted, max_stack)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        s.spell_id, lv.grade, lv.ap_cost, lv.crit_probability,
                        lv.range_min, lv.range_max, lv.max_cast_per_turn,
                        lv.max_cast_per_target, lv.min_player_level,
                        int(lv.cast_in_line), int(lv.needs_line_of_sight),
                        int(lv.range_can_be_boosted), lv.max_stack,
                    )
                    for s in spells for lv in s.levels
                ],
            )
            if base_boosts:
                conn.executemany(
                    "INSERT OR IGNORE INTO spell_base_boost"
                    "(target_spell_id, source_spell_id, source_grade, boost) "
                    "VALUES (?,?,?,?)",
                    base_boosts,
                )
            conn.executemany(
                """INSERT INTO spell_roll (spell_id, grade, over_time, element,
                                           base_min, base_max, crit_min, crit_max)
                   VALUES (?,?,?,?,?,?,?,?)""",
                [
                    (
                        s.spell_id, lv.grade, over_time, roll.element,
                        roll.base_min, roll.base_max, roll.crit_min, roll.crit_max,
                    )
                    for s in spells for lv in s.levels
                    for over_time, rolls in ((0, lv.rolls), (1, lv.over_time_rolls))
                    for roll in rolls
                ],
            )

        if monsters:
            conn.executemany(
                "INSERT INTO monster(monster_id, name) VALUES (?,?)",
                [(m.monster_id, m.name) for m in monsters],
            )
            conn.executemany(
                """INSERT INTO monster_grade (monster_id, grade, level, life_points,
                                              action_points, movement_points)
                   VALUES (?,?,?,?,?,?)""",
                [
                    (m.monster_id, g.grade, g.level, g.life_points,
                     g.action_points, g.movement_points)
                    for m in monsters for g in m.grades
                ],
            )
            conn.executemany(
                "INSERT INTO monster_resistance(monster_id, grade, element, res_pct) "
                "VALUES (?,?,?,?)",
                [
                    (m.monster_id, g.grade, element, value)
                    for m in monsters for g in m.grades
                    for element, value in g.res_pct.items()
                ],
            )

        conn.commit()
    finally:
        conn.close()


def load_condition(conn: sqlite3.Connection, item_id: int):
    row = conn.execute(
        "SELECT condition_json FROM item WHERE ankama_id = ?", (item_id,)
    ).fetchone()
    if not row or not row[0]:
        return None
    return condition_from_dict(json.loads(row[0]))
