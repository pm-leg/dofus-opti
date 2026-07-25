"""Effets de sorts qui produisent des dégâts.

Même principe qu'en M0 : la table est explicite, et tout ce qui n'y figure pas est
ignoré délibérément — un sort porte des dizaines d'effets (états, buffs,
invocations) dont l'immense majorité ne concerne pas le calcul de dégâts.

La différence avec les équipements : DofusDB expose sa propre table `effects`,
descriptions comprises. On s'en sert pour **vérifier** notre table à chaque
ingestion. Si Ankama renumérote un effet, la description ne correspondra plus et
l'ingestion échouera au lieu de produire silencieusement des dégâts faux.
"""

from __future__ import annotations

from dataclasses import dataclass

#: `elementId` de DofusDB → élément. Déduit et vérifié sur les descriptions
#: officielles (« dommages Terre » porte `elementId = 1`, etc.).
ELEMENT_BY_ID: dict[int, str] = {
    0: "neutre",
    1: "terre",
    2: "feu",
    3: "eau",
    4: "air",
}

#: `elementId = 5` désigne un effet non élémentaire (soins génériques, dommages
#: bruts). `-1` signifie « sans élément ».
NON_ELEMENTAL_IDS = {-1, 5}


@dataclass(frozen=True, slots=True)
class SpellEffectMapping:
    kind: str  # "damage" | "steal"
    element: str
    #: fragment attendu dans la description DofusDB, en minuscules.
    expected_description: str


#: `effectId` → sens, pour les seuls effets qui infligent des dégâts.
DAMAGE_EFFECTS: dict[int, SpellEffectMapping] = {
    100: SpellEffectMapping("damage", "neutre", "dommages neutre"),
    97: SpellEffectMapping("damage", "terre", "dommages terre"),
    99: SpellEffectMapping("damage", "feu", "dommages feu"),
    96: SpellEffectMapping("damage", "eau", "dommages eau"),
    98: SpellEffectMapping("damage", "air", "dommages air"),
    # Le vol de vie inflige les mêmes dégâts qu'une attaque classique ; seul
    # s'y ajoute un soin pour le lanceur, hors du calcul offensif.
    95: SpellEffectMapping("steal", "neutre", "vol neutre"),
    92: SpellEffectMapping("steal", "terre", "vol terre"),
    94: SpellEffectMapping("steal", "feu", "vol feu"),
    91: SpellEffectMapping("steal", "eau", "vol eau"),
    93: SpellEffectMapping("steal", "air", "vol air"),
}

#: Effets écartés sciemment, avec la raison. Documente les décisions de modélisation.
EXCLUDED_DAMAGE_EFFECTS: dict[int, str] = {
    109: "dommages infligés au lanceur — pas à la cible",
    85: "dommages Eau en % des PV du lanceur — mécanique distincte, hors modèle v1",
    86: "dommages Terre en % des PV du lanceur — idem",
    87: "dommages Air en % des PV du lanceur — idem",
    88: "dommages Feu en % des PV du lanceur — idem",
    89: "dommages Neutre en % des PV du lanceur — idem",
    90: "transfert de PV en pourcentage — ni dégâts ni soin classiques",
}


class SpellEffectTableMismatch(RuntimeError):
    """La table locale ne correspond plus aux descriptions de DofusDB."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__(
            "La table des effets de sorts ne correspond plus à la source :\n  "
            + "\n  ".join(problems)
            + "\nVérifiez DAMAGE_EFFECTS (src/dofus_opti/ingest/spell_effects.py) : "
            "les identifiants d'effet ont probablement changé."
        )
        self.problems = problems


def verify_against_source(raw_effects: list[dict]) -> None:
    """Confronte `DAMAGE_EFFECTS` à la table officielle de DofusDB.

    C'est le garde-fou qui rend l'ingestion des sorts sûre : une renumérotation
    d'effet côté Ankama casserait silencieusement tous les dégâts, et ce contrôle
    la transforme en échec explicite.
    """
    by_id = {e["id"]: e for e in raw_effects}
    problems: list[str] = []

    for effect_id, mapping in DAMAGE_EFFECTS.items():
        source = by_id.get(effect_id)
        if source is None:
            problems.append(f"effet {effect_id} absent de la source")
            continue

        description = ((source.get("description") or {}).get("fr") or "").lower()
        if mapping.expected_description not in description:
            problems.append(
                f"effet {effect_id} : attendu « {mapping.expected_description} », "
                f"trouvé « {description[:60]} »"
            )

        element_id = source.get("elementId")
        expected_element = ELEMENT_BY_ID.get(element_id)
        if expected_element != mapping.element:
            problems.append(
                f"effet {effect_id} : élément {mapping.element} attendu, "
                f"elementId={element_id} donne {expected_element}"
            )

    for element_id, element in ELEMENT_BY_ID.items():
        matching = [
            e for e in raw_effects
            if e.get("elementId") == element_id
            and element in ((e.get("description") or {}).get("fr") or "").lower()
        ]
        if not matching:
            problems.append(
                f"elementId={element_id} ne correspond plus à « {element} » "
                "dans les descriptions"
            )

    if problems:
        raise SpellEffectTableMismatch(problems)
