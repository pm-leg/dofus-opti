"""Conversion des sorts bruts DofusDB en modèle de domaine."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..model.spells import Breed, ClassSpell, DamageRoll, SpellLevel
from .spell_effects import DAMAGE_EFFECTS, EXCLUDED_DAMAGE_EFFECTS, ELEMENT_BY_ID

#: « #1 : +#3 dégâts de base » — le mécanisme des sorts à charges.
BASE_BOOST_EFFECT = 293


@dataclass
class SpellIngestReport:
    breeds: int = 0
    spells_in: int = 0
    spells_kept: int = 0
    levels_kept: int = 0
    damaging_spells: int = 0
    rolls: int = 0
    over_time_rolls: int = 0
    #: branches conditionnelles écartées au profit de la meilleure (voir `_extract_damage`)
    conditional_branches: int = 0
    #: effets de dégâts rencontrés mais absents de la table — à examiner
    unmapped_damage_effects: dict[int, int] = field(default_factory=dict)
    spells_by_breed: dict[str, int] = field(default_factory=dict)
    #: classes dont le barème de points de niveau est incomplet
    breeds_without_stat_costs: list[str] = field(default_factory=list)
    #: bonus « +N dégâts de base » portés par les sorts compagnons (effet 293) :
    #: (sort cible, sort porteur, grade du porteur, valeur)
    base_boosts: list[tuple[int, int, int, int]] = field(default_factory=list)


def _localized(value, lang: str = "fr") -> str:
    if isinstance(value, dict):
        return value.get(lang) or value.get("en") or ""
    return str(value or "")


def _roll_bounds(effect: dict) -> tuple[int, int]:
    """Bornes d'un jet.

    Les effets de dégâts utilisent des dés : `diceNum` porte le minimum et
    `diceSide` le maximum. Un `diceSide` nul signale une valeur fixe.
    """
    minimum = int(effect.get("diceNum") or 0)
    maximum = int(effect.get("diceSide") or 0)
    if maximum <= 0:
        maximum = minimum
    if maximum < minimum:
        minimum, maximum = maximum, minimum
    return minimum, maximum


def _extract_damage(effects: list[dict] | None, report: SpellIngestReport) -> dict:
    """Indexe les effets de dégâts par (élément, durée).

    Le `targetMask` conditionne l'effet à l'état de la cible : `*E3531` signifie
    « la cible porte l'état 3531 », `*e3531` « elle ne le porte pas ». Deux effets
    de masques différents sont donc des **branches alternatives**, pas des coups
    cumulés — Souffle Alcoolisé inflige 28-32 ou 34-38 selon l'état, jamais 62-70.

    D'où la règle : on **somme au sein d'un même masque** (un sort qui frappe
    plusieurs fois dans les mêmes conditions), et on **retient la meilleure
    branche entre masques différents**. C'est optimiste — on suppose que le joueur
    met en place l'état favorable, ce qui est le cœur du jeu des classes
    concernées — mais jamais absurde.
    """
    # masque → (élément, durée) → fourchette
    buckets: dict[str, dict[tuple[str, bool], tuple[int, int]]] = {}

    for effect in effects or []:
        effect_id = effect.get("effectId")
        if effect_id in EXCLUDED_DAMAGE_EFFECTS:
            continue
        mapping = DAMAGE_EFFECTS.get(effect_id)
        if mapping is None:
            # La grande majorité des effets ne sont pas des dégâts (états, buffs,
            # invocations) : on ne les compte comme anomalie que s'ils portent un
            # élément, signe d'un effet élémentaire qu'on aurait manqué.
            element_id = effect.get("effectElement")
            if element_id in ELEMENT_BY_ID and (effect.get("duration") or 0) == 0:
                report.unmapped_damage_effects[effect_id] = (
                    report.unmapped_damage_effects.get(effect_id, 0) + 1
                )
            continue

        over_time = (effect.get("duration") or 0) > 0
        minimum, maximum = _roll_bounds(effect)
        if maximum <= 0:
            continue

        bucket = buckets.setdefault(effect.get("targetMask") or "", {})
        key = (mapping.element, over_time)
        previous = bucket.get(key)
        bucket[key] = (
            (minimum, maximum) if previous is None
            else (previous[0] + minimum, previous[1] + maximum)
        )

    merged: dict[tuple[str, bool], tuple[int, int]] = {}
    for bucket in buckets.values():
        for key, bounds in bucket.items():
            previous = merged.get(key)
            if previous is None or bounds[1] > previous[1]:
                merged[key] = bounds
            if previous is not None:
                report.conditional_branches += 1

    return merged


def _build_rolls(
    normal: dict, critical: dict, *, over_time: bool
) -> tuple[DamageRoll, ...]:
    rolls: list[DamageRoll] = []
    elements = {el for (el, ot) in normal if ot is over_time}
    elements |= {el for (el, ot) in critical if ot is over_time}

    for element in sorted(elements):
        base = normal.get((element, over_time))
        crit = critical.get((element, over_time))
        if base is None:
            # Effet présent uniquement en critique : hors critique, il n'existe pas.
            base = (0, 0)
        if crit is None:
            crit = base
        rolls.append(
            DamageRoll(
                element=element,
                base_min=base[0], base_max=base[1],
                crit_min=crit[0], crit_max=crit[1],
            )
        )
    return tuple(rolls)


def normalize_levels(
    raw_levels: list[dict], report: SpellIngestReport
) -> dict[int, list[SpellLevel]]:
    """Regroupe les paliers par `spellId`."""
    by_spell: dict[int, list[SpellLevel]] = {}

    for raw in raw_levels:
        spell_id = raw.get("spellId")
        if spell_id is None:
            continue

        normal = _extract_damage(raw.get("effects"), report)
        critical = _extract_damage(raw.get("criticalEffect"), report)

        # Effet 293 : « +N dégâts de base au sort #cible ». C'est ainsi que le
        # jeu encode les charges — Os à Moelle porte un sort compagnon qui
        # ajoute +4/+5/+6 par lancer, Torrent Arcanique +2 par combinaison.
        for effect in raw.get("effects") or []:
            if effect.get("effectId") == BASE_BOOST_EFFECT:
                target = int(effect.get("diceNum") or 0)
                value = int(effect.get("value") or 0)
                if target > 0 and value > 0:
                    report.base_boosts.append(
                        (target, int(spell_id), int(raw.get("grade") or 1), value)
                    )

        direct = _build_rolls(normal, critical, over_time=False)
        lasting = _build_rolls(normal, critical, over_time=True)

        level = SpellLevel(
            grade=int(raw.get("grade") or 1),
            max_stack=max(0, int(raw.get("maxStack") or 0)),
            ap_cost=int(raw.get("apCost") or 0),
            crit_probability=int(raw.get("criticalHitProbability") or 0),
            range_min=int(raw.get("minRange") or 0),
            range_max=int(raw.get("range") or 0),
            max_cast_per_turn=int(raw.get("maxCastPerTurn") or 0),
            max_cast_per_target=int(raw.get("maxCastPerTarget") or 0),
            min_player_level=int(raw.get("minPlayerLevel") or 1),
            rolls=direct,
            cast_in_line=bool(raw.get("castInLine")),
            needs_line_of_sight=bool(raw.get("castTestLos", True)),
            range_can_be_boosted=bool(raw.get("rangeCanBeBoosted")),
            over_time_rolls=lasting,
        )
        by_spell.setdefault(int(spell_id), []).append(level)
        report.levels_kept += 1
        report.rolls += len(direct)
        report.over_time_rolls += len(lasting)

    for levels in by_spell.values():
        levels.sort(key=lambda lv: lv.grade)
    return by_spell


def normalize_spells(
    raw_spells: list[dict],
    levels_by_spell: dict[int, list[SpellLevel]],
    breed_by_spell: dict[int, int],
    breeds: dict[int, Breed],
    report: SpellIngestReport,
) -> list[ClassSpell]:
    report.spells_in = len(raw_spells)
    out: list[ClassSpell] = []

    for raw in raw_spells:
        spell_id = raw.get("id")
        breed_id = breed_by_spell.get(spell_id)
        if breed_id is None or breed_id not in breeds:
            continue

        levels = tuple(levels_by_spell.get(spell_id) or ())
        spell = ClassSpell(
            spell_id=int(spell_id),
            name=_localized(raw.get("name")),
            breed_id=breed_id,
            levels=levels,
        )
        out.append(spell)

        breed_name = breeds[breed_id].name
        report.spells_by_breed[breed_name] = report.spells_by_breed.get(breed_name, 0) + 1
        if any(lv.deals_direct_damage for lv in levels):
            report.damaging_spells += 1

    report.spells_kept = len(out)
    return out


#: champ DofusDB → caractéristique, pour les barèmes de points de niveau.
STAT_COST_FIELDS = {
    "statsPointsForStrength": "strength",
    "statsPointsForIntelligence": "intelligence",
    "statsPointsForChance": "chance",
    "statsPointsForAgility": "agility",
    "statsPointsForVitality": "vitality",
    "statsPointsForWisdom": "wisdom",
}


def _stat_costs(raw: dict) -> dict[str, tuple[tuple[int, int], ...]]:
    """Barèmes `(seuil, coût unitaire)` de répartition des points de niveau."""
    costs: dict[str, tuple[tuple[int, int], ...]] = {}
    for source_field, stat in STAT_COST_FIELDS.items():
        tiers = raw.get(source_field) or []
        parsed = tuple(
            (int(tier[0]), int(tier[1]))
            for tier in tiers
            if isinstance(tier, (list, tuple)) and len(tier) >= 2
        )
        if parsed:
            costs[stat] = parsed
    return costs


def normalize_breeds(raw_breeds: list[dict], report: SpellIngestReport) -> dict[int, Breed]:
    breeds = {
        int(b["id"]): Breed(
            breed_id=int(b["id"]),
            name=_localized(b.get("shortName")),
            stat_costs=_stat_costs(b),
        )
        for b in raw_breeds
        if b.get("id") is not None
    }
    report.breeds = len(breeds)
    missing = [b.name for b in breeds.values() if len(b.stat_costs) < len(STAT_COST_FIELDS)]
    if missing:
        report.breeds_without_stat_costs = missing
    return breeds


def map_spells_to_breeds(raw_variants: list[dict], known_breeds: set[int]) -> dict[int, int]:
    """`spellId` → `breedId`, depuis la collection `spell-variants`.

    Les variantes référencent des classes absentes de `breeds` (contenu non
    publié) : on les écarte.
    """
    mapping: dict[int, int] = {}
    for variant in raw_variants:
        breed_id = variant.get("breedId")
        if breed_id not in known_breeds:
            continue
        for spell_id in variant.get("spellIds") or []:
            mapping[int(spell_id)] = int(breed_id)
    return mapping
