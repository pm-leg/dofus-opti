"""Table de correspondance `effect_id` → sens métier.

C'est le point de rupture du projet : à chaque mise à jour de Dofus, de nouveaux
`effect_id` apparaissent. La règle est donc stricte — **tout id absent de cette table
fait échouer l'ingestion**. Un effet qu'on décide d'ignorer doit être ignoré
*explicitement*, jamais par omission.

Les ids ont été relevés sur le catalogue complet de l'API dofusdude (dofus3/v1),
équipements + panoplies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..model.stats import StatKey


class EffectKind(StrEnum):
    STAT = "stat"  # caractéristique agrégeable
    WEAPON_HIT = "weapon_hit"  # dégâts/vol/soin propres à l'arme
    SPELL_MODIFIER = "spell_modifier"  # « <Sort> : +2 Portée maximale »
    SPECIAL = "special"  # effet libre (dofus, prysmaradites…)
    IGNORED = "ignored"  # cosmétique / méta, sans effet sur l'optimisation


@dataclass(frozen=True, slots=True)
class EffectMapping:
    kind: EffectKind
    stat: StatKey | None = None
    hit_kind: str | None = None  # "damage" | "steal" | "heal"
    element: str | None = None
    note: str = ""


def _stat(s: StatKey) -> EffectMapping:
    return EffectMapping(EffectKind.STAT, stat=s)


def _hit(kind: str, element: str) -> EffectMapping:
    return EffectMapping(EffectKind.WEAPON_HIT, hit_kind=kind, element=element)


_SPELL_MOD = EffectMapping(EffectKind.SPELL_MODIFIER)
_SPECIAL = EffectMapping(EffectKind.SPECIAL)


def _ignore(note: str) -> EffectMapping:
    return EffectMapping(EffectKind.IGNORED, note=note)


EFFECT_MAP: dict[int, EffectMapping] = {
    # ------------------------------------------------------------------ stats
    9: _stat(StatKey.VITALITE),
    10: _stat(StatKey.SAGESSE),
    45: _stat(StatKey.FORCE),
    13: _stat(StatKey.INTELLIGENCE),
    22: _stat(StatKey.CHANCE),
    36: _stat(StatKey.AGILITE),
    # PA/PM ont deux ids : bonus (12/8) et malus (179/238). Les valeurs des ids
    # de malus sont déjà négatives dans les données — vérifié par test.
    12: _stat(StatKey.PA),
    179: _stat(StatKey.PA),
    8: _stat(StatKey.PM),
    238: _stat(StatKey.PM),
    31: _stat(StatKey.PO),
    24: _stat(StatKey.INITIATIVE),
    25: _stat(StatKey.PROSPECTION),
    220: _stat(StatKey.PODS),
    28: _stat(StatKey.INVOCATIONS),
    121: _stat(StatKey.SOINS),
    # offensif
    32: _stat(StatKey.PUISSANCE),
    30: _stat(StatKey.DOMMAGES),
    48: _stat(StatKey.DOMMAGES_TERRE),
    61: _stat(StatKey.DOMMAGES_FEU),
    27: _stat(StatKey.DOMMAGES_EAU),
    47: _stat(StatKey.DOMMAGES_AIR),
    49: _stat(StatKey.DOMMAGES_NEUTRE),
    29: _stat(StatKey.CRITIQUE_PCT),
    38: _stat(StatKey.DOMMAGES_CRITIQUES),
    40: _stat(StatKey.DOMMAGES_PCT_MELEE),
    71: _stat(StatKey.DOMMAGES_PCT_DISTANCE),
    41: _stat(StatKey.DOMMAGES_PCT_ARMES),
    93: _stat(StatKey.DOMMAGES_PCT_SORTS),
    62: _stat(StatKey.DOMMAGES_POUSSEE),
    106: _stat(StatKey.PUISSANCE_PIEGES),
    112: _stat(StatKey.DOMMAGES_PIEGES),
    249: _stat(StatKey.DOMMAGES_RENVOYES),
    # défensif
    63: _stat(StatKey.RES_PCT_TERRE),
    37: _stat(StatKey.RES_PCT_FEU),
    17: _stat(StatKey.RES_PCT_EAU),
    16: _stat(StatKey.RES_PCT_AIR),
    34: _stat(StatKey.RES_PCT_NEUTRE),
    15: _stat(StatKey.RES_FIXE_TERRE),
    14: _stat(StatKey.RES_FIXE_FEU),
    82: _stat(StatKey.RES_FIXE_EAU),
    60: _stat(StatKey.RES_FIXE_AIR),
    33: _stat(StatKey.RES_FIXE_NEUTRE),
    65: _stat(StatKey.RES_PCT_MELEE),
    108: _stat(StatKey.RES_PCT_DISTANCE),
    46: _stat(StatKey.RES_CRITIQUES),
    70: _stat(StatKey.RES_POUSSEE),
    # positionnement
    75: _stat(StatKey.ESQUIVE_PA),
    39: _stat(StatKey.ESQUIVE_PM),
    64: _stat(StatKey.RETRAIT_PA),
    50: _stat(StatKey.RETRAIT_PM),
    26: _stat(StatKey.TACLE),
    59: _stat(StatKey.FUITE),
    # ------------------------------------------------- dégâts propres aux armes
    195: _hit("damage", "neutre"),
    194: _hit("damage", "terre"),
    198: _hit("damage", "feu"),
    214: _hit("damage", "eau"),
    189: _hit("damage", "air"),
    248: _hit("damage", "best"),
    223: _hit("steal", "neutre"),
    221: _hit("steal", "terre"),
    193: _hit("steal", "feu"),
    203: _hit("steal", "eau"),
    224: _hit("steal", "air"),
    257: _hit("steal", "best"),
    261: _hit("heal", "feu"),
    # ------------------------------------------------ modificateurs de sorts
    # Portés par les chapeaux/capes « à modificateur ». Décisifs pour les dégâts,
    # mais l'API ne donne que le libellé : l'association au sort viendra plus tard.
    204: _SPELL_MOD,  # « <Sort> : Portée modifiable »
    205: _SPELL_MOD,  # « <Sort> : ligne de vue désactivée »
    206: _SPELL_MOD,  # « <Sort> : +N lancer(s) par tour »
    207: _SPELL_MOD,  # « <Sort> : -N PA »
    208: _SPELL_MOD,  # « <Sort> : +N Portée maximale »
    209: _SPELL_MOD,  # « <Sort> : lancer en ligne désactivé »
    226: _SPELL_MOD,  # « <Sort> : +N% Critique »
    227: _SPELL_MOD,  # « <Sort> : -N de relance »
    231: _SPELL_MOD,  # « <Sort> : +N lancer(s) par cible »
    240: _SPELL_MOD,  # « <Sort> : case occupée nécessaire désactivée »
    243: _SPELL_MOD,  # « <Sort> : +N dégâts de base »
    245: _SPELL_MOD,  # « <Sort> : +N Dommages »
    274: _SPELL_MOD,  # « <Sort> : -N Portée minimale »
    # --------------------------------------------------------- effets spéciaux
    163: _SPECIAL,  # effets de dofus / prysmaradites, texte libre
    145: _SPECIAL,  # ajoute un sort temporaire
    # ----------------------------------- effets d'arme non pris en compte (v1)
    225: _ignore("repousse de N cases — positionnement, hors modèle de dégâts"),
    255: _ignore("attire de N cases — idem"),
    258: _ignore("avance de N cases — idem"),
    233: _ignore("vole N PM à la cible — hors modèle v1"),
    241: _ignore("vole des kamas"),
    251: _ignore("modifie la taille du personnage — cosmétique"),
    # ------------------------------------------------ méta / cosmétique / craft
    0: _ignore("« Échangeable » — méta d'objet"),
    35: _ignore("titre accordé"),
    # Sans effet sur les caractéristiques, mais capté à part : un item lié n'est
    # pas récupérable, voir BOUND_TO_CHARACTER_EFFECT.
    81: _ignore("lié au personnage — capté séparément comme critère d'obtention"),
    83: _ignore("fabrication coopérative impossible"),
    84: _ignore("date de réception"),
    92: _ignore("arme de chasse"),
    98: _ignore("attitude / émote"),
    101: _ignore("« Quelqu'un vous suit ! » — cosmétique"),
    117: _ignore("change l'apparence"),
    119: _ignore("change les paroles"),
    123: _ignore("compteur de victimes"),
    166: _ignore("« max. » — compteur de panoplie, non exploitable"),
    191: _ignore("compteur d'utilisations « N / N »"),
    262: _ignore("« Fertile » — élevage de montures"),
}


#: « Lié au personnage » — l'item ne peut ni s'échanger ni s'acheter. En pratique
#: c'est une récompense de quête à usage unique : on ne peut pas décider de se la
#: procurer pour compléter un stuff.
BOUND_TO_CHARACTER_EFFECT = 81


class UnknownEffectError(RuntimeError):
    """Un `effect_id` inconnu est apparu : le jeu a probablement été mis à jour."""

    def __init__(self, unknown: dict[int, str]) -> None:
        lines = "\n".join(f"  id={i:<5} name={n!r}" for i, n in sorted(unknown.items()))
        super().__init__(
            f"{len(unknown)} type(s) d'effet inconnu(s) dans les données source.\n"
            f"{lines}\n"
            "Ajoutez-les explicitement dans EFFECT_MAP (src/dofus_opti/ingest/effects.py), "
            "y compris ceux à ignorer."
        )
        self.unknown = unknown


def lookup(effect_id: int) -> EffectMapping | None:
    return EFFECT_MAP.get(effect_id)
