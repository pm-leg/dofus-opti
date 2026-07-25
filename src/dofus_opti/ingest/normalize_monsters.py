"""Conversion des monstres bruts DofusDB en cibles exploitables."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.monsters import Monster, MonsterGrade

#: champ DofusDB → élément. Explicite, comme partout ailleurs.
RESISTANCE_FIELDS: dict[str, str] = {
    "neutralResistance": "neutre",
    "earthResistance": "terre",
    "fireResistance": "feu",
    "waterResistance": "eau",
    "airResistance": "air",
}


@dataclass
class MonsterIngestReport:
    monsters_in: int = 0
    monsters_kept: int = 0
    grades_kept: int = 0
    without_grades: int = 0
    #: monstres portant au moins une résistance négative (vulnérabilité)
    with_vulnerability: int = 0
    missing_resistance_fields: dict[str, int] = field(default_factory=dict)


class MissingResistanceFieldError(RuntimeError):
    def __init__(self, missing: dict[str, int]) -> None:
        super().__init__(
            "Champs de résistance absents des données monstres :\n  "
            + "\n  ".join(f"{field} (manquant sur {n} grades)" for field, n in missing.items())
            + "\nVérifiez RESISTANCE_FIELDS (src/dofus_opti/ingest/normalize_monsters.py)."
        )
        self.missing = missing


def _localized(value, lang: str = "fr") -> str:
    if isinstance(value, dict):
        return value.get(lang) or value.get("en") or ""
    return str(value or "")


def normalize_monsters(
    raw_monsters: list[dict], report: MonsterIngestReport
) -> list[Monster]:
    report.monsters_in = len(raw_monsters)
    out: list[Monster] = []

    for raw in raw_monsters:
        monster_id = raw.get("id")
        name = _localized(raw.get("name"))
        if monster_id is None or not name:
            continue

        grades: list[MonsterGrade] = []
        for raw_grade in raw.get("grades") or []:
            resistances: dict[str, int] = {}
            for source_field, element in RESISTANCE_FIELDS.items():
                if source_field not in raw_grade:
                    report.missing_resistance_fields[source_field] = (
                        report.missing_resistance_fields.get(source_field, 0) + 1
                    )
                    continue
                resistances[element] = int(raw_grade.get(source_field) or 0)

            grades.append(
                MonsterGrade(
                    grade=int(raw_grade.get("grade") or 1),
                    level=int(raw_grade.get("level") or 0),
                    life_points=int(raw_grade.get("lifePoints") or 0),
                    action_points=int(raw_grade.get("actionPoints") or 0),
                    movement_points=int(raw_grade.get("movementPoints") or 0),
                    res_pct=resistances,
                )
            )

        if not grades:
            report.without_grades += 1
            continue

        out.append(Monster(monster_id=int(monster_id), name=name, grades=tuple(grades)))
        report.grades_kept += len(grades)
        if any(v < 0 for g in grades for v in g.res_pct.values()):
            report.with_vulnerability += 1

    report.monsters_kept = len(out)
    return out


def raise_if_fields_missing(report: MonsterIngestReport) -> None:
    """Un champ de résistance disparu fausserait toutes les cibles en silence."""
    if report.missing_resistance_fields:
        raise MissingResistanceFieldError(report.missing_resistance_fields)
