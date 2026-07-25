"""Point d'entrée de l'ingestion : `python -m dofus_opti.ingest.build`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .db import write_database
from .dofusdb import DofusDbSource
from .normalize import IngestReport, normalize_items, normalize_sets
from .normalize_monsters import (
    MonsterIngestReport,
    normalize_monsters,
    raise_if_fields_missing,
)
from .normalize_spells import (
    SpellIngestReport,
    map_spells_to_breeds,
    normalize_breeds,
    normalize_levels,
    normalize_spells,
)
from .source import DofusDudeSource
from .spell_effects import verify_against_source

DEFAULT_ROOT = Path(__file__).resolve().parents[3]


def _print_report(report: IngestReport, version: str) -> None:
    print(f"\n  version du jeu      : {version}")
    print(f"  items reçus         : {report.items_in}")
    print(f"  items conservés     : {report.items_kept}")
    print(f"  items exclus        : {report.items_excluded}")
    for name, n in sorted(report.excluded_by_type.items(), key=lambda kv: -kv[1]):
        print(f"      - {name:<32} {n}")
    print(f"  panoplies           : {report.sets_kept}")

    print("\n  items par emplacement :")
    for slot, n in sorted(report.items_by_slot.items(), key=lambda kv: -kv[1]):
        print(f"      {slot:<12} {n:>5}")

    print("\n  effets traités par catégorie :")
    for kind, n in sorted(report.effect_kind_counts.items(), key=lambda kv: -kv[1]):
        print(f"      {kind:<16} {n:>6}")


def _print_spell_report(report: SpellIngestReport, version: str) -> None:
    print(f"\n  --- sorts (DofusDB, version {version}) ---")
    print(f"  classes             : {report.breeds}")
    print(f"  sorts conservés     : {report.spells_kept} / {report.spells_in}")
    print(f"  dont offensifs      : {report.damaging_spells}")
    print(f"  paliers             : {report.levels_kept}")
    print(f"  jets directs        : {report.rolls}")
    print(f"  jets sur la durée   : {report.over_time_rolls}")
    print(f"  branches conditionnelles écartées : {report.conditional_branches}")

    if report.spells_by_breed:
        counts = sorted(report.spells_by_breed.items())
        line = "  ".join(f"{name} {n}" for name, n in counts)
        print(f"\n  par classe : {line}")


def _ingest_spells(source: DofusDbSource, report: SpellIngestReport):
    # Garde-fou : la table locale doit encore correspondre aux descriptions.
    verify_against_source(source.effects())

    breeds = normalize_breeds(source.breeds(), report)
    breed_by_spell = map_spells_to_breeds(source.spell_variants(), set(breeds))
    spell_ids = sorted(breed_by_spell)

    raw_spells = source.spells(spell_ids)
    levels = normalize_levels(source.spell_levels(spell_ids), report)
    return breeds, normalize_spells(raw_spells, levels, breed_by_spell, breeds, report)


def _ingest_sources(source: DofusDbSource, ankama_ids: list[int]) -> dict[int, tuple[int, bool]]:
    """Provenance de chaque item : combien de monstres le lâchent, a-t-il une recette.

    Un item sans butin ni recette n'est pas obtenable par un joueur — ce sont les
    objets d'administrateur et d'évènement. Sans ce filtre, le solveur les
    choisit systématiquement : ils écrasent tout le reste.
    """
    sources = {
        int(row["id"]): (len(row.get("dropMonsterIds") or []), bool(row.get("hasRecipe")))
        for row in source.item_sources(ankama_ids)
        if row.get("id") is not None
    }
    unobtainable = sum(1 for drops, recipe in sources.values() if not drops and not recipe)
    print("\n  --- provenance des items ---")
    print(f"  renseignés          : {len(sources)} / {len(ankama_ids)}")
    print(f"  sans butin ni recette : {unobtainable}")
    return sources


def _print_monster_report(report: MonsterIngestReport) -> None:
    print("\n  --- monstres (cibles d'optimisation) ---")
    print(f"  monstres conservés  : {report.monsters_kept} / {report.monsters_in}")
    print(f"  grades              : {report.grades_kept}")
    print(f"  sans grade (ignorés): {report.without_grades}")
    print(f"  avec vulnérabilité  : {report.with_vulnerability}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Construit le catalogue Dofus local.")
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_ROOT / "data" / "dofus.db",
        help="chemin de la base SQLite produite",
    )
    parser.add_argument(
        "--cache", type=Path, default=DEFAULT_ROOT / "data" / "cache",
        help="répertoire de cache des réponses brutes",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="ignore le cache et retélécharge depuis l'API",
    )
    parser.add_argument("--lang", default="fr")
    parser.add_argument(
        "--no-spells", action="store_true",
        help="n'ingère que les équipements (dofusdude), sans les sorts (DofusDB)",
    )
    args = parser.parse_args(argv)

    source = DofusDudeSource(args.cache, lang=args.lang, refresh=args.refresh)
    print(f"Source : {source.name} ({source.base})")

    raw_items = source.equipment()
    raw_sets = source.sets()
    version = source.game_version()

    report = IngestReport()
    items = normalize_items(raw_items, report)
    sets = normalize_sets(raw_sets, report)

    _print_report(report, version)

    if not report.is_clean:
        print("\nIngestion interrompue : données non reconnues.\n", file=sys.stderr)
        report.raise_if_dirty()

    breeds: dict = {}
    spells: list = []
    monsters: list = []
    sources: dict = {}
    base_boosts: list = []
    spell_version = "—"
    if not args.no_spells:
        ddb = DofusDbSource(args.cache, refresh=args.refresh)
        spell_version = ddb.game_version()

        spell_report = SpellIngestReport()
        breeds, spells = _ingest_spells(ddb, spell_report)
        base_boosts = spell_report.base_boosts
        _print_spell_report(spell_report, spell_version)
        print(f"  bonus « dégâts de base » (sorts à charges) : {len(base_boosts)}")

        monster_report = MonsterIngestReport()
        monsters = normalize_monsters(ddb.monsters(), monster_report)
        raise_if_fields_missing(monster_report)
        _print_monster_report(monster_report)

        sources = _ingest_sources(ddb, [i.ankama_id for i in items])

        if spell_report.unmapped_damage_effects:
            print(
                "\n  Effets élémentaires non classés (à examiner) : "
                + ", ".join(
                    f"{eid} (×{n})"
                    for eid, n in sorted(spell_report.unmapped_damage_effects.items())
                ),
                file=sys.stderr,
            )

        if version != "?" and spell_version not in ("?", version):
            print(
                f"\n  Attention : équipements en {version}, sorts en {spell_version}. "
                "Les deux sources ne sont pas alignées sur la même version de jeu.",
                file=sys.stderr,
            )

    write_database(
        args.out,
        items,
        sets,
        meta={
            "source": source.name,
            "game_version": version,
            "spell_source": DofusDbSource.name if spells else "—",
            "spell_game_version": spell_version,
            "lang": args.lang,
        },
        breeds=breeds,
        spells=spells,
        monsters=monsters,
        sources=sources,
        base_boosts=base_boosts,
    )
    size_mb = args.out.stat().st_size / 1e6
    print(f"\nBase écrite : {args.out}  ({size_mb:.1f} Mo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
