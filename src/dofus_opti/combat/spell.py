"""Modèle de sort et évaluation d'un lancer."""

from __future__ import annotations

from dataclasses import dataclass

from .formula import (
    CastContext,
    CritPolicy,
    FormulaVariant,
    Target,
    critical_probability,
    critical_shortfall,
    expected_damage,
    required_crit_stat,
)
from ..model.spells import ClassSpell, DamageRoll, SpellLevel
from .stats import StatVector

__all__ = [
    "CritTarget",
    "DamageRoll",
    "Spell",
    "expected_spell_damage",
    "from_class_spell",
]


@dataclass(frozen=True, slots=True)
class Spell:
    """Un sort à un niveau donné, tel qu'utilisable dans une rotation."""

    name: str
    ap_cost: int
    rolls: tuple[DamageRoll, ...]
    crit_probability: int = 0  # % de base du sort
    max_cast_per_turn: int | None = None
    max_cast_per_target: int | None = None
    range_min: int = 0
    range_max: int = 0
    is_weapon: bool = False
    spell_id: int | None = None
    grade: int | None = None

    @property
    def casts_allowed(self) -> int:
        """Lancers permis par tour, `0` signifiant « illimité » côté API.

        On retient le minimum entre la limite par tour et celle par cible : c'est
        l'hypothèse **mono-cible**, la bonne pour comparer des builds de dégâts.
        En multi-cible la limite par cible ne mordrait pas, et la rotation serait
        plus généreuse.
        """
        limits = [
            limit for limit in (self.max_cast_per_turn, self.max_cast_per_target)
            if limit
        ]
        return min(limits) if limits else 99

    @property
    def can_crit(self) -> bool:
        return self.crit_probability > 0

    def crit_rate(self, stats: StatVector) -> float:
        """Taux critique effectif de ce sort pour un build donné."""
        return critical_probability(self.crit_probability, stats)

    def crit_stat_needed(self, target_pct: int = 100) -> int | None:
        """Critique à atteindre pour `target_pct` sur ce sort (`None` si impossible)."""
        return required_crit_stat(target_pct, self.crit_probability)


def from_class_spell(spell: ClassSpell, level: SpellLevel) -> Spell:
    """Convertit un sort du catalogue en sort utilisable par le moteur."""
    return Spell(
        name=spell.name,
        ap_cost=level.ap_cost,
        rolls=level.rolls,
        crit_probability=level.crit_probability,
        max_cast_per_turn=level.max_cast_per_turn or None,
        max_cast_per_target=level.max_cast_per_target or None,
        range_min=level.range_min,
        range_max=level.range_max,
        spell_id=spell.spell_id,
        grade=level.grade,
    )


@dataclass(frozen=True, slots=True)
class CritTarget:
    """Exigence de taux critique portée par l'utilisateur.

    « Je veux du 100 % critique » n'est pas exprimable globalement : le taux
    dépend du taux de base de chaque sort. On ancre donc l'objectif sur un sort
    de référence, et on en déduit la statistique Critique à atteindre — que le
    solveur posera comme contrainte au même titre que « 12 PA ».
    """

    percent: int  # 100, 50, 33…
    reference_spell: Spell

    @property
    def critique_needed(self) -> int | None:
        return self.reference_spell.crit_stat_needed(self.percent)

    @property
    def is_reachable(self) -> bool:
        return self.critique_needed is not None

    def shortfall(self, stats: StatVector) -> int:
        """Points de Critique manquants — 0 si l'exigence est tenue."""
        return critical_shortfall(self.percent, self.reference_spell.crit_probability, stats)

    def is_met_by(self, stats: StatVector) -> bool:
        return self.shortfall(stats) == 0

    def describe(self) -> str:
        if not self.is_reachable:
            return (
                f"{self.reference_spell.name} ne peut pas être critique : "
                f"objectif {self.percent} % inatteignable"
            )
        return (
            f"{self.percent} % critique sur {self.reference_spell.name} "
            f"(base {self.reference_spell.crit_probability} %) "
            f"→ {self.critique_needed} Critique requis"
        )


def expected_spell_damage(
    spell: Spell,
    stats: StatVector,
    *,
    target: Target | None = None,
    ctx: CastContext | None = None,
    variant: FormulaVariant = FormulaVariant.ADDITIVE,
    crit_policy: CritPolicy = CritPolicy.EXPECTED,
) -> float:
    """Dégâts moyens d'un lancer, tous éléments cumulés.

    `crit_policy` décide du traitement des critiques : jamais, en espérance
    pondérée par le taux réel du build, ou systématiquement.
    """
    ctx = ctx or CastContext(is_weapon=spell.is_weapon)

    if crit_policy is CritPolicy.NEVER or not spell.can_crit:
        p_crit = 0.0
    elif crit_policy is CritPolicy.ALWAYS:
        p_crit = 1.0
    else:
        p_crit = spell.crit_rate(stats)

    total = 0.0
    for roll in spell.rolls:
        if p_crit < 1:
            normal = expected_damage(
                roll.base_min, roll.base_max, roll.element, stats,
                target=target, ctx=ctx, critical=False, variant=variant,
            )
        else:
            normal = 0.0
        if p_crit <= 0:
            total += normal
            continue
        crit = expected_damage(
            roll.crit_min, roll.crit_max, roll.element, stats,
            target=target, ctx=ctx, critical=True, variant=variant,
        )
        total += (1 - p_crit) * normal + p_crit * crit
    return total
