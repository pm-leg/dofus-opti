"""Contrat du solveur : ne rien modifier chez l'appelant, tenir son budget."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from dofus_opti.combat.formula import CritPolicy
from dofus_opti.model.items import Slot
from dofus_opti.model.stats import StatKey
from dofus_opti.optim.request import BuildRequest, StatBound
from dofus_opti.optim.solver import optimize

DB = Path(__file__).resolve().parents[1] / "data" / "dofus.db"


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


def _request(**overrides):
    base = dict(
        level=175, breed="Ouginak", elements={"terre"},
        bounds={StatKey.PA: StatBound.exactly(12)},
        crit_policy=CritPolicy.EXPECTED,
        excluded_slots={Slot.MONTURE},
    )
    base.update(overrides)
    return BuildRequest(**base)


@pytest.mark.slow
def test_optimize_does_not_mutate_the_request(conn):
    """Le plafond de Critique déduit des sorts ne doit pas rester collé à l'objet.

    Sinon, réutiliser la requête pour une autre classe conserverait un plafond
    calculé sur les sorts de la précédente.
    """
    request = _request()
    before = dict(request.bounds)

    optimize(conn, request, max_iterations=1, time_limit=8.0)

    assert request.bounds == before
    assert StatKey.CRITIQUE_PCT not in request.bounds


@pytest.mark.slow
def test_the_same_request_reused_gives_the_same_answer(conn):
    """Deux appels identiques doivent rendre le même build.

    Avec assez de temps pour prouver l'optimalité, la réponse est unique. Sous un
    plafond serré, la recherche parallèle peut rendre des solutions différentes :
    c'est pourquoi la graine du solveur est fixée.
    """
    request = _request()
    first = optimize(conn, request, max_iterations=1, time_limit=30.0)
    second = optimize(conn, request, max_iterations=1, time_limit=30.0)

    assert first.status == "OPTIMAL", "budget insuffisant pour un test déterministe"
    assert first.damage == second.damage
    assert {i.ankama_id for i in first.items} == {i.ankama_id for i in second.items}


@pytest.mark.slow
def test_the_time_limit_is_a_global_budget(conn):
    """Demander 10 s ne doit pas en consommer 27 sur cinq itérations."""
    started = time.monotonic()
    optimize(conn, _request(), max_iterations=5, time_limit=10.0)
    elapsed = time.monotonic() - started

    # Marge pour l'évaluation du moteur de dégâts entre deux résolutions.
    assert elapsed < 10.0 * 1.8, f"{elapsed:.1f} s pour un budget de 10 s"


@pytest.mark.slow
def test_a_user_cap_on_critical_is_respected(conn):
    """Le plafond automatique ne doit pas écraser celui du joueur."""
    request = _request(
        bounds={
            StatKey.PA: StatBound.exactly(12),
            StatKey.CRITIQUE_PCT: StatBound.at_most(20),
        }
    )
    solution = optimize(conn, request, max_iterations=1, time_limit=10.0)
    assert solution.solved
    assert solution.totals[StatKey.CRITIQUE_PCT] <= 20
