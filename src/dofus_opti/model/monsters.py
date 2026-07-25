"""Monstres — les cibles réelles contre lesquelles optimiser.

Un monstre existe en plusieurs « grades » (paliers de puissance) : le Bouftou de
niveau 1 et celui de niveau 20 sont le même monstre à deux grades différents.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class MonsterGrade:
    """Un palier de puissance d'un monstre.

    Les résistances sont des pourcentages, et **peuvent être négatives** : le
    Bouftou a −50 en Air, c'est-à-dire qu'il subit 50 % de dégâts Air en plus.
    """

    grade: int
    level: int
    life_points: int
    action_points: int
    movement_points: int
    res_pct: dict[str, int] = field(default_factory=dict)

    def weakest_element(self) -> str | None:
        """Élément le moins résisté — le plus rentable à jouer contre cette cible."""
        return min(self.res_pct, key=self.res_pct.__getitem__) if self.res_pct else None


@dataclass(frozen=True, slots=True)
class Monster:
    monster_id: int
    name: str
    grades: tuple[MonsterGrade, ...] = ()

    def at_grade(self, grade: int | None = None) -> MonsterGrade | None:
        """Un grade précis, ou le plus élevé par défaut."""
        if not self.grades:
            return None
        if grade is None:
            return max(self.grades, key=lambda g: g.level)
        return next((g for g in self.grades if g.grade == grade), None)
