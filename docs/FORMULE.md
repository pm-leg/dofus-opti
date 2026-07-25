# Formule de dégâts — sources et incertitudes

## Ce qui est établi

Les sources concordent sur la **structure** et sur les **arrondis à l'inférieur
entre chaque étape**.

### Côté attaquant

```
pourcentage = caractéristique + puissance [+ % dommages contextuels]
brut        = ⌊ base × (100 + pourcentage) / 100  +  dommages_fixes ⌋
final       = ⌊ brut × (1 + dommages_finaux / 100) ⌋
```

- `caractéristique` : Force (Terre **et** Neutre), Intelligence (Feu),
  Chance (Eau), Agilité (Air).
- Une caractéristique **négative compte comme 0** — elle ne réduit jamais les dégâts.
- `dommages_fixes` = `+ Dommages` générique **+** `+ Dommages <élément>` de l'élément
  concerné, **+** `Dommages Critiques` en cas de coup critique.
- Coup critique : la base du sort ou de l'arme est augmentée du bonus CC.

### Côté cible

```
subis = ⌊ (final − résistance_fixe) × (1 − résistance_% / 100) ⌋      minimum 0
```

**La résistance fixe s'applique avant la résistance en pourcentage** — c'est
l'inverse de l'ordre des dommages, et c'est confirmé par le wiki JOL comme par les
discussions du forum officiel.

### Coup critique

En Dofus 2 et 3, le taux de critique est une **statistique plate en pourcentage** :

```
p_critique = min(100, %CC du sort + Critique du personnage) / 100
dégâts_espérés = (1 − p) × normal + p × critique
```

Un sort dont le taux de base est nul **ne peut pas être critique**, quelle que soit
la statistique Critique du personnage.

*(L'ancienne formule `BaseCC × 2.9901 / ln(Agilité + 12)` du wiki JOL date de
Dofus 1.x et ne s'applique plus.)*

#### Il n'existe pas de taux critique global

C'est le piège de l'objectif « je veux du 100 % critique ». Le taux dépend du taux
de base **de chaque sort** : viser 100 % sur Pression (10 % de base) demande
90 de Critique, alors que Colère de Iop (25 % de base) n'en demande que 75. Un même
build est donc à 100 % sur un sort et à 85 % sur l'autre.

Une exigence de taux doit par conséquent être **ancrée sur un sort de référence** :
`CritTarget(percent, reference_spell)` convertit l'objectif en une statistique
Critique à atteindre, que le solveur posera comme contrainte au même titre que
« 12 PA ».

#### Deux notions à ne pas confondre

| | |
|---|---|
| `CritPolicy` | **comment on note** un build — ce qu'on mesure |
| `CritTarget` | **ce qu'on exige** du build — une contrainte du solveur |

`CritPolicy` a trois valeurs :

- `NEVER` — dégâts hors critique. Le plancher garanti, pour qui refuse de dépendre
  du hasard.
- `EXPECTED` *(défaut)* — espérance pondérée par le taux réel du build. Le seul mode
  honnête pour comparer deux builds à taux critiques différents.
- `ALWAYS` — critique systématique. N'a de sens que sur un build qui atteint
  réellement 100 % : `CritTarget.shortfall()` permet de vérifier que c'est le cas,
  et de signaler un chiffre trompeur sinon.

Le choix change la notation, mais aussi **la rotation retenue** : un sort à faible
base et gros bonus critique peut être le meilleur en `ALWAYS` et le pire en `NEVER`.

## Ce qui reste incertain

**Un seul point ouvert, et il est mineur.** Les sources divergent sur le
traitement des `% dommages sorts / armes / mêlée / distance` :

| Variante | Traitement |
|---|---|
| `ADDITIVE` | additionnés à `caractéristique + puissance`, dans la même parenthèse |
| `MULTIPLICATIVE` | multiplicateur séparé appliqué après les dommages fixes |

`combat/formula.py` implémente **les deux**, sélectionnables par
`FormulaVariant`. `ADDITIVE` est le défaut — c'est la compréhension communautaire
majoritaire pour Dofus 2/3.

Pourquoi ça n'est pas bloquant : l'ingestion M0 a montré que ces stats sont
**quasi absentes des équipements** — 2 items portent `% dommages mêlée`, 9
`% dommages distance`, 9 `% dommages d'armes`, 5 `% dommages aux sorts`. Et
**aucun item ne donne de `% dommages finaux`** : cette statistique provient
exclusivement des sorts et buffs. Sur un build typique, les deux variantes
donnent donc le même résultat. `tests/test_formula.py` le vérifie.

## Ce qui est volontairement hors modèle

- **Maîtrise d'arme et bonus de classe par type d'arme** : mécaniques de Dofus 1.x,
  supprimées depuis. Le wiki JOL les documente encore — ne pas les reprendre.
- **Érosion, dommages de poussée, renvoi** : hors du calcul de dégâts direct.
- **Réductions physiques / magiques** (armures Féca) : buffs, pas équipement.

### Dégâts sur la durée : la limite qui compte

**Les poisons et dégâts sur la durée ne sont pas comptabilisés.** L'ingestion l'a
établi factuellement : sur les 1 389 effets de dégâts portés par les sorts de
classe, **tous ont `duration = 0`**. En Dofus 3, un poison n'est pas un effet de
dégâts prolongé — c'est un effet d'état (identifiants 950, 280, 1020…) qui
déclenche des dégâts par un mécanisme distinct, non exposé sous une forme
exploitable par l'API.

Conséquence directe : les classes dont une partie des dégâts passe par des états
— Sram, Sadida, Roublard notamment — sont **sous-évaluées** par le moteur. Pour
elles, le classement des builds reste utilisable (les stats offensives valorisées
sont les mêmes), mais les dégâts par tour affichés sont un plancher, pas un total.

Le modèle prévoit `SpellLevel.over_time_rolls` : le champ existe, il est
aujourd'hui toujours vide. Si Ankama expose un jour ces dégâts autrement, la
structure est prête.

### Dégâts conditionnés par un état

Beaucoup de sorts changent de valeur selon l'état de la cible. Le `targetMask`
l'encode : `*E3531` signifie « la cible porte l'état 3531 », `*e3531` « elle ne le
porte pas ». Ce sont des **branches alternatives**, jamais cumulatives — Souffle
Alcoolisé inflige 28-32 ou 34-38, pas 62-70.

Règle retenue : on **somme au sein d'un même masque** (un sort qui frappe plusieurs
fois dans les mêmes conditions) et on **retient la meilleure branche entre masques
différents**. C'est optimiste : on suppose le joueur capable de placer l'état
favorable, ce qui est précisément le cœur du jeu des classes concernées. Les
Pandawas, Ouginaks et Sacrieurs sont donc évalués à leur potentiel, pas à leur
plancher.

L'ingestion en dénombre **463** et l'affiche à chaque exécution.

### Paliers d'escalade non captés

Certains sorts affichent en jeu plusieurs fourchettes selon un compteur — Os à
Moelle de l'Ouginak monte de 12-14 à 28-30 selon les charges accumulées. Ces
paliers **ne figurent pas dans les données de sorts** : seule la valeur de base y
est. Les classes bâties sur l'accumulation sont donc, elles, évaluées à leur
plancher. C'est la même famille de limite que les dégâts sur la durée.

### Multi-cible

`Spell.casts_allowed` retient le minimum entre la limite par tour et la limite par
cible : c'est l'hypothèse **mono-cible**. En zone, la limite par cible ne mordrait
pas et la rotation serait plus généreuse.

## Sources

- [Wiki JOL — Les formules de calcul dans Dofus](https://forums.jeuxonline.info/sujet/801243/les-formules-de-calcul-dans-dofus)
  — référence historique la plus complète. **Attention** : dernière révision en 2015,
  plusieurs formules sont obsolètes (voir ci-dessus).
- [charon25/DofusDamageOptimizer](https://github.com/charon25/DofusDamageOptimizer)
  — implémentation moderne, avec tests unitaires. Même approche que nous : sac à dos
  sur les PA pour la rotation.
- [Patacode/dofus-dmg-calculator](https://github.com/Patacode/dofus-dmg-calculator)
- Forum officiel Dofus, fils sur l'ordre d'application des résistances.

La page officielle Ankama « Dommages et résistances » (tutoriel 420181) **n'est plus
en ligne** — elle renvoie une 404 depuis le site.

## Calibration restante

La structure est solide, mais elle mérite une vérification en jeu. Il suffit de
**quelques relevés**, pas des trente initialement prévus :

1. Un sort mono-élément sur une cible à résistances connues, hors critique.
2. Le même en critique.
3. Un cas avec résistance fixe **et** résistance en pourcentage, pour verrouiller
   l'ordre d'application.
4. Un cas à caractéristique négative, pour confirmer le plancher à 0.

Ces cas alimenteront `tests/test_formula.py::test_golden_cases`.
