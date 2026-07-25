# dofus-opti

Optimiseur de stuff **Dofus 3** : trouve l'équipement qui maximise les dégâts sous
contraintes — PA/PM/PO imposés, plancher de points de vie, résistances minimales,
Dofus possédés, exotiques, items forgemagés.

Ce n'est pas un éditeur de stuff de plus. DofusBook, DofusLab et le créateur de
DofusDB vous laissent choisir les items et additionnent les statistiques.
Celui-ci **résout** : il choisit les items pour vous, avec optimalité prouvée par
un solveur de contraintes, sur un objectif de dégâts réels — rotation de sorts
calculée sous budget de PA, pas une somme pondérée de caractéristiques.

État : **utilisable**. 259 tests. Validé contre des builds de stuffeurs
reconnus (voir *Ce qui est vérifié*), mais éprouvé sur trois classes seulement.

---

## Installation

Python 3.11 ou plus.

```bash
git clone <url> dofus-opti
cd dofus-opti
python -m pip install -e ".[web,dev]"
```

Puis construire la base locale — environ **18 secondes**, connexion requise :

```bash
python -m dofus_opti.ingest.build
```

Vérifier que tout va bien :

```bash
python -m pytest -q
```

## Utilisation

### Interface web (recommandé)

```bash
python -m dofus_opti.web
```

Puis <http://127.0.0.1:8410>. Tout y est : classe, niveau, éléments, répartition
des points de niveau aux jauges, parchemins, exotiques, contraintes, Dofus
possédés, items imposés, exclusion d'un item avec relance en un clic, et
publication du résultat vers DofusDB.

### Ligne de commande

```bash
python -m dofus_opti.optimize_cli \
    --breed Ouginak --level 175 --elements terre \
    --pa 12 --pm 5 --min-hp 2800 \
    --stats force --scrolls 100 --crit-policy never \
    --exo pa pm \
    --dofus "Dofus Ocre" "Dofus Pourpre" "Dofus Émeraude" \
    --exclude-slot monture
```

Options utiles :

| Option | Effet |
|---|---|
| `--pa 12` / `--min-pa 12` | valeur exacte / plancher. Idem pour toute caractéristique |
| `--min-hp 2800` | points de vie **totaux**, base du niveau comprise |
| `--stats force intelligence` | où placer les points de niveau ; plusieurs valeurs les équilibrent |
| `--scrolls 100` | caractéristiques parcheminées |
| `--exo pa pm` | exotiques dont vous disposez |
| `--dofus "…" "…"` | Dofus possédés (les trophées restent tous disponibles) |
| `--custom "Gelano:pm=+1"` | item forgemagé |
| `--spells "Torrent Arcanique"` | optimiser sur un sort précis |
| `--charges max` | sorts à charges évalués au cumul maximal (défaut) |
| `--target "Bouftou Royal"` | applique les résistances d'un vrai monstre |
| `--crit-policy never` | build sans critique |
| `--export-dofusdb out.json` | écrit le build au format DofusDB |

### Explorer le catalogue

```bash
python -m dofus_opti.explore stats
python -m dofus_opti.explore top --slot chapeau --level 175 --stat force
python -m dofus_opti.explore item "Casque Keutumedi"
python -m dofus_opti.explore spells --breed Iop --level 175 --elements terre
python -m dofus_opti.explore monster "Bouftou Royal"
```

---

## Les données

**Elles ne sont pas dans le dépôt, et c'est délibéré.**

`data/dofus.db` fait 9,5 Mo, le cache des réponses brutes une trentaine. Trois
raisons de ne pas les versionner :

1. **Elles se régénèrent en 18 secondes.** Un dépôt qui grossit de 40 Mo pour
   éviter 18 secondes est un mauvais échange.
2. **Elles périment.** Chaque mise à jour du jeu les rend fausses ; une copie
   versionnée donnerait des builds silencieusement obsolètes.
3. **Elles appartiennent à Ankama.** Les consommer via des API publiques est
   l'usage admis de tout l'écosystème depuis quinze ans ; les redistribuer en
   masse est autre chose.

Ce qui **est** versionné : le code d'ingestion, les tables de correspondance, et
`tests/fixtures/` — un échantillon réduit d'items et de panoplies réels, pour que
les tests unitaires tournent sur un dépôt fraîchement cloné.

Les tests qui ont besoin de la base complète se **sautent** proprement tant que
l'ingestion n'a pas tourné.

### Sources

| Source | Contenu |
|---|---|
| [dofusdude](https://github.com/dofusdude/doduapi) | équipements, panoplies, conditions d'équipement |
| [DofusDB](https://dofusdb.fr) | sorts, classes, monstres, barèmes de points, provenance des items |

Les deux sont alignées sur la même version de jeu, vérifié à chaque ingestion :
un écart déclenche un avertissement.

### Reproductibilité

La table `meta` de la base enregistre la version du jeu et la date de
construction. Un build produit à une date donnée est rejouable si l'on repart de
la même version — sinon l'ingestion le signale.

---

## Comment c'est fait

```
① INGESTION            2 API → SQLite 9,5 Mo (18 s)
                       3 795 items · 928 panoplies · 836 sorts · 5 134 monstres
                       tables de correspondance explicites : un identifiant
                       inconnu fait ÉCHOUER l'ingestion, jamais de silence

② MODÈLE               StatKey (vocabulaire unique) · Item/Slot/Panoplie
                       Sort/Jet · Monstre

③ MOTEUR DE COMBAT     formule (arithmétique entière, troncature par étape)
                       → rotation optimale sous PA (sac à dos borné, exact)
                       → dégâts moyens par tour = la fonction objectif

④ SOLVEUR              pool : obtenabilité → élagage par dominance (~50 %)
                       CP-SAT : emplacements, panoplies linéarisées,
                       conditions d'équipement réifiées, plafonds du jeu
                       boucle de linéarisation successive contre ③

⑤ EXPORT               charge utile DofusDB → lien partageable
```

Détail des choix et des sources de la formule : [docs/FORMULE.md](docs/FORMULE.md).
Plan et jalons : [PLAN.md](PLAN.md).

### Le principe qui tient tout

**Aucune donnée source n'est acceptée implicitement.** Les tables
`EFFECT_MAP`, `TYPE_TO_SLOT`, `DAMAGE_EFFECTS`, `CONDITION_ELEMENTS` couvrent
l'intégralité du catalogue ; tout identifiant absent fait échouer l'ingestion
avec un rapport des inconnus. Un effet qu'on décide d'ignorer est ignoré
*explicitement*, avec sa justification écrite.

C'est délibérément rigide. Dofus change à chaque mise à jour, et une
caractéristique avalée en silence produirait un optimiseur qui se trompe sans
jamais le dire.

---

## Ce qui est vérifié

- **Relevés en jeu** : cinq infobulles de sorts reproduites au point près, y
  compris l'échelle de charges d'Os à Moelle (12-14 → 28-30).
- **Comparaison externe** : les totaux d'un build DofusBook reconnu reproduits à
  l'unité (3 696 PV calculés contre 3 701 affichés), et le solveur le dépasse de
  17 % à base de sort identique.
- **Mécaniques confirmées empiriquement** sur les builds publics : PA de base
  passant de 6 à 7 au niveau 100, plafonds de 12 PA et 6 PM.
- **259 tests**, dont un garde-fou qui tombe au lendemain d'une mise à jour du jeu.

## Ce qui ne l'est pas

- **Trois classes exercées sur dix-neuf.** C'est le vrai risque.
- **Poisons et dégâts sur la durée absents** : Sram, Sadida et Roublard sont
  sous-évalués.
- **Modificateurs de sorts des items** (« Agitation : −1 PA », 95 items)
  volontairement hors objectif.
- **Prysmaradites écartées par défaut** : leurs contreparties sont en texte
  libre, non modélisables. `--allow-prysmaradites` pour les réintégrer.
- **Formule non calibrée finement** : l'ordre des troncatures reste supposé.
  L'écart est de l'ordre de l'unité et ne change pas les classements.

Chaque limite est signalée dans la sortie de l'outil, jamais silencieuse.

---

## Licence et statut

Projet personnel, sans affiliation avec Ankama. Dofus, les noms d'items et de
sorts sont la propriété d'Ankama Games.
