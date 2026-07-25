"""Calcul des dégâts.

Voir `docs/FORMULE.md` pour les sources, les incertitudes assumées et les
mécaniques volontairement hors modèle.

Tout est en arithmétique entière : le jeu tronque à l'inférieur entre chaque
étape, et passer par des flottants introduirait des écarts d'une unité sur les
valeurs pile (`99.99999` au lieu de `100`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..model.stats import FLAT_DAMAGE_BY_ELEMENT, PRIMARY_STAT_BY_ELEMENT, StatKey
from .stats import StatVector


class CritPolicy(StrEnum):
    """Comment les coups critiques entrent dans la notation d'un build.

    Ce n'est **pas** une contrainte : voir `CritTarget` pour exiger un taux
    minimum. Ici on choisit seulement ce qu'on mesure.
    """

    #: Dégâts hors critique. Le plancher garanti — utile quand on refuse de
    #: dépendre du hasard.
    NEVER = "never"
    #: Espérance pondérée par le taux réel du build. Le défaut, et le seul mode
    #: honnête pour comparer deux builds à taux critiques différents.
    EXPECTED = "expected"
    #: Dégâts en critique systématique. N'a de sens que sur un build qui atteint
    #: réellement 100 % — `critical_shortfall()` permet de le vérifier.
    ALWAYS = "always"


class FormulaVariant(StrEnum):
    """Traitement des `% dommages sorts / armes / mêlée / distance`.

    Les sources communautaires divergent. Voir `docs/FORMULE.md` : l'écart est
    sans effet pratique, ces statistiques étant quasi absentes des équipements.
    """

    ADDITIVE = "additive"  # dans la même parenthèse que caractéristique + puissance
    MULTIPLICATIVE = "multiplicative"  # multiplicateur distinct, après dommages fixes


@dataclass(frozen=True, slots=True)
class Target:
    """Cible : résistances par élément."""

    res_pct: dict[str, int] = field(default_factory=dict)
    res_flat: dict[str, int] = field(default_factory=dict)
    vulnerability_pct: int = 0

    @staticmethod
    def unarmored() -> "Target":
        """Cible sans résistance — utile pour comparer des builds entre eux."""
        return Target()


@dataclass(frozen=True, slots=True)
class CastContext:
    """Circonstances du lancer, hors caractéristiques du personnage."""

    is_weapon: bool = False
    distance: str | None = None  # "melee" | "range" | None
    #: `% dommages finaux`, qui ne provient que des sorts et buffs — jamais des items.
    final_damage_pct: int = 0


def _contextual_pct(stats: StatVector, ctx: CastContext) -> int:
    total = stats[StatKey.DOMMAGES_PCT_ARMES] if ctx.is_weapon else stats[StatKey.DOMMAGES_PCT_SORTS]
    if ctx.distance == "melee":
        total += stats[StatKey.DOMMAGES_PCT_MELEE]
    elif ctx.distance == "range":
        total += stats[StatKey.DOMMAGES_PCT_DISTANCE]
    return total


def compute_hit(
    base: int,
    element: str,
    stats: StatVector,
    *,
    target: Target | None = None,
    ctx: CastContext | None = None,
    critical: bool = False,
    variant: FormulaVariant = FormulaVariant.ADDITIVE,
) -> int:
    """Dégâts infligés par un lancer, pour une valeur de base donnée.

    `base` est le jet du sort ou de l'arme, bonus critique déjà inclus le cas
    échéant. `critical` ne sert qu'à ajouter les `Dommages Critiques` du build.
    """
    if base <= 0:
        return 0

    target = target or Target()
    ctx = ctx or CastContext()

    # Une caractéristique négative ne réduit jamais les dégâts.
    characteristic = max(0, stats[PRIMARY_STAT_BY_ELEMENT[element]])
    percent = characteristic + stats[StatKey.PUISSANCE]

    contextual = _contextual_pct(stats, ctx)
    if variant is FormulaVariant.ADDITIVE:
        percent += contextual

    flat = stats[StatKey.DOMMAGES] + stats[FLAT_DAMAGE_BY_ELEMENT[element]]
    if critical:
        flat += stats[StatKey.DOMMAGES_CRITIQUES]

    # ⌊ base × (100 + %) / 100 ⌋ + dommages fixes, en entiers exacts.
    damage = (base * (100 + percent)) // 100 + flat

    if variant is FormulaVariant.MULTIPLICATIVE and contextual:
        damage = (damage * (100 + contextual)) // 100

    if ctx.final_damage_pct:
        damage = (damage * (100 + ctx.final_damage_pct)) // 100

    if target.vulnerability_pct:
        damage = (damage * max(0, 100 + target.vulnerability_pct)) // 100

    # Côté cible : la résistance fixe s'applique AVANT le pourcentage.
    damage = max(0, damage - target.res_flat.get(element, 0))
    damage = damage * max(0, 100 - target.res_pct.get(element, 0)) // 100

    return max(0, damage)


def expected_damage(
    base_min: int,
    base_max: int,
    element: str,
    stats: StatVector,
    *,
    target: Target | None = None,
    ctx: CastContext | None = None,
    critical: bool = False,
    variant: FormulaVariant = FormulaVariant.ADDITIVE,
) -> float:
    """Espérance des dégâts sur le jet du sort.

    Le jet est uniforme sur les entiers de `base_min` à `base_max`. On somme
    exactement plutôt que d'évaluer au milieu de la fourchette : les troncatures
    rendent la fonction non linéaire, et la moyenne des extrêmes serait biaisée.
    """
    if base_max < base_min:
        base_min, base_max = base_max, base_min
    rolls = range(base_min, base_max + 1)
    total = sum(
        compute_hit(b, element, stats, target=target, ctx=ctx, critical=critical, variant=variant)
        for b in rolls
    )
    return total / len(rolls)


def critical_probability(spell_crit_pct: int, stats: StatVector) -> float:
    """Probabilité de coup critique, plafonnée à 100 %.

    En Dofus 2 et 3, le taux critique est une statistique plate ajoutée au taux de
    base du sort. (L'ancienne formule logarithmique liée à l'Agilité date de
    Dofus 1.x et ne s'applique plus.)

    Un sort dont le taux de base est nul **ne peut pas être critique**, quelle que
    soit la statistique Critique du personnage.
    """
    if spell_crit_pct <= 0:
        return 0.0
    return min(100, max(0, spell_crit_pct + stats[StatKey.CRITIQUE_PCT])) / 100


def required_crit_stat(target_pct: int, spell_crit_pct: int) -> int | None:
    """Statistique Critique nécessaire pour atteindre `target_pct` sur un sort.

    Renvoie `None` si le sort ne peut pas être critique — aucune quantité de
    Critique n'y changera rien, et une contrainte posée dessus serait insatisfiable.

    C'est cette conversion qui rend l'objectif « je veux du 100 % critique »
    exploitable : il n'existe pas de taux critique global, seulement un taux par
    sort. Viser 100 % sur un sort à 10 % de base demande 90 de Critique ; sur un
    sort à 25 %, seulement 75.
    """
    if spell_crit_pct <= 0:
        return None
    return max(0, min(100, target_pct) - spell_crit_pct)


def critical_shortfall(target_pct: int, spell_crit_pct: int, stats: StatVector) -> int:
    """Points de Critique manquants pour atteindre `target_pct` sur un sort.

    Vaut 0 si l'objectif est atteint. Sert à signaler qu'un build évalué en
    `CritPolicy.ALWAYS` ne tient pas réellement le 100 %.
    """
    needed = required_crit_stat(target_pct, spell_crit_pct)
    if needed is None:
        return max(0, min(100, target_pct))
    return max(0, needed - stats[StatKey.CRITIQUE_PCT])
