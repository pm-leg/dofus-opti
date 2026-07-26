"""Vecteur de caractéristiques agrégées d'un personnage."""

from __future__ import annotations

from collections.abc import Mapping

from ..model.stats import StatKey

#: PA et PM de base d'un personnage niveau 1, avant équipement et buffs.
BASE_PA = 6
BASE_PM = 3

#: Niveau auquel le personnage gagne définitivement un point d'action.
PA_BONUS_LEVEL = 100

#: Plafonds du jeu. Sans eux, un sort à lancers illimités pousse le solveur à
#: empiler les PA sans limite : il proposait 22 PA sur un Torrent Arcanique.
#: Relevé sur 1 500 builds de niveau 200 : 12 PA et 6 PM sont les maximums, les
#: rares valeurs au-dessus venant d'éditeurs qui ne vérifient pas la règle.
MAX_ACTION_POINTS = 12
MAX_MOVEMENT_POINTS = 6

#: Portée maximale. Même relevé : 139 builds à 6, et seulement 21 au-dessus —
#: des saisies d'éditeur, qui ne vérifie aucune règle du jeu.
MAX_RANGE = 6

#: Plafond de la **réduction** apportée par une résistance en pourcentage.
#:
#: À ne pas confondre avec un plafond sur la caractéristique : 41 builds publics
#: sur 1 500 affichent plus de 50 %, jusqu'à 81. Le surplus existe mais ne sert à
#: rien. Contraindre le total à 50 rendrait donc infaisables des builds légaux ;
#: on se contente d'avertir quand un joueur en demande davantage.
MAX_RESISTANCE_PCT = 50


def base_action_points(level: int) -> int:
    """PA de base à un niveau donné.

    Le personnage gagne un PA permanent au niveau 100 : un niveau 175 démarre
    donc à 7, pas à 6. Déduit des builds publics de DofusDB — 156 builds de
    niveau 100 à 149 donnent 7, contre 89 builds sous le niveau 100 qui donnent 6.
    Ignorer ce point décale toutes les contraintes de PA d'une unité.
    """
    return BASE_PA + (1 if level >= PA_BONUS_LEVEL else 0)


def base_movement_points(level: int) -> int:
    """PM de base. Aucun palier connu : 3 à tous les niveaux."""
    return BASE_PM


class StatVector(Mapping[StatKey, int]):
    """Somme des caractéristiques d'un build. Immuable, additionnable."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[StatKey, int] | None = None) -> None:
        self._values = dict(values or {})

    def __getitem__(self, key: StatKey) -> int:
        return self._values.get(key, 0)

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __add__(self, other: Mapping[StatKey, int]) -> "StatVector":
        merged = dict(self._values)
        for key, value in other.items():
            merged[key] = merged.get(key, 0) + value
        return StatVector(merged)

    def with_(self, **kwargs: int) -> "StatVector":
        """Copie enrichie : `vec.with_(force=500, puissance=50)`."""
        merged = dict(self._values)
        for name, value in kwargs.items():
            merged[StatKey(name)] = merged.get(StatKey(name), 0) + value
        return StatVector(merged)

    @property
    def pa(self) -> int:
        return BASE_PA + self[StatKey.PA]

    @property
    def pm(self) -> int:
        return BASE_PM + self[StatKey.PM]

    def __repr__(self) -> str:
        shown = ", ".join(f"{k.value}={v}" for k, v in sorted(self._values.items()) if v)
        return f"StatVector({shown})"
