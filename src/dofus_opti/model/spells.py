"""Modèle de domaine des sorts."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class DamageRoll:
    """Un jet de dégâts sur un élément, hors critique et en critique.

    Quand un sort ne peut pas être critique, `crit_min`/`crit_max` recopient les
    valeurs normales : le moteur ne les utilisera pas.
    """

    element: str
    base_min: int
    base_max: int
    crit_min: int
    crit_max: int

    @property
    def is_fixed(self) -> bool:
        return self.base_min == self.base_max


@dataclass(frozen=True, slots=True)
class SpellLevel:
    """Un sort à un palier donné (`grade` 1 à 6 dans Dofus)."""

    grade: int
    ap_cost: int
    crit_probability: int
    range_min: int
    range_max: int
    max_cast_per_turn: int
    max_cast_per_target: int
    min_player_level: int
    rolls: tuple[DamageRoll, ...] = ()
    cast_in_line: bool = False
    needs_line_of_sight: bool = True
    range_can_be_boosted: bool = False
    #: nombre maximal de charges cumulables (« Cumul : N » en jeu), 0 sinon
    max_stack: int = 0
    #: dégâts sur la durée (poisons) — conservés à part des dégâts directs
    over_time_rolls: tuple[DamageRoll, ...] = ()

    @property
    def deals_direct_damage(self) -> bool:
        return bool(self.rolls)


@dataclass(frozen=True, slots=True)
class ClassSpell:
    """Un sort de classe, tous paliers confondus."""

    spell_id: int
    name: str
    breed_id: int
    levels: tuple[SpellLevel, ...] = ()

    def at_character_level(self, character_level: int) -> SpellLevel | None:
        """Palier le plus élevé accessible à un niveau de personnage donné."""
        eligible = [lv for lv in self.levels if lv.min_player_level <= character_level]
        return max(eligible, key=lambda lv: lv.grade) if eligible else None


@dataclass(frozen=True, slots=True)
class Breed:
    """Une classe de personnage.

    `stat_costs` donne, par caractéristique, le barème de répartition des points
    gagnés en montant de niveau : une liste de paliers `(seuil, coût unitaire)`.
    `[(0, 1), (100, 2), (200, 3), (300, 4)]` se lit « 1 point par unité jusqu'à
    100, puis 2, puis 3, puis 4 au-delà de 300 ».
    """

    breed_id: int
    name: str
    stat_costs: dict[str, tuple[tuple[int, int], ...]] = field(default_factory=dict)
