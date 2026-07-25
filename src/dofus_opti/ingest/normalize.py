"""Conversion des données brutes en modèle de domaine, avec validation stricte.

Principe : on collecte *tous* les problèmes du catalogue, puis on échoue une
seule fois avec un rapport complet. Découvrir les 40 nouveaux effets d'une mise à
jour un par un serait pénible.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.items import (
    Item,
    ItemSet,
    Slot,
    SpellModifier,
    StatRange,
    WeaponHit,
)
from ..model.stats import StatKey
from .conditions import UnknownConditionElementError, parse_condition
from .effects import (
    BOUND_TO_CHARACTER_EFFECT,
    EFFECT_MAP,
    EffectKind,
    UnknownEffectError,
)
from .slots import EXCLUDED_TYPES, TYPE_TO_SLOT, UnknownItemTypeError


@dataclass
class IngestReport:
    """Compte-rendu d'une normalisation."""

    items_in: int = 0
    items_kept: int = 0
    items_excluded: int = 0
    sets_in: int = 0
    sets_kept: int = 0
    unknown_effects: dict[int, str] = field(default_factory=dict)
    unknown_types: dict[int, str] = field(default_factory=dict)
    unknown_condition_elements: dict[int, str] = field(default_factory=dict)
    excluded_by_type: dict[str, int] = field(default_factory=dict)
    items_by_slot: dict[str, int] = field(default_factory=dict)
    effect_kind_counts: dict[str, int] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        return not (
            self.unknown_effects or self.unknown_types or self.unknown_condition_elements
        )

    def raise_if_dirty(self) -> None:
        if self.unknown_types:
            raise UnknownItemTypeError(self.unknown_types)
        if self.unknown_effects:
            raise UnknownEffectError(self.unknown_effects)
        if self.unknown_condition_elements:
            raise UnknownConditionElementError(self.unknown_condition_elements)


def _value_range(effect: dict) -> tuple[int, int]:
    """Fourchette d'un effet.

    `ignore_int_max` signale une valeur unique portée par `int_minimum`
    (`int_maximum` vaut alors 0, ce qui donnerait un max < min si on le prenait
    au pied de la lettre). Symétriquement pour `ignore_int_min`.
    """
    lo = int(effect.get("int_minimum") or 0)
    hi = int(effect.get("int_maximum") or 0)
    if effect.get("ignore_int_max"):
        return lo, lo
    if effect.get("ignore_int_min"):
        return hi, hi
    if hi < lo:
        # Filet de sécurité : certaines entrées ont un max nul sans le signaler.
        return lo, lo
    return lo, hi


def _accumulate(stats: dict[StatKey, StatRange], key: StatKey, lo: int, hi: int) -> None:
    """Additionne une stat déjà présente.

    Nécessaire parce qu'une même caractéristique peut arriver sous plusieurs ids
    sur un même item (typiquement PA en bonus et PA en malus).
    """
    prev = stats.get(key)
    if prev is None:
        stats[key] = StatRange(lo, hi)
    else:
        stats[key] = StatRange(prev.minimum + lo, prev.maximum + hi)


def _parse_effects(
    raw_effects: list[dict] | None, report: IngestReport
) -> tuple[dict[StatKey, StatRange], list[WeaponHit], list[SpellModifier], list[str]]:
    stats: dict[StatKey, StatRange] = {}
    hits: list[WeaponHit] = []
    spell_mods: list[SpellModifier] = []
    specials: list[str] = []

    for effect in raw_effects or []:
        etype = effect.get("type") or {}
        eid = etype.get("id")
        if eid is None:
            continue
        mapping = EFFECT_MAP.get(eid)
        if mapping is None:
            report.unknown_effects[eid] = etype.get("name", "?")
            continue

        report.effect_kind_counts[mapping.kind.value] = (
            report.effect_kind_counts.get(mapping.kind.value, 0) + 1
        )
        lo, hi = _value_range(effect)

        if mapping.kind is EffectKind.STAT:
            assert mapping.stat is not None
            _accumulate(stats, mapping.stat, lo, hi)
        elif mapping.kind is EffectKind.WEAPON_HIT:
            assert mapping.hit_kind and mapping.element
            hits.append(WeaponHit(mapping.hit_kind, mapping.element, lo, hi))
        elif mapping.kind is EffectKind.SPELL_MODIFIER:
            spell_mods.append(SpellModifier(raw=effect.get("formatted") or "", effect_id=eid))
        elif mapping.kind is EffectKind.SPECIAL:
            specials.append(effect.get("formatted") or "")
        # EffectKind.IGNORED : rien à faire, l'omission est délibérée.

    return stats, hits, spell_mods, specials


def _parse_range(raw) -> tuple[int | None, int | None]:
    """La portée d'une arme peut arriver sous forme d'entier ou d'objet min/max."""
    if raw is None:
        return None, None
    if isinstance(raw, dict):
        lo, hi = raw.get("min"), raw.get("max")
        return (int(lo) if lo is not None else None, int(hi) if hi is not None else None)
    if isinstance(raw, (int, float)):
        return int(raw), int(raw)
    return None, None


def normalize_items(raw_items: list[dict], report: IngestReport) -> list[Item]:
    items: list[Item] = []
    report.items_in = len(raw_items)

    for raw in raw_items:
        rtype = raw.get("type") or {}
        type_id = rtype.get("id")
        type_name = rtype.get("name", "?")

        if type_id in EXCLUDED_TYPES:
            report.items_excluded += 1
            report.excluded_by_type[type_name] = report.excluded_by_type.get(type_name, 0) + 1
            continue

        slot = TYPE_TO_SLOT.get(type_id)
        if slot is None:
            report.unknown_types[type_id] = type_name
            continue

        stats, hits, spell_mods, specials = _parse_effects(raw.get("effects"), report)
        condition = parse_condition(raw.get("conditions"), report.unknown_condition_elements)
        range_min, range_max = _parse_range(raw.get("range"))
        parent_set = raw.get("parent_set") or {}

        items.append(
            Item(
                ankama_id=int(raw["ankama_id"]),
                name=raw.get("name", "?"),
                slot=slot,
                type_id=int(type_id),
                type_name=type_name,
                level=int(raw.get("level") or 0),
                is_weapon=bool(raw.get("is_weapon")),
                pods=int(raw.get("pods") or 0),
                stats=stats,
                set_id=int(parent_set["id"]) if parent_set.get("id") is not None else None,
                condition=condition,
                bound_to_character=any(
                    (e.get("type") or {}).get("id") == BOUND_TO_CHARACTER_EFFECT
                    for e in raw.get("effects") or []
                ),
                weapon_hits=tuple(hits),
                ap_cost=raw.get("ap_cost"),
                crit_probability=raw.get("critical_hit_probability"),
                crit_bonus=raw.get("critical_hit_bonus"),
                max_cast_per_turn=raw.get("max_cast_per_turn"),
                range_min=range_min,
                range_max=range_max,
                spell_modifiers=tuple(spell_mods),
                special_effects=tuple(specials),
            )
        )
        report.items_by_slot[slot.value] = report.items_by_slot.get(slot.value, 0) + 1

    report.items_kept = len(items)
    return items


def normalize_sets(raw_sets: list[dict], report: IngestReport) -> list[ItemSet]:
    """Les bonus de panoplie arrivent sous forme `{ "2": [...], "3": [...] }`."""
    out: list[ItemSet] = []
    report.sets_in = len(raw_sets)

    for raw in raw_sets:
        bonuses: dict[int, dict[StatKey, StatRange]] = {}
        raw_bonuses: dict[int, tuple[str, ...]] = {}

        for count_key, effects in (raw.get("effects") or {}).items():
            if not effects:
                continue
            n = int(count_key)
            stats, _hits, spell_mods, specials = _parse_effects(effects, report)
            if stats:
                bonuses[n] = stats
            extra = tuple(m.raw for m in spell_mods) + tuple(specials)
            if extra:
                raw_bonuses[n] = extra

        out.append(
            ItemSet(
                ankama_id=int(raw["ankama_id"]),
                name=raw.get("name", "?"),
                level=int(raw.get("level") or 0),
                n_items=int(raw.get("items") or 0),
                bonuses=bonuses,
                raw_bonuses=raw_bonuses,
            )
        )

    report.sets_kept = len(out)
    return out
