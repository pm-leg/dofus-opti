"""Contrôles de normalisation sur des items réels connus."""

from __future__ import annotations

import pytest

from dofus_opti.ingest.effects import EFFECT_MAP, EffectKind
from dofus_opti.ingest.normalize import IngestReport, _value_range, normalize_items, normalize_sets
from dofus_opti.model.items import Slot
from dofus_opti.model.stats import StatKey


@pytest.fixture(scope="module")
def parsed(raw_items):
    report = IngestReport()
    items = normalize_items(raw_items, report)
    return {i.name: i for i in items}, report


def test_fixture_normalizes_cleanly(parsed):
    _, report = parsed
    assert report.is_clean
    assert report.items_kept == 6


def test_cape_fulgurante(parsed):
    items, _ = parsed
    cape = items["Cape Fulgurante"]

    assert cape.slot is Slot.CAPE
    assert cape.level == 55
    assert cape.set_id == 993
    assert cape.stats[StatKey.VITALITE].minimum == 21
    assert cape.stats[StatKey.VITALITE].maximum == 25
    assert cape.stats[StatKey.SAGESSE].minimum == 11
    # « 1 PA » est une valeur fixe malgré un int_maximum nul dans la source.
    assert cape.stats[StatKey.PA].minimum == 1
    assert cape.stats[StatKey.PA].maximum == 1
    assert cape.stats[StatKey.PA].is_fixed


def test_kaiser_carries_negative_ap(parsed):
    """L'effet 179 est le malus de PA : la valeur doit rester négative."""
    items, _ = parsed
    kaiser = items["Kaiser"]
    assert kaiser.stats[StatKey.PA].maximum == -1
    assert kaiser.slot is Slot.ARME


def test_weapon_hits_are_separated_from_stats(parsed):
    items, _ = parsed
    epee = items["Épée de Boisaille"]

    assert epee.is_weapon
    assert epee.ap_cost is not None
    assert epee.weapon_hits, "les dégâts d'arme doivent être extraits"
    for hit in epee.weapon_hits:
        assert hit.kind in {"damage", "steal", "heal"}
        assert hit.maximum >= hit.minimum
    # Les dégâts de l'arme ne doivent pas polluer les bonus de dommages fixes.
    elements = {h.element for h in epee.weapon_hits}
    assert "neutre" in elements


def test_spell_modifiers_are_preserved_verbatim(parsed):
    items, _ = parsed
    casque = items["Casque Keutumedi"]
    assert casque.spell_modifiers
    assert all(m.raw for m in casque.spell_modifiers)
    assert any(":" in m.raw for m in casque.spell_modifiers)


def test_special_effects_are_kept_as_text(parsed):
    items, _ = parsed
    dofus = items["Dofus Pourpre"]
    assert dofus.slot is Slot.DOFUS
    assert dofus.special_effects


def test_item_stat_accessor_rolls(parsed):
    items, _ = parsed
    cape = items["Cape Fulgurante"]
    assert cape.stat(StatKey.VITALITE, roll="max") == 25
    assert cape.stat(StatKey.VITALITE, roll="min") == 21
    assert cape.stat(StatKey.VITALITE, roll="avg") == 23
    assert cape.stat(StatKey.FUITE) == 0  # stat absente → 0


@pytest.mark.parametrize(
    "effect, expected",
    [
        ({"int_minimum": 21, "int_maximum": 25}, (21, 25)),
        ({"int_minimum": 1, "int_maximum": 0, "ignore_int_max": True}, (1, 1)),
        ({"int_minimum": -1, "int_maximum": 0, "ignore_int_max": True}, (-1, -1)),
        ({"int_minimum": 0, "int_maximum": 7, "ignore_int_min": True}, (7, 7)),
        # max < min sans indicateur : on retombe sur la valeur unique.
        ({"int_minimum": 5, "int_maximum": 0}, (5, 5)),
    ],
)
def test_value_range(effect, expected):
    assert _value_range(effect) == expected


def test_set_bonuses_are_tiered(raw_sets):
    report = IngestReport()
    sets = {s.name: s for s in normalize_sets(raw_sets, report)}
    gelax = sets["Panoplie Gelax"]

    assert gelax.n_items == 6
    assert 1 not in gelax.bonuses, "aucun bonus à 1 item porté"
    assert gelax.bonuses[2][StatKey.INTELLIGENCE].minimum == 20
    # Les paliers doivent croître avec le nombre d'items.
    tiers = sorted(n for n in gelax.bonuses if StatKey.INTELLIGENCE in gelax.bonuses[n])
    values = [gelax.bonuses[n][StatKey.INTELLIGENCE].minimum for n in tiers]
    assert values == sorted(values)


def test_effect_map_is_internally_consistent():
    for eid, mapping in EFFECT_MAP.items():
        if mapping.kind is EffectKind.STAT:
            assert mapping.stat is not None, f"effet {eid} de type STAT sans stat"
        if mapping.kind is EffectKind.WEAPON_HIT:
            assert mapping.hit_kind and mapping.element, f"effet {eid} incomplet"
        if mapping.kind is EffectKind.IGNORED:
            assert mapping.note, f"effet {eid} ignoré sans justification"
