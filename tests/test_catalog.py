"""Chargement des sorts depuis la base — nécessite une ingestion préalable."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from dofus_opti.combat.catalog import (
    UnknownBreedError,
    available_breeds,
    load_class_spells,
    load_spells,
)
from dofus_opti.combat.rotation import best_rotation
from dofus_opti.combat.stats import StatVector

DB = Path(__file__).resolve().parents[1] / "data" / "dofus.db"


@pytest.fixture(scope="module")
def conn():
    if not DB.exists():
        pytest.skip("base absente — lancez `python -m dofus_opti.ingest.build`")
    connection = sqlite3.connect(DB)
    if not connection.execute("SELECT COUNT(*) FROM breed").fetchone()[0]:
        connection.close()
        pytest.skip("base construite sans les sorts (--no-spells)")
    yield connection
    connection.close()


def test_all_nineteen_classes_are_present(conn):
    breeds = available_breeds(conn)
    assert len(breeds) == 19
    assert "Iop" in breeds and "Crâ" in breeds


def test_unknown_breed_lists_the_alternatives(conn):
    with pytest.raises(UnknownBreedError) as exc:
        load_class_spells(conn, "Chevalier")
    assert "Iop" in str(exc.value)


def test_every_class_has_offensive_spells(conn):
    for breed in available_breeds(conn):
        spells = load_spells(conn, breed, 200)
        assert spells, f"aucun sort offensif pour {breed}"


def test_element_filter_restricts_the_selection(conn):
    every = load_spells(conn, "Iop", 175)
    earth = load_spells(conn, "Iop", 175, elements={"terre"})
    assert 0 < len(earth) <= len(every)
    assert all(any(r.element == "terre" for r in s.rolls) for s in earth)


def test_character_level_gates_spell_grades(conn):
    low = load_spells(conn, "Iop", 20, elements={"terre"})
    high = load_spells(conn, "Iop", 200, elements={"terre"})
    by_name_low = {s.name: s for s in low}
    by_name_high = {s.name: s for s in high}

    shared = set(by_name_low) & set(by_name_high)
    assert shared
    # Au moins un sort progresse en palier avec le niveau.
    assert any(by_name_high[n].grade > by_name_low[n].grade for n in shared)


def test_loaded_spells_are_usable_by_the_engine(conn):
    spells = load_spells(conn, "Iop", 175, elements={"terre"})
    stats = StatVector().with_(pa=6, force=600, puissance=100, critique_pct=40)
    rotation = best_rotation(spells, stats)

    assert rotation.damage > 0
    assert rotation.ap_used <= 12
    assert rotation.casts


def test_spell_data_is_internally_coherent(conn):
    for spell in load_spells(conn, "Iop", 200):
        assert spell.ap_cost > 0, spell.name
        assert spell.rolls, spell.name
        for roll in spell.rolls:
            assert roll.base_max >= roll.base_min >= 0
            assert roll.crit_max >= roll.crit_min >= 0
            # Un critique ne doit jamais être plus faible qu'un coup normal.
            assert roll.crit_max >= roll.base_max, f"{spell.name} {roll.element}"


def test_pression_matches_the_game(conn):
    """Contrôle sur une valeur connue, relevée sur DofusDB."""
    spells = {s.name: s for s in load_spells(conn, "Iop", 175, elements={"terre"})}
    pression = spells["Pression"]

    assert pression.ap_cost == 3
    assert pression.crit_probability == 10
    roll = next(r for r in pression.rolls if r.element == "terre")
    assert (roll.base_min, roll.base_max) == (26, 30)
    assert (roll.crit_min, roll.crit_max) == (31, 36)
