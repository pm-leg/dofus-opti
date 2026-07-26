"""Modèle de contraintes CP-SAT.

Le choix d'un stuff est une sélection binaire sous contraintes. Trois points
demandent un peu de soin :

1. **Les bonus de panoplie** forment une fonction en escalier du nombre d'items
   portés. On la linéarise exactement avec des booléens réifiés `n ≥ k`.
2. **Les conditions d'équipement** (« Force > 500 ») couplent le choix d'un item
   aux caractéristiques totales — donc à d'autres items. CP-SAT gère ces
   implications nativement ; la plupart des outils existants les ignorent.
3. **PA et PM** incluent la base du personnage, pas seulement l'équipement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from ..combat.stats import (
    MAX_ACTION_POINTS,
    MAX_MOVEMENT_POINTS,
    MAX_RANGE,
    base_action_points,
    base_movement_points,
)
from ..model.items import (
    EXCLUSIVE_SLOT_GROUPS,
    SLOT_CAPACITY,
    Condition,
    ConditionNode,
    ConditionOp,
    ConditionSubject,
    Item,
    ItemSet,
    Slot,
    StatRange,
)
from ..model.stats import StatKey
from .request import BuildRequest


class ForcedItemUnavailable(RuntimeError):
    """Un item imposé n'est pas dans le pool de candidats."""

    def __init__(self, item_ids: list[int]) -> None:
        super().__init__(
            "items imposés absents du pool : "
            + ", ".join(str(i) for i in sorted(item_ids))
        )
        self.item_ids = item_ids


def _range_value(value: StatRange | None, roll: str) -> int:
    if value is None:
        return 0
    if roll == "min":
        return value.minimum
    if roll == "avg":
        return (value.minimum + value.maximum) // 2
    return value.maximum


@dataclass
class BuildModel:
    """Modèle construit, avec de quoi relire une solution."""

    model: cp_model.CpModel
    items: list[Item]
    selection: dict[int, cp_model.IntVar]
    totals: dict[StatKey, object]
    set_active: dict[int, dict[int, cp_model.IntVar]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def selected_items(self, solver: cp_model.CpSolver) -> list[Item]:
        return [i for i in self.items if solver.Value(self.selection[i.ankama_id])]

    def stat_totals(self, solver: cp_model.CpSolver) -> dict[StatKey, int]:
        return {key: solver.Value(expr) for key, expr in self.totals.items()}


class ConditionEncoder:
    """Traduit un arbre de conditions d'équipement en contraintes réifiées."""

    def __init__(
        self,
        model: cp_model.CpModel,
        totals: dict[StatKey, object],
        request: BuildRequest,
        active_set_count,
    ) -> None:
        self.model = model
        self.totals = totals
        self.request = request
        self.active_set_count = active_set_count
        self._true = model.NewBoolVar("cond_true")
        model.Add(self._true == 1)
        self._false = model.NewBoolVar("cond_false")
        model.Add(self._false == 0)
        self.unsupported: set[str] = set()

    def constant(self, value: bool):
        return self._true if value else self._false

    def encode(self, condition: Condition | None):
        if condition is None:
            return self._true

        if isinstance(condition, ConditionNode):
            children = [self.encode(c) for c in condition.children]
            result = self.model.NewBoolVar("cond_node")
            if condition.relation == "and":
                self.model.AddBoolAnd(children).OnlyEnforceIf(result)
                self.model.AddBoolOr([c.Not() for c in children]).OnlyEnforceIf(result.Not())
            else:
                self.model.AddBoolOr(children).OnlyEnforceIf(result)
                self.model.AddBoolAnd([c.Not() for c in children]).OnlyEnforceIf(result.Not())
            return result

        subject = condition.subject
        if subject is ConditionSubject.LEVEL:
            return self.constant(self._compare(self.request.level, condition.operator,
                                               condition.value))
        if subject is ConditionSubject.SUBSCRIPTION:
            return self.constant(self.request.subscribed)
        if subject in (ConditionSubject.ALIGNMENT_LEVEL, ConditionSubject.KAMAS):
            # Hors modèle : on suppose la condition remplie plutôt que d'écarter
            # arbitrairement l'item.
            self.unsupported.add(subject.value)
            return self._true

        if subject is ConditionSubject.SET_BONUS_COUNT:
            return self._reify(self.active_set_count, condition.operator, condition.value)

        if subject is ConditionSubject.STAT and condition.stat is not None:
            total = self.totals.get(condition.stat)
            if total is None:
                return self._true
            return self._reify(total, condition.operator, condition.value)

        self.unsupported.add(str(subject))
        return self._true

    @staticmethod
    def _compare(actual: int, operator: ConditionOp, value: int) -> bool:
        return {
            ConditionOp.GT: actual > value,
            ConditionOp.LT: actual < value,
            ConditionOp.EQ: actual == value,
            ConditionOp.GTE: actual >= value,
            ConditionOp.LTE: actual <= value,
        }[operator]

    def _reify(self, expression, operator: ConditionOp, value: int):
        holds = self.model.NewBoolVar("cond_leaf")
        if operator is ConditionOp.GT:
            self.model.Add(expression >= value + 1).OnlyEnforceIf(holds)
            self.model.Add(expression <= value).OnlyEnforceIf(holds.Not())
        elif operator is ConditionOp.LT:
            self.model.Add(expression <= value - 1).OnlyEnforceIf(holds)
            self.model.Add(expression >= value).OnlyEnforceIf(holds.Not())
        elif operator is ConditionOp.GTE:
            self.model.Add(expression >= value).OnlyEnforceIf(holds)
            self.model.Add(expression <= value - 1).OnlyEnforceIf(holds.Not())
        elif operator is ConditionOp.LTE:
            self.model.Add(expression <= value).OnlyEnforceIf(holds)
            self.model.Add(expression >= value + 1).OnlyEnforceIf(holds.Not())
        else:
            self.model.Add(expression == value).OnlyEnforceIf(holds)
            self.model.Add(expression != value).OnlyEnforceIf(holds.Not())
        return holds


def build_model(
    items: list[Item],
    sets: dict[int, ItemSet],
    request: BuildRequest,
    tracked_stats: set[StatKey],
) -> BuildModel:
    model = cp_model.CpModel()
    roll = request.roll

    selection = {
        item.ankama_id: model.NewBoolVar(f"x{item.ankama_id}") for item in items
    }

    # --- capacité de chaque emplacement
    by_slot: dict[Slot, list[Item]] = {}
    for item in items:
        by_slot.setdefault(item.slot, []).append(item)
    for slot, slot_items in by_slot.items():
        model.Add(
            sum(selection[i.ankama_id] for i in slot_items) <= SLOT_CAPACITY[slot]
        )

    # Emplacements confondus en jeu : familier et monture n'en font qu'un.
    for group, capacity in EXCLUSIVE_SLOT_GROUPS:
        shared = [i for i in items if i.slot in group]
        if shared:
            model.Add(sum(selection[i.ankama_id] for i in shared) <= capacity)

    # --- items imposés par le joueur
    missing = [i for i in request.forced_items if i not in selection]
    if missing:
        # Ne jamais ignorer une contrainte en silence : le joueur croirait que
        # l'item a été jugé inutile alors qu'il n'a même pas été considéré.
        raise ForcedItemUnavailable(missing)
    for item_id in request.forced_items:
        model.Add(selection[item_id] == 1)

    # --- bonus de panoplie : fonction en escalier, linéarisée exactement
    members: dict[int, list[Item]] = {}
    for item in items:
        if item.set_id is not None:
            members.setdefault(item.set_id, []).append(item)

    set_active: dict[int, dict[int, cp_model.IntVar]] = {}
    set_contributions: dict[StatKey, list] = {}

    for set_id, set_items in members.items():
        item_set = sets.get(set_id)
        if item_set is None or len(set_items) < 2:
            continue

        # L'échelle va de 2 au nombre d'items disponibles, indépendamment des
        # paliers où un bonus est défini : elle sert aussi à compter les bonus.
        max_worn = min(len(set_items), item_set.n_items or len(set_items))
        ladder = list(range(2, max_worn + 1))
        if not ladder:
            continue

        count = sum(selection[i.ankama_id] for i in set_items)
        reached: dict[int, cp_model.IntVar] = {}
        for tier in ladder:
            flag = model.NewBoolVar(f"set{set_id}_ge{tier}")
            model.Add(count >= tier).OnlyEnforceIf(flag)
            model.Add(count <= tier - 1).OnlyEnforceIf(flag.Not())
            reached[tier] = flag
        # Monotonie : atteindre 4 items implique en avoir atteint 3.
        for lower, higher in zip(ladder, ladder[1:]):
            model.AddImplication(reached[higher], reached[lower])

        tiers = sorted(t for t in item_set.bonuses if 2 <= t <= max_worn)

        # Les paliers se remplacent (3 items donnent 30, pas 20 + 30) : on somme
        # donc les écarts entre paliers successifs.
        previous: dict[StatKey, int] = {}
        for tier in tiers:
            current = {
                key: _range_value(value, roll)
                for key, value in item_set.bonuses[tier].items()
                if key in tracked_stats
            }
            for key in set(current) | set(previous):
                delta = current.get(key, 0) - previous.get(key, 0)
                if delta:
                    set_contributions.setdefault(key, []).append(delta * reached[tier])
            previous = current

        set_active[set_id] = reached

    # Nombre de bonus de panoplie, au sens du jeu : **un bonus par paire d'items
    # au-delà du premier**. Deux items d'une panoplie donnent 1 bonus, trois en
    # donnent 2, quatre en donnent 3. Porter 3 items de deux panoplies fait donc
    # 4 bonus, pas 2.
    #
    # C'est la grandeur que visent les conditions « bonus de panoplies < 3 » des
    # 87 trophées et prysmaradites. La somme des booléens « n ≥ k » pour k de 2 à
    # n vaut exactement n − 1, et 0 si un seul item est porté.
    active_set_count = sum(
        flag for reached in set_active.values() for flag in reached.values()
    ) if set_active else 0

    if request.max_set_bonuses is not None and set_active:
        model.Add(active_set_count <= request.max_set_bonuses)

    # --- caractéristiques totales
    totals: dict[StatKey, object] = {}
    for key in tracked_stats:
        expression = sum(
            item.stat(key, roll=roll) * selection[item.ankama_id]
            for item in items
            if item.stat(key, roll=roll)
        )
        for term in set_contributions.get(key, []):
            expression = expression + term
        if key is StatKey.PA:
            expression = expression + base_action_points(request.level)
        elif key is StatKey.PM:
            expression = expression + base_movement_points(request.level)
        # Points de niveau, parchemins et exotiques : ils comptent dans les
        # dégâts comme dans les conditions d'équipement, et sous les plafonds.
        base_value = request.base_characteristics.get(key, 0) + request.exos.get(key, 0)
        if base_value:
            expression = expression + base_value
        totals[key] = expression

    # --- plafonds du jeu, indépendants de ce que demande le joueur
    #
    # Seules les ressources de tour sont réellement bornées par le jeu. Les
    # résistances, elles, peuvent dépasser 50 % sur la fiche : c'est la réduction
    # appliquée qui plafonne, pas la caractéristique. Les contraindre ici
    # rendrait infaisables des builds parfaitement légaux.
    for key, cap in (
        (StatKey.PA, MAX_ACTION_POINTS),
        (StatKey.PM, MAX_MOVEMENT_POINTS),
        (StatKey.PO, MAX_RANGE),
    ):
        if key in totals:
            model.Add(totals[key] <= cap)

    # --- contraintes du joueur
    for key, bound in request.bounds.items():
        total = totals.get(key)
        if total is None:
            continue
        if bound.minimum is not None:
            model.Add(total >= bound.minimum)
        if bound.maximum is not None:
            model.Add(total <= bound.maximum)

    # --- conditions d'équipement des items
    encoder = ConditionEncoder(model, totals, request, active_set_count)
    conditioned = 0
    for item in items:
        if item.condition is None:
            continue
        model.AddImplication(selection[item.ankama_id], encoder.encode(item.condition))
        conditioned += 1

    notes = [f"{conditioned} items à condition d'équipement encodés"]
    if encoder.unsupported:
        notes.append(
            "conditions supposées remplies : " + ", ".join(sorted(encoder.unsupported))
        )

    return BuildModel(
        model=model, items=items, selection=selection, totals=totals,
        set_active=set_active, notes=notes,
    )
