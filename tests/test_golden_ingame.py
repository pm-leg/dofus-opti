"""Relevés effectués en jeu — vérité terrain de l'ingestion.

Source : infobulles des sorts d'un Ouginak niveau 1, toutes caractéristiques à 0.
À 0 en tout, l'infobulle affiche la base brute du sort (tous les multiplicateurs
valent 1) : ces relevés valident donc l'**ingestion**, pas la formule de dégâts.

Ce qu'ils verrouillent, et qui serait autrement invisible en cas d'erreur :
- l'extraction des fourchettes (`diceNum` / `diceSide`) et des jets critiques ;
- la correspondance élément ↔ `effectId`, confirmée ici par le jeu lui-même ;
- le coût en PA, le taux critique de base, les limites de lancers par tour et par
  cible, la portée, et le palier ouvert à chaque niveau de personnage.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

DB = Path(__file__).resolve().parents[1] / "data" / "dofus.db"

#: spell_id → (nom, grade, niveau requis, PA, %CC, portée, lancers/tour,
#:             lancers/cible, élément, min, max, crit_min, crit_max)
IN_GAME = [
    (13756, "Molosse",     1,   1, 3, 20, (1, 2), 3, 2, "terre", 18, 20, 22, 24),
    (13787, "Mâchoire",    1,  95, 3, 20, (0, 5), 2, 0, "feu",   23, 25, 27, 30),
    (13762, "Os à Moelle", 1,   1, 3, 10, (1, 6), 3, 2, "eau",   12, 14, 15, 17),
    (13755, "Traque",      1,   1, 3, 10, (1, 6), 2, 0, "feu",   19, 21, 22, 25),
    (13804, "Dépeçage",    1, 110, 4, 25, (1, 4), 2, 0, "air",   34, 38, 41, 46),
]


@pytest.fixture(scope="module")
def conn():
    if not DB.exists():
        pytest.skip("base absente — lancez `python -m dofus_opti.ingest.build`")
    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    if not connection.execute("SELECT COUNT(*) FROM spell").fetchone()[0]:
        connection.close()
        pytest.skip("base construite sans les sorts (--no-spells)")
    yield connection
    connection.close()


@pytest.mark.parametrize(
    "spell_id, name, grade, min_level, ap, crit, rng, per_turn, per_target,"
    " element, dmg_min, dmg_max, crit_min, crit_max",
    IN_GAME,
    ids=[row[1] for row in IN_GAME],
)
def test_matches_the_in_game_tooltip(
    conn, spell_id, name, grade, min_level, ap, crit, rng, per_turn, per_target,
    element, dmg_min, dmg_max, crit_min, crit_max,
):
    spell = conn.execute(
        "SELECT s.name, b.name AS breed FROM spell s "
        "JOIN breed b ON b.breed_id = s.breed_id WHERE s.spell_id = ?",
        (spell_id,),
    ).fetchone()
    assert spell is not None, f"{name} (id {spell_id}) absent de la base"
    assert spell["name"] == name
    assert spell["breed"] == "Ouginak"

    level = conn.execute(
        "SELECT * FROM spell_level WHERE spell_id = ? AND grade = ?", (spell_id, grade)
    ).fetchone()
    assert level is not None, f"{name} : palier {grade} absent"

    assert level["min_player_level"] == min_level
    assert level["ap_cost"] == ap
    assert level["crit_probability"] == crit
    assert (level["range_min"], level["range_max"]) == rng
    assert level["max_cast_per_turn"] == per_turn
    assert level["max_cast_per_target"] == per_target

    rolls = conn.execute(
        "SELECT element, base_min, base_max, crit_min, crit_max FROM spell_roll "
        "WHERE spell_id = ? AND grade = ? AND over_time = 0",
        (spell_id, grade),
    ).fetchall()
    assert len(rolls) == 1, f"{name} : un seul élément attendu, {len(rolls)} trouvés"

    roll = rolls[0]
    assert roll["element"] == element
    assert (roll["base_min"], roll["base_max"]) == (dmg_min, dmg_max)
    assert (roll["crit_min"], roll["crit_max"]) == (crit_min, crit_max)


def test_zero_stats_reproduce_the_tooltip_exactly(conn):
    """À 0 en tout, le moteur doit rendre la base brute, sans dérive d'arrondi."""
    from dofus_opti.combat.formula import compute_hit
    from dofus_opti.combat.stats import StatVector

    empty = StatVector()
    for _, name, _, _, _, _, _, _, _, element, dmg_min, dmg_max, _, _ in IN_GAME:
        assert compute_hit(dmg_min, element, empty) == dmg_min, name
        assert compute_hit(dmg_max, element, empty) == dmg_max, name


def test_os_a_moelle_ladder_matches_the_in_game_tooltip(conn):
    """Capture en jeu : 12-14 base, puis 16-18 / 20-22 / 24-26 / 28-30 par charge.

    À charges pleines (« Cumul : 4 »), le sort vaut 28-30 : c'est ce que la
    politique « charge max » doit produire, via l'effet 293 du sort compagnon.
    """
    from dofus_opti.combat.catalog import load_spells

    bare = {s.name: s for s in load_spells(conn, "Ouginak", 60, charges="none")}
    full = {s.name: s for s in load_spells(conn, "Ouginak", 60, charges="max")}

    naked = bare["Os à Moelle"].rolls[0]
    charged = full["Os à Moelle"].rolls[0]

    assert (naked.base_min, naked.base_max) == (12, 14)
    assert (charged.base_min, charged.base_max) == (28, 30)
    assert (charged.crit_min, charged.crit_max) == (31, 33)


def test_torrent_arcanique_at_full_combos(conn):
    """2 de base + 2 × 6 combinaisons = 14 par élément (16 en critique)."""
    from dofus_opti.combat.catalog import load_spells

    spells = {s.name: s for s in load_spells(conn, "Huppermage", 200, charges="max")}
    torrent = spells["Torrent Arcanique"]

    assert len(torrent.rolls) == 4
    for roll in torrent.rolls:
        assert (roll.base_min, roll.base_max) == (14, 14)
        assert (roll.crit_min, roll.crit_max) == (16, 16)


def test_every_element_is_covered_by_the_in_game_sample(conn):
    """L'échantillon couvre Terre, Feu, Eau et Air — la correspondance est validée."""
    assert {row[9] for row in IN_GAME} == {"terre", "feu", "eau", "air"}
