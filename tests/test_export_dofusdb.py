"""Export au format DofusDB.

Le format cible a été relevé sur la collection publique `stuffs` de
`api.dofusdb.fr` : `base` porte les points de niveau investis, `parchment` les
parchemins, `exo` la liste des exotiques.
"""

from __future__ import annotations

import json

import pytest

from dofus_opti.export.dofusdb import (
    CHARACTERISTIC_KEYS,
    EXO_STAT_IDS,
    SLOT_KEYS,
    UNSUPPORTED_SLOTS,
    build_payload,
    build_url,
    to_json,
)
from dofus_opti.model.items import Item, Slot, StatRange
from dofus_opti.model.stats import StatKey


def item(item_id, name, slot):
    return Item(
        ankama_id=item_id, name=name, slot=slot, type_id=0, type_name=slot.value,
        level=100, is_weapon=False, pods=0,
        stats={StatKey.FORCE: StatRange(50, 50)},
    )


def full_set():
    return [
        item(1, "Chapeau", Slot.CHAPEAU),
        item(2, "Cape", Slot.CAPE),
        item(3, "Amulette", Slot.AMULETTE),
        item(4, "Anneau A", Slot.ANNEAU),
        item(5, "Anneau B", Slot.ANNEAU),
        item(6, "Ceinture", Slot.CEINTURE),
        item(7, "Bottes", Slot.BOTTES),
        item(8, "Arme", Slot.ARME),
        item(9, "Bouclier", Slot.BOUCLIER),
        item(10, "Familier", Slot.FAMILIER),
        *[item(100 + n, f"Dofus {n}", Slot.DOFUS) for n in range(6)],
    ]


def test_slots_map_to_the_dofusdb_keys():
    payload, report = build_payload(full_set(), name="Test", level=200, breed_id=8)
    items = payload["items"]

    assert items["helmet"] == 1
    assert items["cape"] == 2
    assert items["amulet"] == 3
    assert items["rings"] == [4, 5]
    assert items["belt"] == 6
    assert items["boots"] == 7
    assert items["weapon"] == 8
    assert items["shield"] == 9
    assert items["pet"] == 10
    assert len(items["dofus"]) == 6
    assert not report.warnings


def test_multi_item_slots_are_lists_and_others_are_scalars():
    payload, _ = build_payload(full_set(), name="Test", level=200, breed_id=8)
    for slot, (key, is_list) in SLOT_KEYS.items():
        value = payload["items"].get(key)
        if value is None:
            continue
        assert isinstance(value, list) is is_list, key


def test_the_mount_slot_is_reported_as_unsupported():
    """DofusDB n'a pas d'emplacement monture : il faut le dire, pas l'ignorer."""
    items = full_set() + [item(999, "Muldo Ambre et Doré", Slot.MONTURE)]
    payload, report = build_payload(items, name="Test", level=200, breed_id=8)

    assert Slot.MONTURE in UNSUPPORTED_SLOTS
    assert "mount" not in payload["items"]
    assert any("monture" in w and "Muldo" in w for w in report.warnings)


def test_invested_points_and_scrolls_are_separate():
    payload, _ = build_payload(
        full_set(), name="Test", level=175, breed_id=8,
        invested={StatKey.FORCE: 367},
        scrolls={stat: 100 for stat in CHARACTERISTIC_KEYS},
    )
    assert payload["base"]["strength"] == 367
    assert payload["base"]["intelligence"] == 0
    assert payload["parchment"] == {key: 100 for key in CHARACTERISTIC_KEYS.values()}


def test_every_characteristic_key_is_present_even_at_zero():
    payload, _ = build_payload(full_set(), name="Test", level=200, breed_id=8)
    expected = set(CHARACTERISTIC_KEYS.values())
    assert set(payload["base"]) == expected
    assert set(payload["parchment"]) == expected


def test_exos_use_the_numeric_stat_ids():
    payload, report = build_payload(
        full_set(), name="Test", level=175, breed_id=8,
        exos={StatKey.PM: 1, StatKey.PA: 1},
    )
    assert {"stat": EXO_STAT_IDS[StatKey.PM], "value": 1} in payload["exo"]
    assert {"stat": EXO_STAT_IDS[StatKey.PA], "value": 1} in payload["exo"]
    assert not report.warnings


def test_an_unrepresentable_exo_is_reported():
    payload, report = build_payload(
        full_set(), name="Test", level=175, breed_id=8,
        exos={StatKey.FORCE: 50},
    )
    assert payload["exo"] == []
    assert any("force" in w for w in report.warnings)


def test_a_forged_item_is_exported_as_its_catalogue_model():
    """DofusDB attend l'item réel ; la forgemagie passe par le champ « exo »."""
    from dataclasses import replace

    forged = replace(
        item(-1, "Gelano (perso)", Slot.ANNEAU), derived_from=2469
    )
    payload, report = build_payload(
        [forged, item(4, "Anneau A", Slot.ANNEAU)],
        name="Test", level=175, breed_id=8, exos={StatKey.PM: 1},
    )

    assert sorted(payload["items"]["rings"]) == [4, 2469]
    assert payload["exo"] == [{"stat": EXO_STAT_IDS[StatKey.PM], "value": 1}]
    assert any("Gelano (perso)" in w and "exo" in w for w in report.warnings)


def test_an_item_without_a_catalogue_model_is_omitted():
    orphan = item(-1, "Item inventé", Slot.ANNEAU)
    payload, report = build_payload(
        [orphan, item(4, "Anneau A", Slot.ANNEAU)], name="Test", level=175, breed_id=8
    )
    assert payload["items"]["rings"] == [4]
    assert any("aucun modèle" in w for w in report.warnings)


def test_builds_default_to_private():
    payload, _ = build_payload(full_set(), name="Test", level=200, breed_id=8)
    assert payload["shared"] == "private"


def test_the_class_is_sent_as_breed():
    """Sans `breed`, DofusDB enregistre le build comme Féca — constaté en publiant."""
    payload, _ = build_payload(full_set(), name="Test", level=175, breed_id=18)
    assert payload["breed"] == 18
    assert payload["classe"] == 18


def test_payload_is_serialisable():
    payload, _ = build_payload(full_set(), name="Test", level=200, breed_id=8)
    assert json.loads(to_json(payload)) == payload


def test_build_url_format():
    assert build_url("abc123") == "https://dofusdb.fr/fr/tools/stuff/abc123"


@pytest.mark.parametrize("stat", [StatKey.PA, StatKey.PM, StatKey.PO])
def test_turn_resources_are_the_only_exo_targets(stat):
    assert stat in EXO_STAT_IDS
