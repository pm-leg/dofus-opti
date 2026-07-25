"""Expression d'une demande d'optimisation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..combat.formula import CritPolicy, FormulaVariant, Target
from ..model.items import Item, Slot
from ..model.stats import StatKey

if TYPE_CHECKING:
    from .custom import CustomItemSpec


@dataclass(frozen=True, slots=True)
class StatBound:
    """Contrainte sur une caractéristique totale du build."""

    minimum: int | None = None
    maximum: int | None = None

    @staticmethod
    def exactly(value: int) -> "StatBound":
        return StatBound(minimum=value, maximum=value)

    @staticmethod
    def at_least(value: int) -> "StatBound":
        return StatBound(minimum=value)

    @staticmethod
    def at_most(value: int) -> "StatBound":
        return StatBound(maximum=value)

    def describe(self, key: StatKey) -> str:
        if self.minimum is not None and self.minimum == self.maximum:
            return f"{key.value} = {self.minimum}"
        parts = []
        if self.minimum is not None:
            parts.append(f"≥ {self.minimum}")
        if self.maximum is not None:
            parts.append(f"≤ {self.maximum}")
        return f"{key.value} " + " et ".join(parts)


@dataclass
class BuildRequest:
    """Tout ce qui définit « le meilleur stuff » pour un joueur donné.

    Les contraintes portent sur les caractéristiques **totales** du build, PA et
    PM inclus (base du personnage comprise).
    """

    level: int
    breed: str
    elements: set[str]

    #: contraintes dures — `StatKey.PA: StatBound.exactly(12)`
    bounds: dict[StatKey, StatBound] = field(default_factory=dict)

    #: cible du calcul de dégâts
    target: Target = field(default_factory=Target)
    crit_policy: CritPolicy = CritPolicy.EXPECTED
    variant: FormulaVariant = FormulaVariant.ADDITIVE

    #: jet retenu sur les items : "max", "avg" ou "min"
    roll: str = "max"

    #: sorts à charges : "max" les évalue au cumul maximal, "none" à la base nue
    charge_policy: str = "max"

    #: restreint la rotation à ces sorts. Vide = tous les sorts offensifs.
    spell_names: set[str] = field(default_factory=set)
    #: remplace la base d'un sort, pour ceux dont les dégâts sont scriptés
    spell_bases: dict[str, tuple[int, int]] = field(default_factory=dict)

    #: si renseigné, seuls ces dofus/trophées sont autorisés (par identifiant)
    allowed_dofus: set[int] | None = None
    #: items exclus du pool, quelle qu'en soit la raison
    banned_items: set[int] = field(default_factory=set)
    #: items réintégrés par leur nom malgré le filtre d'obtention
    allowed_items: set[str] = field(default_factory=set)
    #: emplacements laissés vides d'office
    excluded_slots: set[Slot] = field(default_factory=set)
    #: items forgemagés ou exotiques définis par le joueur (Gelano PA/PM…)
    custom_items: list[Item] = field(default_factory=list)
    #: mêmes items, décrits par rapport à un modèle du catalogue
    custom_specs: list["CustomItemSpec"] = field(default_factory=list)
    #: items imposés dans la solution, par identifiant
    forced_items: set[int] = field(default_factory=set)

    #: caractéristiques hors équipement : points de niveau et parchemins
    base_characteristics: dict[StatKey, int] = field(default_factory=dict)

    #: exotiques disponibles, par caractéristique. Un exo se pose sur un item mais
    #: se comporte comme un bonus de build : avec seize emplacements, savoir
    #: lequel le porte ne change pas le total. C'est aussi la représentation de
    #: DofusDB.
    exos: dict[StatKey, int] = field(default_factory=dict)

    #: Réintègre les prysmaradites. Écartées par défaut : leurs contreparties
    #: (« sacrifie 35 % de dommages finaux ») sont en texte libre, donc invisibles
    #: du solveur, qui les prendrait pour du gain pur.
    allow_prysmaradites: bool = False

    #: plafond du nombre de panoplies actives, imposé par le joueur.
    #: Les items portent déjà leur propre condition (« bonus de panoplies < 3 »),
    #: encodée automatiquement ; ceci ne sert qu'à la durcir.
    max_set_bonuses: int | None = None

    #: on suppose le compte abonné (conditionne certains items)
    subscribed: bool = True
    #: écarte les items sans butin ni recette (objets d'administrateur et
    #: d'évènement). L'emplacement Dofus y échappe : son pool est défini par le
    #: joueur, et les Dofus de quête n'ont par nature aucune de ces sources.
    require_obtainable: bool = True

    def bound(self, key: StatKey) -> StatBound | None:
        return self.bounds.get(key)

    def describe_constraints(self) -> list[str]:
        return [bound.describe(key) for key, bound in sorted(self.bounds.items())]
