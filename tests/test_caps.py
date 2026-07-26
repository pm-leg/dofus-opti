"""Plafonds du jeu : PA, PM, PO — et le cas particulier des résistances."""

from __future__ import annotations

import pytest
from ortools.sat.python import cp_model

from dofus_opti.combat.stats import (
    MAX_ACTION_POINTS,
    MAX_MOVEMENT_POINTS,
    MAX_RANGE,
    MAX_RESISTANCE_PCT,
)
from dofus_opti.model.items import Item, Slot, StatRange
from dofus_opti.model.stats import StatKey
from dofus_opti.optim.model import build_model
from dofus_opti.optim.request import BuildRequest, StatBound

TRACKED = {StatKey.PA, StatKey.PM, StatKey.PO, StatKey.RES_PCT_TERRE, StatKey.FORCE}


def _item(item_id, name, slot, stats):
    return Item(
        ankama_id=item_id, name=name, slot=slot, type_id=0, type_name=slot.value,
        level=1, is_weapon=False, pods=0,
        stats={StatKey(k): StatRange(v, v) for k, v in stats.items()},
    )


def _maximise(items, key, request=None):
    request = request or BuildRequest(level=200, breed="Iop", elements={"terre"})
    built = build_model(items, {}, request, TRACKED)
    built.model.Maximize(built.totals[key])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    status = solver.Solve(built.model)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return None
    return solver.Value(built.totals[key])


SLOTS = [Slot.CHAPEAU, Slot.CAPE, Slot.AMULETTE, Slot.CEINTURE, Slot.BOTTES, Slot.BOUCLIER]


def _generous(stat, per_item=1):
    """Un item par emplacement, pour que le plafond soit atteignable au point près.

    Avec des paliers de 4 PA on ne peut pas totaliser 12 exactement (7+4=11,
    7+8=15) : le test échouerait sur la granularité, pas sur le plafond.
    """
    return [
        _item(i + 1, f"Item {i}", SLOTS[i], {stat: per_item, "force": 1})
        for i in range(len(SLOTS))
    ]


@pytest.mark.parametrize(
    "stat, key, cap",
    [
        ("pa", StatKey.PA, MAX_ACTION_POINTS),
        ("pm", StatKey.PM, MAX_MOVEMENT_POINTS),
        ("po", StatKey.PO, MAX_RANGE),
    ],
)
def test_turn_resources_are_capped(stat, key, cap):
    """24 points d'équipement disponibles : le plafond doit mordre."""
    assert _maximise(_generous(stat), key) == cap


def test_range_cap_is_six():
    """Relevé sur 1 500 builds de niveau 200 : 6 est le maximum atteignable."""
    assert MAX_RANGE == 6


def test_resistances_are_not_capped_in_the_model():
    """41 builds publics dépassent 50 % : contraindre le total serait faux.

    Le plafond de 50 porte sur la réduction appliquée en combat, pas sur la
    caractéristique affichée.
    """
    total = _maximise(_generous("res_pct_terre", per_item=15), StatKey.RES_PCT_TERRE)
    assert total > MAX_RESISTANCE_PCT, "le modèle ne doit pas brider la stat"


def test_an_excessive_resistance_constraint_is_flagged_not_refused():
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        bounds={StatKey.RES_PCT_TERRE: StatBound.at_least(70)},
    )
    warnings = request.pointless_constraints()
    assert len(warnings) == 1
    assert "res_pct_terre" in warnings[0]
    assert "50" in warnings[0]

    # La contrainte reste satisfaisable : on avertit, on n'interdit pas.
    total = _maximise(_generous("res_pct_terre", per_item=15),
                      StatKey.RES_PCT_TERRE, request)
    assert total is not None and total >= 70


def test_a_reasonable_resistance_constraint_is_silent():
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        bounds={StatKey.RES_PCT_TERRE: StatBound.at_least(40)},
    )
    assert request.pointless_constraints() == []


def test_an_exact_range_above_the_cap_is_infeasible():
    """Demander 8 PO est impossible : le solveur doit le dire, pas l'arrondir."""
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        bounds={StatKey.PO: StatBound.exactly(8)},
    )
    assert _maximise(_generous("po"), StatKey.PO, request) is None
