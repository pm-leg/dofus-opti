"""Politique d'évaluation des critiques et exigence de taux minimum."""

from __future__ import annotations

import pytest

from dofus_opti.combat.formula import (
    CritPolicy,
    critical_probability,
    critical_shortfall,
    required_crit_stat,
)
from dofus_opti.combat.rotation import best_rotation
from dofus_opti.combat.spell import CritTarget, DamageRoll, Spell, expected_spell_damage
from dofus_opti.combat.stats import StatVector


def make_spell(name="Sort", *, ap=3, base=100, crit_base=120, crit_pct=10, cap=None):
    return Spell(
        name=name,
        ap_cost=ap,
        rolls=(DamageRoll("terre", base, base, crit_base, crit_base),),
        crit_probability=crit_pct,
        max_cast_per_turn=cap,
    )


# ------------------------------------------------------------- politiques

def test_never_ignores_criticals_entirely():
    spell = make_spell()
    stats = StatVector().with_(critique_pct=90)  # 100 % en pratique
    damage = expected_spell_damage(spell, stats, crit_policy=CritPolicy.NEVER)
    assert damage == 100


def test_always_assumes_a_critical_every_cast():
    spell = make_spell()
    stats = StatVector()  # aucune Critique : le taux réel serait de 10 %
    damage = expected_spell_damage(spell, stats, crit_policy=CritPolicy.ALWAYS)
    assert damage == 120


def test_expected_weights_by_the_real_rate():
    spell = make_spell()  # 10 % de base
    stats = StatVector().with_(critique_pct=40)  # 50 % au total
    damage = expected_spell_damage(spell, stats, crit_policy=CritPolicy.EXPECTED)
    assert damage == pytest.approx(0.5 * 100 + 0.5 * 120)


def test_expected_is_the_default():
    spell = make_spell()
    stats = StatVector().with_(critique_pct=40)
    assert expected_spell_damage(spell, stats) == expected_spell_damage(
        spell, stats, crit_policy=CritPolicy.EXPECTED
    )


def test_policies_are_ordered_never_expected_always():
    spell = make_spell()
    stats = StatVector().with_(critique_pct=40)
    never = expected_spell_damage(spell, stats, crit_policy=CritPolicy.NEVER)
    expected = expected_spell_damage(spell, stats, crit_policy=CritPolicy.EXPECTED)
    always = expected_spell_damage(spell, stats, crit_policy=CritPolicy.ALWAYS)
    assert never < expected < always


def test_crit_damage_stat_only_counts_on_critical_casts():
    spell = make_spell()
    stats = StatVector().with_(dommages_critiques=50)
    assert expected_spell_damage(spell, stats, crit_policy=CritPolicy.NEVER) == 100
    assert expected_spell_damage(spell, stats, crit_policy=CritPolicy.ALWAYS) == 170


# ------------------------------------------------ sorts non critiquables

def test_a_spell_with_zero_base_rate_can_never_crit():
    spell = make_spell(crit_pct=0)
    stats = StatVector().with_(critique_pct=100)
    assert spell.can_crit is False
    assert spell.crit_rate(stats) == 0.0
    # Même en politique ALWAYS, on ne peut pas inventer un critique.
    assert expected_spell_damage(spell, stats, crit_policy=CritPolicy.ALWAYS) == 100


def test_required_crit_stat_is_none_for_a_non_critable_spell():
    assert required_crit_stat(100, 0) is None
    assert make_spell(crit_pct=0).crit_stat_needed() is None


# --------------------------------------------- conversion objectif → stat

@pytest.mark.parametrize(
    "target_pct, spell_base, expected",
    [
        (100, 10, 90),   # Pression : 10 % de base → 90 Critique pour le 100 %
        (100, 25, 75),   # un sort à 25 % de base coûte bien moins cher
        (50, 10, 40),
        (50, 60, 0),     # déjà au-delà de l'objectif
        (100, 100, 0),
    ],
)
def test_required_crit_stat(target_pct, spell_base, expected):
    assert required_crit_stat(target_pct, spell_base) == expected


def test_the_same_target_costs_differently_on_two_spells():
    """Le point qui rend un objectif « 100 % crit » global ambigu."""
    pression = make_spell("Pression", crit_pct=10)
    colere = make_spell("Colère de Iop", crit_pct=25)
    assert pression.crit_stat_needed(100) == 90
    assert colere.crit_stat_needed(100) == 75


# -------------------------------------------------------- CritTarget

def test_crit_target_reports_the_required_stat():
    target = CritTarget(percent=100, reference_spell=make_spell("Pression", crit_pct=10))
    assert target.critique_needed == 90
    assert target.is_reachable
    assert "90 Critique requis" in target.describe()


def test_crit_target_is_met_when_the_build_has_enough():
    target = CritTarget(percent=100, reference_spell=make_spell(crit_pct=10))
    assert target.is_met_by(StatVector().with_(critique_pct=90))
    assert target.is_met_by(StatVector().with_(critique_pct=120))
    assert not target.is_met_by(StatVector().with_(critique_pct=89))


def test_crit_target_reports_the_shortfall():
    target = CritTarget(percent=100, reference_spell=make_spell(crit_pct=10))
    assert target.shortfall(StatVector().with_(critique_pct=70)) == 20
    assert target.shortfall(StatVector().with_(critique_pct=90)) == 0


def test_crit_target_on_a_non_critable_spell_is_unreachable():
    target = CritTarget(percent=100, reference_spell=make_spell("Muet", crit_pct=0))
    assert not target.is_reachable
    assert target.critique_needed is None
    assert target.shortfall(StatVector().with_(critique_pct=200)) == 100
    assert "ne peut pas être critique" in target.describe()


def test_half_crit_target_is_cheaper_than_full():
    spell = make_spell(crit_pct=10)
    assert CritTarget(50, spell).critique_needed < CritTarget(100, spell).critique_needed


# ------------------------------------------------- interaction rotation

def test_policy_can_change_the_optimal_rotation():
    """Un sort à fort bonus critique peut ne gagner qu'en mode critique."""
    steady = make_spell("Régulier", ap=4, base=100, crit_base=105, crit_pct=10)
    swingy = make_spell("Aléatoire", ap=4, base=80, crit_base=200, crit_pct=10)
    stats = StatVector()

    never = best_rotation([steady, swingy], stats, ap=8, crit_policy=CritPolicy.NEVER)
    always = best_rotation([steady, swingy], stats, ap=8, crit_policy=CritPolicy.ALWAYS)

    assert {s.name for s, _ in never.casts} == {"Régulier"}
    assert {s.name for s, _ in always.casts} == {"Aléatoire"}


def test_shortfall_flags_an_unrealistic_always_evaluation():
    """Évaluer en ALWAYS un build qui ne tient pas le 100 % doit être détectable."""
    spell = make_spell(crit_pct=10)
    stats = StatVector().with_(critique_pct=20)  # 30 % réel, loin du compte
    assert critical_shortfall(100, spell.crit_probability, stats) == 70


def test_critical_probability_is_capped_and_floored():
    assert critical_probability(50, StatVector().with_(critique_pct=200)) == 1.0
    assert critical_probability(10, StatVector().with_(critique_pct=-50)) == 0.0
