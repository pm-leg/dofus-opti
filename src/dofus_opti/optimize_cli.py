"""CLI d'optimisation de stuff.

    python -m dofus_opti.optimize_cli --breed Iop --level 175 --elements terre \
        --pa 12 --pm 5 --po 0
"""

from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

from .combat.catalog import load_target, missing_spells
from .combat.formula import CritPolicy, Target
from .export.dofusdb import (
    CHARACTERISTIC_KEYS,
    EXO_STAT_IDS,
    UNSUPPORTED_SLOTS,
    build_payload,
    build_url,
    publish_command,
    to_json,
)
from .model.items import SLOT_CAPACITY, Slot
from .model.stats import StatKey
from .optim.custom import CustomItemError, CustomItemSpec
from .optim.request import BuildRequest, StatBound
from .optim.statpoints import (
    ASSIGNABLE,
    base_characteristics,
    base_hit_points,
    points_available,
)
from .optim.solver import BuildSolution, optimize

DEFAULT_DB = Path(__file__).resolve().parents[2] / "data" / "dofus.db"

#: contraintes exposées en ligne de commande, sous leur nom courant.
BOUNDABLE = {
    # ressources de tour
    "pa": StatKey.PA, "pm": StatKey.PM, "po": StatKey.PO,
    # caractéristiques
    "vitalite": StatKey.VITALITE, "sagesse": StatKey.SAGESSE,
    "force": StatKey.FORCE, "intelligence": StatKey.INTELLIGENCE,
    "chance": StatKey.CHANCE, "agilite": StatKey.AGILITE,
    # offensif
    "puissance": StatKey.PUISSANCE, "dommages": StatKey.DOMMAGES,
    "critique": StatKey.CRITIQUE_PCT,
    "dommages-critiques": StatKey.DOMMAGES_CRITIQUES,
    "soins": StatKey.SOINS, "invocations": StatKey.INVOCATIONS,
    # jeu de positionnement — c'est là que vivent les builds « retrait »
    "retrait-pa": StatKey.RETRAIT_PA, "retrait-pm": StatKey.RETRAIT_PM,
    "esquive-pa": StatKey.ESQUIVE_PA, "esquive-pm": StatKey.ESQUIVE_PM,
    "tacle": StatKey.TACLE, "fuite": StatKey.FUITE,
    "initiative": StatKey.INITIATIVE, "prospection": StatKey.PROSPECTION,
    # défensif
    "res-terre": StatKey.RES_PCT_TERRE, "res-feu": StatKey.RES_PCT_FEU,
    "res-eau": StatKey.RES_PCT_EAU, "res-air": StatKey.RES_PCT_AIR,
    "res-neutre": StatKey.RES_PCT_NEUTRE,
    "res-critiques": StatKey.RES_CRITIQUES, "res-poussee": StatKey.RES_POUSSEE,
}


def _print_sets(conn: sqlite3.Connection, solution: BuildSolution) -> None:
    """Panoplies actives, avec le bonus effectivement obtenu."""
    counts: dict[int, int] = {}
    for item in solution.items:
        if item.set_id is not None:
            counts[item.set_id] = counts.get(item.set_id, 0) + 1

    active = {set_id: n for set_id, n in counts.items() if n >= 2}
    if not active:
        return

    # Un bonus par item au-delà du premier : 3 items d'une panoplie = 2 bonus.
    total_bonuses = sum(worn - 1 for worn in active.values())
    print(f"\n  Panoplies actives ({total_bonuses} bonus de panoplie au total) :")
    for set_id, worn in sorted(active.items(), key=lambda kv: -kv[1]):
        row = conn.execute(
            "SELECT name, n_items FROM item_set WHERE ankama_id = ?", (set_id,)
        ).fetchone()
        if row is None:
            continue
        bonus = conn.execute(
            """SELECT stat, max FROM set_bonus
               WHERE set_id = ? AND item_count = ? ORDER BY max DESC LIMIT 6""",
            (set_id, worn),
        ).fetchall()
        detail = ", ".join(f"{stat} {value}" for stat, value in bonus)
        print(f"    {row[0]} — {worn}/{row[1]} items"
              + (f"  →  {detail}" if detail else "  →  aucun bonus à ce palier"))


def _export_dofusdb(conn, solution, args, allocations, custom_specs, base_hp=0,
                    extra_exos=None) -> None:
    """Écrit le build au format DofusDB, sans rien publier."""
    row = conn.execute(
        "SELECT breed_id FROM breed WHERE name = ? COLLATE NOCASE", (args.breed,)
    ).fetchone()
    if row is None:
        print(f"\n  Export impossible : classe « {args.breed} » inconnue.")
        return

    invested = {a.stat: a.invested for a in allocations}
    scrolls = (
        {stat: args.scrolls for stat in CHARACTERISTIC_KEYS} if args.scrolls else {}
    )
    # Les exotiques déclarés item par item deviennent des bonus de build : c'est
    # ainsi que DofusDB les représente. On ne déclare que ceux dont l'item porteur
    # est effectivement retenu — sinon le build afficherait un bonus fantôme.
    equipped = {item.name for item in solution.items}
    exos: dict[StatKey, int] = dict(extra_exos or {})
    for spec in custom_specs:
        if f"{spec.base_name} (perso)" not in equipped:
            continue
        for stat, (mode, value) in spec.overrides.items():
            if mode == "delta" and value:
                exos[stat] = exos.get(stat, 0) + value

    payload, report = build_payload(
        solution.items,
        name=args.build_name,
        level=args.level,
        breed_id=row[0],
        invested=invested,
        scrolls=scrolls,
        exos=exos,
        shared=args.share,
    )

    args.export_dofusdb.parent.mkdir(parents=True, exist_ok=True)
    args.export_dofusdb.write_text(to_json(payload), encoding="utf-8")

    print(f"\n  Export DofusDB écrit dans {args.export_dofusdb}")
    for warning in report.warnings:
        print(f"    ⚠ {warning}")

    # Ce que le lien affichera réellement : les emplacements que DofusDB ne
    # modélise pas retranchent silencieusement leur contribution.
    missing = [i for i in solution.items if i.slot in UNSUPPORTED_SLOTS]
    if missing:
        print("\n  Écart entre le jeu et le lien, dû aux emplacements absents :")
        for key, label in (
            (StatKey.PA, "PA"), (StatKey.PM, "PM"), (StatKey.VITALITE, "Vitalité"),
        ):
            lost = sum(i.stat(key, roll=args.roll) for i in missing)
            if not lost:
                continue
            total = solution.totals.get(key, 0)
            suffix = ""
            if key is StatKey.VITALITE:
                suffix = f"   (soit {base_hp + total} PV en jeu, {base_hp + total - lost} sur le lien)"
            print(f"    {label:<9} en jeu {total}  →  sur le lien {total - lost}{suffix}")
        print("    Pour un lien fidèle, relancez avec --exclude-slot monture.")
    if payload["shared"] == "private":
        print("\n  Visibilité « private » : DofusDB l'accepte uniquement depuis un")
        print("  compte authentifié. Pour un envoi anonyme, ajoutez --share public.")
    print("\n  Ce fichier n'a pas été publié. Pour créer le lien vous-même :")
    print(f"    {publish_command(str(args.export_dofusdb))}")
    print("  La réponse contient un champ « _id » ; le lien est alors")
    print(f"    {build_url('<_id>')}")


def _print_solution(
    solution: BuildSolution, request: BuildRequest, elapsed: float, base_hp: int = 0
) -> None:
    pool = solution.pool
    print(f"\n  Pool : {pool.loaded} items ≤ niveau {request.level} → "
          f"{pool.after_obtainable} obtenables → {pool.kept} candidats "
          f"({pool.dominated} dominés écartés)")
    for note in pool.custom_notes:
        print(f"  Item perso : {note}")
    if pool.prysmaradites_removed:
        print(f"  {pool.prysmaradites_removed} prysmaradites écartées "
              "(contreparties non modélisées) — --allow-prysmaradites pour les garder")
    if pool.unobtainable_removed:
        examples = ", ".join(pool.unobtainable_examples)
        print(f"  {pool.unobtainable_removed} items sans butin ni recette écartés "
              f"({examples}…)")
    for note in solution.notes:
        print(f"  {note}")

    if not solution.solved:
        print(f"\n  Aucune solution ({solution.status}).")
        print("  Les contraintes sont probablement incompatibles entre elles.")
        return

    print(f"\n  Résolu en {elapsed:.1f} s, {solution.iterations} itération(s), "
          f"statut {solution.status}\n")

    by_slot: dict[Slot, list] = {}
    for item in solution.items:
        by_slot.setdefault(item.slot, []).append(item)

    for slot in SLOT_CAPACITY:
        chosen = by_slot.get(slot, [])
        for item in chosen:
            marker = "  " if item.set_id is None else " ◆"
            print(f"    {slot.value:<10}{marker} {item.name}  (niv.{item.level})")
        if not chosen:
            print(f"    {slot.value:<10}   —")

    print("\n  Caractéristiques du build :")
    interesting = [
        StatKey.PA, StatKey.PM, StatKey.PO, StatKey.FORCE, StatKey.INTELLIGENCE,
        StatKey.CHANCE, StatKey.AGILITE, StatKey.PUISSANCE, StatKey.DOMMAGES,
        StatKey.CRITIQUE_PCT, StatKey.DOMMAGES_CRITIQUES, StatKey.VITALITE,
    ]
    line = []
    for key in interesting:
        value = solution.totals.get(key)
        if value:
            line.append(f"{key.value} {value}")
    print("    " + "   ".join(line))

    for key, bound in sorted(request.bounds.items()):
        actual = solution.totals.get(key, 0)
        ok = ((bound.minimum is None or actual >= bound.minimum)
              and (bound.maximum is None or actual <= bound.maximum))
        print(f"    contrainte {bound.describe(key):<28} → {actual}  {'✓' if ok else '✗'}")

    if base_hp:
        vitality = solution.totals.get(StatKey.VITALITE, 0)
        print(f"    points de vie totaux         → {base_hp + vitality}"
              f"  ({base_hp} de base niveau {request.level} + {vitality} de Vitalité)")

    print(f"\n  Rotation : {solution.rotation.describe()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Optimise un stuff sous contraintes.")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--breed", required=True, help="classe, ex. Iop")
    parser.add_argument("--level", type=int, default=200)
    parser.add_argument("--elements", nargs="+", required=True,
                        help="terre feu eau air neutre")
    parser.add_argument("--target", help="monstre servant de cible")
    parser.add_argument("--crit-policy", default="expected",
                        choices=["never", "expected", "always"])
    parser.add_argument("--roll", default="max", choices=["max", "avg", "min"])
    parser.add_argument("--time-limit", type=float, default=30.0)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--exclude-slot", nargs="*", default=[],
                        help="emplacements laissés vides, ex. familier monture")
    parser.add_argument(
        "--export-dofusdb", type=Path, metavar="FICHIER",
        help="écrit le build au format DofusDB. Le fichier n'est pas publié : "
             "la commande d'envoi est affichée pour que vous décidiez.",
    )
    parser.add_argument("--build-name", default="Build optimisé",
                        help="nom du build dans l'export")
    parser.add_argument(
        "--share", default="private", choices=["private", "public"],
        help="visibilité du build. DofusDB refuse « private » sans compte "
             "authentifié : un envoi anonyme doit être public.",
    )
    parser.add_argument(
        "--min-hp", type=int, metavar="N",
        help="points de vie totaux minimum, PV de base du niveau compris "
             "(à ne pas confondre avec --min-vitalite, qui ne porte que sur la stat)",
    )
    parser.add_argument(
        "--base-hp", type=int, metavar="N",
        help="force les PV de base du niveau si la valeur calculée est fausse",
    )
    parser.add_argument(
        "--max-set-bonuses", type=int, metavar="N",
        help="plafonne le nombre de panoplies actives. Les conditions propres aux "
             "items (« bonus de panoplies < 3 ») sont déjà appliquées d'office.",
    )
    parser.add_argument(
        "--charges", default="max", choices=["max", "none"],
        help="sorts à charges (Os à Moelle, Torrent Arcanique…) : « max » les "
             "évalue au cumul maximal, « none » à leur base nue",
    )
    parser.add_argument(
        "--spells", nargs="+", metavar="NOM",
        help="restreint la rotation à ces sorts, pour optimiser sur l'un d'eux",
    )
    parser.add_argument(
        "--spell-base", nargs="+", default=[], metavar="NOM=MIN-MAX",
        help="remplace la base d'un sort par élément, pour ceux dont les dégâts "
             "sont calculés par script (ex. « Torrent Arcanique=55-60 »)",
    )
    parser.add_argument(
        "--stats", nargs="+", metavar="CARAC",
        help="caractéristiques où répartir les points de niveau. Plusieurs valeurs "
             "les équilibrent, ex. « --stats force intelligence chance agilite ».",
    )
    parser.add_argument(
        "--scrolls", type=int, nargs="?", const=100, metavar="N",
        help="caractéristiques parcheminées à N (100 par défaut si l'option est "
             "donnée sans valeur)",
    )
    parser.add_argument(
        "--dofus", nargs="*", metavar="NOM",
        help="Dofus que vous possédez. Restreint les seuls Dofus : trophées et "
             "prysmaradites restent disponibles. Omis, tous sont autorisés.",
    )
    parser.add_argument(
        "--custom", nargs="*", default=[], metavar="SPEC",
        help="item forgemagé, sous la forme « Nom:stat=+1,stat=valeur » "
             "(ex. « Gelano:pm=+1 »). Le modèle du catalogue est remplacé.",
    )
    parser.add_argument("--allow-item", nargs="*", default=[], metavar="NOM",
                        help="réintègre un item écarté par le filtre d'obtention")
    parser.add_argument(
        "--exo", nargs="*", default=[], metavar="CARAC",
        help="exotiques dont vous disposez, ex. « --exo pa pm ». Chacun ajoute "
             "+1 ; « pa=2 » pour une autre valeur. Compte dans les plafonds.",
    )
    parser.add_argument(
        "--allow-prysmaradites", action="store_true",
        help="réintègre les prysmaradites. Écartées par défaut : leurs "
             "contreparties sont en texte libre, donc invisibles du solveur.",
    )
    parser.add_argument("--allow-unobtainable", action="store_true",
                        help="conserve les items sans butin ni recette (objets MJ, "
                             "serveurs évènementiels)")

    for name in BOUNDABLE:
        parser.add_argument(f"--{name}", type=int, help=f"valeur exacte de {name}")
        parser.add_argument(f"--min-{name}", type=int, help=f"minimum de {name}")
        parser.add_argument(f"--max-{name}", type=int, help=f"maximum de {name}")

    args = parser.parse_args(argv)

    bounds: dict[StatKey, StatBound] = {}
    for name, key in BOUNDABLE.items():
        attribute = name.replace("-", "_")
        exact = getattr(args, attribute, None)
        low = getattr(args, f"min_{attribute}", None)
        high = getattr(args, f"max_{attribute}", None)
        if exact is not None:
            bounds[key] = StatBound.exactly(exact)
        elif low is not None or high is not None:
            bounds[key] = StatBound(minimum=low, maximum=high)

    # Les PV totaux valent les PV de base du niveau plus la Vitalité : on
    # convertit donc l'exigence en plancher de Vitalité.
    base_hp = args.base_hp if args.base_hp is not None else base_hit_points(args.level)
    if args.min_hp is not None:
        needed = max(0, args.min_hp - base_hp)
        existing = bounds.get(StatKey.VITALITE)
        floor = max(needed, existing.minimum or 0) if existing else needed
        bounds[StatKey.VITALITE] = StatBound(
            minimum=floor, maximum=existing.maximum if existing else None
        )

    if not args.db.exists():
        raise SystemExit(
            f"Base introuvable : {args.db}\n"
            "Lancez d'abord `python -m dofus_opti.ingest.build`."
        )
    conn = sqlite3.connect(args.db)
    try:
        target = load_target(conn, args.target) if args.target else Target()

        allowed_dofus = None
        if args.dofus is not None:
            allowed_dofus = set()
            for name in args.dofus:
                row = conn.execute(
                    "SELECT ankama_id, type_name FROM item WHERE name = ? COLLATE NOCASE",
                    (name,),
                ).fetchone()
                if row is None:
                    raise SystemExit(f"Dofus « {name} » introuvable dans le catalogue.")
                if row[1] != "Dofus":
                    raise SystemExit(
                        f"« {name} » est un {row[1]}, pas un Dofus. "
                        "Trophées et prysmaradites sont déjà disponibles librement."
                    )
                allowed_dofus.add(row[0])

        try:
            custom_specs = [CustomItemSpec.parse(spec) for spec in args.custom]
        except CustomItemError as error:
            raise SystemExit(str(error)) from None

        exos: dict[StatKey, int] = {}
        for entry in args.exo:
            name, _, raw = entry.partition("=")
            try:
                key = StatKey(name.strip())
            except ValueError:
                raise SystemExit(
                    f"« {name} » n'est pas une caractéristique exotable. "
                    f"Possible : {', '.join(k.value for k in EXO_STAT_IDS)}"
                ) from None
            if key not in EXO_STAT_IDS:
                raise SystemExit(
                    f"exotique sur « {key.value} » non pris en charge. "
                    f"Possible : {', '.join(k.value for k in EXO_STAT_IDS)}"
                )
            exos[key] = int(raw) if raw else 1

        spell_bases: dict[str, tuple[int, int]] = {}
        for entry in args.spell_base:
            name, _, span = entry.partition("=")
            lo, _, hi = span.partition("-")
            try:
                spell_bases[name.strip()] = (int(lo), int(hi or lo))
            except ValueError:
                raise SystemExit(
                    f"« {entry} » : format attendu « Nom=min-max », ex. « Traversée=30-33 »"
                ) from None

        spell_names = set(args.spells or [])
        if spell_names:
            unknown = missing_spells(conn, args.breed, spell_names)
            if unknown:
                raise SystemExit(
                    f"sort(s) inconnu(s) chez {args.breed} : {', '.join(sorted(unknown))}"
                )

        invest: list[StatKey] = []
        for name in (args.stats or []):
            try:
                invest.append(StatKey(name))
            except ValueError:
                raise SystemExit(
                    f"« {name} » n'est pas une caractéristique. "
                    f"Possible : {', '.join(k.value for k in ASSIGNABLE)}"
                ) from None
        try:
            base_chars, allocations = base_characteristics(
                conn, args.breed, args.level, invest=invest or None, scrolled=args.scrolls
            )
        except (LookupError, ValueError) as error:
            raise SystemExit(str(error)) from None

        request = BuildRequest(
            level=args.level,
            breed=args.breed,
            elements=set(args.elements),
            bounds=bounds,
            target=target,
            crit_policy=CritPolicy(args.crit_policy),
            roll=args.roll,
            excluded_slots={Slot(s) for s in args.exclude_slot},
            require_obtainable=not args.allow_unobtainable,
            allowed_items=set(args.allow_item),
            allow_prysmaradites=args.allow_prysmaradites,
            exos=exos,
            allowed_dofus=allowed_dofus,
            custom_specs=custom_specs,
            base_characteristics=base_chars,
            max_set_bonuses=args.max_set_bonuses,
            spell_names=spell_names,
            spell_bases=spell_bases,
            charge_policy=args.charges,
        )

        print(f"  {args.breed} niveau {args.level}, élément "
              f"{'/'.join(sorted(request.elements))}"
              + (f", contre {args.target}" if args.target else ""))
        if request.bounds:
            print("  Contraintes : " + " · ".join(request.describe_constraints()))
        if args.scrolls:
            print(f"  Parchemins : {args.scrolls} dans les six caractéristiques")
        if allocations:
            print(f"  Points de niveau ({points_available(args.level)}) répartis :")
            for entry in allocations:
                print(f"    {entry.describe()}")

        started = time.monotonic()
        solution = optimize(
            conn, request, max_iterations=args.iterations, time_limit=args.time_limit
        )
        _print_solution(solution, request, time.monotonic() - started, base_hp=base_hp)
        if solution.solved:
            _print_sets(conn, solution)
            if args.export_dofusdb:
                _export_dofusdb(
                    conn, solution, args, allocations, custom_specs,
                    base_hp=base_hp, extra_exos=exos,
                )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

