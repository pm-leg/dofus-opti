"""Constitution du pool de candidats, et élagage par dominance.

L'espace de recherche brut fait ~10³⁰ combinaisons. L'élagage par dominance est
le gain le plus rentable du projet : il supprime typiquement 90 % du catalogue
avant même que le solveur ne démarre.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from ..ingest.conditions import condition_from_dict
from ..model.items import Condition, Item, ItemSet, Slot, StatRange
from ..model.stats import StatKey
from .custom import apply_custom_items
from .request import BuildRequest


@dataclass
class PoolReport:
    loaded: int = 0
    after_level: int = 0
    after_bans: int = 0
    after_obtainable: int = 0
    kept: int = 0
    by_slot: dict[str, int] = field(default_factory=dict)
    dominated: int = 0
    unobtainable_removed: int = 0
    unobtainable_examples: list[str] = field(default_factory=list)
    custom_added: int = 0
    custom_notes: list[str] = field(default_factory=list)
    prysmaradites_removed: int = 0


#: Emplacements où « ni butin ni recette » signifie réellement « inobtenable ».
#:
#: Ailleurs, le critère ne veut rien dire : les montures s'élèvent, les familiers
#: viennent d'évènements, les Dofus de quête ne se droppent pas. Appliqué
#: aveuglément, ce filtre supprimait les 308 montures du catalogue.
DROP_OR_CRAFT_SLOTS = {
    Slot.CHAPEAU, Slot.CAPE, Slot.AMULETTE, Slot.ANNEAU,
    Slot.CEINTURE, Slot.BOTTES, Slot.ARME, Slot.BOUCLIER,
}

#: Marqueur des objets d'administrateur, qui échappent au critère précédent
#: parce qu'ils occupent des emplacements exemptés.
ADMIN_MARKER = "(MJ)"

#: Type d'item désignant un Dofus, par opposition aux trophées et prysmaradites
#: qui partagent le même emplacement.
DOFUS_TYPE_NAME = "Dofus"

#: Les 25 prysmaradites portent **toutes** un effet en texte libre, et c'est
#: presque toujours une contrepartie : « sacrifie 35 % de dommages finaux pour
#: gagner 2 PA », « 100 % Critique au premier tour puis 35 % au deuxième ».
#:
#: Le solveur ne lit que les statistiques : il les verrait comme du gain pur et
#: en remplirait les six emplacements. On les écarte par défaut, quitte à passer
#: à côté de quelques cas où elles sont réellement rentables.
PRYSMARADITE_TYPE_NAME = "Prysmaradite"

#: Items que le critère « ni butin ni recette » écarte à tort.
#:
#: Ce critère attrape aussi les récompenses de quête et de succès, qui n'ont par
#: nature ni l'une ni l'autre. Ces exceptions sont confirmées par un joueur ; la
#: liste est faite pour grandir, et `--allow-item` permet d'y ajouter au cas par cas.
CONFIRMED_OBTAINABLE: frozenset[str] = frozenset({
    "Faux Maudite du Saigneur Guerrier",
    "Amulette Ementaire Deluxe",
})


def load_obtainability(conn: sqlite3.Connection) -> dict[int, bool]:
    """`ankama_id` → l'item est-il planifiable dans un stuff.

    Trois critères, appliqués dans cet ordre :

    1. le marqueur `(MJ)` désigne un objet d'administrateur — un familier niveau
       20 à +600 dans chaque caractéristique n'existe pas autrement ;
    2. « lié au personnage » : ni échangeable ni achetable, donc impossible à se
       procurer pour compléter un stuff (Masque mortuaire, Ménologium béni,
       Lame de Danaba) ;
    3. sur l'équipement classique uniquement, l'absence de butin **et** de
       recette.

    L'emplacement Dofus échappe aux critères 2 et 3 : six Dofus de quête sont
    liés au personnage, et aucun ne se droppe ni ne se craft. C'est le joueur qui
    déclare ceux qu'il possède.

    Le critère 3 reste le plus grossier — il attrape aussi des récompenses de
    quête, d'où `CONFIRMED_OBTAINABLE`.
    """
    result: dict[int, bool] = {}
    for item_id, name, slot, drops, has_recipe, bound in conn.execute(
        "SELECT ankama_id, name, slot, drop_count, has_recipe, bound FROM item"
    ):
        if ADMIN_MARKER in name:
            result[item_id] = False
            continue

        # Le joueur déclare lui-même son pool de Dofus et de trophées.
        if Slot(slot) is Slot.DOFUS:
            result[item_id] = True
            continue

        if bound:
            result[item_id] = False
            continue

        if Slot(slot) not in DROP_OR_CRAFT_SLOTS or name in CONFIRMED_OBTAINABLE:
            result[item_id] = True
            continue

        if drops < 0 or has_recipe < 0:
            result[item_id] = True
            continue

        result[item_id] = bool(drops) or has_recipe == 1
    return result


def load_items(conn: sqlite3.Connection, max_level: int) -> list[Item]:
    """Charge les items équipables jusqu'à un niveau donné."""
    stats: dict[int, dict[StatKey, StatRange]] = {}
    for item_id, stat, lo, hi in conn.execute(
        """SELECT s.item_id, s.stat, s.min, s.max FROM item_stat s
           JOIN item i ON i.ankama_id = s.item_id WHERE i.level <= ?""",
        (max_level,),
    ):
        stats.setdefault(item_id, {})[StatKey(stat)] = StatRange(lo, hi)

    items: list[Item] = []
    for row in conn.execute(
        """SELECT ankama_id, name, slot, type_id, type_name, level, is_weapon, pods,
                  set_id, ap_cost, crit_probability, crit_bonus, max_cast_per_turn,
                  range_min, range_max, condition_json
           FROM item WHERE level <= ?""",
        (max_level,),
    ):
        condition = condition_from_dict(json.loads(row[15])) if row[15] else None
        items.append(
            Item(
                ankama_id=row[0], name=row[1], slot=Slot(row[2]), type_id=row[3],
                type_name=row[4], level=row[5], is_weapon=bool(row[6]), pods=row[7],
                stats=stats.get(row[0], {}), set_id=row[8],
                condition=condition,
                ap_cost=row[9], crit_probability=row[10], crit_bonus=row[11],
                max_cast_per_turn=row[12], range_min=row[13], range_max=row[14],
            )
        )
    return items


def load_sets(conn: sqlite3.Connection) -> dict[int, ItemSet]:
    bonuses: dict[int, dict[int, dict[StatKey, StatRange]]] = {}
    for set_id, count, stat, lo, hi in conn.execute(
        "SELECT set_id, item_count, stat, min, max FROM set_bonus"
    ):
        bonuses.setdefault(set_id, {}).setdefault(count, {})[StatKey(stat)] = StatRange(lo, hi)

    return {
        row[0]: ItemSet(
            ankama_id=row[0], name=row[1], level=row[2], n_items=row[3],
            bonuses=bonuses.get(row[0], {}),
        )
        for row in conn.execute("SELECT ankama_id, name, level, n_items FROM item_set")
    }


def relevant_stats(request: BuildRequest, weights: dict[StatKey, float]) -> set[StatKey]:
    """Caractéristiques qui peuvent influencer la solution.

    Une stat sans poids dans l'objectif et absente des contraintes ne doit pas
    empêcher un élagage : la Prospection ne rend pas un chapeau incomparable.
    """
    keys = {key for key, weight in weights.items() if weight}
    keys |= set(request.bounds)
    # PA, PM et PO changent la rotation ou sont presque toujours contraints.
    keys |= {StatKey.PA, StatKey.PM, StatKey.PO}
    return keys


def _comparison_key(item: Item) -> tuple:
    """Deux items ne sont comparables que dans le même contexte.

    On ne compare jamais entre panoplies différentes : un item individuellement
    plus faible peut ouvrir un bonus de panoplie qui compense largement. C'est
    volontairement conservateur.
    """
    return (item.slot, item.set_id)


def _condition_allows_domination(better: Item, worse: Item) -> bool:
    """Un item conditionné ne peut pas en dominer un qui ne l'est pas.

    Sinon on éliminerait un item toujours équipable au profit d'un autre qui
    exige, par exemple, 500 de Force.
    """
    if better.condition is None:
        return True
    return better.condition == worse.condition


def filter_dominated(
    items: list[Item], keys: set[StatKey], *, roll: str = "max"
) -> tuple[list[Item], int]:
    """Retire les items qu'un autre surpasse sur toutes les stats utiles."""
    groups: dict[tuple, list[Item]] = {}
    for item in items:
        groups.setdefault(_comparison_key(item), []).append(item)

    kept: list[Item] = []
    removed = 0
    ordered_keys = sorted(keys)

    for group in groups.values():
        vectors = {
            item.ankama_id: tuple(item.stat(k, roll=roll) for k in ordered_keys)
            for item in group
        }
        # Trier par somme décroissante : un dominant est rencontré tôt, ce qui
        # écourte la comparaison.
        group.sort(key=lambda it: sum(vectors[it.ankama_id]), reverse=True)

        survivors: list[Item] = []
        for candidate in group:
            vector = vectors[candidate.ankama_id]
            dominated = False
            for other in survivors:
                other_vector = vectors[other.ankama_id]
                if not _condition_allows_domination(other, candidate):
                    continue
                if all(o >= c for o, c in zip(other_vector, vector)) and any(
                    o > c for o, c in zip(other_vector, vector)
                ):
                    dominated = True
                    break
            if dominated:
                removed += 1
            else:
                survivors.append(candidate)
        kept.extend(survivors)

    return kept, removed


def build_pool(
    conn: sqlite3.Connection,
    request: BuildRequest,
    weights: dict[StatKey, float],
) -> tuple[list[Item], dict[int, ItemSet], PoolReport]:
    """Pool de candidats prêt pour le solveur."""
    report = PoolReport()

    items = load_items(conn, request.level)
    report.loaded = report.after_level = len(items)

    # Les items imposés traversent tous les filtres : le joueur a décidé, et une
    # contrainte silencieusement écartée est pire qu'une contrainte refusée.
    forced = set(request.forced_items)
    reserved = [i for i in items if i.ankama_id in forced]

    items = [i for i in items if i.slot not in request.excluded_slots]
    items = [i for i in items if i.ankama_id not in request.banned_items]

    if request.allowed_dofus is not None:
        # Ne restreindre que les Dofus eux-mêmes. Les 261 trophées et 25
        # prysmaradites partagent le même emplacement mais se craftent : les
        # écarter parce que le joueur n'a listé que ses Dofus serait absurde.
        items = [
            i for i in items
            if i.type_name != DOFUS_TYPE_NAME or i.ankama_id in request.allowed_dofus
        ]
    if not request.allow_prysmaradites:
        before = len(items)
        items = [i for i in items if i.type_name != PRYSMARADITE_TYPE_NAME]
        report.prysmaradites_removed = before - len(items)

    report.after_bans = len(items)

    if request.require_obtainable:
        obtainable = load_obtainability(conn)
        survivors = []
        for item in items:
            if item.name in request.allowed_items or obtainable.get(item.ankama_id, True):
                survivors.append(item)
            else:
                report.unobtainable_removed += 1
                if len(report.unobtainable_examples) < 5:
                    report.unobtainable_examples.append(item.name)
        items = survivors
    report.after_obtainable = len(items)

    keys = relevant_stats(request, weights)
    items, removed = filter_dominated(items, keys, roll=request.roll)
    report.dominated = removed

    # Réintégration de ce que les filtres auraient pu emporter.
    present = {i.ankama_id for i in items}
    items.extend(i for i in reserved if i.ankama_id not in present)

    # Les items du joueur (forgemagie, exotiques) échappent à tout élagage et à
    # tout filtre d'obtention : il les possède déjà.
    if request.custom_specs:
        items, replaced, notes = apply_custom_items(
            conn, request.custom_specs, items, roll=request.roll
        )
        report.custom_added = len(request.custom_specs)
        report.custom_notes = notes
    items.extend(request.custom_items)

    report.kept = len(items)
    for item in items:
        report.by_slot[item.slot.value] = report.by_slot.get(item.slot.value, 0) + 1

    return items, load_sets(conn), report
