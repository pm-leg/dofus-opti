"""Correspondance `type_id` d'item → emplacement d'équipement.

Même règle que pour les effets : un `type_id` inconnu fait échouer l'ingestion.
Les types non équipables (outils, équipements de percepteur, certificats de
monture) sont exclus explicitement.
"""

from __future__ import annotations

from ..model.items import Slot

#: type_id → emplacement.
TYPE_TO_SLOT: dict[int, Slot] = {
    27: Slot.CHAPEAU,
    43: Slot.CAPE,
    33: Slot.AMULETTE,
    17: Slot.ANNEAU,
    58: Slot.CEINTURE,
    45: Slot.BOTTES,
    87: Slot.BOUCLIER,
    # Dofus, trophées et prysmaradites partagent les 6 emplacements du bas.
    177: Slot.DOFUS,
    23: Slot.DOFUS,
    124: Slot.DOFUS,
    # Familiers et montiliers.
    1: Slot.FAMILIER,
    180: Slot.FAMILIER,
    # Montures.
    247: Slot.MONTURE,
    242: Slot.MONTURE,
    245: Slot.MONTURE,
    # Armes.
    80: Slot.ARME,  # Épée
    42: Slot.ARME,  # Marteau
    125: Slot.ARME,  # Bâton
    93: Slot.ARME,  # Dague
    65: Slot.ARME,  # Baguette
    39: Slot.ARME,  # Arc
    73: Slot.ARME,  # Hache
    52: Slot.ARME,  # Pelle
    111: Slot.ARME,  # Lance
    163: Slot.ARME,  # Faux
    199: Slot.ARME,  # Pioche
    182: Slot.ARME,  # Arme magique
}

#: type_id volontairement exclus, avec la raison.
EXCLUDED_TYPES: dict[int, str] = {
    105: "Outil — récolte, non équipable en combat",
    157: "Compagnon — hors modèle de personnage",
    # Les certificats sont la forme échangeable d'une monture ; la monture
    # elle-même est déjà présente sous son propre type.
    22: "Certificat de Dragodinde — doublon de la monture",
    126: "Certificat de Muldo — doublon de la monture",
    71: "Certificat de Volkorne — doublon de la monture",
    # Équipement de percepteur : porté par le percepteur de guilde, pas par le joueur.
    78: "Tunique de Percepteur",
    79: "Cuirasses de Percepteur",
    81: "Poignards de Percepteur",
    102: "Fers de Percepteur",
    161: "Bannière de Percepteur",
    190: "Coffres de Percepteur",
    193: "Sacoches de Percepteur",
}


class UnknownItemTypeError(RuntimeError):
    def __init__(self, unknown: dict[int, str]) -> None:
        lines = "\n".join(f"  id={i:<5} name={n!r}" for i, n in sorted(unknown.items()))
        super().__init__(
            f"{len(unknown)} type(s) d'item inconnu(s).\n{lines}\n"
            "Ajoutez-les à TYPE_TO_SLOT ou à EXCLUDED_TYPES "
            "(src/dofus_opti/ingest/slots.py)."
        )
        self.unknown = unknown
