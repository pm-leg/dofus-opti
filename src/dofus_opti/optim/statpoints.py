"""Points de caractéristiques gagnés en montant de niveau.

Ils comptent double dans le calcul : ils alimentent directement les dégâts, et
ils comptent dans les conditions d'équipement (« Force > 500 »). Les ignorer
sous-estime le personnage de plusieurs centaines de points.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from ..model.stats import StatKey

#: Points gagnés à chaque montée de niveau.
POINTS_PER_LEVEL = 5

#: Points de vie d'un personnage niveau 1, avant toute Vitalité.
BASE_HP_AT_LEVEL_1 = 50
#: Points de vie gagnés à chaque montée de niveau.
HP_PER_LEVEL = 5


def base_hit_points(level: int) -> int:
    """Points de vie du personnage avant équipement, points et parchemins.

    1 point de Vitalité vaut 1 point de vie : les PV totaux valent donc
    `base_hit_points(niveau) + Vitalité totale`.

    Formule à confirmer en jeu — c'est une constante, un écart se verrait
    immédiatement sur la feuille de personnage.
    """
    return BASE_HP_AT_LEVEL_1 + HP_PER_LEVEL * max(0, level - 1)

#: Valeur atteinte par une caractéristique entièrement parcheminée. Les parchemins
#: la montent sans consommer de points de niveau — c'est du gain sec.
SCROLLED_BASE = 100

#: Caractéristiques auxquelles on peut affecter des points.
ASSIGNABLE = {
    StatKey.FORCE: "strength",
    StatKey.INTELLIGENCE: "intelligence",
    StatKey.CHANCE: "chance",
    StatKey.AGILITE: "agility",
    StatKey.VITALITE: "vitality",
    StatKey.SAGESSE: "wisdom",
}


def points_available(level: int) -> int:
    """Total des points de caractéristiques à un niveau donné."""
    return max(0, level - 1) * POINTS_PER_LEVEL


@dataclass(frozen=True, slots=True)
class Allocation:
    """Résultat d'une répartition.

    `invested` est ce que les points de niveau achètent, `scroll` ce que les
    parchemins ajoutent par-dessus. Les deux s'additionnent, mais **le parchemin
    n'entre pas dans le barème de coût** : les points se dépensent comme si la
    caractéristique partait de zéro.
    """

    stat: StatKey
    invested: int
    scroll: int
    points_spent: int
    points_left: int

    @property
    def value(self) -> int:
        return self.invested + self.scroll

    def describe(self) -> str:
        parts = [f"{self.invested} investis"]
        if self.scroll:
            parts.append(f"{self.scroll} de parchemins")
        text = (f"{self.stat.value} = {self.value} "
                f"({' + '.join(parts)}, {self.points_spent} points dépensés)")
        if self.points_left:
            text += f", {self.points_left} inutilisables"
        return text


def load_stat_costs(conn: sqlite3.Connection, breed: str) -> dict[str, list[tuple[int, int]]]:
    row = conn.execute(
        "SELECT breed_id FROM breed WHERE name = ? COLLATE NOCASE", (breed,)
    ).fetchone()
    if row is None:
        raise LookupError(f"classe « {breed} » inconnue")

    costs: dict[str, list[tuple[int, int]]] = {}
    for stat, threshold, cost in conn.execute(
        "SELECT stat, threshold, cost FROM breed_stat_cost WHERE breed_id = ? "
        "ORDER BY stat, threshold",
        (row[0],),
    ):
        costs.setdefault(stat, []).append((threshold, cost))
    return costs


def unit_cost(value: int, tiers: list[tuple[int, int]]) -> int:
    """Coût du point suivant, quand la caractéristique vaut déjà `value`."""
    cost = 1
    for threshold, tier_cost in tiers:
        if value >= threshold:
            cost = tier_cost
        else:
            break
    return cost


def allocate(
    stat: StatKey,
    points: int,
    tiers: list[tuple[int, int]],
    *,
    scroll: int = 0,
) -> Allocation:
    """Met tous les points disponibles dans une caractéristique.

    Le barème s'applique aux seuls points **investis** : un parchemin ajoute sa
    valeur au total sans rendre les points suivants plus chers. C'est ce qui fait
    qu'un Iop 175 parcheminé atteint 467 de Force (367 investis + 100) et non 392.

    On avance unité par unité : le barème est en escalier, une division globale
    fausserait le résultat au franchissement de chaque palier.
    """
    invested = 0
    spent = 0
    remaining = points
    while True:
        step = unit_cost(invested, tiers)
        if step > remaining:
            break
        remaining -= step
        spent += step
        invested += 1
    return Allocation(
        stat=stat, invested=invested, scroll=scroll,
        points_spent=spent, points_left=remaining,
    )


def allocate_many(
    stats: list[StatKey],
    points: int,
    tiers_by_stat: dict[StatKey, list[tuple[int, int]]],
    *,
    scrolls: dict[StatKey, int] | None = None,
) -> list[Allocation]:
    """Répartit les points entre plusieurs caractéristiques.

    Stratégie gloutonne : on achète toujours le point le moins cher parmi les
    caractéristiques visées. Le barème étant croissant par paliers et l'objectif
    d'un sort multi-élément symétrique, cela revient à les équilibrer — et c'est
    optimal, une caractéristique déjà haute coûtant plus cher qu'une autre basse.
    """
    scrolls = scrolls or {}
    invested = {stat: 0 for stat in stats}
    spent = {stat: 0 for stat in stats}
    remaining = points

    while True:
        # Le point le moins cher, à égalité la caractéristique la plus basse.
        best = min(
            stats,
            key=lambda s: (unit_cost(invested[s], tiers_by_stat[s]), invested[s]),
        )
        cost = unit_cost(invested[best], tiers_by_stat[best])
        if cost > remaining:
            break
        remaining -= cost
        spent[best] += cost
        invested[best] += 1

    return [
        Allocation(
            stat=stat, invested=invested[stat], scroll=scrolls.get(stat, 0),
            points_spent=spent[stat],
            points_left=remaining if stat == stats[0] else 0,
        )
        for stat in stats
    ]


def base_characteristics(
    conn: sqlite3.Connection,
    breed: str,
    level: int,
    *,
    invest: StatKey | list[StatKey] | None,
    scrolled: int | None = None,
) -> tuple[dict[StatKey, int], list[Allocation]]:
    """Caractéristiques de base du personnage, hors équipement.

    `scrolled` porte les six caractéristiques parcheminables à cette valeur sans
    consommer de points de niveau — c'est ce que font les parchemins.
    """
    base: dict[StatKey, int] = {}
    if scrolled:
        base = {key: scrolled for key in ASSIGNABLE}

    if invest is None:
        return base, []

    targets = [invest] if isinstance(invest, StatKey) else list(invest)
    if not targets:
        return base, []

    costs = load_stat_costs(conn, breed)
    tiers_by_stat: dict[StatKey, list[tuple[int, int]]] = {}
    for stat in targets:
        field = ASSIGNABLE.get(stat)
        if field is None:
            raise ValueError(
                f"impossible d'affecter des points à « {stat.value} ». "
                f"Possible : {', '.join(k.value for k in ASSIGNABLE)}"
            )
        tiers = costs.get(field)
        if not tiers:
            raise LookupError(f"barème introuvable pour {stat.value} chez {breed}")
        tiers_by_stat[stat] = tiers

    allocations = allocate_many(
        targets, points_available(level), tiers_by_stat,
        scrolls={stat: base.get(stat, 0) for stat in targets},
    )
    for allocation in allocations:
        base[allocation.stat] = allocation.value
    return base, allocations
