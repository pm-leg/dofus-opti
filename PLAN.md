# Dofus Stuff Optimizer — Plan détaillé

## 0. Objectif

Étant donné :
- un **niveau** de personnage, une **classe**, un **élément** (ou multi-élément),
- des **contraintes dures** (PA = 12, PM = 5, PO = 0, vitalité ≥ X, résistances ≥ Y, invocations ≥ Z…),
- un **pool d'items autorisés** (dofus possédés, items déjà en banque, items FM/exo custom type « Gelano PA/PM »),

trouver l'**ensemble d'équipements** (16 emplacements) qui **maximise les dégâts moyens par tour**
sur une rotation de sorts réaliste.

Ce n'est pas « maximiser la somme des stats » : c'est un problème d'optimisation combinatoire
sous contraintes avec une fonction objectif **non linéaire**.

---

## 1. Architecture générale

```
┌──────────────────────────────────────────────────────────────┐
│ 1. INGESTION            data/ingest/                         │
│    API DofusDB / dofusdude → normalisation → SQLite/DuckDB   │
├──────────────────────────────────────────────────────────────┤
│ 2. DOMAINE              core/model/                          │
│    Item, Slot, Set, Effect, Character, Spell, Build          │
├──────────────────────────────────────────────────────────────┤
│ 3. MOTEUR DE COMBAT     core/combat/                         │
│    Formules de dégâts, crit, rotation optimale sous PA       │
│    → f(stats) → dégâts moyens/tour        [testé unitairement]│
├──────────────────────────────────────────────────────────────┤
│ 4. SOLVEUR              core/solver/                         │
│    Pré-filtrage dominance → CP-SAT (OR-Tools) → recherche    │
│    locale sur objectif exact                                 │
├──────────────────────────────────────────────────────────────┤
│ 5. API                  api/  (FastAPI)                      │
├──────────────────────────────────────────────────────────────┤
│ 6. UI                   web/  (React + Vite)                 │
└──────────────────────────────────────────────────────────────┘
```

**Stack recommandée** : Python 3.12 + OR-Tools (CP-SAT) + Pydantic + DuckDB/SQLite + FastAPI ;
front React/Vite plus tard. Python parce que CP-SAT y est excellent et que le prototypage du
moteur de dégâts (à calibrer) doit être rapide.

**Règle d'or** : le solveur doit tourner en CLI et produire des builds corrects **avant** qu'on
écrive la moindre ligne de front.

---

## 2. Phase 1 — Données (~1 semaine)

### 2.1 Sources

| Source | Contenu | Verdict |
|---|---|---|
| `api.dofusdu.de` (dofusdude/doduapi) | items, équipements, sets, montures, multi-langue, versionné | **Source principale** — REST propre, open source, SDK Python officiel |
| `dofusdb.fr/api` | items, sorts, monstres, jobs — très complet | **Source secondaire**, surtout pour les **sorts** |
| `bot4dofus/Datafus` | dump JSON brut du client (D2O) | Filet de sécurité si les API décrochent d'une MAJ |
| Encyclopédie officielle | vérité terrain | Uniquement pour valider ponctuellement |

Décision : **abstraire derrière une interface `ItemSource`** avec une implémentation par source.
Les API Dofus cassent à chaque MAJ du jeu ; on ne veut pas que ça contamine le solveur.

### 2.2 Ce qu'on récupère

- **Items** : id, nom, niveau, type/slot, rareté, `set_id`, effets (min/max par caractéristique),
  **conditions d'équipement** (niveau, classe, `Force > 500`, `PA < 12`…)
- **Panoplies** : bonus par palier (2/3/4/…/8 items) — attention c'est une fonction en escalier
- **Sorts par classe** : coût PA, dégâts base min/max par niveau et par élément, %CC de base,
  dommages critiques, PO, nombre de lancers max par tour / par cible, conditions de ligne
- **Montures / familiers / dofus / trophées / prysmaradites**

### 2.3 Normalisation — le vrai travail

Les effets arrivent en texte semi-structuré. Il faut une table de mapping stricte
`effect_id → (stat_key, signe, cible)` et **un test qui échoue si un `effect_id` inconnu apparaît**
lors d'une réingestion. C'est ce qui empêchera les régressions silencieuses aux MAJ du jeu.

Stats à modéliser :
```
vitalite, sagesse, force, intelligence, chance, agilite,
pa, pm, po, portee_modifiable,
dommages_fixes, dommages_terre/feu/eau/air/neutre,
dommages_pct (puissance), puissance_pieges, dommages_pieges,
critique, dommages_critiques, dommages_poussee, dommages_finaux_pct,
soins, invocations, prospection, initiative,
res_pct_terre/feu/eau/air/neutre, res_fixe_*,
res_pvp_pct_*, esquive_pa, esquive_pm, retrait_pa, retrait_pm,
tacle, fuite, erosion, renvoi_dommages
```

### 2.4 Livrable Phase 1
- `data/dofus.db` reproductible via `python -m ingest.build`
- Snapshot versionné (`version_jeu`, `date_ingestion`) pour la reproductibilité des builds

---

### 2.5 Constats de la Phase 1 — ce que les données ont contredit

M0 est livré. Quatre points invalident ou précisent le plan initial :

1. **Il n'existe pas de stat « % dommages finaux » ni de « % dommages par élément »
   sur les items.** Le plan en supposait. Les seuls multiplicateurs disponibles sont
   `% dommages mêlée` (2 items), `% dommages distance` (9), `% dommages d'armes` (9)
   et `% dommages aux sorts` (5) — marginaux. La non-linéarité de l'objectif est donc
   **beaucoup plus faible que prévu** : elle se réduit essentiellement au produit
   `(carac + puissance) × base`. Bonne nouvelle pour le solveur — la linéarisation
   successive convergera vite, voire deviendra inutile.

2. **Les modificateurs de sorts sont des effets d'items** (13 identifiants, 95 items) :
   « Agitation : -1 PA », « Vent Empoisonné : +1 lancer(s) par tour », « Lapement :
   +35 % Critique ». Ils changent directement la rotation, donc les dégâts. Mais l'API
   ne fournit **que le libellé**, pas l'identifiant du sort. Il faudra les rattacher par
   nom lors de l'ingestion des sorts — prévoir que certains ne matcheront pas.

3. **PA et PM ont deux identifiants d'effet chacun** (bonus 12/8, malus 179/238). Les
   valeurs des ids de malus sont déjà négatives dans la source — vérifié et verrouillé
   par un test. C'est exactement le genre de détail qui, mal lu, fait recommander un
   marteau à -1 PA comme s'il en donnait un.

4. **Les dégâts propres aux armes ont leurs propres identifiants** (`dommages Neutre`
   id 195 ≠ `Dommage Neutre` id 49). Les confondre ferait passer les dégâts de base
   d'une arme pour un bonus de dommages fixes. Ils sont stockés séparément
   (`item_weapon_hit`).

Volumétrie réelle : 3 795 items équipables, 928 panoplies, 276 items à condition
d'équipement, jeu en version 3.6.7.7.

---

## 3. Phase 2 — Moteur de dégâts (~1 semaine, le plus critique)

### 3.1 Formule (à calibrer, à ne surtout pas coder « au feeling »)

Dégâts d'un lancer, forme générale :

```
D = ⌊ (Base + dom_élément + dom_fixes) × (1 + (Carac + Puissance)/100) ⌋
    × (1 + dommages_finaux/100)
```
puis côté cible :
```
D_subis = ⌊ (D − res_fixe) × (1 − res_pct/100) ⌋
```

Coup critique : `Base + dommages_critiques`, et `%CC_final = min(100, %CC_sort + Critique)`.

**Dégâts espérés par lancer** :
```
E = (1 − p_cc) × D_normal + p_cc × D_crit
```

⚠️ L'ordre exact des arrondis et l'ordre `res_fixe` / `res_%` varient selon les versions.
→ **Constituer une suite de tests « golden »** : ~30 cas relevés en jeu (stats connues, sort connu,
dégâts observés). Le moteur doit reproduire ces cas au point près. Sans ça, tout l'optimiseur
optimise dans le vide.

### 3.2 Rotation optimale

Avec `PA` disponibles et un ensemble de sorts (coût PA, dégâts espérés, limite de lancers/tour),
choisir la combinaison maximisant les dégâts = **petit sac à dos entier**. PA ≤ ~14 → programmation
dynamique exacte en quelques microsecondes.

C'est ce qui rend la contrainte « 12 PA » intéressante : 12 PA peut valoir plus que 13 PA si le
sort coûte 4 PA (3 lancers vs 3 lancers + 1 PA perdu). Un optimiseur qui maximise juste « PA »
rate complètement ça.

### 3.3 Signature

```python
def expected_damage_per_turn(stats: Stats, ctx: CombatContext) -> float
```
`CombatContext` = classe, niveaux de sorts, cible (résistances, PvP/PvE), corps-à-corps ou distance,
buffs éventuels. **Pur, sans I/O, testé.** C'est le cœur.

---

## 4. Phase 3 — Solveur (~2 semaines, le cœur technique)

### 4.1 L'espace de recherche

16 emplacements × plusieurs centaines d'items chacun ⇒ ~10³⁰ combinaisons.
Brute force impossible. Trois étages.

### 4.2 Étage A — Pré-filtrage par dominance

Pour un profil de recherche donné (élément terre, cible = dégâts), un item A est **dominé** par B si :
- même slot, même panoplie (ou aucune),
- B ≥ A sur toutes les stats pertinentes, avec au moins une stricte,
- B ne viole aucune contrainte que A respecte.

Élimine typiquement 90–95 % du catalogue. **C'est le gain le plus rentable du projet.**
Attention : ne jamais éliminer un item appartenant à une panoplie candidate (le bonus de set
peut compenser une infériorité individuelle).

### 4.3 Étage B — Programmation par contraintes (OR-Tools CP-SAT)

Variables : `x[i] ∈ {0,1}` pour chaque item survivant.

Contraintes :
```
Σ_{i ∈ slot s} x[i] ≤ 1          pour chaque emplacement
Σ_{i ∈ anneaux} x[i] ≤ 2         + règle « pas 2 fois le même anneau »
                                 + règle panoplie sur les anneaux (à vérifier en jeu)
Σ_{i ∈ dofus/trophées} x[i] ≤ 6  + exclusion trophée ↔ prysmaradite équivalente
niveau_item ≤ niveau_perso
condition de classe respectée
```

**Stats totales** (linéaire) :
```
total[s] = Σ_i x[i] · stat_i[s]  +  Σ_panoplies bonus_set[s]
```

**Bonus de panoplie** (fonction en escalier → linéarisation exacte) :
```
n_p = Σ_{i ∈ p} x[i]
b[p][k] ⟺ (n_p ≥ k)          booléens réifiés
b[p][k] ≥ b[p][k+1]           monotonie
contribution[s] = Σ_k (bonus_k[s] − bonus_{k−1}[s]) · b[p][k]
```

**Contraintes utilisateur** : `total[pa] == 12`, `total[pm] == 5`, `total[po] == 0`,
`total[res_terre] ≥ 30`, etc. — directement exprimables.

**Conditions d'équipement des items** (`x[i] ⇒ total[force] ≥ 501`) : CP-SAT gère nativement les
contraintes linéaires réifiées. **La plupart des outils existants ignorent ces conditions** — c'est
un vrai différenciateur.

**Objectif** : linéarisé, `Σ_s w[s] · total[s]`, où `w` = gradient de la vraie fonction de dégâts
au point courant.

### 4.4 Étage C — Linéarisation successive + recherche locale

La vraie fonction de dégâts est **multiplicative** (`carac × %dommages × %finaux`), donc non
linéaire : CP-SAT seul ne peut pas l'optimiser directement.

Boucle de **programmation linéaire successive** :
1. Poids initiaux `w₀` heuristiques
2. CP-SAT → build candidat + les K meilleures solutions
3. Évaluation **exacte** via le moteur de dégâts
4. Recalcul de `w` = ∂dégâts/∂stat au point atteint
5. Retour en 2, jusqu'à convergence (5–10 itérations en pratique)

Puis **recherche locale** (swap 1-item, puis 2-items, best-improvement + recuit simulé) sur
l'objectif exact, initialisée par les K meilleurs candidats CP-SAT. Ça rattrape les effets de
seuil que la linéarisation manque.

**Sortie** : top-N builds **diversifiés** (pas 10 variantes du même stuff à un anneau près) —
un critère de distance minimale entre solutions.

### 4.5 Garanties
CP-SAT fournit une **borne supérieure prouvée** sur l'objectif linéarisé ⇒ on peut afficher un
écart d'optimalité (« ce build est à ≤ 2 % de l'optimum théorique »). Honnête et rassurant pour
l'utilisateur.

### 4.6 Objectif de perf
< 10 s pour une requête typique, < 60 s en mode « exhaustif ».

---

## 5. Phase 4 — API + UI (~2 semaines)

### API (FastAPI)
```
GET  /items          filtres slot / niveau / stats
GET  /sets
GET  /classes/{c}/spells
POST /optimize       → { contraintes, pool, objectif } → top-N builds + explication
POST /builds/import  ← lien DofusBook / DofusDB
```

### UI
- Formulaire de contraintes (niveau, classe, élément, PA/PM/PO, seuils par stat)
- Sélecteur « mes dofus », « mes items custom / FM / exo »
- Résultat : les N builds, stats totales, dégâts/tour, panoplies actives
- **Panneau d'explication** : contribution marginale de chaque item, « remplacer X par Y coûte 3,2 % de dégâts »
- Export vers DofusBook / DofusDB

---

## 6. Points durs identifiés (à traiter explicitement)

1. **Items custom / forgemagie / exos** (ton Gelano PA/PM) → le modèle d'item doit accepter des
   overrides utilisateur et des items entièrement définis à la main. À prévoir **dès la Phase 1**,
   pas après coup.
2. **Jets min/max** : les items ont des fourchettes. Défaut = jet max (stuff parfait), avec option
   « jet moyen » ou saisie des jets réels.
3. **Conditions d'équipement** : couplage circulaire (l'item requiert une stat que l'item fournit).
   Géré proprement par CP-SAT, mais à tester.
4. **Règles d'unicité** : deux fois le même anneau, trophée + prysmaradite du même nom, dofus
   uniques. À encoder comme table de règles et **à vérifier en jeu** — les règles ont bougé.
5. ~~**Sublimations**~~ — retiré : aucune trace dans les données de Dofus 3.6 (ni type
   d'item, ni effet, ni texte). C'était une mécanique de Wakfu importée à tort dans ce
   plan ; un joueur actif de 3.6 ne connaît pas le terme.
6. **Buffs** (Pandawa, Féca, potions, nourriture) : hors v1, ajoutables comme offset de stats.
7. **Dérive des données** : chaque MAJ Dofus casse quelque chose. D'où l'interface `ItemSource` +
   les tests de mapping d'effets.

---

## 7. Idées d'extension (après v1)

| Idée | Intérêt |
|---|---|
| **Multi-objectif / front de Pareto** | Dégâts vs survie vs PdV — voir les compromis au lieu d'un point unique |
| **Contrainte budget kamas** | « Meilleur stuff sous 50 M » — nécessite des prix HDV (saisie manuelle ou communautaire) |
| **Chemin d'amélioration** | « À partir de mon stuff actuel, quel *seul* item changer pour le meilleur gain ? » — probablement la feature la plus utile au quotidien |
| **Modes d'objectif** | Dégâts, soins, invocations, tank (résistances effectives = f(res %, res fixe, PdV)), retrait PA/PM, initiative |
| **Cible paramétrable** | Résistances du monstre / PvP, dos vs face, mono-cible vs zone |
| **Comparateur de builds** | Diff stat par stat entre deux stuffs |
| **Optimisation multi-personnages** | Team de 8 en multi-compte, avec partage de dofus |
| **Mode « stuff évolutif »** | Le meilleur stuff aux niveaux 50/80/120/150/175/200 en réutilisant un maximum d'items |

---

## 8. Jalons

| Jalon | Contenu | Critère de succès |
|---|---|---|
| **M0** ✅ | Squelette repo, ingestion items + sets en base | ~~`SELECT` sur 15 000 items, 0 effet non mappé~~ → **3 795 items équipables, 928 panoplies, 0 effet non mappé, 32 tests** |
| **M1** ✅ | Moteur de dégâts, rotation sous PA, ingestion des sorts | 19 classes, 836 sorts, 1 729 paliers ingérés depuis DofusDB (3.6.7.7). Formule, politiques de critique et rotation implémentées. **122 tests.** Reste la calibration en jeu (4 relevés) |
| **M2** ✅ | Élagage par dominance, modèle CP-SAT, linéarisation successive | Build valide 12 PA / 5 PM / 0 PO, optimum prouvé, ~50 s. **167 tests** |
| **M3** | Linéarisation successive + recherche locale | Bat un build méta connu (ou l'égale) sur DofusLab |
| **M4** | CLI complète + export DofusBook | Ton cas d'usage terre 175 fonctionne bout en bout |
| **M5** | API + UI web | Utilisable par quelqu'un d'autre que toi |

**M0→M4 : environ 4–5 semaines de travail focalisé.** M2/M3 sont les jalons risqués.

---

## 9. Validation

- Comparer les sorties contre **DofusLab** et des builds méta connus des sites communautaires.
  Si on trouve systématiquement mieux, soit c'est une vraie valeur ajoutée, soit un bug de formule.
  **Toujours suspecter le bug d'abord.**
- Rejouer les builds proposés en jeu et vérifier les stats affichées.
