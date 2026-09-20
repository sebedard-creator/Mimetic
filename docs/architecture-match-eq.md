# Mimetic — Recherche et architecture du bouton « Match EQ »

Version 1.0 — 19 septembre 2026.

Document de passation pour l'implémentation. Il complète `architecture.md` et tient compte de la bêta réellement présente dans le dépôt, notamment des décisions D5 à D10. Il ne prescrit pas un retour à PySide6 ou aux anciens onglets.

Livrable de cette étape : ce document uniquement. Aucun fichier applicatif n'a été modifié. Les propositions doivent encore être implémentées et évaluées sur du dialogue. Un contrôle numérique synthétique a été exécuté ; sa portée est précisée en §13.

## 1. Décision principale

Ajouter un bouton optionnel **Match EQ**, désactivé par défaut, dans l'interface web actuelle SOURCE / DESTINATION. Il calcule une **EQ statique de raccord** à partir des deux fichiers, puis l'applique à l'ADR avant de générer le dry corrigé et sa reverb.

Méthode retenue : comparaison de spectres moyens de parole, protégée contre le bruit, lissée en fréquence et régularisée, en tenant compte de la reverb déjà produite par Mimetic. Un FIR causal à phase minimale approximée applique la courbe à la fréquence originale de l'ADR. Aucun entraînement d'EQ, génération de voix ou appel cloud n'est nécessaire. Un petit détecteur de parole local est recommandé pour l'analyse.

L'objectif est de rapprocher la couleur de prise de son : grave de proximité, médium, présence, brillance. « Texture exacte » ne doit pas devenir une promesse : une EQ ne reproduit pas à elle seule diction, hauteur de voix, compression, saturation, bruit, mouvements de micro ni toute la structure des réflexions. Une autre performance peut rester différente malgré une bonne correction.

Hypothèse de travail, faute de précision supplémentaire : de préférence le même acteur, mais les phrases peuvent différer. Le mode général ne suppose aucun alignement temporel des prises. Un mode même réplique est prévu en évolution, pas exigé pour le premier bouton.

| Match EQ | Export principal | Utilisation dans le DAW |
|---|---|---|
| Désactivé | `ADR_REVERB.wav` comme aujourd'hui | Superposer à l'ADR original |
| Activé | `ADR_EQ_DRY.wav` + `ADR_EQ_REVERB.wav` + rapport commun | Remplacer l'ADR original par EQ_DRY, puis superposer EQ_REVERB |

EQ_DRY est sans ajout de reverb ; celle éventuellement présente dans l'ADR n'est pas supprimée. Le wet reste constitué des réflexions générées. Ne pas superposer EQ_DRY à l'original : cela doublerait la voix.

## 2. État du dépôt et intégration

Inspection sur HEAD `c690c6b`, avec des modifications locales préexistantes dans README, serveur, interface et tests. Les préserver. Relire les fonctions concernées au moment de coder si le dépôt a évolué.

| Élément actuel | Conséquence pour Match EQ |
|---|---|
| FastAPI + JS/CSS, une session partagée | Étendre l'existant, aucun changement de framework |
| `Project._run()` analyse puis rend | Insérer l'EQ après le profil de pièce, seulement si activée |
| `RoomProfile`, `profile_ir_and_info()` | Réutiliser exactement l'IR de rendu, extension HF et graine incluses |
| `pipeline.render()` → `dsp.render_wet()` | Préparer l'ADR corrigé avant la convolution wet |
| `RenderResult.dry` et `.wet` de même longueur | Maintenir ce contrat pour l'écoute et la somme |
| `/api/pcm/dry` et `/api/pcm/wet` chargés séparément | Versionner les deux flux contre le mélange de révisions |
| `dependency_key()` sans EQ | Ajouter une branche de dépendances EQ |
| Export actuel wet + JSON, IR optionnelle | Exporter un ensemble dry/wet/report quand EQ est actif |
| Python 3.11, NumPy 2.2.6, SciPy 1.13.1 | Concevoir pour ces versions, sans mise à jour générale |
| Rec-RIR CPU dans `.venv-engine` | Aucun nouvel appel Rec-RIR pour déplacer l'intensité EQ |
| Tout sous le projet | Modèle VAD, caches et rapports respectent cette convention |

L'EQ analyse les WAV pleine bande, pas la copie à 16 kHz destinée à Rec-RIR. Elle peut donc estimer des différences au-dessus de 8 kHz. Cependant, la reverb du baseline au-dessus de 7,8 kHz est synthétique ; cela réduit la confiance du raccord dans cette zone.

Les résultats de validation antérieurs de la bêta n'ont pas été réexécutés pour cette étude.

## 3. Recherche et comparaison des approches

### 3.1 Résultats utiles

**Germain, Mysore, Fujioka — ICASSP 2016, Equalization Matching of Speech Recordings in Real-World Environments.** Traite directement du raccord de parole. Séparer parole et bruit permet de les corriger différemment [R1]. Conséquence : ne pas apprendre le timbre de voix sur le spectre brut incluant ventilateur, trafic ou room tone. Notre sélection/pondération statistique n'est pas une reproduction de leur système complet de séparation et recombinaison.

**Carbonneau et al. — SSW 2025, Analyzing and Improving Speaker Similarity Assessment in Speech Synthesis.** Emploie un ratio de densités spectrales de puissance puis un FIR à 16 bandes pour réduire un biais de coloration. Les auteurs montrent aussi la sensibilité de certaines mesures de similarité du locuteur à l'EQ et au bruit [R2]. Un point de départ spectral explicite est pertinent ; un score d'identité ne suffit pas à évaluer le raccord ADR.

**Su, Jin, Finkelstein — ICASSP 2020, Acoustic Matching by Embedding Impulse Responses.** Apprend une représentation de l'environnement puis une transformation waveform-to-waveform [R3]. Pertinent pour le transfert acoustique global. Pour ce bouton, remplacer le signal par une sortie neuronale compliquerait la séparation timbre/voix/reverb : non retenu en V1.

**Moliner et al. — Automatic Audio Equalization with Semantic Embeddings, AES 2025 ; prépublication arXiv 2026.** Utilise CLAP et une tête apprise pour prédire une cible spectrale d'égalisation aveugle [R4]. Vise une reconstruction de balance à partir d'un signal dégradé ; ce n'est pas directement un moteur prêt à copier une prise de tournage arbitraire. Aucun ensemble code/poids utilisable n'a été vérifié ici. Piste future si les différences de contenu limitent réellement la méthode explicite.

**Neural-Driven Multi-Band Processing — DAFx 2025.** Associe EQ et dynamique pour le transfert de style [R5]. Intéressant si l'on souhaite copier la compression. Match EQ doit ici garder la dynamique de jeu ; ce moteur n'est pas une dépendance initiale.

**SpectralBalance et Dialogue Match.** Leurs documentations distinguent EQ et reverb ; Dialogue Match traite aussi l'ambiance. SpectralBalance propose une EQ fixe pour la postproduction [R6, R7]. Références d'usage, pas divulgations de leurs algorithmes propriétaires.

### 3.2 Décision

| Approche | Avantage | Limite | Choix |
|---|---|---|---|
| Ratio FFT brut référence/ADR | Simple | Harmoniques, phonèmes, bruit et reverb confondus | Baseline de benchmark uniquement |
| Spectres de parole lissés, correction bornée | Explicable, CPU, pleine bande | Biais sur phrases courtes ou voix différentes | Base V1 |
| Comparaison au mélange virtuel ADR + pièce actuelle | Prend en compte la coloration déjà rendue | Dépend du profil de pièce | Couplage retenu, à valider par ablation |
| Alignement même réplique | Réduit le biais de contenu | Mauvais alignement possible | V1.1 avec repli automatique |
| Déréverbération de référence avant analyse EQ | Peut isoler le direct | Artefacts et biais de restauration | Comparaison de recherche, pas défaut V1 |
| EQ neuronale / transfert de style | Invariances apprises possibles | Poids, domaine, coût, dynamique modifiée | Différé jusqu'à preuve d'un gain utile |

Cette recommandation est adaptée à la bêta et à son matériel ; aucune supériorité universelle n'est démontrée. L'intégration proposée compare la référence au résultat sans EQ déjà contextualisé par Mimetic, puis corrige les deux branches ensemble. Les publications citées ne valident pas cette combinaison particulière.

## 4. Éviter une double correction de la pièce

```text
x       = ADR original, canal sélectionné
h       = IR wet exacte utilisée par le renderer, au fs de l'ADR
v       = gain wet linéaire actuel
D(h)    = h avec le pré-délai supplémentaire actuel
h_eff   = v × D(h)
k_room  = delta + h_eff
y0      = x * k_room                # mélange virtuel sans EQ
r       = référence de production
q       = filtre EQ appris, causal
a       = gain commun de conservation du niveau ADR
x_eq    = a × (x * q)
w_eq    = x_eq * h_eff
mix_eq  = pad(x_eq) + w_eq
```

`*` signifie convolution linéaire complète. Obtenir `h_eff` par le même code que le rendu final, génération HF, délais et gain inclus. `delta + h_eff` est une somme alignée à l'échantillon zéro.

Estimer la correction depuis `r` et `y0`, et non depuis `r` et `x` seuls. Pour des filtres fixes linéaires :

```text
(x * q) * (delta + h_eff) = q * (x * (delta + h_eff)) = q * y0
```

L'EQ vise donc la différence restante entre référence et mélange sans EQ. Au rendu, appliquer une seule fois `q` à l'ADR, puis produire la reverb depuis cet ADR corrigé.

Cette égalité est exacte dans le modèle LTI. L'estimation par spectres de parole fenêtrée reste approximative. Une IR erronée, un micro mobile ou une autre position de bouche peuvent invalider l'interprétation physique d'un filtre fixe.

Interdictions d'implémentation :

- Apprendre une EQ globale sur la référence réverbérée puis ajouter la reverb sans traiter la double coloration.
- Diviser par la FFT complexe de la pièce pour obtenir une prétendue EQ micro : phases et creux rendent l'inversion fragile.
- Soustraire les puissances dry et mix pour isoler le wet : il existe des termes croisés.
- Appliquer `q` à `RoomProfile.wet_ir` puis encore à l'ADR : double correction.
- Renormaliser indépendamment le wet après EQ pour rétablir un RMS arbitraire.

## 5. Pipeline d'analyse de parole

### 5.1 Sélection et VAD

Le seuil d'énergie actuel de Rec-RIR n'est pas un détecteur de parole. Ne pas changer la sélection de pièce en même temps que cette extension.

Pour l'EQ, ajouter un adaptateur **Silero VAD ONNX**, local et épinglé, utilisant les E/S SoundFile existantes [R8]. Tester une version d'ONNX Runtime compatible avec le NumPy installé. Ne pas importer une pile CUDA/torchaudio ni télécharger via `torch.hub` à chaque lancement. Stocker poids, provenance, licence et SHA-256 sous `third_party/` ; installation explicite par le setup.

Une copie à 16 kHz sert seulement à détecter la parole. L'EQ utilise les WAV pleine bande. Convertir les timestamps vers les indices de chaque fréquence native. Respecter blocs/états/contexte du modèle ONNX choisi ; réinitialiser son état entre fichiers.

Politique initiale :

1. Référence : partir des fenêtres de `RoomProfile.parameters["windows"]` pour apprendre la situation acoustique déjà retenue. Lire leur schéma réel dans l'adaptateur.
2. Conserver au plus 30 s de contexte, visant 5–15 s de parole active. Ne pas chercher de meilleures statistiques dans une autre scène du fichier.
3. ADR : jusqu'à 30 s représentatives, réparties dans le fichier, pas uniquement les syllabes les plus fortes. Le filtre final s'applique à toute la destination.
4. Minimum initial : 2 s de parole exploitable de chaque côté ; entre 2 et 5 s, correction prudente. Sous 2 s : « extrait trop court », pas de profil automatique déclaré fiable.
5. Une destination comportant plusieurs voix ou acoustiques doit être traitée en clips homogènes. La V1 ne fait pas de diarisation.

Ne pas concaténer les bouts de parole avant STFT. Calculer les statistiques dans les contextes d'origine. Pour un extrait de `y0`, inclure le contexte ADR précédent d'au moins la longueur de `h_eff`, puis analyser seulement la zone centrale : autrement la reverb au début du passage manquerait.

Si le modèle VAD manque, le bouton EQ indique la dépendance manquante ; la reverb continue de fonctionner. Le seuil d'énergie peut servir aux tests synthétiques ou à un mode explicitement dégradé, jamais être présenté comme une détection vocale équivalente.

### 5.2 Parole et bruit ne sont pas la même cible

Le VAD ne sépare pas le bruit sous la parole. Pondérer les trames par la fiabilité vocale et écarter les transitoires aberrants/écrêtages détectés.

Estimer le bruit sur des pauses éloignées de la parole d'au moins la durée de queue estimée plus 200 ms. Si la queue est tronquée ou si aucune pause adaptée n'existe : `noise_estimate = unknown`. Ne pas appeler une queue réverbérée « bruit pur ».

Pour les bandes exploitables :

```text
P_speech(b) = max(P_active(b) - P_noise(b), floor_relative)
```

Cette soustraction s'applique uniquement aux statistiques. Aucun masque de débruitage n'atteint l'audio exporté. Une bande où presque tout a été soustrait reçoit une confiance nulle ; le plancher numérique ne crée pas du signal.

Pour `y0`, utiliser le masque vocal de l'ADR d'origine et un contexte de bruit cohérent avec le rendu. Ne pas compter les seules queues comme voix active. Si le bruit est inconnu, ne rien soustraire d'inventé : statut réduit et boosts limités. Musique ou autre voix superposée peuvent tromper le VAD ; une analyse trop contaminée doit pouvoir refuser l'EQ tout en gardant la reverb existante.

### 5.3 Statistiques spectrales

Analyser à la fréquence de l'ADR (44,1 ou 48 kHz). Convertir la copie de référence si nécessaire comme une **forme d'onde**, sans le facteur de gain employé lors du rééchantillonnage des coefficients d'IR.

Paramètres initiaux : Hann 2048 échantillons à 48 kHz, durée équivalente arrondie à 44,1 kHz, hop 10 ms, FFT 4096. Pas de préaccentuation vocale automatique. Un retrait de moyenne/DC sur la copie d'analyse ne doit pas devenir un filtre de tonalité sur le rendu.

Grille logarithmique : 1/12 d'octave entre 40 Hz et 16 kHz, dans la bande commune. Convertir les puissances FFT par noyaux positifs normalisés identiques des deux côtés. À basse fréquence, élargir les noyaux jusqu'à couvrir plusieurs bins effectivement résolus. Le zéro-padding n'ajoute pas de résolution.

Calculer des moyennes de **puissance linéaire**, pondérées par parole, sur blocs de 0,5 s. Borner le poids de chaque bloc et agréger robustement entre blocs, afin qu'un cri ou une fricative répétée ne gouverne pas tout le filtre. Méthode initiale reproductible : moyenne pondérée dans chaque bloc, puis moyenne tronquée de 10 % des blocs extrêmes par bande si au moins dix blocs valides ; sinon médiane avec statut faible effectif.

Pour comparer les formes, normaliser uniquement les statistiques de chaque bloc par son énergie vocale 200 Hz–5 kHz, sous réserve d'énergie suffisante. Effectuer la soustraction du bruit avant cette normalisation, dans les mêmes unités de puissance ; ne jamais soustraire un bruit brut à un spectre déjà normalisé. Conserver aussi les spectres non normalisés pour le diagnostic de bruit et de niveau. Aucun de ces gains d'analyse ne modifie les fichiers.

Stocker par bande puissance, nombre de blocs indépendants, dispersion, couverture, SNR disponible et provenance de la composante de pièce. Exclure padding et absence de parole. Les paramètres de normalisation/agrégation font partie de la version d'algorithme et doivent être testés sur voyelles, fricatives et phrases différentes.

## 6. Courbe d'EQ stable et réglages

### 6.1 Ratio et séparation du niveau

Depuis `P_ref` et `P_base` (spectres de parole robustes, baseline = `y0`) :

```text
d_raw(b) = 10 log10(P_ref(b) + eps_ref) - 10 log10(P_base(b) + eps_base)
c        = médiane pondérée de d_raw sur les bandes fiables 200 Hz–5 kHz
d(b)     = d_raw(b) - c
```

`d` est un gain d'amplitude en dB : facteur `10^(d/20)`. Sur des **puissances**, la différence emploie 10 log10, pas 20 log10. Un changement global de niveau ne doit pas devenir une EQ.

Epsilons relatifs à la puissance valide de chaque signal ; tester l'invariance à -20 dB. Si la zone d'ancrage manque de bandes fiables, échouer proprement.

### 6.2 Poids et plages

Les poids `w_b` combinent couverture, SNR et stabilité entre blocs. Ce sont des heuristiques, pas des probabilités de réussite. Départ : poids de bruit nul sous 6 dB de SNR estimé, rampe vers 1 à 18 dB ; jamais de boost sans énergie ADR mesurable. Avec bruit inconnu : plafond de boost +3 dB et statut réduit.

Au-dessus de 7,8 kHz, si le contexte utilise la reverb synthétique, multiplier le poids par au plus 0,5 et limiter le boost à +3 dB. Cela concerne l'incertitude de la pièce, pas une prétendue absence de voix directe dans cette bande.

Zone principale : 80 Hz–12 kHz ; retour continu vers zéro entre 40–80 Hz et 12–16 kHz. DC et Nyquist : 0 dB de courbe spectrale avant gain scalaire. Aucun boost hors bande commune ou dans une bande sans signal.

### 6.3 Régularisation

Résoudre sur la grille logarithmique un petit système de moindres carrés bornés :

```text
min_g mean(w × (g-d)^2)
      + lambda_s × mean((D2 g)^2)
      + mean(lambda_0(b) × g(b)^2)
D2 g = g[b-1] - 2g[b] + g[b+1]
```

Convention : pas de division par le pas dans `D2`. Grille et conventions versionnées. Les bandes peu fiables ont une pénalité vers zéro plus forte. Fixer les bandes externes à zéro ou utiliser une transition continue dont l'effet final est vérifié.

Point de départ à évaluer : `lambda_s = 20`, `lambda_0 = 0.05 + 2*(1-w)`, avec poids dans [0,1]. Valeurs propres au projet, pas des résultats publiés. Les comparer à des courbes de lissage effectif voisin de 1/3 et 1/2 octave, puis figer les paramètres après benchmark.

Assembler les termes en système augmenté avec leurs normalisations et utiliser `scipy.optimize.lsq_linear` [R9]. Bornes initiales : -9/+6 dB ; confiance réduite : -6/+3 dB. Imposer une borne supérieure de 0 dB aux bandes sans énergie ADR exploitable : un poids nul seul n'interdit pas une hausse propagée par le lissage. Pour les bandes fixées exactement à zéro, éliminer les variables correspondantes du système plutôt que passer des bornes égales à un solveur qui exige des intervalles stricts. Mesurer la courbe après toutes les transitions/interpolations. Si elle touche les limites, signaler « correction limitée » ; ne pas élargir automatiquement.

Pas de notches étroits pour suivre chaque harmonique. Enregistrer la proportion de bandes saturées et le désaccord entre deux moitiés temporelles indépendantes de l'analyse.

### 6.4 Intensité et conservation de niveau

Intensité `alpha ∈ [0,1]` : `g_alpha = alpha*g`, puis concevoir le filtre. Ne pas interpoler par `x + alpha*(EQ(x)-x)` : mélange de phases et interpolation en amplitude différents du résultat en dB souhaité.

Valeur initiale : 100 % de la courbe déjà bornée/régularisée. Si l'analyse échoue, aucune grande correction « par défaut ».

Gain commun de conservation du niveau de l'ADR :

```text
a_db = clip(10 log10(E_active(x)/E_active(x*q)), -6, +6)
a    = 10^(a_db/20)
```

Mesurer l'énergie sur le même masque de parole figé, sans padding final. Protéger les énergies quasi nulles. C'est une compensation RMS de parole, pas un matching LUFS ni une copie du niveau de la production. Appliquer ce gain une fois à l'entrée commune dry/wet, le stocker dans le rapport et l'afficher dans les détails. Plafond atteint = avertissement.

Décision V1 : compensation activée. Bypass ou intensité zéro force `q=[1]`, `a=1` et emprunte exactement le chemin historique : aucune correction cachée ne subsiste.

## 7. Filtre audio et synchronisation

### 7.1 FIR causal à phase minimale approximée

L'EQ est fixe sur tout le fichier. Un FIR à phase linéaire compensé peut produire du pré-écho avant une attaque ; retenir une construction causale par cepstre réel puis troncature contrôlée.

Recette testable :

1. Interpoler continûment `g_alpha` sur `rfftfreq(N, 1/fs)`, avec `N=32768` au départ et les transitions vers 0 dB.
2. `L = g_alpha*ln(10)/20` puis `c = irfft(L, n=N)`.
3. Cepstre causal : conserver `c[0]` et `c[N/2]`, doubler `c[1:N/2]`, mettre la seconde moitié à zéro.
4. `q_periodic = irfft(exp(rfft(c_causal)), n=N)`.
5. Prendre 2048 coefficients, puis 4096 si nécessaire ; fondu sur les derniers 128 seulement. Aucun recentrage, fenêtre d'attaque, normalisation au pic ou normalisation de somme.
6. Mesurer la réponse FFT sur une grille dense. Objectifs : erreur <= 0,25 dB dans les bandes fiables et dépassement des bornes <= 0,2 dB. Augmenter au besoin jusqu'à 8192 coefficients/FFT 65536, puis refuser si ces objectifs restent impossibles.
7. Vérifier énergie tronquée et repli circulaire. Une troncature est une approximation ; ne pas revendiquer la position exacte de tous les zéros sans vérification.

Courbe nulle : `[1.0]` exact. Toute valeur non finie invalide le filtre.

**Piège SciPy 1.13.1 :** `signal.minimum_phase` produit une magnitude approximativement égale à la racine carrée de la magnitude initiale [R10]. Lui passer directement une FIR à la courbe voulue puis supposer le gain conservé serait incorrect. La recette précédente part de la log-magnitude désirée et évite cette confusion. Ne pas copier une signature récente incompatible avec la version installée.

Des biquads seraient possibles [R11], mais exigeraient un ajustement supplémentaire des fréquences/Q/gains. Ne pas développer deux renderers EQ en V1.

### 7.2 Longueurs

Avec `N=len(x)`, `L=len(q)`, `M=len(h_eff)` :

```text
len(x_eq_non_padded) = N+L-1
len(w_eq)           = N+L+M-2
len(dry_for_mix)    = len(w_eq)
```

Les deux WAV EQ exportés ont cette même dernière longueur et la fréquence ADR. La courte queue EQ est conservée. Tous les débuts correspondent à l'échantillon zéro de l'ADR.

La phase minimale implique un délai de groupe dépendant de la fréquence ; il ne faut ni supprimer le début du filtre ni réaligner les pics par corrélation. Les deux stems dérivent du même `x_eq` et restent cohérents. Conserver les zéros initiaux exacts comme dans `render_wet`, sans laisser la FFT introduire une trace numérique avant le premier son.

### 7.3 Mémoire

Convolution overlap-add complète. Filtrer d'abord `x*q`, puis appeler la convolution wet existante ; `RoomProfile` reste inchangé. Compter original, corrigé, wet, baseline, sommes temporaires et FFT dans le budget mémoire.

N'analyser qu'au plus 30 s de baseline contextualisé, libérer ces buffers avant le rendu complet, construire le mix à la demande. Ne pas garder toutes les variantes de dix minutes en float64. Aucun besoin de relancer Rec-RIR.

## 8. Export et préécoute

### 8.1 Ensemble cohérent

EQ désactivé : conserver valeurs, noms et comportement actuels. Activé et prêt : un clic produit un ensemble associé à un `render_id` :

```text
prise_ADR_EQ_DRY.wav
prise_ADR_EQ_REVERB.wav
prise_ADR_EQ_REPORT.json
```

Même snapshot immuable et suffixe commun en cas de collision. Écrire en temporaire, vérifier les trois fichiers, puis publier le manifeste et les liens seulement lorsque l'ensemble est complet. Aucun écrasement de fichiers source.

WAV mono 32 bits flottants, sans limiteur ni normalisation séparée. Pics dry/wet/mix, y compris >0 dBFS, dans le rapport. L'auto-gain explicite du §6.4 est le seul ajustement automatique prévu.

Le JSON associe hashes audio, canal, profil de pièce, réglages wet, EQ, intensité, gain commun, longueurs, versions et limites. Placement : **« Remplacer l'ADR original par EQ_DRY et ajouter EQ_REVERB au même début. »**

Ne pas exporter par défaut un résidu `EQ(x)-x` à superposer au clean. Cette reconstruction par annulation de phase serait fragile aux gains, décalages et traitements ultérieurs ; ce n'est pas une reverb wet.

L'export facultatif PROFILE_WET_IR reste le profil de pièce sans EQ. Il ne doit pas incorporer silencieusement un filtre appris pour une paire acteur/ADR. Un futur export de filtre EQ séparé porterait son propre manifeste et sa propre destination.

### 8.2 Écoute

Modes : original, ADR corrigé, reverb seule, résultat ; même position temporelle pour les variantes ADR. SOURCE garde son transport propre.

`RenderResult.dry` devient la branche directe du rendu courant : original en bypass, corrigé quand EQ est actif. Ajouter un flux `original` explicite et adapter les libellés ; ne jamais appeler « original » l'audio corrigé.

Gain de monitoring commun indépendant de l'export. Pas de normalisation séparée des buffers dans le navigateur. La compensation RMS n'est pas une garantie de niveau perceptif identique ; un réglage d'écoute additionnel éventuel reste identifié et hors fichier.

### 8.3 Course entre téléchargements

Le navigateur charge actuellement dry et wet séparément. Une modification de réglages entre les deux peut mélanger les révisions. Ajouter `render_id` à l'état et aux réponses PCM, et demander par exemple `/api/pcm/dry?render_id=...` / `/api/pcm/wet?render_id=...`.

409 si le rendu demandé n'est plus disponible. Vérifier identifiant, fréquence et longueur avant de publier les nouveaux buffers. Garder l'ancien ensemble cohérent ou arrêter la lecture pendant le chargement ; ne remplacer aucun stem isolément. `Cache-Control: no-store` ne suffit pas à résoudre ce problème.

## 9. Contrats de données et responsabilités

Conserver `RenderSettings` pour la reverb et ajouter `EqSettings` séparément. Les objets de résultat sont des snapshots : aucune modification en place d'un tableau partagé par un job, une écoute et un export.

### 9.1 Objets proposés

| Objet | Contenu |
|---|---|
| `EqSettings` | `enabled: bool=False`, `amount: float=1.0`, `preserve_adr_level: bool=True`, version de politique |
| `SpeechStats` | hash/canal/régions, fréquence, grille de bandes, puissances, poids, dispersion, VAD et bruit, version |
| `EqProfile` | courbe à 100 %, bandes/confiance, dépendances de la paire et du baseline, méthode, diagnostics |
| `EqFilter` | coefficients numériques `q`, fs, intensité, hash, erreur de réalisation, longueur, `a_db` appliqué |
| `RenderResult` étendu | dry/wet actuels, métadonnées EQ facultatives, longueur ADR initiale, `render_id`, pics et clé |

`EqProfile` est lié à **une paire** source/destination et à son contexte de rendu ; il n'est pas un attribut universel de la pièce. Réutiliser aveuglément la même correction sur une autre voix ou une autre prise ADR est incorrect.

Séparer trois identifiants :

```text
analysis_key = hash(source + destination + canaux + régions
                    + profil de pièce/IR rendue + wet_gain + predelay
                    + versions VAD/statistiques/politique)
filter_key   = hash(EqProfile + fs + amount + preserve_level
                    + version conception FIR + identité ADR)
render_key   = hash(dépendances actuelles + enabled + filter_key + version DSP)
```

Hasher les tableaux de courbe/coefficients et les paramètres sérialisés canoniquement. Ne pas utiliser seulement un UUID de profil ou l'arrondi des valeurs affichées. Inclure le hash de l'IR de contexte et ses paramètres HF/graines : un changement réel de ce filtre doit invalider la calibration EQ.

Courbe nulle ou mode désactivé : identité explicite, aucune empreinte d'un ancien filtre actif réutilisée par accident. Conserver une branche historique dans le renderer pour garantir le bypass numérique ; les versions de rapport peuvent évoluer, les échantillons doivent rester identiques.

### 9.2 Manifeste indicatif

Exemple de structure, pas un résultat mesuré ; les tableaux seraient complets dans un vrai export :

```json
{
  "schema_version": 1,
  "method": "speech-ltas-room-aware-v1",
  "status": "proposed_not_validated",
  "analysis_key": null,
  "reference_sha256": null,
  "destination_sha256": null,
  "reference_channel": "mono",
  "destination_channel": "mono",
  "regions": {"reference": [], "destination": []},
  "room_context": {
    "profile_id": null,
    "render_ir_sha256": null,
    "wet_gain_db": 0.0,
    "additional_predelay_seconds": 0.0,
    "hf_synthesized": true
  },
  "curve": {"frequencies_hz": [], "gain_db": [], "weights": []},
  "vad": {"id": "silero_onnx", "revision": null, "weights_sha256": null},
  "noise_status": "unknown",
  "amount": 1.0,
  "filter": {
    "type": "causal_fir_cepstral",
    "sample_rate_hz": 48000,
    "coefficient_count": null,
    "coefficients_sha256": null,
    "fit_error_max_db": null
  },
  "preserve_adr_level": true,
  "applied_common_gain_db": null,
  "warnings": []
}
```

Dans le vrai export, les informations nécessaires à la reproductibilité ne peuvent pas rester `null`. Une valeur inconnue de diagnostic peut le rester avec motif explicite. JSON interdit NaN/Inf. Un stockage `.npy` ne charge que du numérique, `allow_pickle=False`. Un export de profil ne charge jamais du code ou un pickle tiers.

### 9.3 Fonctions conceptuelles

```text
detect_speech(asset, channel, regions) -> SpeechMask
measure_speech_stats(audio, fs, mask, regions, policy) -> SpeechStats
build_baseline(adr, room_profile, render_settings, regions) -> audio + context
estimate_eq(reference_stats, baseline_stats, context) -> EqProfile
design_eq_filter(eq_profile, fs, amount) -> EqFilter
apply_eq(adr_signal, eq_filter, level_policy, mask) -> corrected_signal + gain
render(..., eq_settings=None, eq_profile=None) -> RenderResult
export(render_snapshot, ...) -> bundle_manifest
```

Ne pas placer une référence aux assets dans les fonctions DSP pures. Le module statistique peut traiter des tableaux ; l'orchestrateur apporte les fichiers, canaux et régions. Le frontend affiche l'état et les contrôles, il ne calcule pas une autre courbe EQ en JavaScript.

Le champ `a_db` appartient au filtre appliqué à une destination précise : ne pas le réutiliser comme gain universel lors de l'emploi de la même courbe sur une autre phrase. Le hash final du filtre/rendu inclut le gain réellement appliqué, pas seulement le booléen de conservation de niveau.

## 10. Ordonnancement, invalidation et API

### 10.1 Séquence automatique

```text
Deux fichiers présents
    -> profil de pièce valide ? sinon analyse Rec-RIR existante
    -> Match EQ activé ?
         non : rendu historique
         oui : statistiques parole -> baseline -> EqProfile -> filtre -> rendu
    -> écoute/export quand toutes les dépendances sont encore courantes
```

Ajouter `eq_profile`, `eq_profile_deps`, `eq_settings` et l'état EQ à `Project`. `kick()` et `_work_key()` doivent intégrer le travail EQ pour ne pas considérer un ancien wet comme prêt. La boucle conserve une étape lourde à la fois.

La construction du baseline appelle un primitive de rendu **sans EQ**. Elle ne doit pas appeler récursivement l'orchestrateur automatique ou utiliser le rendu déjà égalisé. Les réglages d'analyse ne dépendent pas du résultat corrigé ; pas de boucle de recalibration sans limite.

| Changement | Recalcul nécessaire |
|---|---|
| SOURCE/canal SOURCE | Pièce, statistiques source, EQ, rendu |
| DESTINATION/canal DESTINATION | Statistiques ADR, baseline, EQ, rendu ; conserver la pièce |
| Gain wet/pré-délai | Baseline et courbe EQ si actif, puis rendu ; pas de Rec-RIR |
| Amount | Filtre et gain de conservation, rendu ; pas de nouvelles statistiques |
| Bouton EQ OFF | Rendu historique ; profil EQ éventuellement gardé en cache |
| Bouton EQ ON | Réutiliser seulement un profil dont toutes les dépendances concordent |
| Volume de monitoring/position lecture | Aucun recalcul audio exporté |
| Version de modèle VAD/politique | Statistiques et EQ, pas nécessairement la pièce |

La correction tient compte du dosage wet : changer ce dosage peut donc aussi faire changer légèrement la courbe proposée pour garder la cible tonale. C'est le comportement explicite retenu. Un futur mode « figer la courbe » serait une option différente ; ne pas le simuler en oubliant d'invalider le profil.

Débouncer les curseurs comme aujourd'hui, puis ignorer tout résultat d'une ancienne clé. Pendant l'analyse, un clic OFF doit annuler/écarter le travail EQ et revenir au résultat sans EQ. Aucun résultat tardif ne réactive le bouton.

### 10.2 API minimale

- `POST /api/eq/settings` : validation stricte de `enabled`, `amount ∈ [0,1]`, `preserve_adr_level`. Rejeter NaN, infinis, types ambigus et champs non reconnus.
- `GET /api/state` : ajouter bloc `eq` avec `enabled`, `state`, `amount`, `profile_valid`, courbe d'affichage réduite, gain appliqué, diagnostics et clés.
- `GET /api/pcm/original?render_id=...` : canal ADR original paddé à la longueur de ce rendu ; contrôler son appartenance au snapshot.
- PCM dry/wet : même protocole de version que §8.3.
- `POST /api/export` : exporter un snapshot complet du rendu demandé ; mode EQ actif exige les deux stems. Si révision obsolète, 409.

Conserver les routes existantes pour les anciens clients lorsque possible. `POST /api/settings` pour la reverb ne doit pas remettre implicitement l'EQ à sa valeur par défaut.

### 10.3 Erreurs sans casser la reverb

| Code | Réponse |
|---|---|
| `EQ_VAD_UNAVAILABLE` | Expliquer la dépendance ; reverb seule toujours possible |
| `EQ_INSUFFICIENT_SPEECH` | Demander un extrait plus exploitable, pas de courbe fictive |
| `EQ_NO_RELIABLE_BANDS` | Refuser l'application automatique |
| `EQ_REFERENCE_CONTAMINATED` | Proposition réduite ou refus selon couverture ; détailler le motif |
| `EQ_LOW_CONFIDENCE` | Afficher la limite ; courbe bornée plus prudemment |
| `EQ_CORRECTION_LIMITED` | Courbe aux plafonds, possibilité de réduire l'intensité |
| `EQ_FILTER_FIT_FAILED` | Ne pas publier un filtre ne respectant pas sa cible |
| `EQ_ROOM_CONTEXT_UNCERTAIN` | Reverb tronquée/HF synthétique, signaler la dépendance |
| `EQ_STALE` | Pas d'export du couple dry/wet obsolète |

Un échec EQ ne doit pas détruire le profil de pièce réussi. S'il reste un rendu sans EQ valide, l'utilisateur peut revenir à OFF. Ne pas laisser ON tout en servant discrètement ce rendu sans correction.

## 11. Interface attendue

Un bouton bascule **Match EQ** dans la zone de résultat, sans nouvelle page ni nouveau fichier à importer. Premier clic : analyse puis activation si réussie. Afficher « Analyse du timbre… » pendant le calcul. Une fois prêt : intensité 0–100 %, comparaison avant/après et mention courte « Export : ADR corrigé + reverb ».

La courbe et le gain commun se trouvent dans un panneau de détails repliable ; ne pas exposer VAD, lambda, FFT ou bandes de confiance dans le parcours principal. Les paramètres techniques restent versionnés et fixés pour cette version.

États distincts : désactivé, en analyse, actif, à recalculer, non disponible/échec. Ne pas afficher un score « 98 % identique ». L'utilisateur décide du raccord à l'écoute, assisté de diagnostics compréhensibles.

Sur la zone export, rappeler seulement quand EQ est actif : « Utiliser les deux fichiers : l'ADR corrigé remplace l'original. » La reverb seule conserve exactement son usage actuel en mode OFF.

## 12. Plan de validation

### 12.1 Tests DSP bloquants

1. **Bypass** : avec OFF et amount zéro, tableaux dry/wet identiques au chemin historique, mêmes longueurs et pics. Anciennes fonctions CLI inchangées sans option EQ.
2. **Identité spectrale** : référence égale au baseline (pas nécessairement à l'ADR sec) → courbe proche de zéro ; aucune correction fabriquée depuis le bruit numérique. Pour `h=0` et fichiers identiques, identité complète.
3. **Niveau seul** : référence multipliée par un scalaire, sans clipping → forme de courbe inchangée ; aucun nivellement de la voix vers la référence.
4. **Courbe connue** : vérifier gains, signe et facteur 10/20 log10 avec filtres larges connus. Quantifier la différence entre courbe cible et FIR réalisée.
5. **Intensité** : courbe à 50 % = moitié en dB avant réalisation ; amount zéro = identité exacte, sans queue artificielle ni gain.
6. **Bornes et stabilité** : aucune valeur non finie ni suramplification hors limites ; bande sans information ne reçoit pas un boost à cause d'un epsilon.
7. **Temps** : impulsion et attaque après silence, absence d'énergie significative avant l'entrée, origine conservée, queue EQ complète et longueurs §7.
8. **Routage** : `dry_eq + wet_eq` égale `a*q*y0` à tolérance flottante ; wet calculé depuis le dry corrigé une seule fois, gain wet/pré-délai une seule fois.
9. **Dosage** : aucun auto-gain individuel du wet ; le gain commun est identique sur les deux branches. Le DRR énergétique peut varier avec la pondération fréquentielle de l'EQ, sans recalibration arbitraire.
10. **Bruit** : ajouter du bruit de pause à la référence ne doit pas transformer une bande inaudible de voix en boost important ; afficher la confiance insuffisante si nécessaire.
11. **Fréquences** : source 44,1/destination 48 et inverse, avec gain correct et bande commune ; détection à 16 kHz n'ampute pas les stats audio pleine bande.
12. **Courbes extrêmes** : grave, shelf aigu, bosse médium, creux large ; fitter FIR mesuré après troncature, y compris à 44,1 kHz.
13. **Export** : dry/wet même identifiant, longueur, origine et fréquence, relus identiques aux buffers à la précision float32 ; aucun écrasement source.

Tolérance de référence pour les égalités de convolution float64 sur fixtures d'amplitude <=1 : erreur absolue <=1e-6. Fixer des tolérances propres aux mesures spectrales ; ne pas appliquer une tolérance échantillon à échantillon à deux prises humaines différentes.

### 12.2 Benchmark scientifique pertinent

Construire un corpus autorisé avec trois cas complémentaires :

- **Oracle de calcul** : même signal sec, filtres EQ/IR connus. Sert à tester facteurs, phase, routage et coloration de pièce. Trop facile pour conclure sur la qualité métier.
- **Même acteur, phrases différentes** : phrase A pour la référence, phrase B pour apprendre la paire ADR, puis phrase C non utilisée pour évaluer le filtre. Synthétiser une vérité terrain connue avec EQ et IR ; varier la distribution phonétique.
- **Vrais couples production/ADR** : même réplique et répliques différentes, voix chuchotée/parlée/projetée, perche/lav, bruit de fond et proximité. Pas d'IR ni d'EQ exacte présumée ; évaluation à l'écoute.

Périmètre de départ : au moins 6 voix et 12 situations de coloration/pièce, incluant du français ; séparer cas de réglage et cas tenus à l'écart. Couvrir des durées actives 2/5/15 s, SNR 5/15/30 dB et plusieurs réverbérations. Ce pilote n'est pas une certification universelle.

Comparer : bypass, ratio spectral brut, EQ robuste sans contexte de pièce, méthode proposée avec contexte, et éventuelle restauration de référence uniquement comme variante de recherche. Ne pas changer simultanément le profil de pièce entre variantes : l'ablation doit isoler le rôle de l'EQ.

Mesures : erreur de courbe large bande après retrait du gain constant sur vérité connue, stabilité entre phrases, énergie des zones de parole/bruit, réponse du filtre réalisé, pics et coût CPU/mémoire. Sur signaux de vérité terrain partageant la même performance, distance log-spectrale et métriques temporelles peuvent être utiles. Sur prises différentes, SI-SDR, PESQ ou comparaison de waveform ne mesurent pas directement le raccord.

Ne pas optimiser seulement un score de similarité de locuteur : il peut être perturbé par EQ/bruit [R2]. Vérifier diction, identité perçue, sifflantes, plosives et absence de voix métallique à l'écoute.

Écoutes randomisées avant/après, niveau de présentation contrôlé et phrases tenues à l'écart. Questions : le résultat s'approche-t-il de la couleur de production ? La voix reste-t-elle naturelle ? L'EQ ajoute-t-elle un défaut ? Distinguer préférence et similarité à la cible, qui ne sont pas la même chose.

Objectifs de projet provisoires, pas résultats acquis : diminuer l'erreur médiane de forme spectrale d'au moins 30 % face au bypass sur cas EQ connus ; aucun échec DSP ; sur pilote réel, préférence de raccord majoritaire sans hausse des défauts audibles. Publier distributions et échecs, pas seulement la moyenne. Enregistrer une référence d'écoute avant de choisir les seuils définitifs.

### 12.3 Tests web et concurrence

Compter les appels du faux estimateur de pièce existant : activer/désactiver EQ, déplacer amount ou wet gain ne relance pas Rec-RIR. Ajouter faux VAD et profils EQ connus aux tests, sans téléchargement de modèle.

Tester source changée pendant EQ, destination changée pendant rendu, OFF pendant analyse, erreur de filtre, cache périmé, exports successifs, et deux clients LAN. Vérifier qu'un téléchargement dry de révision A et wet de révision B ne peut jamais être publié comme mix. L'état `READY` exige la cohérence des deux stems et de l'EQ.

## 13. Contrôle numérique réalisé pour cette architecture

Un script temporaire en mémoire a été exécuté avec l'environnement `.venv` existant, sans modifier le moteur. Ce n'était ni un test sur voix ni une validation perceptive.

Conditions : 48 kHz, 12 s de bruit blanc, graine NumPy 81, EQ lisse connue (deux bosses larges +4/-3 dB avec retours à zéro aux extrémités), FIR cepstrale de 4096 coefficients obtenue sur FFT 32768. Pièce synthétique : bruit filtré passe-bas 1 kHz, décroissance exponentielle, 5 ms sans réflexion initiale et énergie wet 0,49. Référence construite par application de l'EQ au baseline, donc même réalisation du signal des deux côtés.

| Contrôle | Résultat observé |
|---|---|
| Erreur maximale de réalisation FIR, 100 Hz–12 kHz | Environ 0,00094 dB |
| Erreur de forme du ratio référence/baseline, après retrait gain constant | Environ 0,0035 dB RMS |
| Erreur du ratio naïf référence/ADR sec, même mesure | Environ 2,94 dB RMS |
| Différence maximale entre `q*(dry+wet)` et les stems calculés depuis `q*dry` | Environ 2,22e-16 |

Ce contrôle vérifie la convention de conception et la logique de double coloration dans un cas favorable. Il ne valide ni VAD, robustesse au bruit, différences phonétiques, choix des lambdas, même acteur/différents acteurs, qualité Rec-RIR, ni comportement perceptif réel. Les valeurs ne doivent pas être présentées comme performances du futur bouton. Transformer ce cas en test reproductible du dépôt lors du lot A ci-dessous, puis élargir fortement les conditions.

## 14. Plan de réalisation pour Sol ou Opus

### Lot A — Filtre et routage sans analyse automatique

Créer un module `audio/eq_filter.py` avec courbe connue, conception FIR, gain commun et application causale. Étendre le renderer avec EQ facultative en gardant le chemin OFF exact. Tester identité, intensité, temps, somme des stems et export corrigé. Aucune UI complète ni modèle neural EQ nécessaire à ce stade.

### Lot B — Statistiques de parole et baseline

Créer `analysis/speech_stats.py` et adaptateur VAD ; l'ajout du répertoire `analysis/` est nouveau dans cette bêta. Installation locale épinglée. Réutiliser la source complète pleine bande et ses fenêtres de pièce, sélectionner l'ADR, calculer le baseline sans EQ avec contexte, mesurer les statistiques et la confiance. Tests avec masques injectés et WAV autorisés.

### Lot C — Estimateur EQ et qualification

Créer `analysis/eq_match.py`, contrats dans `domain/models.py` ou module dédié, résoudre la courbe régularisée, mesurer le filtre réel, produire manifeste et erreurs. Comparer immédiatement aux baselines sur cas synthétiques et différentes phrases. Ne pas polir une interface autour d'un estimateur manifestement biaisé.

### Lot D — Bouton web, aperçu et export

Intégrer dans `web/server.py`, `web/static/app.js`, `index.html` et `app.css` en respectant les modifications locales présentes. Ajouter étapes/états/invalidation et l'identité des rendus PCM. Exporter dry/wet/report ensemble quand actif. Mettre à jour README pour expliquer le remplacement de l'ADR original.

### Lot E — Écoute sur matériel réel et livraison

Exécuter le benchmark, recueillir les cas de dégradation, ajuster une fois les paramètres sur le jeu de développement puis tester le jeu tenu à l'écart. Documenter les résultats dans `docs/eq-validation.md` et les décisions dans `docs/decisions.md`. Ajouter seulement les dépendances réellement testées ; pas de refonte du moteur de pièce.

Livraison réussie = bouton fonctionnel + amélioration audible sur cas pertinents + absence de régression OFF + fichiers correctement superposables. Une courbe dessinée ou une baisse de distance spectrale sur la phrase d'apprentissage ne suffit pas.

## 15. Évolutions après validation de la V1

### Même réplique : alignement d'analyse

Si les prises contiennent réellement les mêmes mots, un alignement DTW de caractéristiques de parole peut réduire les biais de phonèmes/durée [R12]. L'alignement sert uniquement à associer des régions pour estimer l'EQ. Il ne doit pas étirer, recaler ou resynthétiser l'audio de destination.

Construire des caractéristiques à faible sensibilité au niveau et à la coloration (par exemple MFCC avec normalisation temporelle pour l'alignement seulement), chemin monotone borné, pondération évitant de compter dix fois une voyelle allongée, exclusion des correspondances incertaines. Calculer ensuite la correction sur les spectres pleine bande originaux des paires retenues, pas sur les MFCC normalisés.

DTW trouve toujours un chemin dans sa matrice ; un chemin ne prouve pas que les textes correspondent. Exiger une déclaration « même réplique » ou une vérification de contenu, puis des critères de coût/couverture validés. En cas de doute, revenir à la méthode non alignée. Une reconnaissance phonétique multilingue peut améliorer cela plus tard, mais sa dépendance et son coût doivent être justifiés par un gain réel.

### Référence difficile et autres voix

Une référence très bruyante peut justifier une séparation de parole **sur copie d'analyse**, à comparer au simple masque statistique. Une déréverbération de référence peut faciliter un autre mode de mesure, mais change la cible ; il faut alors revoir le couplage à la reverb et ne pas appliquer deux compensations simultanément.

Pour une autre voix, une partie du spectre appartient à l'acteur. Proposer une correction plus large/faible, sans promettre une distinction parfaite voix/micro. Ne pas ajouter silencieusement conversion de voix ou modification de formants au bouton EQ.

EQ dynamique, compression et transfert de style sont des fonctions distinctes : elles changent la dynamique et la commutation avec la reverb n'est plus celle des filtres LTI. Elles demandent une nouvelle architecture, pas le remplacement discret du filtre fixe.

## 16. Sources et règles de passation

Sources publiques consultées les 18–19 septembre 2026. Les publications motivent certains principes ; les paramètres et l'assemblage proposés ici sont des choix de projet, non des performances attribuées aux auteurs.

- [R1 — Adobe Research, Germain et al., ICASSP 2016](https://research.adobe.com/publication/equalization-matching-of-speech-recordings-in-real-world-environments/) : parole et bruit ne se corrigent pas avec la même EQ.
- [R2 — Carbonneau et al., SSW 2025](https://www.isca-archive.org/ssw_2025/carbonneau25_ssw.pdf), [code officiel](https://github.com/ubisoft/ubisoft-laforge-spkrid) : ratio spectral/FIR et biais des métriques de locuteur.
- [R3 — Su et al., ICASSP 2020](https://pixl.cs.princeton.edu/pubs/Su_2020_AMB/) : transfert acoustique neuronal global.
- [R4 — Moliner et al., texte arXiv](https://arxiv.org/html/2607.23846v1), [version AES déposée à Aalto](https://acris.aalto.fi/ws/portalfiles/portal/198138463/Automatic_Audio_Equalization_with_Semantic_Embeddings.pdf) : prédiction de cible spectrale pour EQ aveugle.
- [R5 — Neural-Driven Multi-Band Processing, DAFx 2025](https://dafx.de/paper-archive/2025/DAFx25_paper_81.pdf) : EQ et dynamique apprises pour transfert de style.
- [R6 — Manuel SpectralBalance](https://www.accentize.com/products/SpectralBalanceManual.pdf) : usages statique/dynamique du matching tonal.
- [R7 — iZotope, utilisation de Dialogue Match](https://www.izotope.com/community/blog/how-to-use-dialogue-match) : distinction EQ, reverb et ambiance.
- [R8 — Silero VAD, dépôt officiel](https://github.com/snakers4/silero-vad) : détecteur local, options ONNX et fréquences d'analyse.
- [R9 — SciPy 1.13.1, lsq_linear](https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.optimize.lsq_linear.html) : résolution linéaire bornée.
- [R10 — SciPy 1.13.1, minimum_phase](https://docs.scipy.org/doc/scipy-1.13.1/reference/generated/scipy.signal.minimum_phase.html) : convention de magnitude et version de l'API.
- [R11 — W3C, Audio EQ Cookbook](https://www.w3.org/TR/audio-eq-cookbook/) : référence pour une éventuelle réalisation en biquads, non retenue ici.
- [R12 — AudioLabs/FMP, Dynamic Time Warping](https://www.audiolabs-erlangen.de/resources/MIR/FMP/C3/C3S2_DTWbasic.html) : alignement de séquences, distinct d'un recalage audio rendu.

Consignes au modèle chargé du code :

1. Lire ce fichier et les décisions récentes ; respecter la page web et le moteur qui fonctionnent déjà.
2. Commencer par le filtre/routage/test bypass, puis l'analyse statistique. Pas de refonte de Rec-RIR.
3. Ne jamais apprendre sur un rendu déjà égalisé ni exporter un wet recalculé avec un ancien dry.
4. Ne pas confondre normalisation statistique, gain RMS commun et volume de monitoring.
5. Conserver identité des fichiers, origine temporelle, pleine bande des WAV et provenance de la reverb synthétique.
6. Épingler VAD/runtime et documenter les licences réellement distribuées. Une page de recherche publique n'atteste pas la présence de poids réutilisables.
7. Si l'écoute invalide une hypothèse, documenter le cas et réviser le choix précisément. Ne pas masquer le problème avec de la compression, un débruitage ou une génération de voix non demandés.
