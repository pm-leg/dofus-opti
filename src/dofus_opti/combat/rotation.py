"""Rotation optimale sous budget de PA.

C'est le point qui distingue un vrai optimiseur d'une somme pondérée de
statistiques : avec 12 PA et un sort à 4 PA, on lance trois fois. Un 13ᵉ PA ne
vaut alors strictement rien. Un optimiseur qui maximise « PA » le recommanderait
quand même.

Le choix des lancers est un sac à dos borné. Le budget de PA ne dépasse jamais
une vingtaine : la programmation dynamique est exacte et instantanée.
"""

from __future__ import annotations

from dataclasses import dataclass

from .formula import CastContext, CritPolicy, FormulaVariant, Target
from .spell import Spell, expected_spell_damage
from .stats import StatVector


@dataclass(frozen=True, slots=True)
class Rotation:
    """Combinaison de lancers retenue pour un tour."""

    casts: tuple[tuple[Spell, int], ...]
    ap_used: int
    ap_available: int
    damage: float

    @property
    def ap_wasted(self) -> int:
        return self.ap_available - self.ap_used

    def describe(self) -> str:
        if not self.casts:
            return "aucun lancer possible"
        parts = [f"{count}× {spell.name} ({spell.ap_cost} PA)" for spell, count in self.casts]
        text = " + ".join(parts)
        text += f" = {self.damage:.0f} dégâts, {self.ap_used}/{self.ap_available} PA"
        if self.ap_wasted:
            text += f" ({self.ap_wasted} PA perdu{'s' if self.ap_wasted > 1 else ''})"
        return text


def best_rotation(
    spells: list[Spell],
    stats: StatVector,
    *,
    ap: int | None = None,
    target: Target | None = None,
    ctx: CastContext | None = None,
    variant: FormulaVariant = FormulaVariant.ADDITIVE,
    crit_policy: CritPolicy = CritPolicy.EXPECTED,
) -> Rotation:
    """Combinaison de lancers maximisant les dégâts du tour.

    `ap` vaut par défaut les PA du build (base + équipement).
    """
    budget = stats.pa if ap is None else ap
    if budget <= 0:
        return Rotation((), 0, max(0, budget), 0.0)

    usable = [s for s in spells if 0 < s.ap_cost <= budget]
    unit_damage = {
        id(s): expected_spell_damage(
            s, stats, target=target, ctx=ctx, variant=variant, crit_policy=crit_policy
        )
        for s in usable
    }
    usable = [s for s in usable if unit_damage[id(s)] > 0]

    # dp[a] = (meilleurs dégâts avec exactement a PA au plus, lancers retenus)
    dp: list[tuple[float, tuple[tuple[Spell, int], ...]]] = [(0.0, ())] * (budget + 1)

    for spell in usable:
        per_cast = unit_damage[id(spell)]
        cap = spell.casts_allowed
        previous = dp
        dp = list(previous)
        for available in range(spell.ap_cost, budget + 1):
            max_casts = min(cap, available // spell.ap_cost)
            for count in range(1, max_casts + 1):
                base_damage, base_casts = previous[available - count * spell.ap_cost]
                candidate = base_damage + count * per_cast
                if candidate > dp[available][0]:
                    dp[available] = (candidate, base_casts + ((spell, count),))

    damage, casts = dp[budget]
    ap_used = sum(spell.ap_cost * count for spell, count in casts)
    return Rotation(casts, ap_used, budget, damage)


def damage_per_turn(
    spells: list[Spell],
    stats: StatVector,
    *,
    ap: int | None = None,
    target: Target | None = None,
    ctx: CastContext | None = None,
    variant: FormulaVariant = FormulaVariant.ADDITIVE,
    crit_policy: CritPolicy = CritPolicy.EXPECTED,
) -> float:
    """Fonction objectif du solveur : dégâts moyens par tour d'un build."""
    return best_rotation(
        spells, stats, ap=ap, target=target, ctx=ctx,
        variant=variant, crit_policy=crit_policy,
    ).damage
