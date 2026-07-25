"""Chargement des sorts depuis le catalogue local.

C'est le pont entre l'ingestion et le moteur : il rend les dégâts calculables sur
la vraie classe du joueur, au niveau qu'il a réellement.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ..model.monsters import Monster, MonsterGrade
from ..model.spells import ClassSpell, DamageRoll, SpellLevel
from .formula import Target
from .spell import Spell, from_class_spell


class UnknownBreedError(LookupError):
    def __init__(self, name: str, available: list[str]) -> None:
        super().__init__(
            f"classe « {name} » inconnue. Classes disponibles : {', '.join(available)}"
        )


def available_breeds(conn: sqlite3.Connection) -> list[str]:
    return [row[0] for row in conn.execute("SELECT name FROM breed ORDER BY name")]


def load_class_spells(
    conn: sqlite3.Connection, breed: str, *, charges: str = "max"
) -> list[ClassSpell]:
    """Tous les sorts d'une classe, tous paliers, avec leurs jets de dégâts.

    `charges="max"` évalue les sorts à charges à leur cumul maximal — Os à
    Moelle à 4 charges, Torrent Arcanique à 6 combinaisons. C'est l'hypothèse
    d'un joueur qui monte ses charges avant de frapper. `charges="none"` garde
    la base nue.
    """
    row = conn.execute(
        "SELECT breed_id FROM breed WHERE name = ? COLLATE NOCASE", (breed,)
    ).fetchone()
    if row is None:
        raise UnknownBreedError(breed, available_breeds(conn))
    breed_id = row[0]

    rolls: dict[tuple[int, int], list[DamageRoll]] = {}
    for spell_id, grade, element, bmin, bmax, cmin, cmax in conn.execute(
        """SELECT r.spell_id, r.grade, r.element, r.base_min, r.base_max, r.crit_min, r.crit_max
           FROM spell_roll r JOIN spell s ON s.spell_id = r.spell_id
           WHERE s.breed_id = ? AND r.over_time = 0""",
        (breed_id,),
    ):
        rolls.setdefault((spell_id, grade), []).append(
            DamageRoll(element, bmin, bmax, cmin, cmax)
        )

    # Bonus « +N dégâts de base » : le mécanisme des sorts à charges. Le sort
    # compagnon (Os à Moelle caché, compteur de combos de Torrent Arcanique)
    # porte l'incrément par palier ; le sort visible porte le cumul maximal.
    boosts: dict[int, list[tuple[int, int]]] = {}
    for target, source_grade, boost in conn.execute(
        """SELECT b.target_spell_id, b.source_grade, b.boost
           FROM spell_base_boost b JOIN spell s ON s.spell_id = b.target_spell_id
           WHERE s.breed_id = ? ORDER BY b.source_grade""",
        (breed_id,),
    ):
        boosts.setdefault(target, []).append((source_grade, boost))

    levels: dict[int, list[SpellLevel]] = {}
    for row in conn.execute(
        """SELECT l.spell_id, l.grade, l.ap_cost, l.crit_probability, l.range_min,
                  l.range_max, l.max_cast_per_turn, l.max_cast_per_target,
                  l.min_player_level, l.cast_in_line, l.needs_line_of_sight,
                  l.range_can_be_boosted, l.max_stack
           FROM spell_level l JOIN spell s ON s.spell_id = l.spell_id
           WHERE s.breed_id = ? ORDER BY l.spell_id, l.grade""",
        (breed_id,),
    ):
        spell_id, grade = row[0], row[1]
        levels.setdefault(spell_id, []).append(
            SpellLevel(
                grade=grade, ap_cost=row[2], crit_probability=row[3],
                range_min=row[4], range_max=row[5],
                max_cast_per_turn=row[6], max_cast_per_target=row[7],
                min_player_level=row[8],
                rolls=tuple(rolls.get((spell_id, grade), ())),
                cast_in_line=bool(row[9]), needs_line_of_sight=bool(row[10]),
                range_can_be_boosted=bool(row[11]), max_stack=row[12],
            )
        )

    if charges == "max":
        for spell_id, tiers in boosts.items():
            if spell_id in levels:
                levels[spell_id] = _apply_max_charges(levels[spell_id], tiers)

    return [
        ClassSpell(
            spell_id=spell_id, name=name, breed_id=breed_id,
            levels=tuple(levels.get(spell_id, ())),
        )
        for spell_id, name in conn.execute(
            "SELECT spell_id, name FROM spell WHERE breed_id = ? ORDER BY name", (breed_id,)
        )
    ]


def load_spells(
    conn: sqlite3.Connection,
    breed: str,
    character_level: int,
    *,
    elements: set[str] | None = None,
    offensive_only: bool = True,
    names: set[str] | None = None,
    base_overrides: dict[str, tuple[int, int]] | None = None,
    charges: str = "max",
) -> list[Spell]:
    """Sorts prêts pour le moteur : meilleur palier accessible au niveau donné.

    `elements` restreint aux sorts touchant au moins un des éléments demandés —
    c'est ce qui donne sens à « je joue Terre ». `names` restreint à une liste
    précise, pour optimiser sur un seul sort.

    `base_overrides` remplace la base d'un sort, par élément. Nécessaire pour les
    sorts dont les dégâts sont calculés par script : Torrent Arcanique porte `2`
    dans les données, sa valeur réelle dépendant des runes accumulées.
    """
    wanted = {n.casefold() for n in names} if names else None
    overrides = {k.casefold(): v for k, v in (base_overrides or {}).items()}

    spells: list[Spell] = []
    for class_spell in load_class_spells(conn, breed, charges=charges):
        if wanted is not None and class_spell.name.casefold() not in wanted:
            continue
        level = class_spell.at_character_level(character_level)
        if level is None:
            continue
        if offensive_only and not level.deals_direct_damage:
            continue
        if elements and not any(r.element in elements for r in level.rolls):
            continue

        override = overrides.get(class_spell.name.casefold())
        if override is not None:
            level = _with_base(level, *override)
        spells.append(from_class_spell(class_spell, level))
    return spells


def _apply_max_charges(
    levels: list[SpellLevel], tiers: list[tuple[int, int]]
) -> list[SpellLevel]:
    """Ajoute `incrément × cumul maximal` aux jets d'un sort à charges.

    Le sort compagnon a parfois un palier de plus que le sort visible (le
    premier ne fait que poser l'état, sans bonus) : on aligne les paliers **par
    la fin** — le dernier grade du compagnon correspond au dernier du sort.
    Vérifié sur Os à Moelle : compagnon +4/+5/+6 sur grades 2-4, sort visible
    12-14/16-19/21-24 sur grades 1-3, et l'échelle en jeu du grade 1 est bien
    12-14 → 28-30 à 4 charges (+4 ×4).
    """
    from dataclasses import replace

    increments = [boost for _, boost in tiers]
    out: list[SpellLevel] = []
    offset = len(increments) - len(levels)
    for index, level in enumerate(levels):
        pick = min(max(index + offset, 0), len(increments) - 1)
        bonus = increments[pick] * level.max_stack
        if bonus <= 0 or not level.rolls:
            out.append(level)
            continue
        out.append(
            replace(
                level,
                rolls=tuple(
                    DamageRoll(
                        element=r.element,
                        base_min=r.base_min + bonus, base_max=r.base_max + bonus,
                        crit_min=r.crit_min + bonus, crit_max=r.crit_max + bonus,
                    )
                    for r in level.rolls
                ),
            )
        )
    return out


def _with_base(level: SpellLevel, minimum: int, maximum: int) -> SpellLevel:
    """Remplace la base de chaque élément, en conservant le ratio critique."""
    from dataclasses import replace

    rolls = []
    for roll in level.rolls:
        # Le bonus critique du sort est conservé en proportion : Torrent
        # Arcanique double sa base en critique, on garde ce rapport.
        ratio = (roll.crit_max / roll.base_max) if roll.base_max else 1.0
        rolls.append(
            DamageRoll(
                element=roll.element,
                base_min=minimum, base_max=maximum,
                crit_min=round(minimum * ratio), crit_max=round(maximum * ratio),
            )
        )
    return replace(level, rolls=tuple(rolls))


def missing_spells(
    conn: sqlite3.Connection, breed: str, names: set[str]
) -> set[str]:
    """Noms demandés qui n'existent pas chez cette classe."""
    known = {s.name.casefold() for s in load_class_spells(conn, breed)}
    return {n for n in names if n.casefold() not in known}


class UnknownMonsterError(LookupError):
    def __init__(self, name: str, suggestions: list[str]) -> None:
        hint = f" Proches : {', '.join(suggestions)}." if suggestions else ""
        super().__init__(f"monstre « {name} » introuvable.{hint}")


def find_monsters(conn: sqlite3.Connection, pattern: str, limit: int = 20) -> list[Monster]:
    """Recherche par fragment de nom."""
    rows = conn.execute(
        "SELECT monster_id, name FROM monster WHERE name LIKE ? COLLATE NOCASE "
        "ORDER BY name LIMIT ?",
        (f"%{pattern}%", limit),
    ).fetchall()
    return [load_monster(conn, monster_id) for monster_id, _ in rows]


def load_monster(conn: sqlite3.Connection, monster_id: int) -> Monster:
    name = conn.execute(
        "SELECT name FROM monster WHERE monster_id = ?", (monster_id,)
    ).fetchone()[0]

    resistances: dict[int, dict[str, int]] = {}
    for grade, element, value in conn.execute(
        "SELECT grade, element, res_pct FROM monster_resistance WHERE monster_id = ?",
        (monster_id,),
    ):
        resistances.setdefault(grade, {})[element] = value

    grades = tuple(
        MonsterGrade(
            grade=row[0], level=row[1], life_points=row[2],
            action_points=row[3], movement_points=row[4],
            res_pct=resistances.get(row[0], {}),
        )
        for row in conn.execute(
            """SELECT grade, level, life_points, action_points, movement_points
               FROM monster_grade WHERE monster_id = ? ORDER BY grade""",
            (monster_id,),
        )
    )
    return Monster(monster_id=monster_id, name=name, grades=grades)


def load_target(
    conn: sqlite3.Connection, monster_name: str, *, grade: int | None = None
) -> Target:
    """Cible de combat construite depuis un vrai monstre du jeu.

    Remplace la saisie manuelle de résistances : « optimise contre le Bouftou »
    devient exprimable tel quel.
    """
    row = conn.execute(
        "SELECT monster_id FROM monster WHERE name = ? COLLATE NOCASE", (monster_name,)
    ).fetchone()
    if row is None:
        near = [m.name for m in find_monsters(conn, monster_name, limit=5)]
        raise UnknownMonsterError(monster_name, near)

    monster = load_monster(conn, row[0])
    monster_grade = monster.at_grade(grade)
    if monster_grade is None:
        raise UnknownMonsterError(f"{monster_name} (grade {grade})", [])

    # Les monstres n'exposent pas de résistance fixe dans les données.
    return Target(res_pct=dict(monster_grade.res_pct), res_flat={})


def open_catalog(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(
            f"Base introuvable : {path}\nLancez d'abord `python -m dofus_opti.ingest.build`."
        )
    return sqlite3.connect(path)
