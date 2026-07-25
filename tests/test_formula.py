"""Tests du moteur de dégâts.

Les cas dits « golden » viennent de sources externes vérifiables (wiki JOL). Ils
figent l'arithmétique ; la calibration finale contre le jeu reste à faire, voir
`docs/FORMULE.md`.
"""

from __future__ import annotations

import pytest

from dofus_opti.combat.formula import (
    CastContext,
    FormulaVariant,
    Target,
    compute_hit,
    critical_probability,
    expected_damage,
)
from dofus_opti.combat.stats import BASE_PA, StatVector
from dofus_opti.model.stats import StatKey


# --------------------------------------------------------------- cas de référence

def test_golden_cra_fleche_magique():
    """Wiki JOL : 400 Intelligence, +23 Puissance, +14 Dommages fixes.

    Flèche Magique (11 à 15 feu) → 71 minimum, 92 maximum.
    """
    stats = StatVector().with_(intelligence=400, puissance=23, dommages=14)
    assert compute_hit(11, "feu", stats) == 71
    assert compute_hit(15, "feu", stats) == 92


def test_golden_resistances_order():
    """Wiki JOL : 600 dégâts neutres, 20 de résistance fixe, 40 % de résistance.

    La résistance fixe s'applique avant le pourcentage : (600 − 20) × 0,6 = 348.
    Dans l'autre ordre on obtiendrait 340, valeur observable et donc discriminante.
    """
    target = Target(res_pct={"neutre": 40}, res_flat={"neutre": 20})
    stats = StatVector()
    assert compute_hit(600, "neutre", stats, target=target) == 348


def test_rounding_is_truncation_not_rounding():
    """99,99 doit donner 99, pas 100."""
    # 10 × (100 + 899) / 100 = 99,9
    stats = StatVector().with_(force=899)
    assert compute_hit(10, "terre", stats) == 99


# ------------------------------------------------------------------- mécaniques

def test_earth_and_neutral_both_scale_with_strength():
    stats = StatVector().with_(force=300)
    assert compute_hit(100, "terre", stats) == compute_hit(100, "neutre", stats) == 400


@pytest.mark.parametrize(
    "element, stat",
    [("terre", "force"), ("feu", "intelligence"), ("eau", "chance"), ("air", "agilite")],
)
def test_each_element_uses_its_characteristic(element, stat):
    stats = StatVector().with_(**{stat: 100})
    assert compute_hit(100, element, stats) == 200
    # Une autre caractéristique ne doit rien changer.
    assert compute_hit(100, element, StatVector().with_(sagesse=1000)) == 100


def test_negative_characteristic_is_floored_at_zero():
    """0 ou −100 en Force donnent le même résultat."""
    neutral = compute_hit(100, "terre", StatVector())
    negative = compute_hit(100, "terre", StatVector().with_(force=-100))
    assert neutral == negative == 100


def test_flat_damage_applies_after_the_percentage():
    stats = StatVector().with_(force=100, dommages=50)
    # (100 × 200 / 100) + 50 = 250, et non (100 + 50) × 2 = 300
    assert compute_hit(100, "terre", stats) == 250


def test_elemental_flat_damage_is_element_specific():
    stats = StatVector().with_(dommages_terre=30)
    assert compute_hit(100, "terre", stats) == 130
    assert compute_hit(100, "feu", stats) == 100


def test_critical_damage_only_counts_on_criticals():
    stats = StatVector().with_(dommages_critiques=25)
    assert compute_hit(100, "terre", stats, critical=False) == 100
    assert compute_hit(100, "terre", stats, critical=True) == 125


def test_percent_resistance_above_100_cannot_heal_the_target():
    target = Target(res_pct={"terre": 150})
    assert compute_hit(100, "terre", StatVector(), target=target) == 0


def test_flat_resistance_larger_than_damage_gives_zero():
    target = Target(res_flat={"terre": 500})
    assert compute_hit(100, "terre", StatVector(), target=target) == 0


def test_negative_resistance_increases_damage():
    target = Target(res_pct={"terre": -50})
    assert compute_hit(100, "terre", StatVector(), target=target) == 150


def test_final_damage_multiplies_after_flat_damage():
    stats = StatVector().with_(dommages=100)
    ctx = CastContext(final_damage_pct=50)
    # (100 + 100) × 1,5 = 300
    assert compute_hit(100, "terre", stats, ctx=ctx) == 300


def test_zero_base_deals_nothing():
    assert compute_hit(0, "terre", StatVector().with_(force=1000)) == 0


# ----------------------------------------------------------- variantes de formule

def test_variants_agree_when_contextual_stats_are_absent():
    """Le cas de l'immense majorité des builds : aucune divergence possible."""
    stats = StatVector().with_(force=500, puissance=80, dommages=40)
    additive = compute_hit(30, "terre", stats, variant=FormulaVariant.ADDITIVE)
    multiplicative = compute_hit(30, "terre", stats, variant=FormulaVariant.MULTIPLICATIVE)
    assert additive == multiplicative


def test_variants_diverge_only_with_contextual_stats():
    stats = StatVector().with_(force=500, dommages=40, dommages_pct_sorts=10)
    additive = compute_hit(30, "terre", stats, variant=FormulaVariant.ADDITIVE)
    multiplicative = compute_hit(30, "terre", stats, variant=FormulaVariant.MULTIPLICATIVE)
    assert additive != multiplicative


def test_melee_and_range_bonuses_are_contextual():
    stats = StatVector().with_(force=100, dommages_pct_melee=50)
    melee = compute_hit(100, "terre", stats, ctx=CastContext(distance="melee"))
    ranged = compute_hit(100, "terre", stats, ctx=CastContext(distance="range"))
    assert melee > ranged


# ------------------------------------------------------------------- espérance

def test_expected_damage_averages_over_the_dice():
    stats = StatVector()
    # Jet de 10 à 12 sans bonus : moyenne exacte de 10, 11 et 12.
    assert expected_damage(10, 12, "terre", stats) == pytest.approx(11.0)


def test_expected_damage_accounts_for_truncation():
    """La moyenne des extrêmes est biaisée : les troncatures ne sont pas linéaires.

    Cas réel — Pression (26 à 30 Terre) avec 350 Force et 60 Puissance : les jets
    valent 132, 137, 142, 147 et 153, soit 142,2 en moyenne. Évaluer aux seuls
    extrêmes donnerait 142,5.
    """
    stats = StatVector().with_(force=350, puissance=60)
    exact = expected_damage(26, 30, "terre", stats)
    naive = (compute_hit(26, "terre", stats) + compute_hit(30, "terre", stats)) / 2

    assert exact == pytest.approx(142.2)
    assert naive == pytest.approx(142.5)


def test_expected_damage_tolerates_reversed_bounds():
    stats = StatVector()
    assert expected_damage(12, 10, "terre", stats) == expected_damage(10, 12, "terre", stats)


# ----------------------------------------------------------------------- critique

def test_critical_probability_adds_gear_to_spell_base():
    stats = StatVector().with_(critique_pct=25)
    assert critical_probability(10, stats) == pytest.approx(0.35)


def test_critical_probability_is_capped_at_one():
    stats = StatVector().with_(critique_pct=200)
    assert critical_probability(50, stats) == 1.0


def test_critical_probability_never_negative():
    assert critical_probability(0, StatVector().with_(critique_pct=-50)) == 0.0


# -------------------------------------------------------------------- StatVector

def test_stat_vector_addition_sums_components():
    combined = StatVector().with_(force=100) + StatVector().with_(force=50, puissance=20)
    assert combined[StatKey.FORCE] == 150
    assert combined[StatKey.PUISSANCE] == 20


def test_stat_vector_exposes_base_action_points():
    assert StatVector().pa == BASE_PA
    assert StatVector().with_(pa=6).pa == BASE_PA + 6
