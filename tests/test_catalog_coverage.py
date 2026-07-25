"""Garde-fou de régression sur les données source.

C'est le test qui doit tomber le lendemain d'une mise à jour de Dofus. Il vérifie
que rien dans le catalogue complet n'échappe aux tables de correspondance.
"""

from __future__ import annotations

from dofus_opti.ingest.conditions import CONDITION_ELEMENTS
from dofus_opti.ingest.effects import EFFECT_MAP
from dofus_opti.ingest.normalize import IngestReport, normalize_items, normalize_sets
from dofus_opti.ingest.slots import EXCLUDED_TYPES, TYPE_TO_SLOT


def test_every_effect_id_is_mapped(full_catalog):
    items, sets = full_catalog
    seen: dict[int, str] = {}
    for item in items:
        for effect in item.get("effects") or []:
            seen[effect["type"]["id"]] = effect["type"]["name"]
    for item_set in sets:
        for effects in (item_set.get("effects") or {}).values():
            for effect in effects or []:
                seen[effect["type"]["id"]] = effect["type"]["name"]

    unknown = {i: n for i, n in seen.items() if i not in EFFECT_MAP}
    assert not unknown, f"effets non mappés : {unknown}"


def test_every_item_type_is_classified(full_catalog):
    items, _ = full_catalog
    seen = {i["type"]["id"]: i["type"]["name"] for i in items}
    unknown = {
        i: n for i, n in seen.items() if i not in TYPE_TO_SLOT and i not in EXCLUDED_TYPES
    }
    assert not unknown, f"types d'item non classés : {unknown}"


def test_type_tables_are_disjoint():
    overlap = set(TYPE_TO_SLOT) & set(EXCLUDED_TYPES)
    assert not overlap, f"types à la fois mappés et exclus : {overlap}"


def test_every_condition_element_is_known(full_catalog):
    items, _ = full_catalog
    seen: dict[int, str] = {}

    def walk(node):
        if not node:
            return
        if cond := node.get("condition"):
            seen[cond["element"]["id"]] = cond["element"].get("name", "?")
        for child in node.get("children") or []:
            walk(child)

    for item in items:
        walk(item.get("conditions"))

    unknown = {i: n for i, n in seen.items() if i not in CONDITION_ELEMENTS}
    assert not unknown, f"éléments de condition inconnus : {unknown}"


def test_full_normalization_is_clean(full_catalog):
    items, sets = full_catalog
    report = IngestReport()
    normalize_items(items, report)
    normalize_sets(sets, report)

    assert report.is_clean, (
        f"effets={report.unknown_effects} types={report.unknown_types} "
        f"conditions={report.unknown_condition_elements}"
    )
    assert report.items_kept + report.items_excluded == report.items_in


def test_catalog_has_expected_volume(full_catalog):
    """Détecte une réponse tronquée ou un changement d'endpoint silencieux."""
    items, sets = full_catalog
    report = IngestReport()
    parsed = normalize_items(items, report)
    normalize_sets(sets, report)

    assert len(parsed) > 3000, f"seulement {len(parsed)} items équipables"
    assert len(sets) > 800, f"seulement {len(sets)} panoplies"
    # Tous les emplacements doivent être pourvus, sinon un type a disparu.
    for slot, count in report.items_by_slot.items():
        assert count > 0, f"aucun item pour l'emplacement {slot}"
    assert len(report.items_by_slot) == 11, report.items_by_slot
