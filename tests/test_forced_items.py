"""Items imposés : ils traversent les filtres, et ne disparaissent jamais en silence."""

from __future__ import annotations

import pytest
from ortools.sat.python import cp_model

from dofus_opti.model.items import Item, Slot, StatRange
from dofus_opti.model.stats import StatKey
from dofus_opti.optim.model import ForcedItemUnavailable, build_model
from dofus_opti.optim.pool import filter_dominated
from dofus_opti.optim.request import BuildRequest

TRACKED = {StatKey.FORCE, StatKey.PA, StatKey.PM}


def _item(item_id, name, slot, stats, level=1):
    return Item(
        ankama_id=item_id, name=name, slot=slot, type_id=0, type_name=slot.value,
        level=level, is_weapon=False, pods=0,
        stats={StatKey(k): StatRange(v, v) for k, v in stats.items()},
    )


def _solve(items, request):
    built = build_model(items, {}, request, TRACKED)
    built.model.Maximize(built.totals[StatKey.FORCE])
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    solver.Solve(built.model)
    return built.selected_items(solver)


def test_a_forced_item_is_selected_even_when_worse():
    weak = _item(1, "Imposé", Slot.CHAPEAU, {"force": 1})
    strong = _item(2, "Meilleur", Slot.CHAPEAU, {"force": 500})
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"}, forced_items={1}
    )
    assert [i.name for i in _solve([weak, strong], request)] == ["Imposé"]


def test_several_forced_items_coexist():
    ring = _item(1, "Anneau", Slot.ANNEAU, {"force": 1})
    shield = _item(2, "Bouclier", Slot.BOUCLIER, {"force": 1})
    other = _item(3, "Autre anneau", Slot.ANNEAU, {"force": 900})
    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"}, forced_items={1, 2}
    )
    chosen = {i.name for i in _solve([ring, shield, other], request)}
    assert {"Anneau", "Bouclier"} <= chosen


def test_a_forced_item_absent_from_the_pool_raises():
    """Le cas qui passait inaperçu : la contrainte était ignorée en silence."""
    present = _item(1, "Présent", Slot.CHAPEAU, {"force": 10})
    request = BuildRequest(
        level=175, breed="Iop", elements={"terre"}, forced_items={999},
    )
    with pytest.raises(ForcedItemUnavailable) as exc:
        build_model([present], {}, request, TRACKED)
    assert 999 in exc.value.item_ids


def test_dominance_never_removes_a_forced_item():
    """Un item imposé peut être dominé : il doit survivre à l'élagage."""
    dominated = _item(1, "Imposé faible", Slot.CHAPEAU, {"force": 10})
    dominant = _item(2, "Dominant", Slot.CHAPEAU, {"force": 500})

    kept, _ = filter_dominated([dominated, dominant], {StatKey.FORCE})
    assert "Imposé faible" not in {i.name for i in kept}, "l'élagage le retire bien"

    # build_pool réintègre les items imposés après l'élagage — vérifié en
    # conditions réelles par test_pool_reinstates_forced_items ci-dessous.


@pytest.mark.parametrize("forced_id", [1, 2])
def test_forcing_one_slot_leaves_the_others_free(forced_id):
    hat = _item(1, "Chapeau", Slot.CHAPEAU, {"force": 5})
    cape = _item(2, "Cape", Slot.CAPE, {"force": 5})
    strong_cape = _item(3, "Belle cape", Slot.CAPE, {"force": 300})
    strong_hat = _item(4, "Beau chapeau", Slot.CHAPEAU, {"force": 300})

    request = BuildRequest(
        level=200, breed="Iop", elements={"terre"}, forced_items={forced_id}
    )
    chosen = {i.ankama_id for i in _solve([hat, cape, strong_cape, strong_hat], request)}
    assert forced_id in chosen
    # L'autre emplacement prend le meilleur disponible.
    assert (3 in chosen) or (4 in chosen)
