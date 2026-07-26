# Notes pour un agent travaillant sur ce dépôt

## Mise en route

```bash
python -m pip install -e ".[web,dev]"
python -m dofus_opti.ingest.build      # ~18 s, réseau requis, crée data/dofus.db
python -m pytest -q                    # 259 tests, ~19 s
```

Sans `data/dofus.db`, une partie des tests se saute proprement. Ne jamais
commiter cette base ni `data/cache/` : voir la section « Les données » du README.

## Carte du code

| Chemin | Rôle |
|---|---|
| `model/stats.py` | `StatKey` — **le** vocabulaire de caractéristiques. Rien d'autre ne circule |
| `model/items.py` | Item, Slot, panoplie, conditions, groupes d'emplacements exclusifs |
| `model/spells.py` | sort, palier, jet de dégâts, classe |
| `ingest/effects.py` | `effect_id` → sens métier — **table critique** |
| `ingest/spell_effects.py` | effets de dégâts des sorts — **table critique** |
| `ingest/slots.py` | `type_id` → emplacement, et exclusions justifiées |
| `ingest/normalize*.py` | brut → domaine, validation stricte |
| `combat/formula.py` | calcul des dégâts, arithmétique entière |
| `combat/rotation.py` | rotation optimale sous PA (sac à dos borné, exact) |
| `combat/catalog.py` | chargement des sorts, charges, cibles |
| `optim/pool.py` | obtenabilité, élagage par dominance |
| `optim/model.py` | modèle CP-SAT |
| `optim/solver.py` | linéarisation successive contre le vrai moteur |
| `web/` | service FastAPI, file d'attente, page unique |

## Les règles du jeu qu'il faut connaître

Elles ont **toutes** été découvertes par un joueur, aucune n'était visible dans
les données. Les modifier sans preuve serait une régression.

- **PA de base : 6, mais 7 à partir du niveau 100.** Déduit de 138 builds publics.
- **Plafonds : 12 PA, 6 PM.** Sans eux, un sort à lancers illimités fait empiler
  les PA à l'infini.
- **Un bonus de panoplie = un item au-delà du premier.** 3 items d'une panoplie
  font **2** bonus, pas 1. C'est ce que visent les conditions
  « bonus de panoplies < 3 » de 87 trophées.
- **Familier et monture partagent un emplacement.** L'un ou l'autre.
- **Les parchemins n'entrent pas dans le barème de coût** des points de niveau :
  on investit comme si la caractéristique partait de zéro, le parchemin s'ajoute
  au total. Un Iop 175 parcheminé atteint 467 de Force, pas 392.
- **Portée plafonnée à 6.** Même relevé que les PA : 139 builds à 6, 21 au-dessus.
- **Les résistances plafonnent à 50 %, mais côté *réduction*, pas côté
  caractéristique.** 41 builds publics sur 1 500 affichent davantage, jusqu'à 81.
  Contraindre le total dans le modèle rendrait infaisables des builds légaux :
  on avertit que le surplus ne sert à rien, on n'interdit pas.
- **Les résistances fixes s'appliquent avant les résistances en pourcentage.**
- **Un sort à taux critique nul ne peut pas critiquer**, quelle que soit la
  statistique Critique.
- **Il n'existe pas de taux critique global** : il dépend du taux de base de
  chaque sort. Voir `CritTarget`.

## L'angle mort principal

Tout ce que le jeu **calcule par script** plutôt que de le stocker en donnée.

- Les **sorts à charges** portent une base ridicule (Torrent Arcanique : `2`).
  Leur vraie valeur vient d'un sort compagnon caché, via l'**effet 293**
  (« +N dégâts de base au sort cible »). C'est ingéré, table
  `spell_base_boost`, appliqué par `--charges max`.
- Les **poisons** ne sont pas captés du tout.
- Les **contreparties des prysmaradites** sont en texte libre.

Face à un cas de ce genre : **ne jamais optimiser sur la valeur brute**. Soit
demander la valeur réelle à l'utilisateur (`--spell-base`), soit refuser. Un
build confiant et faux est pire qu'un refus — c'est arrivé, et il a fallu une
comparaison externe pour s'en apercevoir.

## Comment travailler ici

**Vérifier plutôt que supposer.** Ce dépôt a une méthode : quand une valeur
paraît douteuse, on interroge les données publiques. Les mécaniques ci-dessus ont
été *déduites* de 1 500 builds DofusBook, pas devinées. `api.dofusdb.fr` et
`www.dofusbook.net/api/` sont ouvertes en lecture.

**Un chiffre suspect est une piste, pas un détail.** « 0 jet sur la durée »,
« 587 effets mais 96 retenus », « 22 PA » : les trois ont mené à un bug réel ou à
une limite à documenter. Les réconcilier avant de continuer.

**Ne jamais ignorer une contrainte en silence.** Un item imposé hors du pool
levait autrefois zéro erreur ; l'utilisateur croyait à un arbitrage du solveur.
Toute contrainte impossible doit lever une exception explicite.

**Tester contre le réel.** `tests/test_golden_ingame.py` fige des relevés
d'infobulles. Le meilleur test disponible reste la comparaison avec un build de
stuffeur reconnu : charger ses items, les évaluer avec notre moteur, vérifier
qu'on l'égale ou le dépasse.

**Ne rien modifier chez l'appelant.** `optimize()` reçoit une `BuildRequest` et
ne doit jamais la muter : elle a longtemps injecté son plafond de Critique dans
l'objet reçu, ce qui rendait le résultat dépendant de l'historique d'appels.
`tests/test_solver_contract.py` verrouille ce contrat, ainsi que le fait que
`time_limit` est un budget **global** et non par itération.

**Écrire en français**, comme le reste du code et des commentaires. Les
commentaires expliquent *pourquoi*, jamais *quoi*.

## Pièges vérifiés

- `Set-Content -Encoding utf8` de PowerShell 5.1 ajoute un BOM qui casse Python.
  Utiliser l'outil Write, ou `[System.IO.File]::WriteAllText` avec
  `UTF8Encoding($false)`.
- Les identifiants d'items **DofusBook** ne sont pas ceux d'Ankama. Un
  rapprochement passe par les noms.
- DofusDB ignore le champ `classe` à l'écriture : c'est **`breed`** qui compte,
  sinon le build est enregistré comme Féca.
- DofusDB refuse `shared: "private"` sans compte authentifié.
- DofusDB n'a **pas d'emplacement monture** : un build avec monture perd sa
  contribution dans le lien. L'export le signale chiffres à l'appui.

## En cours

Le seul chantier bloquant avant d'ouvrir l'outil à d'autres joueurs : un **banc
de validation** confrontant le solveur aux builds de la communauté, classe par
classe. Les briques existent — un tableur public recense les scénarios,
l'API DofusBook fournit les builds de référence, notre moteur arbitre.
