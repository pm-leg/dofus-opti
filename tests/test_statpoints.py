"""Points de caractéristiques, parchemins, et plafond de panoplies."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from ortools.sat.python import cp_model

from dofus_opti.model.items import (
    ConditionOp,
    ConditionSubject,
    Item,
    ItemSet,
    LeafCondition,
    Slot,
    StatRange,
)
from dofus_opti.model.stats import StatKey
from dofus_opti.optim.model import build_model
from dofus_opti.optim.request import BuildRequest
from dofus_opti.optim.statpoints import (
    POINTS_PER_LEVEL,
    allocate,
    base_characteristics,
    points_available,
    unit_cost,
)

DB = Path(__file__).resolve().parents[1] / "data" / "dofus.db"

#: barème commun aux 19 classes pour les caractéristiques élémentaires.
TIERS = [(0, 1), (100, 2), (200, 3), (300, 4)]


# ------------------------------------------------------------------ barème

@pytest.mark.parametrize(
    "value, expected", [(0, 1), (99, 1), (100, 2), (199, 2), (200, 3), (300, 4), (900, 4)]
)
def test_unit_cost_follows_the_tiers(value, expected):
    assert unit_cost(value, TIERS) == expected


def test_base_hit_points_grow_with_the_level():
    from dofus_opti.optim.statpoints import base_hit_points

    assert base_hit_points(1) == 50
    assert base_hit_points(175) == 920
    assert base_hit_points(200) == 1045
    # 1 point de Vitalité = 1 point de vie : les PV totaux s'en déduisent.
    assert base_hit_points(175) + 1900 == 2820


def test_points_available_is_five_per_level():
    assert points_available(1) == 0
    assert points_available(175) == 174 * POINTS_PER_LEVEL == 870
    assert points_available(200) == 995


# ------------------------------------------------------------- répartition

def test_allocation_crosses_tiers_correctly():
    """100 unités à 1 point, 100 à 2, 100 à 3 : 600 points donnent 300."""
    result = allocate(StatKey.FORCE, 600, TIERS)
    assert result.invested == 300
    assert result.points_spent == 600
    assert result.points_left == 0


def test_scrolls_do_not_raise_the_cost_of_later_points():
    """Relevé en jeu : Iop 175 parcheminé 100 → 467 de Force, pas 392.

    Le parchemin s'ajoute au total sans décaler le barème : les 870 points
    s'investissent comme si la caractéristique partait de zéro.
    """
    result = allocate(StatKey.FORCE, points_available(175), TIERS, scroll=100)

    assert result.invested == 367
    assert result.scroll == 100
    assert result.value == 467
    assert result.points_left == 2, "deux points restent inutilisables"


def test_scrolls_are_a_flat_addition():
    plain = allocate(StatKey.FORCE, 870, TIERS)
    scrolled = allocate(StatKey.FORCE, 870, TIERS, scroll=100)
    assert scrolled.invested == plain.invested
    assert scrolled.value == plain.value + 100


def test_allocation_leaves_unspendable_remainder():
    result = allocate(StatKey.FORCE, 3, TIERS)
    assert result.invested == 3  # trois unités au premier palier
    result = allocate(StatKey.FORCE, 0, TIERS)
    assert result.invested == 0


def test_description_mentions_both_sources():
    text = allocate(StatKey.FORCE, 870, TIERS, scroll=100).describe()
    assert "367 investis" in text
    assert "100 de parchemins" in text


# --------------------------------------------------------------- catalogue

@pytest.fixture(scope="module")
def conn():
    if not DB.exists():
        pytest.skip("base absente — lancez `python -m dofus_opti.ingest.build`")
    connection = sqlite3.connect(DB)
    if not connection.execute("SELECT COUNT(*) FROM breed_stat_cost").fetchone()[0]:
        connection.close()
        pytest.skip("barèmes non ingérés")
    yield connection
    connection.close()


def test_every_class_has_a_complete_cost_table(conn):
    rows = conn.execute(
        "SELECT breed_id, COUNT(DISTINCT stat) n FROM breed_stat_cost GROUP BY breed_id"
    ).fetchall()
    assert len(rows) == 19
    for _, distinct_stats in rows:
        assert distinct_stats == 6, "six caractéristiques attribuables attendues"


def test_full_strength_iop_at_175(conn):
    """Valeur confirmée en jeu par un joueur : 467 de Force de base."""
    base, allocations = base_characteristics(
        conn, "Iop", 175, invest=StatKey.FORCE, scrolled=100
    )
    assert len(allocations) == 1
    assert base[StatKey.FORCE] == allocations[0].value == 467
    # Les autres caractéristiques restent au niveau des parchemins.
    assert base[StatKey.INTELLIGENCE] == 100


def test_no_investment_leaves_only_the_scrolls(conn):
    base, allocations = base_characteristics(conn, "Iop", 175, invest=None, scrolled=100)
    assert allocations == []
    assert set(base.values()) == {100}


def test_no_scrolls_no_investment_gives_nothing(conn):
    base, allocations = base_characteristics(conn, "Iop", 175, invest=None)
    assert base == {}
    assert allocations == []


def test_points_are_spread_evenly_across_several_characteristics(conn):
    """Un sort multi-élément veut les quatre caractéristiques, pas une seule.

    Le barème étant croissant, équilibrer achète plus de points au total que
    concentrer : 4 × 174 coûte moins cher que 696 dans une seule.
    """
    elements = [StatKey.FORCE, StatKey.INTELLIGENCE, StatKey.CHANCE, StatKey.AGILITE]
    base, allocations = base_characteristics(
        conn, "Huppermage", 200, invest=elements, scrolled=100
    )

    invested = sorted(a.invested for a in allocations)
    assert len(allocations) == 4
    assert max(invested) - min(invested) <= 1, "les quatre doivent être équilibrées"
    assert sum(a.points_spent for a in allocations) <= points_available(200)

    concentrated = allocate(
        StatKey.FORCE, points_available(200),
        [(0, 1), (100, 2), (200, 3), (300, 4)],
    )
    assert sum(invested) > concentrated.invested


def test_an_unassignable_characteristic_is_refused(conn):
    with pytest.raises(ValueError):
        base_characteristics(conn, "Iop", 175, invest=StatKey.PUISSANCE)


# ------------------------------------------- prise en compte par le solveur

def _item(item_id, name, slot, stats, set_id=None, condition=None):
    return Item(
        ankama_id=item_id, name=name, slot=slot, type_id=0, type_name=slot.value,
        level=1, is_weapon=False, pods=0, set_id=set_id, condition=condition,
        stats={StatKey(k): StatRange(v, v) for k, v in stats.items()},
    )


def _solve(items, sets, request, tracked):
    built = build_model(items, sets, request, tracked)
    built.model.Maximize(built.totals[StatKey.FORCE])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    solver.Solve(built.model)
    # Un bonus par item au-delà du premier.
    bonuses = sum(
        solver.Value(flag) for flags in built.set_active.values() for flag in flags.values()
    )
    return built.selected_items(solver), built.stat_totals(solver), bonuses


def test_base_characteristics_count_in_the_totals():
    hat = _item(1, "Chapeau", Slot.CHAPEAU, {"force": 50})
    request = BuildRequest(
        level=175, breed="Iop", elements={"terre"},
        base_characteristics={StatKey.FORCE: 392},
    )
    _, totals, _ = _solve([hat], {}, request, {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert totals[StatKey.FORCE] == 392 + 50


def test_base_characteristics_satisfy_equipment_conditions():
    """« Force > 300 » doit pouvoir être rempli par les points de niveau seuls."""
    condition = LeafCondition(ConditionSubject.STAT, ConditionOp.GT, 300, StatKey.FORCE)
    gated = _item(1, "Exigeant", Slot.CHAPEAU, {"force": 10}, condition=condition)

    without = BuildRequest(level=175, breed="Iop", elements={"terre"})
    selected, _, _ = _solve([gated], {}, without, {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert selected == [], "sans points de niveau, la condition n'est pas remplie"

    with_points = BuildRequest(
        level=175, breed="Iop", elements={"terre"},
        base_characteristics={StatKey.FORCE: 392},
    )
    selected, _, _ = _solve([gated], {}, with_points, {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert [i.name for i in selected] == ["Exigeant"]


SLOTS = [Slot.CHAPEAU, Slot.CAPE, Slot.AMULETTE, Slot.CEINTURE,
         Slot.BOTTES, Slot.BOUCLIER, Slot.ARME, Slot.ANNEAU]


def _sets_of_size(sizes, *, with_trophy=True, per_item_force=10):
    """Construit des panoplies de tailles données, plus un trophée conditionné."""
    items, sets = [], {}
    item_id = 1
    slot_index = 0
    for index, size in enumerate(sizes):
        for _ in range(size):
            items.append(
                _item(item_id, f"Set{index}-{slot_index}", SLOTS[slot_index],
                      {"force": per_item_force}, set_id=100 + index)
            )
            item_id += 1
            slot_index += 1
        sets[100 + index] = ItemSet(
            ankama_id=100 + index, name=f"Panoplie {index}", level=1, n_items=size,
            bonuses={n: {StatKey.FORCE: StatRange(500 * (n - 1), 500 * (n - 1))}
                     for n in range(2, size + 1)},
        )
    if with_trophy:
        condition = LeafCondition(ConditionSubject.SET_BONUS_COUNT, ConditionOp.LT, 3, None)
        items.append(
            _item(999, "Trophée", Slot.DOFUS, {"force": 100000}, condition=condition)
        )
    return items, sets


def test_a_pair_counts_as_one_bonus():
    items, sets = _sets_of_size([2], with_trophy=False)
    request = BuildRequest(level=200, breed="Iop", elements={"terre"})
    _, _, bonuses = _solve(items, sets, request, {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert bonuses == 1


def test_three_items_of_one_set_count_as_two_bonuses():
    """C'est le point que le jeu explique mal : n items donnent n − 1 bonus."""
    items, sets = _sets_of_size([3], with_trophy=False)
    request = BuildRequest(level=200, breed="Iop", elements={"terre"})
    _, _, bonuses = _solve(items, sets, request, {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert bonuses == 2


def test_two_sets_of_three_would_make_four_bonuses():
    items, sets = _sets_of_size([3, 3], with_trophy=False)
    request = BuildRequest(level=200, breed="Iop", elements={"terre"})
    _, _, bonuses = _solve(items, sets, request, {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert bonuses == 4


def test_a_trophy_forbids_two_sets_of_three():
    """Avec « < 3 », on garde une panoplie de 3 (2 bonus), pas deux."""
    items, sets = _sets_of_size([3, 3])
    request = BuildRequest(level=200, breed="Iop", elements={"terre"})
    selected, _, bonuses = _solve(
        items, sets, request, {StatKey.FORCE, StatKey.PA, StatKey.PM}
    )

    assert any(i.ankama_id == 999 for i in selected), "le trophée doit rester rentable"
    assert bonuses <= 2, f"{bonuses} bonus alors que la condition impose moins de 3"


def test_a_trophy_allows_two_pairs():
    """Deux panoplies de 2 items font 2 bonus : c'est autorisé."""
    items, sets = _sets_of_size([2, 2])
    request = BuildRequest(level=200, breed="Iop", elements={"terre"})
    selected, _, bonuses = _solve(
        items, sets, request, {StatKey.FORCE, StatKey.PA, StatKey.PM}
    )
    assert any(i.ankama_id == 999 for i in selected)
    assert bonuses == 2


def test_the_player_can_tighten_the_set_limit():
    items, sets = _sets_of_size([3, 3], with_trophy=False)
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"}, max_set_bonuses=1
    )
    _, _, bonuses = _solve(items, sets, request, {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert bonuses <= 1
