"""Résolution : linéarisation successive autour du vrai moteur de dégâts.

CP-SAT optimise une fonction linéaire. Les dégâts ne le sont pas — la
caractéristique multiplie la base, et la rotation change par sauts quand les PA
franchissent un seuil. On procède donc par itérations :

1. estimer le poids de chaque caractéristique par différences finies sur le vrai
   moteur, au point courant ;
2. résoudre exactement le problème linéarisé ;
3. évaluer la solution avec le vrai moteur ;
4. recommencer autour du nouveau point.

En pratique la boucle converge en trois ou quatre tours, parce que l'ingestion a
montré que la non-linéarité est faible : aucun item ne donne de `% dommages
finaux`, et les `% dommages` contextuels sont marginaux.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field, replace

from ortools.sat.python import cp_model

from ..combat.catalog import load_spells
from ..combat.rotation import Rotation, best_rotation
from ..combat.spell import Spell
from ..combat.stats import StatVector, base_action_points, base_movement_points
from ..model.items import Item
from ..model.stats import (
    FLAT_DAMAGE_BY_ELEMENT,
    PRIMARY_STAT_BY_ELEMENT,
    StatKey,
)
from ..combat.formula import CritPolicy
from .model import build_model
from .pool import PoolReport, build_pool
from .request import BuildRequest, StatBound

#: pas utilisé pour estimer la pente de chaque caractéristique.
GRADIENT_STEP: dict[StatKey, int] = {
    StatKey.PA: 1,
    StatKey.PM: 1,
    StatKey.PO: 1,
    StatKey.CRITIQUE_PCT: 10,
    StatKey.DOMMAGES: 10,
    StatKey.DOMMAGES_CRITIQUES: 10,
}
DEFAULT_STEP = 25

#: Caractéristiques dont l'effet sur les dégâts est en escalier : leur pente se
#: mesure vers le bas, sinon elle est nulle entre deux seuils.
STEPWISE_STATS = {StatKey.PA, StatKey.PM}

#: En deçà, une itération n'a pas le temps de produire quoi que ce soit d'utile.
MIN_ITERATION_SECONDS = 3.0

#: point de départ de la linéarisation — un build de milieu de tableau, plus
#: représentatif qu'un personnage nu.
SEED_STATS = {
    "puissance": 100,
    "dommages": 40,
    "critique_pct": 30,
    "dommages_critiques": 20,
}
SEED_CHARACTERISTIC = 500


@dataclass
class BuildSolution:
    items: list[Item]
    totals: dict[StatKey, int]
    damage: float
    rotation: Rotation
    status: str
    iterations: int
    pool: PoolReport
    notes: list[str] = field(default_factory=list)

    @property
    def solved(self) -> bool:
        return bool(self.items)


def damage_stats(request: BuildRequest) -> set[StatKey]:
    """Caractéristiques susceptibles de peser sur les dégâts du build."""
    keys = {
        StatKey.PUISSANCE, StatKey.DOMMAGES, StatKey.CRITIQUE_PCT,
        StatKey.DOMMAGES_CRITIQUES, StatKey.PA,
        StatKey.DOMMAGES_PCT_SORTS, StatKey.DOMMAGES_PCT_ARMES,
        StatKey.DOMMAGES_PCT_MELEE, StatKey.DOMMAGES_PCT_DISTANCE,
    }
    for element in request.elements:
        keys.add(PRIMARY_STAT_BY_ELEMENT[element])
        keys.add(FLAT_DAMAGE_BY_ELEMENT[element])
    return keys


def tracked_stats(request: BuildRequest) -> set[StatKey]:
    """Tout ce que le modèle doit savoir totaliser."""
    keys = damage_stats(request)
    keys |= set(request.bounds)
    keys |= {StatKey.PA, StatKey.PM, StatKey.PO}
    # Les conditions d'équipement portent sur ces caractéristiques.
    keys |= {
        StatKey.FORCE, StatKey.INTELLIGENCE, StatKey.CHANCE, StatKey.AGILITE,
        StatKey.VITALITE, StatKey.SAGESSE,
    }
    return keys


def to_stat_vector(totals: dict[StatKey, int], level: int) -> StatVector:
    """Convertit les totaux du modèle en vecteur de caractéristiques d'équipement.

    Le modèle compte PA et PM base du personnage comprise ; on la retire ici pour
    ne pas la compter deux fois. Les PA effectifs sont passés explicitement à la
    rotation, `totals` faisant foi.
    """
    values = dict(totals)
    if StatKey.PA in values:
        values[StatKey.PA] -= base_action_points(level)
    if StatKey.PM in values:
        values[StatKey.PM] -= base_movement_points(level)
    return StatVector(values)


def seed_vector(request: BuildRequest) -> StatVector:
    stats = StatVector().with_(**SEED_STATS)
    for element in request.elements:
        stats = stats.with_(**{PRIMARY_STAT_BY_ELEMENT[element].value: SEED_CHARACTERISTIC})
    # La pente doit être estimée là où se trouve réellement le personnage :
    # 400 points de Force acquis au niveau changent le rendement marginal.
    for key, value in request.base_characteristics.items():
        stats = stats.with_(**{key.value: value})
    pa_bound = request.bound(StatKey.PA)
    target_pa = pa_bound.minimum if pa_bound and pa_bound.minimum else 12
    return stats.with_(pa=max(0, target_pa - base_action_points(request.level)))


def _evaluate(
    spells: list[Spell],
    stats: StatVector,
    request: BuildRequest,
    *,
    ap: int | None = None,
) -> Rotation:
    return best_rotation(
        spells, stats,
        ap=ap if ap is not None else base_action_points(request.level) + stats[StatKey.PA],
        target=request.target,
        variant=request.variant,
        crit_policy=request.crit_policy,
    )


def estimate_weights(
    spells: list[Spell],
    stats: StatVector,
    request: BuildRequest,
) -> dict[StatKey, float]:
    """Pente des dégâts par point de caractéristique, par différences finies."""
    baseline = _evaluate(spells, stats, request).damage
    weights: dict[StatKey, float] = {}

    for key in damage_stats(request):
        step = GRADIENT_STEP.get(key, DEFAULT_STEP)

        if key in STEPWISE_STATS:
            # Les PA ne valent pas par eux-mêmes : ils valent par les lancers
            # qu'ils permettent. La différence en avant est nulle dès qu'on est
            # au plafond ou entre deux seuils, et le solveur brade alors les PA
            # — il tombait à 10 PA, soit trois lancers d'un sort à 3 PA au lieu
            # de quatre. On mesure donc ce que coûterait un point de moins.
            lowered = stats.with_(**{key.value: -step})
            loss = baseline - _evaluate(spells, lowered, request).damage
            if loss:
                weights[key] = loss / step
            continue

        bumped = stats.with_(**{key.value: step})
        gain = _evaluate(spells, bumped, request).damage - baseline
        if gain:
            weights[key] = gain / step
    return weights


def _solve_once(
    items, sets, request, weights, tracked, *, time_limit: float,
) -> tuple[str, dict[StatKey, int] | None, list[Item], list[str]]:
    built = build_model(items, sets, request, tracked)

    scale = 1000
    objective = sum(
        round(weight * scale) * built.totals[key]
        for key, weight in weights.items()
        if key in built.totals and round(weight * scale)
    )
    built.model.Maximize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = 8
    # Sans graine fixe, deux appels identiques rendent des builds différents dès
    # que le plafond de temps interrompt la recherche avant la preuve : les huit
    # fils explorent dans un ordre variable. La graine ne garantit pas le
    # déterminisme absolu — seul un plafond en temps déterministe le ferait, au
    # prix du débit — mais elle supprime la variabilité courante.
    solver.parameters.random_seed = 20260726
    status = solver.Solve(built.model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return solver.StatusName(status), None, [], built.notes

    return (
        solver.StatusName(status),
        built.stat_totals(solver),
        built.selected_items(solver),
        built.notes,
    )


def _cap_useless_critical(request: BuildRequest, spells: list[Spell]) -> BuildRequest:
    """Plafonne la Critique à ce qui sert réellement.

    Le taux critique sature à 100 %. Le sort le plus dur à critiquer fixe donc le
    besoin : `100 − taux de base`. Un point au-delà n'apporte rien, mais le
    solveur, qui optimise une fonction linéaire, continuerait d'en acheter.

    On ne touche pas à un plafond déjà posé par le joueur, et on ne fait rien en
    politique `NEVER`, où la Critique ne sert à rien du tout.

    **Rend une copie** : muter la requête reçue la rendrait dépendante de son
    historique d'appels — un même objet réutilisé pour une autre classe
    conserverait un plafond calculé sur les sorts de la précédente.
    """
    if request.crit_policy is CritPolicy.NEVER:
        return request
    if request.bound(StatKey.CRITIQUE_PCT) is not None:
        return request

    critable = [s.crit_probability for s in spells if s.can_crit]
    if not critable:
        return request

    needed = max(0, 100 - min(critable))
    return replace(
        request,
        bounds={**request.bounds, StatKey.CRITIQUE_PCT: StatBound(maximum=needed)},
    )


def optimize(
    conn: sqlite3.Connection,
    request: BuildRequest,
    *,
    max_iterations: int = 5,
    time_limit: float = 30.0,
) -> BuildSolution:
    """Meilleur stuff pour une demande donnée."""
    spells = load_spells(
        conn, request.breed, request.level,
        elements=request.elements or None,
        names=request.spell_names or None,
        base_overrides=request.spell_bases or None,
        charges=request.charge_policy,
    )
    if not spells:
        return BuildSolution(
            [], {}, 0.0, best_rotation([], StatVector()), "AUCUN_SORT", 0, PoolReport(),
            notes=[f"aucun sort offensif pour {request.breed} en {'/'.join(request.elements)}"],
        )

    # Au-delà de 100 % sur le sort le plus difficile à critiquer, chaque point de
    # Critique est perdu. Sans ce plafond le solveur en empile — il sortait 118 %
    # là où 90 suffisaient, en payant des items pour rien.
    request = _cap_useless_critical(request, spells)

    # Une résistance exigée au-delà du plafond du jeu ferait payer des
    # emplacements pour un gain nul : on ramène la demande, en le disant.
    adjusted, clamp_notes = request.clamped_bounds()
    if clamp_notes:
        request = replace(request, bounds=adjusted)

    point = seed_vector(request)
    weights = estimate_weights(spells, point, request)

    # Le pool dépend des poids : une stat sans poids n'empêche pas l'élagage.
    items, sets, pool_report = build_pool(conn, request, weights)
    tracked = tracked_stats(request)

    best: BuildSolution | None = None
    seen: set[frozenset[int]] = set()
    status = "INCONNU"
    notes: list[str] = []

    # `time_limit` est un budget **global**, pas par itération : l'utilisateur
    # qui demande 45 secondes n'en attend pas 225. Le temps non consommé par une
    # itération qui prouve l'optimalité tôt profite aux suivantes.
    deadline = time.monotonic() + time_limit

    for iteration in range(1, max_iterations + 1):
        remaining = deadline - time.monotonic()
        if remaining < MIN_ITERATION_SECONDS:
            break

        # Chaque itération dispose de **tout** le temps restant. Le répartir à
        # l'avance affamerait la première, qui est celle qui produit la solution :
        # sur un niveau 200, un cinquième du budget ne suffit même pas à en
        # trouver une. Une itération qui prouve l'optimalité tôt rend le reste
        # aux suivantes.
        status, totals, selected, notes = _solve_once(
            items, sets, request, weights, tracked, time_limit=remaining,
        )
        if totals is None:
            break

        stats = to_stat_vector(totals, request.level)
        rotation = _evaluate(spells, stats, request, ap=totals.get(StatKey.PA))

        if best is None or rotation.damage > best.damage:
            best = BuildSolution(
                items=selected, totals=totals, damage=rotation.damage,
                rotation=rotation, status=status, iterations=iteration,
                pool=pool_report, notes=list(notes),
            )

        signature = frozenset(i.ankama_id for i in selected)
        if signature in seen:
            break
        seen.add(signature)

        # Nouveau point de linéarisation : la pente n'est plus la même ici.
        weights = estimate_weights(spells, stats, request)

    if best is not None:
        best.notes.extend(clamp_notes)
        return best

    return BuildSolution(
        [], {}, 0.0, best_rotation([], StatVector()), status, 0, pool_report,
        notes=notes + clamp_notes + ["aucune solution ne satisfait les contraintes"],
    )
