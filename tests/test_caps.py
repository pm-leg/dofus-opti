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


def test_the_total_resistance_is_not_bounded_by_the_model():
    """Un item retenu pour d'autres qualités peut porter la résistance au-delà
    de 50 : ce n'est pas un défaut, et l'interdire écarterait de bons builds."""
    total = _maximise(_generous("res_pct_terre", per_item=15), StatKey.RES_PCT_TERRE)
    assert total > MAX_RESISTANCE_PCT


def test_an_excessive_resistance_demand_is_brought_back_to_the_cap():
    """Exiger 70 % ferait payer des emplacements pour un gain nul.

    Le jeu n'affiche jamais plus de 50 : on ramène la demande et on le dit.
    """
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        bounds={StatKey.RES_PCT_TERRE: StatBound.at_least(70)},
    )
    adjusted, notes = request.clamped_bounds()

    assert adjusted[StatKey.RES_PCT_TERRE].minimum == MAX_RESISTANCE_PCT
    assert len(notes) == 1
    assert "res_pct_terre" in notes[0] and "50" in notes[0]
    # La requête d'origine n'est pas touchée.
    assert request.bounds[StatKey.RES_PCT_TERRE].minimum == 70


def test_a_maximum_on_resistance_is_left_alone():
    """Seul le plancher est ramené : un plafond posé par le joueur reste sien."""
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        bounds={StatKey.RES_PCT_TERRE: StatBound(minimum=70, maximum=80)},
    )
    adjusted, _ = request.clamped_bounds()
    assert adjusted[StatKey.RES_PCT_TERRE].maximum == 80


def test_a_reasonable_resistance_demand_is_untouched():
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        bounds={StatKey.RES_PCT_TERRE: StatBound.at_least(40)},
    )
    adjusted, notes = request.clamped_bounds()
    assert notes == []
    assert adjusted == request.bounds


def test_other_statistics_are_never_clamped():
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        bounds={StatKey.VITALITE: StatBound.at_least(3000)},
    )
    adjusted, notes = request.clamped_bounds()
    assert notes == []
    assert adjusted[StatKey.VITALITE].minimum == 3000


def test_an_exact_range_above_the_cap_is_infeasible():
    """Demander 8 PO est impossible : le solveur doit le dire, pas l'arrondir."""
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"},
        bounds={StatKey.PO: StatBound.exactly(8)},
    )
    assert _maximise(_generous("po"), StatKey.PO, request) is None
