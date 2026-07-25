"""Vocabulaire de caractéristiques.

`StatKey` est la seule liste de stats du projet. Le solveur et le moteur de dégâts
n'utilisent que ces clés ; aucun nom d'effet issu d'une API ne doit fuir au-delà de
la couche d'ingestion.
"""

from __future__ import annotations

from enum import StrEnum

ELEMENTS = ("terre", "feu", "eau", "air", "neutre")


class StatKey(StrEnum):
    # --- caractéristiques primaires
    VITALITE = "vitalite"
    SAGESSE = "sagesse"
    FORCE = "force"
    INTELLIGENCE = "intelligence"
    CHANCE = "chance"
    AGILITE = "agilite"

    # --- ressources de tour
    PA = "pa"
    PM = "pm"
    PO = "po"

    # --- divers
    INITIATIVE = "initiative"
    PROSPECTION = "prospection"
    PODS = "pods"
    INVOCATIONS = "invocations"
    SOINS = "soins"

    # --- offensif
    PUISSANCE = "puissance"
    DOMMAGES = "dommages"
    DOMMAGES_TERRE = "dommages_terre"
    DOMMAGES_FEU = "dommages_feu"
    DOMMAGES_EAU = "dommages_eau"
    DOMMAGES_AIR = "dommages_air"
    DOMMAGES_NEUTRE = "dommages_neutre"
    CRITIQUE_PCT = "critique_pct"
    DOMMAGES_CRITIQUES = "dommages_critiques"
    DOMMAGES_PCT_MELEE = "dommages_pct_melee"
    DOMMAGES_PCT_DISTANCE = "dommages_pct_distance"
    DOMMAGES_PCT_ARMES = "dommages_pct_armes"
    DOMMAGES_PCT_SORTS = "dommages_pct_sorts"
    DOMMAGES_POUSSEE = "dommages_poussee"
    PUISSANCE_PIEGES = "puissance_pieges"
    DOMMAGES_PIEGES = "dommages_pieges"
    DOMMAGES_RENVOYES = "dommages_renvoyes"

    # --- défensif (% et fixe)
    RES_PCT_TERRE = "res_pct_terre"
    RES_PCT_FEU = "res_pct_feu"
    RES_PCT_EAU = "res_pct_eau"
    RES_PCT_AIR = "res_pct_air"
    RES_PCT_NEUTRE = "res_pct_neutre"
    RES_FIXE_TERRE = "res_fixe_terre"
    RES_FIXE_FEU = "res_fixe_feu"
    RES_FIXE_EAU = "res_fixe_eau"
    RES_FIXE_AIR = "res_fixe_air"
    RES_FIXE_NEUTRE = "res_fixe_neutre"
    RES_PCT_MELEE = "res_pct_melee"
    RES_PCT_DISTANCE = "res_pct_distance"
    RES_CRITIQUES = "res_critiques"
    RES_POUSSEE = "res_poussee"

    # --- jeu de positionnement
    ESQUIVE_PA = "esquive_pa"
    ESQUIVE_PM = "esquive_pm"
    RETRAIT_PA = "retrait_pa"
    RETRAIT_PM = "retrait_pm"
    TACLE = "tacle"
    FUITE = "fuite"


#: Caractéristique primaire associée à chaque élément (pour le calcul de dégâts).
PRIMARY_STAT_BY_ELEMENT = {
    "terre": StatKey.FORCE,
    "feu": StatKey.INTELLIGENCE,
    "eau": StatKey.CHANCE,
    "air": StatKey.AGILITE,
    "neutre": StatKey.FORCE,
}

#: Bonus de dommages fixes par élément.
FLAT_DAMAGE_BY_ELEMENT = {
    "terre": StatKey.DOMMAGES_TERRE,
    "feu": StatKey.DOMMAGES_FEU,
    "eau": StatKey.DOMMAGES_EAU,
    "air": StatKey.DOMMAGES_AIR,
    "neutre": StatKey.DOMMAGES_NEUTRE,
}
