"""Items du joueur : forgemagie, exotiques, jets réels.

Un optimiseur qui ne connaît que le catalogue est inutilisable : personne ne joue
avec des items au jet du catalogue. Un Gelano forgemagé PA/PM, un exo PM sur une
cape, un jet moyen sur une amulette — c'est cela qu'il faut pouvoir décrire.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace

from ..model.items import Item, StatRange
from ..model.stats import StatKey


class CustomItemError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CustomItemSpec:
    """« Gelano:pm=+1 » — un item du catalogue, modifié.

    Les valeurs préfixées de `+` ou `-` s'ajoutent au jet du catalogue ; les
    autres le remplacent.
    """

    base_name: str
    overrides: dict[StatKey, tuple[str, int]]  # stat → (mode, valeur)

    @staticmethod
    def parse(text: str) -> "CustomItemSpec":
        if ":" not in text:
            raise CustomItemError(
                f"« {text} » : format attendu « Nom:stat=+1,stat=valeur » "
                "(ex. « Gelano:pm=+1 »)"
            )
        base_name, _, raw_overrides = text.partition(":")
        base_name = base_name.strip()
        if not base_name:
            raise CustomItemError(f"« {text} » : nom d'item manquant")

        overrides: dict[StatKey, tuple[str, int]] = {}
        for chunk in raw_overrides.split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            if "=" not in chunk:
                raise CustomItemError(f"« {chunk} » : attendu « stat=valeur »")
            raw_key, _, raw_value = chunk.partition("=")
            raw_key, raw_value = raw_key.strip(), raw_value.strip()
            try:
                key = StatKey(raw_key)
            except ValueError:
                raise CustomItemError(
                    f"caractéristique inconnue : « {raw_key} ». "
                    f"Valeurs possibles : {', '.join(sorted(k.value for k in StatKey))}"
                ) from None
            mode = "delta" if raw_value[:1] in "+-" else "set"
            try:
                value = int(raw_value)
            except ValueError:
                raise CustomItemError(f"« {raw_value} » n'est pas un entier") from None
            overrides[key] = (mode, value)

        if not overrides:
            raise CustomItemError(f"« {text} » : aucune modification indiquée")
        return CustomItemSpec(base_name=base_name, overrides=overrides)

    def describe(self) -> str:
        parts = [
            f"{key.value} {'+' if mode == 'delta' and value >= 0 else ''}{value}"
            for key, (mode, value) in sorted(self.overrides.items())
        ]
        return f"{self.base_name} ({', '.join(parts)})"


def find_item(conn: sqlite3.Connection, name: str) -> tuple[int, ...] | None:
    row = conn.execute(
        "SELECT ankama_id FROM item WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    return row


def _suggestions(conn: sqlite3.Connection, name: str) -> list[str]:
    return [
        row[0] for row in conn.execute(
            "SELECT name FROM item WHERE name LIKE ? COLLATE NOCASE LIMIT 5",
            (f"%{name}%",),
        )
    ]


def build_custom_item(
    conn: sqlite3.Connection,
    spec: CustomItemSpec,
    base_items: dict[str, Item],
    *,
    custom_id: int,
    roll: str = "max",
) -> Item:
    """Construit l'item modifié à partir de son modèle du catalogue."""
    base = base_items.get(spec.base_name.casefold())
    if base is None:
        near = _suggestions(conn, spec.base_name)
        hint = f" Proches : {', '.join(near)}." if near else ""
        raise CustomItemError(f"item « {spec.base_name} » introuvable.{hint}")

    stats = dict(base.stats)
    for key, (mode, value) in spec.overrides.items():
        if mode == "delta":
            current = base.stat(key, roll=roll)
            total = current + value
        else:
            total = value
        stats[key] = StatRange(total, total)

    return replace(
        base,
        ankama_id=custom_id,
        name=f"{base.name} (perso)",
        stats=stats,
        # Un item que le joueur possède déjà échappe à tout filtre d'obtention.
        bound_to_character=False,
        # Conserver le modèle permet à l'export de désigner l'item réel.
        derived_from=base.ankama_id,
    )


def apply_custom_items(
    conn: sqlite3.Connection,
    specs: list[CustomItemSpec],
    items: list[Item],
    *,
    roll: str = "max",
) -> tuple[list[Item], list[int], list[str]]:
    """Ajoute les items du joueur et retire les modèles dont ils dérivent.

    Retirer le modèle évite qu'un Gelano de catalogue et un Gelano forgemagé
    cohabitent : le jeu interdit de porter deux fois le même anneau, et le
    solveur n'a aucune raison de le savoir.
    """
    by_name = {item.name.casefold(): item for item in items}
    custom: list[Item] = []
    replaced: list[int] = []
    notes: list[str] = []

    for offset, spec in enumerate(specs, start=1):
        built = build_custom_item(
            conn, spec, by_name, custom_id=-offset, roll=roll
        )
        custom.append(built)
        base = by_name[spec.base_name.casefold()]
        replaced.append(base.ankama_id)
        notes.append(
            f"{built.name} : " + ", ".join(
                f"{key.value} {built.stat(key, roll=roll)}"
                for key in sorted(spec.overrides)
            )
        )

    kept = [item for item in items if item.ankama_id not in replaced]
    return kept + custom, replaced, notes
