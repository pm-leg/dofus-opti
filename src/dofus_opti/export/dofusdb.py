"""Export d'un build au format DofusDB.

DofusDB héberge les équipements dans sa collection `stuffs` et les expose à
l'adresse `https://dofusdb.fr/fr/tools/stuff/<id>`. Le format est directement
compatible avec notre modèle : `base` porte les points de niveau investis,
`parchment` les parchemins, `exo` les exotiques — exactement la décomposition
que le solveur manipule.

Ce module **produit la charge utile**, il ne publie rien : envoyer un build crée
un enregistrement public sur un service tiers, ce qui appartient à l'utilisateur.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..model.items import Item, Slot
from ..model.stats import StatKey

BUILD_URL = "https://dofusdb.fr/fr/tools/stuff/{id}"
API_URL = "https://api.dofusdb.fr/stuffs"

#: emplacement interne → clé DofusDB. Les emplacements à plusieurs items
#: (anneaux, dofus) prennent une liste.
SLOT_KEYS: dict[Slot, tuple[str, bool]] = {
    Slot.CHAPEAU: ("helmet", False),
    Slot.CAPE: ("cape", False),
    Slot.AMULETTE: ("amulet", False),
    Slot.CEINTURE: ("belt", False),
    Slot.BOTTES: ("boots", False),
    Slot.ARME: ("weapon", False),
    Slot.BOUCLIER: ("shield", False),
    Slot.FAMILIER: ("pet", False),
    Slot.ANNEAU: ("rings", True),
    Slot.DOFUS: ("dofus", True),
}

#: DofusDB ne modélise pas la monture : elle n'a pas d'emplacement dans son format.
UNSUPPORTED_SLOTS = {Slot.MONTURE}

#: caractéristique interne → identifiant DofusDB, pour `base` et `parchment`.
CHARACTERISTIC_KEYS: dict[StatKey, str] = {
    StatKey.VITALITE: "vitality",
    StatKey.FORCE: "strength",
    StatKey.INTELLIGENCE: "intelligence",
    StatKey.AGILITE: "agility",
    StatKey.CHANCE: "chance",
    StatKey.SAGESSE: "wisdom",
}

#: caractéristique interne → identifiant numérique DofusDB, pour les exotiques.
EXO_STAT_IDS: dict[StatKey, int] = {
    StatKey.PA: 1,
    StatKey.PM: 23,
    StatKey.PO: 19,
}


@dataclass
class ExportReport:
    warnings: list[str] = field(default_factory=list)


def _item_ids_by_slot(items: list[Item], report: ExportReport) -> dict[str, object]:
    grouped: dict[Slot, list[Item]] = {}
    for item in items:
        grouped.setdefault(item.slot, []).append(item)

    payload: dict[str, object] = {}
    for slot, chosen in grouped.items():
        if slot in UNSUPPORTED_SLOTS:
            names = ", ".join(i.name for i in chosen)
            report.warnings.append(
                f"emplacement {slot.value} non représentable dans DofusDB "
                f"— {names} sera absent du lien"
            )
            continue

        mapping = SLOT_KEYS.get(slot)
        if mapping is None:
            report.warnings.append(f"emplacement inconnu de DofusDB : {slot.value}")
            continue

        key, is_list = mapping
        # Un item forgemagé porte un identifiant interne au solveur. DofusDB
        # attend le modèle du catalogue : les exotiques y sont déclarés à part,
        # dans le champ `exo` du build.
        ids = []
        for item in chosen:
            if item.ankama_id >= 0:
                ids.append(item.ankama_id)
            elif item.derived_from is not None:
                ids.append(item.derived_from)
                report.warnings.append(
                    f"{item.name} exporté comme son modèle du catalogue ; "
                    "la forgemagie est portée par le champ « exo »"
                )
            else:
                report.warnings.append(
                    f"{item.name} : aucun modèle du catalogue, item omis"
                )
        if not ids:
            continue
        payload[key] = ids if is_list else ids[0]

    return payload


def build_payload(
    items: list[Item],
    *,
    name: str,
    level: int,
    breed_id: int,
    invested: dict[StatKey, int] | None = None,
    scrolls: dict[StatKey, int] | None = None,
    exos: dict[StatKey, int] | None = None,
    shared: str = "private",
    sexe: str = "male",
) -> tuple[dict, ExportReport]:
    """Construit la charge utile d'un build au format `stuffs`.

    `invested` correspond aux points de niveau dépensés, `scrolls` aux
    parchemins : DofusDB les stocke séparément, comme le fait le jeu.
    """
    report = ExportReport()

    payload = {
        "name": name,
        "level": level,
        # À la création, seul `breed` est retenu : le champ `classe` que renvoie
        # la lecture est ignoré à l'écriture, et un build envoyé sans `breed`
        # ressort classé Féca. Vérifié en publiant.
        "breed": breed_id,
        "classe": breed_id,
        "sexe": sexe,
        "shared": shared,
        "items": _item_ids_by_slot(items, report),
        "base": {
            key: (invested or {}).get(stat, 0)
            for stat, key in CHARACTERISTIC_KEYS.items()
        },
        "parchment": {
            key: (scrolls or {}).get(stat, 0)
            for stat, key in CHARACTERISTIC_KEYS.items()
        },
        "exo": [
            {"stat": EXO_STAT_IDS[stat], "value": value}
            for stat, value in (exos or {}).items()
            if stat in EXO_STAT_IDS and value
        ],
    }

    for stat in (exos or {}):
        if stat not in EXO_STAT_IDS:
            report.warnings.append(
                f"exotique sur {stat.value} non représentable — seuls PA, PM et PO le sont"
            )

    return payload, report


def to_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def publish_command(path: str) -> str:
    """Commande prête à l'emploi pour publier le build soi-même."""
    return (
        f'curl -X POST {API_URL} -H "Content-Type: application/json" '
        f'--data-binary "@{path}"'
    )


def build_url(stuff_id: str) -> str:
    return BUILD_URL.format(id=stuff_id)
