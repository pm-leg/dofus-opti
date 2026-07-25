"""Analyse des conditions d'équipement.

Les conditions arrivent sous forme d'arbre :

    feuille : {"condition": {"operator", "int_value", "element": {...}}, "is_operand": true}
    nœud    : {"relation": "and"|"or", "children": [...], "is_operand": false}

Elles comptent : « Force > 500 » couple le choix d'un item aux stats totales du
build. Le solveur les traduira en contraintes réifiées.
"""

from __future__ import annotations

from ..model.items import (
    Condition,
    ConditionNode,
    ConditionOp,
    ConditionSubject,
    LeafCondition,
)
from ..model.stats import StatKey

#: `element.id` d'une condition → sujet, et stat associée le cas échéant.
CONDITION_ELEMENTS: dict[int, tuple[ConditionSubject, StatKey | None]] = {
    200: (ConditionSubject.LEVEL, None),
    72: (ConditionSubject.SET_BONUS_COUNT, None),
    252: (ConditionSubject.SUBSCRIPTION, None),
    55: (ConditionSubject.ALIGNMENT_LEVEL, None),
    237: (ConditionSubject.KAMAS, None),
    45: (ConditionSubject.STAT, StatKey.FORCE),
    13: (ConditionSubject.STAT, StatKey.INTELLIGENCE),
    36: (ConditionSubject.STAT, StatKey.AGILITE),
    22: (ConditionSubject.STAT, StatKey.CHANCE),
    9: (ConditionSubject.STAT, StatKey.VITALITE),
    10: (ConditionSubject.STAT, StatKey.SAGESSE),
    12: (ConditionSubject.STAT, StatKey.PA),
    8: (ConditionSubject.STAT, StatKey.PM),
}


class UnknownConditionElementError(RuntimeError):
    def __init__(self, unknown: dict[int, str]) -> None:
        lines = "\n".join(f"  id={i:<5} name={n!r}" for i, n in sorted(unknown.items()))
        super().__init__(
            f"{len(unknown)} élément(s) de condition inconnu(s).\n{lines}\n"
            "Ajoutez-les à CONDITION_ELEMENTS (src/dofus_opti/ingest/conditions.py)."
        )
        self.unknown = unknown


def parse_condition(raw: dict | None, unknown: dict[int, str]) -> Condition | None:
    """Convertit l'arbre brut. Les éléments inconnus sont collectés dans `unknown`.

    On ne lève pas ici : l'appelant agrège les inconnus de tout le catalogue pour
    les signaler d'un coup plutôt qu'un par un.
    """
    if not raw:
        return None

    if "relation" in raw:
        children = tuple(
            c
            for c in (parse_condition(child, unknown) for child in raw.get("children") or [])
            if c is not None
        )
        if not children:
            return None
        if len(children) == 1:
            return children[0]
        return ConditionNode(relation=raw["relation"], children=children)

    cond = raw.get("condition")
    if not cond:
        return None

    element = cond["element"]
    element_id = element["id"]
    mapping = CONDITION_ELEMENTS.get(element_id)
    if mapping is None:
        unknown[element_id] = element.get("name", "?")
        return None

    subject, stat = mapping
    return LeafCondition(
        subject=subject,
        operator=ConditionOp(cond["operator"]),
        value=int(cond["int_value"]),
        stat=stat,
        raw_element_id=element_id,
    )


def condition_to_dict(cond: Condition | None) -> dict | None:
    """Sérialisation stable, pour stockage en base et relecture par le solveur."""
    if cond is None:
        return None
    if isinstance(cond, ConditionNode):
        return {
            "relation": cond.relation,
            "children": [condition_to_dict(c) for c in cond.children],
        }
    return {
        "subject": cond.subject.value,
        "operator": cond.operator.value,
        "value": cond.value,
        "stat": cond.stat.value if cond.stat else None,
        "raw_element_id": cond.raw_element_id,
    }


def condition_from_dict(data: dict | None) -> Condition | None:
    if not data:
        return None
    if "relation" in data:
        children = tuple(
            c for c in (condition_from_dict(x) for x in data["children"]) if c is not None
        )
        return ConditionNode(relation=data["relation"], children=children) if children else None
    return LeafCondition(
        subject=ConditionSubject(data["subject"]),
        operator=ConditionOp(data["operator"]),
        value=int(data["value"]),
        stat=StatKey(data["stat"]) if data.get("stat") else None,
        raw_element_id=int(data.get("raw_element_id", -1)),
    )


def condition_to_text(cond: Condition | None) -> str:
    """Rendu lisible, pour les diagnostics et l'affichage."""
    if cond is None:
        return ""
    if isinstance(cond, ConditionNode):
        sep = " ET " if cond.relation == "and" else " OU "
        return "(" + sep.join(condition_to_text(c) for c in cond.children) + ")"
    label = cond.stat.value if cond.stat else cond.subject.value
    return f"{label} {cond.operator.value} {cond.value}"
