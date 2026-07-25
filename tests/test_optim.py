"""Solveur : élagage, modèle de contraintes et résolution."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from ortools.sat.python import cp_model

from dofus_opti.combat.stats import base_action_points, base_movement_points
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
from dofus_opti.optim.pool import filter_dominated, relevant_stats
from dofus_opti.optim.request import BuildRequest, StatBound

DB = Path(__file__).resolve().parents[1] / "data" / "dofus.db"


def item(item_id, name, slot, stats, *, set_id=None, condition=None, level=1):
    return Item(
        ankama_id=item_id, name=name, slot=slot, type_id=0, type_name=slot.value,
        level=level, is_weapon=False, pods=0, set_id=set_id, condition=condition,
        stats={StatKey(k): StatRange(v, v) for k, v in stats.items()},
    )


def request(**overrides):
    base = dict(level=200, breed="Iop", elements={"terre"})
    base.update(overrides)
    return BuildRequest(**base)


def solve(items, sets, req, tracked, objective_stat=StatKey.FORCE):
    built = build_model(items, sets, req, tracked)
    built.model.Maximize(built.totals[objective_stat])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    status = solver.Solve(built.model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, None
    return built.selected_items(solver), built.stat_totals(solver)


# ------------------------------------------------------------------ dominance

def test_a_strictly_worse_item_is_removed():
    good = item(1, "Bon", Slot.CHAPEAU, {"force": 100, "vitalite": 50})
    bad = item(2, "Moins bon", Slot.CHAPEAU, {"force": 80, "vitalite": 40})
    kept, removed = filter_dominated([good, bad], {StatKey.FORCE, StatKey.VITALITE})

    assert [i.name for i in kept] == ["Bon"]
    assert removed == 1


def test_a_trade_off_is_never_removed():
    a = item(1, "Force", Slot.CHAPEAU, {"force": 100, "vitalite": 10})
    b = item(2, "Vita", Slot.CHAPEAU, {"force": 10, "vitalite": 100})
    kept, removed = filter_dominated([a, b], {StatKey.FORCE, StatKey.VITALITE})

    assert len(kept) == 2
    assert removed == 0


def test_domination_never_crosses_sets():
    """Un item plus faible peut ouvrir un bonus de panoplie qui compense."""
    strong = item(1, "Hors panoplie", Slot.CHAPEAU, {"force": 100})
    weak = item(2, "De panoplie", Slot.CHAPEAU, {"force": 10}, set_id=42)
    kept, _ = filter_dominated([strong, weak], {StatKey.FORCE})

    assert len(kept) == 2


def test_domination_ignores_stats_outside_the_objective():
    """La Prospection ne doit pas rendre deux chapeaux incomparables."""
    good = item(1, "Bon", Slot.CHAPEAU, {"force": 100, "prospection": 0})
    bad = item(2, "Moins bon", Slot.CHAPEAU, {"force": 80, "prospection": 50})
    kept, _ = filter_dominated([good, bad], {StatKey.FORCE})

    assert [i.name for i in kept] == ["Bon"]


def test_a_conditioned_item_cannot_dominate_an_unconditioned_one():
    condition = LeafCondition(ConditionSubject.STAT, ConditionOp.GT, 500, StatKey.FORCE)
    conditioned = item(1, "Conditionné", Slot.CHAPEAU, {"force": 100}, condition=condition)
    free = item(2, "Libre", Slot.CHAPEAU, {"force": 80})
    kept, _ = filter_dominated([conditioned, free], {StatKey.FORCE})

    assert {i.name for i in kept} == {"Conditionné", "Libre"}


def test_relevant_stats_always_include_the_turn_resources():
    keys = relevant_stats(request(), {StatKey.FORCE: 1.0})
    assert {StatKey.PA, StatKey.PM, StatKey.PO} <= keys


# ------------------------------------------------------- modèle de contraintes

def test_slot_capacity_is_enforced():
    rings = [item(i, f"Anneau {i}", Slot.ANNEAU, {"force": 100}) for i in range(1, 6)]
    selected, _ = solve(rings, {}, request(), {StatKey.FORCE, StatKey.PA, StatKey.PM})

    assert len(selected) == 2, "deux anneaux au maximum"


def test_a_pet_and_a_mount_cannot_be_worn_together():
    """En jeu c'est le même emplacement : l'un ou l'autre, jamais les deux."""
    candidates = [
        item(1, "Familier", Slot.FAMILIER, {"force": 100}),
        item(2, "Monture", Slot.MONTURE, {"force": 150}),
    ]
    selected, totals = solve(
        candidates, {}, request(), {StatKey.FORCE, StatKey.PA, StatKey.PM}
    )

    assert len(selected) == 1
    assert [i.name for i in selected] == ["Monture"], "le meilleur des deux est retenu"
    assert totals[StatKey.FORCE] == 150


def test_six_dofus_slots():
    dofus = [item(i, f"Dofus {i}", Slot.DOFUS, {"force": 10}) for i in range(1, 10)]
    selected, _ = solve(dofus, {}, request(), {StatKey.FORCE, StatKey.PA, StatKey.PM})

    assert len(selected) == 6


def test_action_points_include_the_character_base():
    hat = item(1, "Chapeau", Slot.CHAPEAU, {"pa": 2, "force": 10})
    _, totals = solve([hat], {}, request(level=200), {StatKey.FORCE, StatKey.PA, StatKey.PM})

    assert totals[StatKey.PA] == base_action_points(200) + 2
    assert totals[StatKey.PM] == base_movement_points(200)


def test_action_and_movement_points_are_capped():
    """Sans plafond, un sort à lancers illimités fait empiler les PA sans fin."""
    generous = [
        item(1, "Chapeau", Slot.CHAPEAU, {"pa": 4, "pm": 3}),
        item(2, "Cape", Slot.CAPE, {"pa": 4, "pm": 3}),
        item(3, "Ceinture", Slot.CEINTURE, {"pa": 4, "pm": 3}),
        item(4, "Bottes", Slot.BOTTES, {"pa": 4, "pm": 3}),
    ]
    built = build_model(generous, {}, request(level=200), {StatKey.PA, StatKey.PM})
    built.model.Maximize(built.totals[StatKey.PA] + built.totals[StatKey.PM])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    solver.Solve(built.model)

    assert solver.Value(built.totals[StatKey.PA]) <= 12
    assert solver.Value(built.totals[StatKey.PM]) <= 6


def test_the_level_100_action_point_is_counted():
    """Un personnage gagne un PA définitif au niveau 100."""
    hat = item(1, "Chapeau", Slot.CHAPEAU, {"pa": 5, "force": 10})

    _, low = solve([hat], {}, request(level=99), {StatKey.PA, StatKey.PM, StatKey.FORCE})
    _, high = solve([hat], {}, request(level=100), {StatKey.PA, StatKey.PM, StatKey.FORCE})

    assert low[StatKey.PA] == 6 + 5
    assert high[StatKey.PA] == 7 + 5


def test_an_exact_bound_beats_a_better_objective():
    """Le meilleur item sur l'objectif doit être écarté s'il rate la cible de PA.

    À niveau 175 la base vaut 7 : atteindre 12 demande exactement 5 PA d'équipement.
    """
    items = [
        item(1, "Trop de PA", Slot.CHAPEAU, {"pa": 6, "force": 500}),   # 7 + 6 = 13
        item(2, "Juste ce qu'il faut", Slot.CHAPEAU, {"pa": 5, "force": 10}),
    ]
    req = request(level=175, bounds={StatKey.PA: StatBound.exactly(12)})
    selected, totals = solve(items, {}, req, {StatKey.FORCE, StatKey.PA, StatKey.PM})

    assert totals[StatKey.PA] == 12
    assert [i.name for i in selected] == ["Juste ce qu'il faut"]


def test_infeasible_constraints_yield_no_solution():
    hat = item(1, "Chapeau", Slot.CHAPEAU, {"force": 10})
    req = request(bounds={StatKey.PA: StatBound.exactly(99)})
    selected, _ = solve([hat], {}, req, {StatKey.FORCE, StatKey.PA, StatKey.PM})

    assert selected is None


def test_a_minimum_bound_is_respected():
    items = [
        item(1, "Sans vita", Slot.CHAPEAU, {"force": 500}),
        item(2, "Avec vita", Slot.CHAPEAU, {"force": 100, "vitalite": 1000}),
    ]
    req = request(bounds={StatKey.VITALITE: StatBound.at_least(500)})
    selected, totals = solve(
        items, {}, req, {StatKey.FORCE, StatKey.VITALITE, StatKey.PA, StatKey.PM}
    )

    assert [i.name for i in selected] == ["Avec vita"]
    assert totals[StatKey.VITALITE] >= 500


# ---------------------------------------------------------- bonus de panoplie

def _set_of_three():
    items = [
        item(1, "Chapeau", Slot.CHAPEAU, {"force": 10}, set_id=7),
        item(2, "Cape", Slot.CAPE, {"force": 10}, set_id=7),
        item(3, "Bottes", Slot.BOTTES, {"force": 10}, set_id=7),
    ]
    sets = {
        7: ItemSet(
            ankama_id=7, name="Panoplie", level=1, n_items=3,
            bonuses={
                2: {StatKey.FORCE: StatRange(20, 20)},
                3: {StatKey.FORCE: StatRange(30, 30)},
            },
        )
    }
    return items, sets


def test_set_bonus_tiers_replace_rather_than_accumulate():
    """Trois items donnent 30, pas 20 + 30."""
    items, sets = _set_of_three()
    _, totals = solve(items, sets, request(), {StatKey.FORCE, StatKey.PA, StatKey.PM})

    assert totals[StatKey.FORCE] == 30 + 30  # 3 items × 10 + bonus de palier 3


def test_set_bonus_requires_enough_items():
    items, sets = _set_of_three()
    req = request(excluded_slots={Slot.BOTTES})
    kept = [i for i in items if i.slot is not Slot.BOTTES]
    _, totals = solve(kept, sets, req, {StatKey.FORCE, StatKey.PA, StatKey.PM})

    assert totals[StatKey.FORCE] == 20 + 20  # 2 items × 10 + bonus de palier 2


def test_a_single_set_item_grants_no_bonus():
    items, sets = _set_of_three()
    _, totals = solve(items[:1], sets, request(), {StatKey.FORCE, StatKey.PA, StatKey.PM})

    assert totals[StatKey.FORCE] == 10


# ------------------------------------------------- conditions d'équipement

def test_an_item_requiring_strength_is_rejected_without_it():
    condition = LeafCondition(ConditionSubject.STAT, ConditionOp.GT, 500, StatKey.FORCE)
    gated = item(1, "Exigeant", Slot.CHAPEAU, {"vitalite": 1000}, condition=condition)
    plain = item(2, "Simple", Slot.CHAPEAU, {"vitalite": 10})

    selected, _ = solve(
        [gated, plain], {}, request(),
        {StatKey.FORCE, StatKey.VITALITE, StatKey.PA, StatKey.PM},
        objective_stat=StatKey.VITALITE,
    )
    assert [i.name for i in selected] == ["Simple"]


def test_an_item_requiring_strength_is_accepted_when_another_provides_it():
    condition = LeafCondition(ConditionSubject.STAT, ConditionOp.GT, 500, StatKey.FORCE)
    gated = item(1, "Exigeant", Slot.CHAPEAU, {"vitalite": 1000}, condition=condition)
    provider = item(2, "Fournisseur", Slot.CAPE, {"force": 600})

    selected, totals = solve(
        [gated, provider], {}, request(),
        {StatKey.FORCE, StatKey.VITALITE, StatKey.PA, StatKey.PM},
        objective_stat=StatKey.VITALITE,
    )
    assert {i.name for i in selected} == {"Exigeant", "Fournisseur"}
    assert totals[StatKey.FORCE] > 500


def test_a_level_condition_is_resolved_statically():
    condition = LeafCondition(ConditionSubject.LEVEL, ConditionOp.GT, 190, None)
    gated = item(1, "Haut niveau", Slot.CHAPEAU, {"force": 100}, condition=condition)

    selected, _ = solve([gated], {}, request(level=150), {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert selected == []

    selected, _ = solve([gated], {}, request(level=200), {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert [i.name for i in selected] == ["Haut niveau"]


def test_forced_items_are_always_selected():
    forced = item(1, "Imposé", Slot.CHAPEAU, {"force": 1})
    better = item(2, "Meilleur", Slot.CHAPEAU, {"force": 500})
    req = request(forced_items={1})

    selected, _ = solve([forced, better], {}, req, {StatKey.FORCE, StatKey.PA, StatKey.PM})
    assert [i.name for i in selected] == ["Imposé"]


def test_pool_reinstates_a_forced_item_that_filters_would_drop(conn):
    """Imposer un item doit le ramener au pool, même dominé ou inobtenable."""
    from dofus_opti.optim.pool import build_pool, load_items

    weights = {StatKey.FORCE: 1.0, StatKey.PUISSANCE: 1.0}
    plain = BuildRequest(level=175, breed="Ouginak", elements={"terre"})
    kept, _, _ = build_pool(conn, plain, weights)
    survivors = {i.ankama_id for i in kept}

    # Un item que les filtres viennent d'écarter — peu importe lequel.
    dropped = next(
        i for i in load_items(conn, 175) if i.ankama_id not in survivors
    )

    imposed = BuildRequest(
        level=175, breed="Ouginak", elements={"terre"},
        forced_items={dropped.ankama_id},
    )
    kept, _, _ = build_pool(conn, imposed, weights)
    assert dropped.ankama_id in {i.ankama_id for i in kept}, dropped.name


# ------------------------------------------------------- bout en bout

@pytest.fixture(scope="module")
def conn():
    if not DB.exists():
        pytest.skip("base absente — lancez `python -m dofus_opti.ingest.build`")
    connection = sqlite3.connect(DB)
    if not connection.execute("SELECT COUNT(*) FROM spell").fetchone()[0]:
        connection.close()
        pytest.skip("base construite sans les sorts")
    yield connection
    connection.close()


def test_obtainability_keeps_mounts_and_pets(conn):
    """Montures et familiers ne se droppent ni ne se craftent : le critère ne
    s'applique pas à eux."""
    from dofus_opti.optim.pool import load_obtainability

    obtainable = load_obtainability(conn)
    mounts = conn.execute(
        "SELECT ankama_id, name FROM item WHERE slot = 'monture'"
    ).fetchall()
    kept = [name for item_id, name in mounts if obtainable[item_id]]
    assert len(kept) > 250, "les montures ne doivent pas être écartées en masse"


def test_obtainability_rejects_admin_items(conn):
    from dofus_opti.optim.pool import load_obtainability

    obtainable = load_obtainability(conn)
    for item_id, name in conn.execute(
        "SELECT ankama_id, name FROM item WHERE name LIKE '%(MJ)%'"
    ):
        assert not obtainable[item_id], name


#: Verdicts fournis par un joueur sur des cas limites du filtre d'obtention.
#: Règle générale énoncée : un item lié au personnage, ou conditionné à un pseudo
#: ou à une quête en cours, n'est pas planifiable dans un stuff.
PLAYER_VERDICTS = [
    ("Le Ramboton", False),
    ("Masque mortuaire", False),
    ("Ménologium béni", False),
    ("Lame de Danaba", False),
    ("Faux Maudite du Saigneur Guerrier", True),
    ("Amulette Ementaire Deluxe", True),
    ("Surpuissant Chacha de Combat (MJ)", False),
    ("Annobusé de Maître Jarbo", False),
    ("Gelano Ankarton", False),
    ("Dofus Sylvestre", True),
    ("Dofus Verdoyant", True),
    ("Capikténia", True),
]


@pytest.mark.parametrize("name, expected", PLAYER_VERDICTS, ids=[n for n, _ in PLAYER_VERDICTS])
def test_matches_the_player_verdict(conn, name, expected):
    from dofus_opti.optim.pool import load_obtainability

    obtainable = load_obtainability(conn)
    row = conn.execute("SELECT ankama_id FROM item WHERE name = ?", (name,)).fetchone()
    assert row is not None, f"{name} absent du catalogue"
    assert obtainable[row[0]] is expected


def test_bound_items_are_rejected_outside_the_dofus_slot(conn):
    """« Lié au personnage » : ni échangeable ni achetable, donc non planifiable."""
    from dofus_opti.optim.pool import load_obtainability

    obtainable = load_obtainability(conn)
    bound = conn.execute(
        "SELECT ankama_id, name, slot FROM item WHERE bound = 1"
    ).fetchall()
    assert bound, "aucun item lié : le signal n'a pas été ingéré"

    for item_id, name, slot in bound:
        if slot == "dofus":
            assert obtainable[item_id], f"{name} : les Dofus de quête restent au pool"
        else:
            assert not obtainable[item_id], name


def test_quest_dofus_survive_the_filter(conn):
    from dofus_opti.optim.pool import load_obtainability

    obtainable = load_obtainability(conn)
    for name in ("Dofus Ocre", "Dofus Pourpre", "Dofus Émeraude"):
        row = conn.execute("SELECT ankama_id FROM item WHERE name = ?", (name,)).fetchone()
        if row:
            assert obtainable[row[0]], name


@pytest.mark.slow
def test_end_to_end_respects_every_constraint(conn):
    from dofus_opti.optim.solver import optimize

    req = request(
        level=175,
        bounds={
            StatKey.PA: StatBound.exactly(12),
            StatKey.PM: StatBound.exactly(5),
            StatKey.PO: StatBound.exactly(0),
        },
    )
    solution = optimize(conn, req, max_iterations=1, time_limit=25.0)

    assert solution.solved, solution.notes
    assert solution.totals[StatKey.PA] == 12
    assert solution.totals[StatKey.PM] == 5
    assert solution.totals[StatKey.PO] == 0
    assert solution.damage > 0

    # Aucun emplacement ne doit être sur-rempli.
    from dofus_opti.model.items import SLOT_CAPACITY

    counts: dict[Slot, int] = {}
    for chosen in solution.items:
        counts[chosen.slot] = counts.get(chosen.slot, 0) + 1
    for slot, count in counts.items():
        assert count <= SLOT_CAPACITY[slot], slot

    # Tous les items doivent être accessibles au niveau demandé.
    assert all(chosen.level <= 175 for chosen in solution.items)
