"""Ingestion des sorts : table d'effets, normalisation, catalogue."""

from __future__ import annotations

import pytest

from dofus_opti.ingest.normalize_spells import (
    SpellIngestReport,
    _roll_bounds,
    map_spells_to_breeds,
    normalize_breeds,
    normalize_levels,
)
from dofus_opti.ingest.spell_effects import (
    DAMAGE_EFFECTS,
    ELEMENT_BY_ID,
    EXCLUDED_DAMAGE_EFFECTS,
    SpellEffectTableMismatch,
    verify_against_source,
)


def effect_row(effect_id, element_id, description):
    return {"id": effect_id, "elementId": element_id, "description": {"fr": description}}


def truthful_source():
    """Table minimale conforme à ce que renvoie DofusDB."""
    rows = []
    for effect_id, mapping in DAMAGE_EFFECTS.items():
        element_id = next(k for k, v in ELEMENT_BY_ID.items() if v == mapping.element)
        rows.append(effect_row(effect_id, element_id, mapping.expected_description.capitalize()))
    return rows


# ------------------------------------------------------- garde-fou de la table

def test_verification_passes_on_a_faithful_source():
    verify_against_source(truthful_source())


def test_verification_detects_a_renumbered_effect():
    rows = truthful_source()
    for row in rows:
        if row["id"] == 97:
            row["description"] = {"fr": "10 à 15 dommages Feu"}
    with pytest.raises(SpellEffectTableMismatch) as exc:
        verify_against_source(rows)
    assert any("97" in p for p in exc.value.problems)


def test_verification_detects_a_missing_effect():
    rows = [r for r in truthful_source() if r["id"] != 100]
    with pytest.raises(SpellEffectTableMismatch) as exc:
        verify_against_source(rows)
    assert any("100" in p and "absent" in p for p in exc.value.problems)


def test_verification_detects_a_shifted_element_id():
    rows = truthful_source()
    for row in rows:
        if row["id"] == 97:
            row["elementId"] = 2  # Terre annoncé sur l'identifiant du Feu
    with pytest.raises(SpellEffectTableMismatch):
        verify_against_source(rows)


def test_damage_and_excluded_tables_are_disjoint():
    assert not set(DAMAGE_EFFECTS) & set(EXCLUDED_DAMAGE_EFFECTS)


def test_every_excluded_effect_is_justified():
    assert all(reason for reason in EXCLUDED_DAMAGE_EFFECTS.values())


def test_all_five_elements_are_covered_by_damage_effects():
    covered = {m.element for m in DAMAGE_EFFECTS.values() if m.kind == "damage"}
    assert covered == {"neutre", "terre", "feu", "eau", "air"}


# ------------------------------------------------------------- bornes des jets

@pytest.mark.parametrize(
    "effect, expected",
    [
        ({"diceNum": 26, "diceSide": 30}, (26, 30)),
        ({"diceNum": 10, "diceSide": 0}, (10, 10)),  # valeur fixe
        ({"diceNum": 30, "diceSide": 26}, (26, 30)),  # bornes inversées
        ({}, (0, 0)),
    ],
)
def test_roll_bounds(effect, expected):
    assert _roll_bounds(effect) == expected


# --------------------------------------------------------------- normalisation

def damage_effect(effect_id=97, dice_num=26, dice_side=30, duration=0):
    return {
        "effectId": effect_id, "effectElement": 1,
        "diceNum": dice_num, "diceSide": dice_side, "duration": duration,
    }


def spell_level(**overrides):
    base = {
        "spellId": 1, "grade": 3, "apCost": 3, "criticalHitProbability": 10,
        "minRange": 1, "range": 4, "maxCastPerTurn": 4, "maxCastPerTarget": 2,
        "minPlayerLevel": 132, "castInLine": False, "castTestLos": True,
        "effects": [damage_effect()],
        "criticalEffect": [damage_effect(dice_num=31, dice_side=36)],
    }
    base.update(overrides)
    return base


def test_normalizes_a_damage_spell_level():
    report = SpellIngestReport()
    levels = normalize_levels([spell_level()], report)
    level = levels[1][0]

    assert level.ap_cost == 3
    assert level.crit_probability == 10
    assert level.min_player_level == 132
    assert level.deals_direct_damage

    roll = level.rolls[0]
    assert (roll.element, roll.base_min, roll.base_max) == ("terre", 26, 30)
    assert (roll.crit_min, roll.crit_max) == (31, 36)


def test_missing_critical_effect_falls_back_to_normal_rolls():
    report = SpellIngestReport()
    levels = normalize_levels([spell_level(criticalEffect=[])], report)
    roll = levels[1][0].rolls[0]
    assert (roll.crit_min, roll.crit_max) == (roll.base_min, roll.base_max)


def test_several_effects_under_the_same_mask_are_summed():
    """Deux frappes dans les mêmes conditions : elles se cumulent."""
    report = SpellIngestReport()
    hits = [damage_effect(dice_num=10, dice_side=12),
            damage_effect(dice_num=5, dice_side=6)]
    for hit in hits:
        hit["targetMask"] = "A"
    levels = normalize_levels([spell_level(effects=hits, criticalEffect=[])], report)

    roll = levels[1][0].rolls[0]
    assert (roll.base_min, roll.base_max) == (15, 18)
    assert report.conditional_branches == 0


def test_conditional_branches_are_not_summed():
    """Souffle Alcoolisé : 28-32 avec un état, 34-38 avec un autre — jamais 62-70."""
    report = SpellIngestReport()
    branches = [damage_effect(dice_num=28, dice_side=32),
                damage_effect(dice_num=34, dice_side=38)]
    branches[0]["targetMask"] = "A,*E3531"
    branches[1]["targetMask"] = "A,*E498"
    levels = normalize_levels([spell_level(effects=branches, criticalEffect=[])], report)

    roll = levels[1][0].rolls[0]
    assert (roll.base_min, roll.base_max) == (34, 38), "la meilleure branche doit primer"
    assert report.conditional_branches == 1


def test_state_presence_and_absence_are_alternatives():
    """« a l'état » (*E) et « ne l'a pas » (*e) sont exclusifs par construction."""
    report = SpellIngestReport()
    branches = [damage_effect(dice_num=12, dice_side=14),
                damage_effect(dice_num=12, dice_side=14)]
    branches[0]["targetMask"] = "g,A,*e3532"
    branches[1]["targetMask"] = "g,A,*E3532"
    levels = normalize_levels([spell_level(effects=branches, criticalEffect=[])], report)

    roll = levels[1][0].rolls[0]
    assert (roll.base_min, roll.base_max) == (12, 14)


def test_self_damage_effect_is_excluded():
    report = SpellIngestReport()
    self_damage = [{"effectId": 109, "effectElement": 1,
                    "diceNum": 50, "diceSide": 60, "duration": 0}]
    levels = normalize_levels(
        [spell_level(effects=self_damage, criticalEffect=self_damage)], report
    )
    assert not levels[1][0].deals_direct_damage


def test_damage_only_on_criticals_is_kept_with_a_zero_base():
    """Un effet présent uniquement en critique existe, mais ne vaut rien hors critique."""
    report = SpellIngestReport()
    levels = normalize_levels([spell_level(effects=[])], report)
    roll = levels[1][0].rolls[0]
    assert (roll.base_min, roll.base_max) == (0, 0)
    assert (roll.crit_min, roll.crit_max) == (31, 36)


def test_levels_are_sorted_by_grade():
    report = SpellIngestReport()
    levels = normalize_levels(
        [spell_level(grade=3), spell_level(grade=1), spell_level(grade=2)], report
    )
    assert [lv.grade for lv in levels[1]] == [1, 2, 3]


def test_non_damage_effects_are_silently_ignored():
    """Un sort porte des dizaines d'états et de buffs : ils ne sont pas des anomalies."""
    report = SpellIngestReport()
    state = [{"effectId": 950, "effectElement": -1,
              "diceNum": 1, "diceSide": 0, "duration": 2}]
    levels = normalize_levels([spell_level(effects=state, criticalEffect=state)], report)

    assert not levels[1][0].deals_direct_damage
    assert not report.unmapped_damage_effects


def test_an_unmapped_elemental_effect_is_reported():
    """Un effet élémentaire instantané inconnu doit remonter, pas disparaître."""
    report = SpellIngestReport()
    unknown = [{"effectId": 999999, "effectElement": 1,
                "diceNum": 10, "diceSide": 12, "duration": 0}]
    normalize_levels([spell_level(effects=unknown, criticalEffect=[])], report)
    assert report.unmapped_damage_effects == {999999: 1}


# ------------------------------------------------------------------- classes

def test_breed_names_are_localized():
    report = SpellIngestReport()
    breeds = normalize_breeds([{"id": 8, "shortName": {"fr": "Iop", "en": "Iop"}}], report)
    assert breeds[8].name == "Iop"
    assert report.breeds == 1


def test_spell_to_breed_mapping_skips_unknown_breeds():
    variants = [
        {"breedId": 8, "spellId": None, "spellIds": [100, 101]},
        {"breedId": 19, "spellIds": [900]},  # classe absente de `breeds`
    ]
    mapping = map_spells_to_breeds(variants, known_breeds={8})
    assert mapping == {100: 8, 101: 8}


def test_best_level_for_a_character_level():
    from dofus_opti.model.spells import ClassSpell, SpellLevel

    def level(grade, min_level):
        return SpellLevel(
            grade=grade, ap_cost=3, crit_probability=10, range_min=1, range_max=4,
            max_cast_per_turn=2, max_cast_per_target=2, min_player_level=min_level,
        )

    spell = ClassSpell(1, "Pression", 8, (level(1, 1), level(2, 66), level(3, 132)))
    assert spell.at_character_level(175).grade == 3
    assert spell.at_character_level(100).grade == 2
    assert spell.at_character_level(1).grade == 1
    assert ClassSpell(2, "Vide", 8, ()).at_character_level(200) is None
