"""Modèle de domaine : emplacements, items, panoplies, conditions d'équipement."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from .stats import StatKey


class Slot(StrEnum):
    """Emplacements d'équipement du personnage."""

    CHAPEAU = "chapeau"
    CAPE = "cape"
    AMULETTE = "amulette"
    ANNEAU = "anneau"
    CEINTURE = "ceinture"
    BOTTES = "bottes"
    ARME = "arme"
    BOUCLIER = "bouclier"
    FAMILIER = "familier"
    MONTURE = "monture"
    DOFUS = "dofus"  # dofus, trophées et prysmaradites partagent les 6 emplacements


#: Emplacements qui n'en font qu'un en jeu, avec leur capacité commune.
#:
#: Familier et monture partagent le même emplacement : on équipe l'un **ou**
#: l'autre. C'est aussi pourquoi DofusDB n'expose qu'une clé `pet`.
#: (défini après `Slot`, voir plus bas.)

#: Nombre d'emplacements disponibles par type.
SLOT_CAPACITY: dict[Slot, int] = {
    Slot.CHAPEAU: 1,
    Slot.CAPE: 1,
    Slot.AMULETTE: 1,
    Slot.ANNEAU: 2,
    Slot.CEINTURE: 1,
    Slot.BOTTES: 1,
    Slot.ARME: 1,
    Slot.BOUCLIER: 1,
    Slot.FAMILIER: 1,
    Slot.MONTURE: 1,
    Slot.DOFUS: 6,
}

#: Emplacements qui n'en font qu'un en jeu, avec leur capacité commune.
#:
#: Familier et monture partagent le même emplacement : on équipe l'un **ou**
#: l'autre, jamais les deux. C'est aussi pourquoi DofusDB n'expose qu'une clé
#: `pet` et aucune clé monture.
EXCLUSIVE_SLOT_GROUPS: tuple[tuple[frozenset[Slot], int], ...] = (
    (frozenset({Slot.FAMILIER, Slot.MONTURE}), 1),
)


@dataclass(frozen=True, slots=True)
class StatRange:
    """Fourchette de jet d'une caractéristique sur un item.

    Beaucoup d'effets sont à valeur fixe : dans ce cas `minimum == maximum`.
    """

    minimum: int
    maximum: int

    @property
    def is_fixed(self) -> bool:
        return self.minimum == self.maximum


class ConditionOp(StrEnum):
    GT = ">"
    LT = "<"
    EQ = "="
    GTE = ">="
    LTE = "<="


class ConditionSubject(StrEnum):
    """Ce sur quoi porte une condition d'équipement."""

    STAT = "stat"  # une caractéristique du personnage
    LEVEL = "level"  # niveau du personnage
    SET_BONUS_COUNT = "set_bonus_count"  # nombre de bonus de panoplies actifs
    SUBSCRIPTION = "subscription"  # être abonné
    ALIGNMENT_LEVEL = "alignment_level"
    KAMAS = "kamas"


@dataclass(frozen=True, slots=True)
class LeafCondition:
    subject: ConditionSubject
    operator: ConditionOp
    value: int
    stat: StatKey | None = None  # renseigné si subject == STAT
    raw_element_id: int = -1


@dataclass(frozen=True, slots=True)
class ConditionNode:
    """Nœud interne d'un arbre de conditions (`and` / `or`)."""

    relation: str  # "and" | "or"
    children: tuple["Condition", ...]


Condition = LeafCondition | ConditionNode


@dataclass(frozen=True, slots=True)
class WeaponHit:
    """Un « coup » d'arme : dégâts, vol de vie ou soin, sur un élément."""

    kind: str  # "damage" | "steal" | "heal"
    element: str  # élément, ou "best" pour « meilleur élément »
    minimum: int
    maximum: int


@dataclass(frozen=True, slots=True)
class SpellModifier:
    """Modificateur de sort porté par un item (ex. « Agitation : -1 PA »).

    L'API ne fournit que le libellé ; l'association à un sort réel se fera au
    jalon des sorts. On conserve le texte brut pour ne rien perdre.
    """

    raw: str
    effect_id: int


@dataclass(frozen=True, slots=True)
class Item:
    ankama_id: int
    name: str
    slot: Slot
    type_id: int
    type_name: str
    level: int
    is_weapon: bool
    pods: int
    stats: dict[StatKey, StatRange] = field(default_factory=dict)
    set_id: int | None = None
    condition: Condition | None = None
    #: « Lié au personnage » — ni échangeable ni achetable, donc non planifiable.
    bound_to_character: bool = False
    #: identifiant du modèle du catalogue, pour un item forgemagé par le joueur
    derived_from: int | None = None
    # --- armes uniquement
    weapon_hits: tuple[WeaponHit, ...] = ()
    ap_cost: int | None = None
    crit_probability: int | None = None
    crit_bonus: int | None = None
    max_cast_per_turn: int | None = None
    range_min: int | None = None
    range_max: int | None = None
    # --- non structuré, conservé tel quel
    spell_modifiers: tuple[SpellModifier, ...] = ()
    special_effects: tuple[str, ...] = ()

    def stat(self, key: StatKey, *, roll: str = "max") -> int:
        """Valeur d'une stat pour un jet donné (`max`, `min` ou `avg`)."""
        r = self.stats.get(key)
        if r is None:
            return 0
        if roll == "min":
            return r.minimum
        if roll == "avg":
            return (r.minimum + r.maximum) // 2
        return r.maximum


@dataclass(frozen=True, slots=True)
class ItemSet:
    """Panoplie. `bonuses[n]` = stats accordées quand n items sont portés."""

    ankama_id: int
    name: str
    level: int
    n_items: int
    bonuses: dict[int, dict[StatKey, StatRange]] = field(default_factory=dict)
    #: effets de panoplie non convertibles en stats (sorts, titres…)
    raw_bonuses: dict[int, tuple[str, ...]] = field(default_factory=dict)
