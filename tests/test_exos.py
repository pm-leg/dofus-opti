"""Exotiques disponibles, déclarés au niveau du build."""

from __future__ import annotations

import pytest
from ortools.sat.python import cp_model

from dofus_opti.combat.stats import MAX_ACTION_POINTS
from dofus_opti.export.dofusdb import EXO_STAT_IDS, build_payload
from dofus_opti.model.items import Item, Slot, StatRange
from dofus_opti.model.stats import StatKey
from dofus_opti.optim.model import build_model
from dofus_opti.optim.request import BuildRequest, StatBound


def _item(item_id, name, slot, stats):
    return Item(
        ankama_id=item_id, name=name, slot=slot, type_id=0, type_name=slot.value,
        level=1, is_weapon=False, pods=0,
        stats={StatKey(k): StatRange(v, v) for k, v in stats.items()},
    )


def _solve(items, request, tracked, objective=StatKey.FORCE):
    built = build_model(items, {}, request, tracked)
    built.model.Maximize(built.totals[objective])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    status = solver.Solve(built.model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None, None
    return built.selected_items(solver), built.stat_totals(solver)


TRACKED = {StatKey.PA, StatKey.PM, StatKey.FORCE}


def test_an_exo_adds_to_the_total():
    hat = _item(1, "Chapeau", Slot.CHAPEAU, {"pa": 2, "force": 10})
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        exos={StatKey.PA: 1, StatKey.PM: 1},
    )
    _, totals = _solve([hat], request, TRACKED)

    assert totals[StatKey.PA] == 7 + 2 + 1
    assert totals[StatKey.PM] == 3 + 1


def test_an_exo_counts_towards_the_game_cap():
    """L'exo ne permet pas de dépasser les 12 PA du jeu."""
    items = [
        _item(1, "Chapeau", Slot.CHAPEAU, {"pa": 3, "force": 10}),
        _item(2, "Cape", Slot.CAPE, {"pa": 3, "force": 10}),
    ]
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"}, exos={StatKey.PA: 1}
    )
    _, totals = _solve(items, request, TRACKED)
    assert totals[StatKey.PA] <= MAX_ACTION_POINTS


def test_an_exo_helps_reach_an_exact_bound():
    """Avec un exo PA, il ne faut plus que 4 PA d'équipement pour en totaliser 12."""
    items = [
        _item(1, "Cinq PA", Slot.CHAPEAU, {"pa": 5, "force": 10}),
        _item(2, "Quatre PA", Slot.CHAPEAU, {"pa": 4, "force": 500}),
    ]
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        bounds={StatKey.PA: StatBound.exactly(12)},
        exos={StatKey.PA: 1},
    )
    selected, totals = _solve(items, request, TRACKED)

    assert totals[StatKey.PA] == 12
    assert [i.name for i in selected] == ["Quatre PA"]


def test_an_exo_satisfies_an_equipment_condition():
    """Les exos comptent dans les totaux, donc dans les conditions d'équipement."""
    from dofus_opti.model.items import ConditionOp, ConditionSubject, LeafCondition

    condition = LeafCondition(ConditionSubject.STAT, ConditionOp.GT, 11, StatKey.PA)
    gated = Item(
        ankama_id=1, name="Exigeant", slot=Slot.CHAPEAU, type_id=0,
        type_name="chapeau", level=1, is_weapon=False, pods=0,
        stats={StatKey.PA: StatRange(4, 4), StatKey.FORCE: StatRange(10, 10)},
        condition=condition,
    )
    without = BuildRequest(level=200, breed="Iop", elements={"terre"})
    selected, _ = _solve([gated], without, TRACKED)
    assert selected == [], "7 + 4 = 11, la condition n'est pas remplie"

    with_exo = BuildRequest(
        level=200, breed="Iop", elements={"terre"}, exos={StatKey.PA: 1}
    )
    selected, _ = _solve([gated], with_exo, TRACKED)
    assert [i.name for i in selected] == ["Exigeant"]


def test_no_exo_changes_nothing():
    hat = _item(1, "Chapeau", Slot.CHAPEAU, {"pa": 2, "force": 10})
    plain = BuildRequest(level=200, breed="Iop", elements={"terre"})
    _, totals = _solve([hat], plain, TRACKED)
    assert totals[StatKey.PA] == 7 + 2


@pytest.mark.parametrize("stat", [StatKey.PA, StatKey.PM, StatKey.PO])
def test_exos_are_exported_to_dofusdb(stat):
    payload, report = build_payload(
        [_item(1, "Chapeau", Slot.CHAPEAU, {"force": 10})],
        name="Test", level=200, breed_id=17, exos={stat: 1},
    )
    assert payload["exo"] == [{"stat": EXO_STAT_IDS[stat], "value": 1}]
    assert not report.warnings
