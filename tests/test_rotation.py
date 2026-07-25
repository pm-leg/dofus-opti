from __future__ import annotations

from dofus_opti.combat.rotation import best_rotation, damage_per_turn
from dofus_opti.combat.spell import DamageRoll, Spell
from dofus_opti.combat.stats import StatVector


def spell(name, ap, dmg, *, cap=None, crit=0):
    return Spell(
        name=name,
        ap_cost=ap,
        rolls=(DamageRoll("terre", dmg, dmg, dmg, dmg),),
        crit_probability=crit,
        max_cast_per_turn=cap,
    )


def test_spends_ap_on_the_best_combination():
    spells = [spell("Gros", 5, 100), spell("Petit", 2, 30)]
    rotation = best_rotation(spells, StatVector(), ap=10)
    # 2× Gros = 200 bat 1× Gros + 2× Petit = 160 et 5× Petit = 150.
    assert rotation.damage == 200
    assert rotation.ap_used == 10


def test_a_thirteenth_ap_is_worthless_with_a_four_ap_spell():
    """Le cas qui justifie l'existence de ce module."""
    spells = [spell("Sort", 4, 100)]
    twelve = best_rotation(spells, StatVector(), ap=12)
    thirteen = best_rotation(spells, StatVector(), ap=13)

    assert twelve.damage == thirteen.damage == 300
    assert twelve.ap_wasted == 0
    assert thirteen.ap_wasted == 1


def test_cast_limit_per_turn_is_respected():
    spells = [spell("Limité", 3, 100, cap=2), spell("Libre", 3, 40)]
    rotation = best_rotation(spells, StatVector(), ap=12)
    counts = {s.name: n for s, n in rotation.casts}
    assert counts["Limité"] == 2
    # Les PA restants partent sur le sort libre plutôt que d'être perdus.
    assert counts.get("Libre") == 2
    assert rotation.damage == 280


def test_leftover_ap_is_filled_with_a_cheaper_spell():
    spells = [spell("Cher", 5, 100), spell("Bon marché", 1, 15)]
    rotation = best_rotation(spells, StatVector(), ap=12)
    assert rotation.ap_used == 12
    assert rotation.damage == 230  # 2× Cher (10 PA) + 2× Bon marché


def test_unaffordable_spells_are_ignored():
    spells = [spell("Trop cher", 20, 1000), spell("Abordable", 3, 50)]
    rotation = best_rotation(spells, StatVector(), ap=6)
    assert {s.name for s, _ in rotation.casts} == {"Abordable"}
    assert rotation.damage == 100


def test_no_usable_spell_yields_an_empty_rotation():
    rotation = best_rotation([spell("Trop cher", 99, 500)], StatVector(), ap=6)
    assert rotation.casts == ()
    assert rotation.damage == 0
    assert rotation.ap_wasted == 6


def test_zero_ap_yields_nothing():
    rotation = best_rotation([spell("Sort", 3, 100)], StatVector(), ap=0)
    assert rotation.damage == 0


def test_defaults_to_the_build_action_points():
    stats = StatVector().with_(pa=6)  # 6 de base + 6 = 12 PA
    rotation = best_rotation([spell("Sort", 4, 100)], stats)
    assert rotation.ap_available == 12
    assert rotation.damage == 300


def test_more_characteristic_yields_more_damage():
    spells = [spell("Sort", 4, 20)]
    weak = damage_per_turn(spells, StatVector().with_(pa=6, force=100))
    strong = damage_per_turn(spells, StatVector().with_(pa=6, force=500))
    assert strong > weak


def test_rotation_description_reports_wasted_ap():
    rotation = best_rotation([spell("Sort", 4, 100)], StatVector(), ap=13)
    text = rotation.describe()
    assert "3× Sort" in text
    assert "1 PA perdu" in text
