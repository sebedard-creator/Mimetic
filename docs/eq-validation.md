# Match EQ — mesures

Date : 2026-09-19. Implémentation de `architecture-match-eq.md`, lots A à D. Version `speech-ltas-room-aware-v1`.

## Ce qui est implémenté

| Élément | Choix retenu |
|---|---|
| Cible | Référence comparée au **mélange virtuel** `ADR + reverb rendue` (§4), pas à l'ADR sec |
| Détection d'activité | Maison : énergie 100 Hz–6 kHz, rampe de poids de 6 à 18 dB au-dessus du plancher du fichier (`energy_snr_v1`). **Silero VAD différé** : dépendance ONNX non justifiée tant que la sélection n'est pas le facteur limitant |
| Statistiques | Bandes 1/12 d'octave 40 Hz–16 kHz, blocs de 0,5 s, moyenne tronquée à 10 %, soustraction du bruit estimé sur les pauses, normalisation de forme sur 200 Hz–5 kHz |
| Courbe | Moindres carrés bornés (`lsq_linear`), lissage `λs = 20`, retrait vers zéro `λ0 = 0,05 + 0,25·(1−w)`, bornes −9/+6 dB (−6/+3 en confiance réduite) |
| Filtre | FIR causal à phase minimale par cepstre réel, 2048 à 8192 coefficients, vérifié contre la courbe **avec ses transitions** |
| Niveau | Compensation RMS de parole commune aux deux branches, bornée à ±6 dB |
| Export | `…_IR_ONLY.wav` (reverb seule) ou `…_EQ_IR_MIX.wav` (voix corrigée + reverb, remplace l'ADR) |

## Tests automatiques (43 passés, 1 sauté)

Tests bloquants du §12.1 couverts : bypass identique à l'octet près, courbe connue réalisée à ≤ 0,25 dB, intensité 50 % = moitié en dB et 0 % = identité exacte, routage `dry+wet = q*(ADR*(δ+h_eff))` à 1e-6, conservation du niveau à ≤ 0,5 dB, bornes respectées et saturation signalée, refus propre sans parole.

**Oracle** (`test_oracle_recovers_known_curve_and_ignores_level`) : référence fabriquée en appliquant une EQ connue au baseline. Courbe retrouvée à **≤ 1,5 dB RMS** sur 200 Hz–5 kHz, corrélation > 0,9. Un changement de niveau global de la référence (×0,5) ne déplace la courbe que de ≤ 0,5 dB.

Côté web : activer/désactiver l'EQ ne relance **pas** Rec-RIR ; le retour à OFF redonne exactement le rendu initial ; les deux modes d'export produisent les bons fichiers et le `_EQ_IR_MIX` relu vaut `dry + wet`.

## Contrôle de bout en bout avec le vrai moteur

Scénario : source = parole réverbérée (RT60 0,5 s, DRR +4 dB) avec un timbre « perche » (coupe-bas, présence 2,5–6 kHz) ; destination = **autre phrase**, sèche, volontairement plus sourde (passe-bas 5 kHz + effet de proximité). Mesure : écart de forme spectrale (LTAS de parole, moyenne retirée) entre le rendu et la source.

| | Écart RMS 150 Hz–8 kHz | Écart max |
|---|---|---|
| Sans EQ | 3,71 dB | 9,33 dB |
| Avec Match EQ | **1,63 dB** | 6,60 dB |

Par bande (dB relatifs) :

| Bande | Source | Sans EQ | Avec EQ |
|---|---|---|---|
| 150–400 Hz | 10,0 | 12,6 | 10,6 |
| 400 Hz–2 kHz | 4,0 | 5,5 | 4,7 |
| 2–5 kHz | −8,3 | −10,6 | −8,7 |
| 5–8 kHz | −18,6 | −25,1 | −21,5 |

Objectif provisoire du document (−30 % d'erreur médiane face au bypass) : atteint sur ce cas (−56 %).

## Deux erreurs corrigées pendant la mise au point

1. **Poids effondrés** : la présence de signal était jugée par un seuil à −50 dB du maximum, que le spectre de parole franchit naturellement dans l'aigu ; et le rapport signal/bruit était calculé en mélangeant un spectre normalisé et un bruit brut. Résultat : correction quasi nulle au-dessus de 5 kHz. Corrigé par un vrai rapport signal/bruit par bande et une remise à l'échelle relative des poids.
2. **Retrait vers zéro trop fort** : avec `λ0 = 0,05 + 2·(1−w)`, une bande moyennement fiable n'appliquait que ~30 % de la correction mesurée. Ramené à `0,25`, soit ~60 %.

## Premier essai sur des fichiers réels de l'utilisateur (2026-09-19)

`REF5.wav` (4,1 s, 3,5 s de parole détectée) et `DEST5.wav` (3,7 s, **1,1 s** de parole). Pièce estimée : RT60 0,57 s, DRR 19,3 dB.

- **Échec initial corrigé** : le minimum de 2 s de parole refusait l'ADR. Une réplique isolée contient couramment moins d'une seconde de voix ; seuil ramené à 0,8 s, plage d'activité élargie à 35 dB, et message d'erreur désormais chiffré (« DESTINATION : 0,9 s de parole détectée, minimum 0,8 s »).
- **Résultat final** (bornes par incertitude, λ = 5000) : courbe −1,4 dB vers 400–1000 Hz, **+4,4 dB vers 3–6 kHz**, +2,7 dB au-dessus ; 2 % de bandes saturées seulement, donc aucune correction bridée. Écart de forme spectrale au boom **5,74 → 4,69 dB RMS**.

Note de lecture : cet écart global n'est pas un bon juge de qualité, car il inclut la différence de texte entre les deux prises. Une version antérieure, plus « collée » au spectre global, affichait un meilleur chiffre (4,09 dB) tout en produisant deux fois plus de fausse correction au banc ci-dessous. Seule une écoute tranchera.

## Robustesse au texte différent (`benchmarks/eq_phoneme_bias.py`)

Question décisive pour l'ADR : deux prises n'ont ni le même texte ni la même proportion de voyelles et de consonnes sourdes. Le test applique une **EQ connue** à l'ADR ; la correction idéale est son inverse. Un cas « aucune » n'applique aucune EQ : tout ce que l'estimateur produit alors est une **fausse correction** due au seul contenu. 16 cas, deux voix, phrases différentes.

| Réglage | Fausse correction (texte seul) | Erreur sur EQ connue (pour 1,82 dB demandés) |
|---|---|---|
| Lissage λ = 20 (valeur initiale) | 2,02 dB | 2,14 dB |
| **λ = 5000 (retenu)** | **~1,0 dB** | **~1,5 dB** |
| λ = 100000 | 0,31 dB | 1,62 dB |
| Comparaison par classes de phonèmes | 2,75 dB | 3,01 dB |

Trois enseignements :

1. **Le lissage fort est le bon levier.** L'erreur passe par un minimum entre λ = 3000 et 10000 ; au-delà, les vraies différences de micro sont écrasées à leur tour.
2. **La comparaison par classes de phonèmes (voyelles / consonnes sourdes) a été essayée et écartée** : elle dégrade les deux mesures. Le découpage dépend lui-même de la coloration à estimer, il se biaise donc avec elle, et chaque classe reçoit moins de trames.
3. **Un plancher de bruit subsiste** : environ 1 dB RMS de correction inventée par la seule différence de texte. Une vraie différence de micro de 4 à 6 dB reste donc largement au-dessus du bruit, mais une différence de 1 dB n'est pas mesurable de façon fiable.

### Bornes : incertitude plutôt que durée

Première version : bornes resserrées quand la parole durait moins de 3 s. **Mauvaise idée** — un ADR est court par nature, la fonction aurait été bridée en permanence. Les bornes dépendent désormais de l'**erreur type mesurée** sur la différence de spectres. Une différence franche et constante reste exploitable sur une réplique d'une seconde.

Bug corrigé au passage : cette erreur type utilisait la dispersion entre blocs sans la diviser par la racine de leur nombre, ce qui déclenchait la confiance réduite sur du matériel pourtant sain.

## Calage automatique du niveau de reverb : tentative abandonnée (2026-09-19)

Constat de départ, sur du dialogue réel : le DRR estimé par Rec-RIR (16,4 dB sur une perche) donne une reverb à −12 dB sous la voix, jugée trop sèche à l'écoute. Mesure des niveaux après les fins de phrases, en dB sous la parole :

| | 0 ms | 30 ms | 60 ms | 100 ms | plancher de bruit |
|---|---|---|---|---|---|
| Source (perche) | −24 | −31 | −32 | −34 | −36 |
| Rendu | −24 | −34 | −43 | −50 | néant |

Deux estimateurs ont été écrits pour caler le niveau automatiquement sur la source :

1. **Tranches après les fins de phrases** (30–60, 60–100, 100–150 ms), bruit soustrait ;
2. **Temps audible** : proportion des trames de la phrase restant au-dessus de −20 et −30 dB sous la parole.

Banc à vérité connue, profil délibérément trop sec de 0, 6 ou 10 dB, avec des **rythmes de parole différents** entre source et ADR :

| Cas (vérité) | « temps audible » | « tranches » |
|---|---|---|
| Même rythme, manque 0 dB | −12,0 dB | +0,4 dB |
| ADR plus aéré, manque 0 dB | **+11,0 dB** | +0,0 dB |
| ADR plus aéré, manque 10 dB | +12,0 dB | +0,8 dB |

**Les deux échouent.** Le premier suit le rythme de parole plutôt que la reverb (il varie de −12 à +12 dB alors que la vérité ne bouge pas) ; le second est quasi insensible au défaut qu'il doit corriger. Sur les fichiers réels de l'utilisateur, ils proposaient respectivement −3 dB et +12 dB : contradiction complète.

**Décision : fonctionnalité retirée.** Estimer un dosage de reverb à partir d'une *autre performance*, sans référence sèche, revient à de l'estimation aveugle de DRR : ces mesures simples n'y suffisent pas. Le réglage reste manuel, à l'oreille.

Un premier bug avait été corrigé au passage (fins de phrases détectées séparément sur chaque branche, biais de 6 dB), mais il ne sauvait pas la méthode.

**À retenir pour la suite** : la direction du défaut semble robuste (le rendu est plus sec que la source dans la fenêtre 30–100 ms, où le bruit ne domine pas encore), mais son amplitude n'est pas mesurable de façon fiable avec ces outils.

### Ce qui manque aussi, et qui n'est pas de la reverb

La source porte un fond continu (−36 dB large bande, et surtout −20 dB entre 3 et 8 kHz), tandis que l'ADR est en silence numérique entre les mots. Une partie du « ça sonne sec » vient de cette **absence de room tone**, que Mimetic ne fabrique pas (hors périmètre, architecture §2.2). En pratique, le room tone se prend sur la production.

## Lot A — références bruitées (2026-09-19)

Suite de `architecture-references-difficiles.md`, lots A1 et A2. Banc : `benchmarks/eq_noise_bench.py`, 96 cas (2 pièces × 4 EQ connues × 6 conditions de bruit × 2 paires de phrases). Métriques : erreur sur l'EQ connue et **fausse correction** (cas sans EQ, où toute courbe produite est un artefact).

### Les trois défauts vérifiés dans la V2

1. **La confiance absolue était effacée** : la normalisation des poids par leur 90e centile rendait un extrait à 6,2 dB de rapport signal/bruit rigoureusement identique à un extrait propre (courbe −2,95 / +5,06 dB dans les deux cas), puis provoquait un refus brutal à 6,0 dB.
2. **Une queue de réverbération pouvait passer pour du bruit** (`quiet = ~voiced`, sans garde après les mots).
3. **L'incertitude des répliques courtes était conventionnelle** : dispersion remplacée par une constante sous 10 blocs.

### Corrections

- Trois catégories temporelles : parole, décroissance, fond seul. Le fond n'est mesuré que loin des mots (100 ms avant, 250 ms après, prolongé tant que l'énergie décroît, plafonné à 600 ms).
- **Fiabilité absolue `c_abs`** (jamais renormalisée) séparée des **poids relatifs**. Elle combine le rapport signal/bruit, la stabilité du fond et le support statistique ; le côté le plus faible commande.
- Retrait vers zéro en `λ0 = 0,05 + 0,3·(1/c − 1)` : une bande seulement « non vérifiable » garde l'essentiel de sa correction, une bande vraiment mauvaise s'efface. Plus de falaise : de 24 dB à 0 dB de SNR, la correction décroît continûment.
- **Rapport signal/bruit de la parole seule** (`(observé − fond)/fond`), qui peut désormais être négatif, au lieu de `(parole + bruit)/bruit` qui ne descendait jamais sous 0 dB.
- **Fond négligeable ≠ fond inconnu** : un ADR de studio en silence numérique garde une confiance de 1, au lieu d'être pénalisé comme un enregistrement dont le fond est inconnu.

### Résultats

| Condition | V2 err. EQ | A2 err. EQ | V2 fausse corr. | A2 fausse corr. |
|---|---|---|---|---|
| Propre | 1,17 | 1,22 | 0,87 | 0,86 |
| SNR 30 dB | 1,33 | **1,18** | 0,87 | 0,64 |
| SNR 20 dB | 1,46 | **1,28** | 0,89 | 0,69 |
| SNR 10 dB | 1,64 | **1,48** | 0,69 | 0,79 |
| SNR 5 dB | 1,73 | **1,55** | 0,70 | 0,90 |
| Bruit évolutif | 2,18 | **1,87** | 2,14 | 1,83 |

Régression sur le jeu propre : +0,05 dB, sous le seuil d'alerte de 0,25 dB fixé par l'audit. La confiance réduite est désormais signalée (12 cas sur 16 à 10 dB, 16 sur 16 à 5 dB) alors que la V2 ne signalait jamais rien.

### Effet sur les fichiers réels de l'utilisateur

Sur le couple perche/ADR en cours, la V2 demandait **+4,4 dB entre 3 et 6 kHz**. Après soustraction du fond, le besoin mesuré **change de signe** (−0,6 dB à 3–6 kHz, −3,1 dB à 6–12 kHz) : la V2 recopiait en partie le **souffle du plateau** dans le timbre de la voix. La perche de ce couple n'a que 19 trames de fond exploitables, le dialogue étant presque continu ; la confiance descend à 0,70 et la correction est réduite en conséquence.

Aucun bruit n'est ajouté, retiré ni recopié dans l'audio : ces mesures ne servent qu'à décider **quelles bandes méritent d'être corrigées**.

## Ce qui n'est pas prouvé

- **Aucune écoute** : ni comparaison en aveugle, ni évaluation perceptive. Le gain mesuré est spectral.
- **Aucun couple production/ADR réel** : la parole de test vient de la synthèse vocale Windows, en anglais, et les colorations sont des filtres connus.
- **Phrases différentes du même locuteur** : le seul cas testé utilise des phrases différentes, mais la dispersion entre plusieurs phrases n'a pas été quantifiée. C'est le risque principal de la méthode (§3.2).
- **Aucun test avec du bruit de plateau réel**, ni musique, ni voix superposées.
- Le couplage à la reverb signifie que la correction absorbe aussi les erreurs d'estimation de pièce : l'ablation « EQ sans contexte de pièce » n'a pas été mesurée.

## Suite proposée

Lot E : écoute sur du matériel réel, mesure de la dispersion entre phrases, ablation du contexte de pièce, puis réglage définitif des paramètres.
