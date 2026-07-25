"""Monstres : ingestion des résistances et usage comme cible de combat."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dofus_opti.combat.catalog import (
    UnknownMonsterError,
    find_monsters,
    load_target,
)
from dofus_opti.combat.formula import compute_hit
from dofus_opti.combat.stats import StatVector
from dofus_opti.ingest.normalize_monsters import (
    MissingResistanceFieldError,
    MonsterIngestReport,
    normalize_monsters,
    raise_if_fields_missing,
)

DB = Path(__file__).resolve().parents[1] / "data" / "dofus.db"


def raw_grade(**overrides):
    base = {
        "grade": 1, "level": 1, "lifePoints": 29,
        "actionPoints": 5, "movementPoints": 2,
        "neutralResistance": 25, "earthResistance": 0, "fireResistance": -12,
        "waterResistance": 6, "airResistance": -50,
    }
    base.update(overrides)
    return base


def raw_monster(**overrides):
    base = {"id": 31, "name": {"fr": "Bouftou", "en": "Gobball"}, "grades": [raw_grade()]}
    base.update(overrides)
    return base


# ------------------------------------------------------------- normalisation

def test_resistances_are_mapped_to_elements():
    report = MonsterIngestReport()
    monster = normalize_monsters([raw_monster()], report)[0]
    grade = monster.grades[0]

    assert monster.name == "Bouftou"
    assert grade.res_pct == {
        "neutre": 25, "terre": 0, "feu": -12, "eau": 6, "air": -50
    }
    assert report.monsters_kept == 1
    assert report.grades_kept == 1


def test_negative_resistance_is_a_vulnerability():
    report = MonsterIngestReport()
    normalize_monsters([raw_monster()], report)
    assert report.with_vulnerability == 1


def test_weakest_element_is_the_lowest_resistance():
    report = MonsterIngestReport()
    monster = normalize_monsters([raw_monster()], report)[0]
    assert monster.grades[0].weakest_element() == "air"


def test_monsters_without_grades_are_dropped():
    report = MonsterIngestReport()
    kept = normalize_monsters([raw_monster(grades=[])], report)
    assert kept == []
    assert report.without_grades == 1


def test_a_missing_resistance_field_is_fatal():
    """Un champ disparu fausserait toutes les cibles en silence."""
    grade = raw_grade()
    del grade["airResistance"]
    report = MonsterIngestReport()
    normalize_monsters([raw_monster(grades=[grade])], report)

    assert report.missing_resistance_fields == {"airResistance": 1}
    with pytest.raises(MissingResistanceFieldError):
        raise_if_fields_missing(report)


def test_highest_grade_is_the_default():
    report = MonsterIngestReport()
    monster = normalize_monsters(
        [raw_monster(grades=[raw_grade(grade=1, level=10), raw_grade(grade=2, level=30)])],
        report,
    )[0]
    assert monster.at_grade().level == 30
    assert monster.at_grade(1).level == 10
    assert monster.at_grade(99) is None


# ------------------------------------------------------------ usage en combat

def test_vulnerability_increases_damage():
    """−50 % de résistance Air signifie 50 % de dégâts en plus."""
    report = MonsterIngestReport()
    monster = normalize_monsters([raw_monster()], report)[0]
    from dofus_opti.combat.formula import Target

    target = Target(res_pct=dict(monster.grades[0].res_pct))
    assert compute_hit(100, "air", StatVector(), target=target) == 150
    assert compute_hit(100, "neutre", StatVector(), target=target) == 75


# ------------------------------------------------------------ base de données

@pytest.fixture(scope="module")
def conn():
    if not DB.exists():
        pytest.skip("base absente — lancez `python -m dofus_opti.ingest.build`")
    connection = sqlite3.connect(DB)
    if not connection.execute("SELECT COUNT(*) FROM monster").fetchone()[0]:
        connection.close()
        pytest.skip("base construite sans les monstres")
    yield connection
    connection.close()


def test_catalog_holds_the_whole_bestiary(conn):
    count = conn.execute("SELECT COUNT(*) FROM monster").fetchone()[0]
    assert count > 5000


def test_every_grade_carries_all_five_resistances(conn):
    incomplete = conn.execute(
        """SELECT COUNT(*) FROM (
               SELECT monster_id, grade FROM monster_resistance
               GROUP BY monster_id, grade HAVING COUNT(*) != 5
           )"""
    ).fetchone()[0]
    assert incomplete == 0


def test_load_target_from_a_real_monster(conn):
    target = load_target(conn, "Bouftou Royal")
    assert set(target.res_pct) == {"neutre", "terre", "feu", "eau", "air"}
    assert target.res_pct["air"] == 5
    assert target.res_pct["neutre"] == 35


def test_unknown_monster_suggests_alternatives(conn):
    with pytest.raises(UnknownMonsterError) as exc:
        load_target(conn, "Bouftou Roy")
    assert "Bouftou Royal" in str(exc.value)


def test_search_by_fragment(conn):
    found = find_monsters(conn, "Bouftou", limit=10)
    assert found
    assert all("bouftou" in m.name.lower() for m in found)


def test_target_resistances_reduce_damage(conn):
    target = load_target(conn, "Bouftou Royal")
    plain = compute_hit(1000, "neutre", StatVector())
    armored = compute_hit(1000, "neutre", StatVector(), target=target)
    assert armored < plain
    assert armored == 650  # 35 % de résistance Neutre
