"""Exploration du catalogue en ligne de commande.

Sert à vérifier l'ingestion et à se faire une idée du volume de candidats par
emplacement avant d'écrire le solveur.

    python -m dofus_opti.explore stats
    python -m dofus_opti.explore top --slot chapeau --level 175 --stat force
    python -m dofus_opti.explore item "Casque Keutumedi"
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "dofus.db"


def _connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(
            f"Base introuvable : {path}\nLancez d'abord `python -m dofus_opti.ingest.build`."
        )
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def cmd_stats(conn: sqlite3.Connection, _args) -> None:
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    print(f"source={meta.get('source')}  version={meta.get('game_version')}  "
          f"construite={meta.get('built_at')}\n")

    for row in conn.execute(
        """SELECT slot, COUNT(*) n, MAX(level) lvl_max
           FROM item GROUP BY slot ORDER BY n DESC"""
    ):
        print(f"  {row['slot']:<12} {row['n']:>5} items   (niveau max {row['lvl_max']})")

    total = conn.execute("SELECT COUNT(*) FROM item").fetchone()[0]
    sets = conn.execute("SELECT COUNT(*) FROM item_set").fetchone()[0]
    conds = conn.execute("SELECT COUNT(*) FROM item WHERE condition_json IS NOT NULL").fetchone()[0]
    mods = conn.execute("SELECT COUNT(DISTINCT item_id) FROM item_spell_modifier").fetchone()[0]
    print(f"\n  total {total} items, {sets} panoplies")
    print(f"  {conds} items à condition d'équipement")
    print(f"  {mods} items porteurs de modificateurs de sorts")

    print("\n  stats les plus fréquentes :")
    for row in conn.execute(
        "SELECT stat, COUNT(*) n FROM item_stat GROUP BY stat ORDER BY n DESC LIMIT 12"
    ):
        print(f"      {row['stat']:<24} {row['n']:>5}")


def cmd_top(conn: sqlite3.Connection, args) -> None:
    rows = conn.execute(
        """SELECT i.name, i.level, s.max AS value, i.condition_text,
                  (SELECT name FROM item_set WHERE ankama_id = i.set_id) AS set_name
           FROM item i
           JOIN item_stat s ON s.item_id = i.ankama_id
           WHERE i.slot = ? AND i.level <= ? AND s.stat = ?
           ORDER BY s.max DESC
           LIMIT ?""",
        (args.slot, args.level, args.stat, args.limit),
    ).fetchall()

    if not rows:
        print("aucun résultat")
        return

    print(f"  Meilleurs « {args.stat} » — emplacement {args.slot}, niveau ≤ {args.level}\n")
    for row in rows:
        line = f"  {row['value']:>5}  {row['name']:<34} niv.{row['level']:<4}"
        if row["set_name"]:
            line += f" [{row['set_name']}]"
        if row["condition_text"]:
            line += f"  — condition : {row['condition_text']}"
        print(line)


def cmd_item(conn: sqlite3.Connection, args) -> None:
    row = conn.execute(
        "SELECT * FROM item WHERE name = ? COLLATE NOCASE", (args.name,)
    ).fetchone()
    if row is None:
        print(f"« {args.name} » introuvable")
        return

    print(f"\n  {row['name']}  —  {row['type_name']}, niveau {row['level']}")
    if row["condition_text"]:
        print(f"  condition : {row['condition_text']}")
    if row["set_id"]:
        s = conn.execute(
            "SELECT name, n_items FROM item_set WHERE ankama_id = ?", (row["set_id"],)
        ).fetchone()
        print(f"  panoplie  : {s['name']} ({s['n_items']} items)")

    print("\n  caractéristiques :")
    for st in conn.execute(
        "SELECT stat, min, max FROM item_stat WHERE item_id = ? ORDER BY stat",
        (row["ankama_id"],),
    ):
        value = f"{st['min']}" if st["min"] == st["max"] else f"{st['min']} à {st['max']}"
        print(f"      {st['stat']:<24} {value}")

    hits = conn.execute(
        "SELECT kind, element, min, max FROM item_weapon_hit WHERE item_id = ?",
        (row["ankama_id"],),
    ).fetchall()
    if hits:
        print(f"\n  arme : {row['ap_cost']} PA, {row['crit_probability']}% CC "
              f"(+{row['crit_bonus']}), {row['max_cast_per_turn']} lancer(s)/tour")
        for h in hits:
            print(f"      {h['kind']:<8} {h['element']:<8} {h['min']} à {h['max']}")

    for label, table in (("modificateurs de sorts", "item_spell_modifier"),
                         ("effets spéciaux", "item_special_effect")):
        extra = conn.execute(
            f"SELECT raw FROM {table} WHERE item_id = ?", (row["ankama_id"],)
        ).fetchall()
        if extra:
            print(f"\n  {label} :")
            for e in extra:
                print(f"      {e['raw']}")


def cmd_spells(conn: sqlite3.Connection, args) -> None:
    from .combat.catalog import available_breeds, load_spells
    from .combat.formula import CritPolicy, Target
    from .combat.rotation import best_rotation
    from .combat.stats import StatVector

    if not conn.execute("SELECT COUNT(*) FROM breed").fetchone()[0]:
        print("aucun sort en base — relancez l'ingestion sans --no-spells")
        return

    if not args.breed:
        print("classes : " + ", ".join(available_breeds(conn)))
        return

    from .combat.catalog import load_target

    elements = set(args.elements) if args.elements else None
    spells = load_spells(conn, args.breed, args.level, elements=elements)
    if not spells:
        print(f"aucun sort offensif pour {args.breed} au niveau {args.level}")
        return

    print(f"\n  {args.breed}, niveau {args.level}"
          + (f", élément {'/'.join(sorted(elements))}" if elements else "")
          + f" — {len(spells)} sorts offensifs\n")
    for spell in sorted(spells, key=lambda s: s.ap_cost):
        rolls = " ".join(
            f"{r.base_min}-{r.base_max} {r.element}" for r in spell.rolls
        )
        crit = f"{spell.crit_probability}% CC" if spell.can_crit else "pas de CC"
        casts = spell.casts_allowed
        print(f"    {spell.ap_cost} PA  {spell.name:<26} {rolls:<28} "
              f"{crit:<10} {casts if casts < 99 else '∞'}×/tour")

    stats = StatVector().with_(
        pa=args.pa - 6, force=args.stat, intelligence=args.stat,
        chance=args.stat, agilite=args.stat,
        puissance=args.power, critique_pct=args.crit,
    )
    if args.target:
        target = load_target(conn, args.target)
        label = (f"contre {args.target} "
                 + " ".join(f"{e} {v:+d}%" for e, v in sorted(target.res_pct.items())))
    else:
        target = Target.unarmored()
        label = "sans résistances adverses"

    rotation = best_rotation(spells, stats, target=target,
                             crit_policy=CritPolicy(args.crit_policy))
    print(f"\n  Rotation optimale à {args.pa} PA "
          f"({args.stat} en caractéristique, {args.power} Puissance, {args.crit} Critique)")
    print(f"  {label} :")
    print(f"    {rotation.describe()}")


def cmd_monster(conn: sqlite3.Connection, args) -> None:
    from .combat.catalog import find_monsters

    monsters = find_monsters(conn, args.name, limit=args.limit)
    if not monsters:
        print(f"aucun monstre correspondant à « {args.name} »")
        return

    for monster in monsters:
        print(f"\n  {monster.name}")
        for grade in monster.grades:
            res = "  ".join(
                f"{element} {value:+d}%" for element, value in sorted(grade.res_pct.items())
            )
            weakest = grade.weakest_element()
            print(f"    grade {grade.grade}  niv.{grade.level:<4} {grade.life_points:>6} PV  "
                  f"{grade.action_points} PA {grade.movement_points} PM   {res}"
                  + (f"   → point faible : {weakest}" if weakest else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explore le catalogue Dofus local.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("stats", help="volumétrie du catalogue").set_defaults(func=cmd_stats)

    p_top = sub.add_parser("top", help="meilleurs items pour une stat")
    p_top.add_argument("--slot", required=True)
    p_top.add_argument("--level", type=int, default=200)
    p_top.add_argument("--stat", required=True)
    p_top.add_argument("--limit", type=int, default=15)
    p_top.set_defaults(func=cmd_top)

    p_spells = sub.add_parser("spells", help="sorts d'une classe et rotation optimale")
    p_spells.add_argument("--breed", help="nom de classe ; omis, liste les classes")
    p_spells.add_argument("--level", type=int, default=200)
    p_spells.add_argument("--elements", nargs="*", help="terre feu eau air neutre")
    p_spells.add_argument("--pa", type=int, default=12)
    p_spells.add_argument("--stat", type=int, default=600, help="caractéristique principale")
    p_spells.add_argument("--power", type=int, default=100, help="Puissance")
    p_spells.add_argument("--crit", type=int, default=40, help="Critique")
    p_spells.add_argument(
        "--crit-policy", default="expected", choices=["never", "expected", "always"]
    )
    p_spells.add_argument("--target", help="nom d'un monstre : ses résistances sont appliquées")
    p_spells.set_defaults(func=cmd_spells)

    p_monster = sub.add_parser("monster", help="résistances d'un monstre par grade")
    p_monster.add_argument("name", help="nom complet ou fragment")
    p_monster.add_argument("--limit", type=int, default=5)
    p_monster.set_defaults(func=cmd_monster)

    p_item = sub.add_parser("item", help="fiche détaillée d'un item")
    p_item.add_argument("name")
    p_item.set_defaults(func=cmd_item)

    args = parser.parse_args(argv)
    conn = _connect(args.db)
    try:
        args.func(conn, args)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
