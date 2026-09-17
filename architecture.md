# Mimetic — Architecture du matching de réverbération pour ADR

Version : 1.0 — 16 septembre 2026.

Statut : architecture et cahier d'implémentation. Aucun moteur n'a encore été installé, exécuté ou validé dans ce projet. Les choix ci-dessous sont des décisions de conception, sauf lorsqu'une source est explicitement citée. Une publication prometteuse ne constitue pas une preuve de qualité sur du dialogue de tournage.

## 1. Objectif et contrat avec l'utilisateur

L'application comporte deux onglets, **REFERENCE** et **ADR**. L'utilisateur importe un dialogue de tournage dans REFERENCE et une voix de remplacement dans ADR. L'application estime l'acoustique de la référence et exporte un fichier `ADR_REVERB.wav` : la réverbération produite par l'ADR, sans ajout intentionnel de voix directe. Ce fichier se place sur une piste parallèle, exactement au même début que l'ADR original.

Le résultat recherché est un raccord perceptif convaincant. Il ne s'agit pas de promettre la récupération exacte de la réponse physique de la pièce. Les positions de la bouche et du micro font partie de l'acoustique à reproduire : un profil représente une situation de prise de son, pas une pièce universelle.

Le fichier wet contient les premières réflexions et la queue diffuse. Ce n'est ni l'IR elle-même, ni de la réverbération extraite de la référence, ni du room tone. Il doit suivre les mots et le rythme de l'ADR, même lorsque le texte de la référence est différent.

Décisions produit :

- Traitement local, hors ligne après installation des éventuels modèles. « Importer » ne signifie pas envoyer le dialogue à un service distant.
- Application autonome Windows en premier ; pas de plugin DAW dans cette version.
- Un fichier par onglet en première version. Les fichiers n'ont pas besoin d'avoir la même durée ni les mêmes mots.
- Rendu audio calculé hors temps réel ; lecture comparative après calcul.
- Priorité aux dialogues mono et au travail à 48 kHz. La bande passante réellement analysée doit rester visible.
- Reverb seule par défaut. Aucun EQ matching de la voix directe en V1.
- Export principal WAV 32 bits flottants, à la fréquence de l'ADR accepté, avec queue complète.
- Pas de normalisation, de compression ou de limiteur caché dans le rendu.
- Un réglage de niveau de reverb en dB permet de corriger le dosage proposé.

## 2. Périmètre et hypothèses

### 2.1 Entrées V1

Accepter WAV PCM 16/24 bits et WAV flottant 32 bits, 44,1 ou 48 kHz, mono ou deux canaux. Vérifier le décodage réel ; ne pas se fier à l'extension. Les autres fréquences et formats donnent un message explicite, sans conversion silencieuse. Les limites sont des décisions V1, pas des limites théoriques du moteur.

Pour une entrée deux canaux, l'utilisateur choisit le canal à analyser ou à traiter : gauche, droite, ou moyenne mono explicite. Ne jamais sommer automatiquement une perche et un lavalier, ou deux canaux en opposition de phase. L'export V1 est mono. L'interface affiche le canal retenu. Le support stéréo véritable viendra avec un modèle et un routage adaptés.

Limiter initialement chaque fichier à 10 minutes, la sélection de référence à 30 secondes et l'IR de rendu à 10 secondes. Détecter ces limites avant d'allouer les grands buffers. Ce sont des paramètres configurables et affichés si un fichier les dépasse ; ne jamais tronquer silencieusement.

Une sélection de référence de 5 à 20 secondes constitue une suggestion de départ, à ajuster aux contraintes réelles du modèle. Garder les pauses et fins de mots. L'ADR doit être aussi sec que possible : sa réverbération existante resterait audible et serait à nouveau convoluée. La V1 ne la supprime pas automatiquement.

### 2.2 Hors V1

EQ matching, débruitage automatique, déréverbération de l'ADR, room tone, alignement des performances, transcription, surround, stéréo inventée, traitement par lot, VST3/AAX/AU, entraînement massif et publication cloud.

Le moteur audio ne doit dépendre d'aucun modèle de langage ni abonnement à une API. Les modèles utilisés pour programmer le projet n'ont aucun rôle dans le traitement des fichiers de l'utilisateur.

### 2.3 Distinction avec l'EQ de Chameleon

La documentation de Chameleon décrit l'estimation de la décroissance fréquentielle de la reverb et du dosage dry/wet, ainsi que des commandes Bass/Treble et coupe-bas/coupe-haut. Elle ne décrit pas un matching indépendant de l'EQ de la voix directe [S1]. Accentize propose ce matching avec SpectralBalance [S2]. Aucun papier identifié ici n'est présenté par Accentize comme le code ou la méthode exacte de Chameleon.

Décision : modéliser la couleur de la réverbération dans le profil, sans modifier l'ADR sec. Ajouter ultérieurement un mode EQ explicite avec un nouveau stem sec corrigé, qui remplacera l'original.

## 3. Modèle du signal : ce que le moteur doit calculer

Pour une portion de scène approximativement stable :

```text
reference[n] ≈ convolution(voix_originale_seche, h_total)[n] + bruit[n]
h_total = h_direct + h_reflections

x[n] = ADR sélectionné, à son niveau original
h_wet[n] = réflexions estimées, calibrées relativement à un direct unitaire
w[n] = convolution(x, h_wet)[n] × 10^(wet_gain_db / 20)
preview_mix[n] = zero_pad(x)[n] + w[n]
```

La voix originale sèche est inconnue. Séparer exactement la voix, la coloration du micro et la salle n'est généralement pas identifiable à partir du seul signal mono. L'apprentissage ou un modèle acoustique fournit des hypothèses. La convolution finale est déterministe ; l'estimation qui la précède reste incertaine.

Invariants audio obligatoires :

1. La branche dry vaut exactement `x` ; aucun gain d'analyse ne se propage à cette branche.
2. L'export wet ne contient que `w`. Ne pas exporter le mélange sous le nom REVERB.
3. Les silences initiaux de l'ADR sont conservés. L'échantillon zéro du wet correspond à celui de l'ADR.
4. Le temps de propagation absolu source/micro est retiré du profil ; les retards relatifs des réflexions sont conservés.
5. Les latences de calcul et de conversion sont compensées ; les retards acoustiques restent présents.
6. Pas de normalisation au pic ou au LUFS du wet, du profil ou du mélange après calibration.
7. Pas d'ajout de la voix de référence, de ses bruits, ni de ses mots au fichier produit.
8. « 100 % wet » signifie absence de branche dry, pas niveau maximal ni DRR nul.

La séparation estimée peut laisser une fuite de direct. Il faut mesurer et documenter cette limite, pas revendiquer une isolation physique parfaite sans validation.

## 4. Choix du moteur : recherche séparée du produit

### 4.1 Trois backends, un contrat commun

| Backend | Rôle | Statut produit |
|---|---|---|
| `known_ir` | IR synthétique ou mesurée, avec direct et gain connus | Validation du DSP ; outil avancé de développement |
| `parametric_manual` | Reverb construite avec paramètres explicites | Mode manuel clairement identifié, jamais un faux matching automatique |
| `blind_estimator` | Estimation depuis la référence par un modèle réel | Fonction centrale, disponible seulement lorsque ses capacités sont vérifiées |

L'interface doit pouvoir être développée sans un modèle chargé. Elle doit afficher « moteur d'analyse non installé » et désactiver l'analyse automatique dans ce cas. Une IR générique ne doit jamais être présentée comme une analyse réussie.

### 4.2 Candidats documentés

- **Rec-RIR** : candidat prioritaire pour une preuve de fonctionnement à bande limitée. Code officiel public ; dépôt avec licence MIT et instructions d'inférence [S3, S4]. Le papier décrit une estimation de filtre puis sa conversion en IR. La version étudiée emploie 16 kHz et une durée effective d'IR d'environ 0,96 s [S5]. Ce n'est pas une validation pleine bande pour l'ADR.
- **FiNS** : piste pertinente pour la conception d'une IR comportant direct, réflexions précoces et queue de bruit filtré [S6]. La page de recherche comporte des écoutes. Une réimplémentation publique distincte précise qu'elle n'est pas celle des auteurs [S7]. Ne pas supposer l'existence de poids officiels immédiatement réutilisables.
- **BUDDy** : solution alternative d'estimation conjointe ; le dépôt officiel propose un lien vers un modèle préentraîné [S8]. Vérifier son coût, ses formats et ses conditions d'utilisation avant intégration.

Ne pas intégrer ces trois projets en même temps. Commencer par un audit d'exécution Rec-RIR, tout en construisant le moteur de rendu indépendant.

### 4.3 Risques concrets découverts dans Rec-RIR

La configuration publique consultée indique `sr = 16000`. Le script d'inférence sauvegarde une IR divisée par son maximum absolu. Cette normalisation conserve les rapports internes d'une même IR, mais ne fournit pas à elle seule la référence de gain nécessaire pour l'ajouter à un ADR inchangé. L'adaptateur devra récupérer le tableau avant cette sauvegarde ou recalibrer explicitement le direct [S9, S10].

Le fichier de dépendances consulté contient `mamba-ssm`, `causal-conv1d` et une entrée `python==2.9.0` manifestement incohérente comme prescription d'environnement. Ne pas lancer ce fichier aveuglément. Auditer les dépendances réellement utilisées et tester l'installation sur la machine cible ; ni Windows natif, ni CPU seul, ni CUDA ne sont réputés fonctionnels par défaut [S11].

Avant de choisir le backend, produire `docs/model-evaluation.md` avec : commit exact, source et empreinte des poids, licence observée du code et conditions des poids, environnement reproductible, matériel, commande d'inférence, temps et mémoire mesurés, fréquence, durée maximale de réponse et exemples audio. Les fichiers n'ont pas encore été téléchargés ou exécutés lors de la rédaction de cette architecture.

### 4.4 Décision pleine bande

Une IR à 16 kHz ne décrit pas la reverb au-delà de 8 kHz. La convertir en fichier à 48 kHz ne recrée pas cette information. Conserver l'ADR pleine bande ne résout pas le manque dans la branche wet.

Chemin retenu :

1. Prototyper l'estimation et le transfert avec un backend existant, marqué **expérimental, bande limitée**.
2. Comparer à des IR connues et à de vrais dialogues avant d'investir dans l'interface complète.
3. Pour un mode validé à 48 kHz, retenir soit un estimateur pleine bande testé, soit un moteur hybride validé séparément.
4. Le moteur hybride éventuel conserve les réflexions utilisables et reconstruit une queue par bandes. Les hautes fréquences extrapolées sont marquées `synthesized`, jamais `measured` ou `estimated_from_reference` si elles ne l'ont pas été.

Ne pas ajouter d'extension d'aigus automatique en V1. Ne pas entraîner un nouveau réseau sans décision explicite sur les données, le matériel et le coût. Une application qui rend une IR importée n'est pas encore un outil de matching automatique terminé.

## 5. Stack et frontières de responsabilité

Choix pour limiter la complexité : **Python + PySide6**, application de bureau locale. Aucun serveur web, compte utilisateur, React ou Electron nécessaire pour les deux onglets. Ce choix est une décision de projet ; les versions exactes seront verrouillées après validation des environnements.

| Couche | Choix | Responsabilité |
|---|---|---|
| Interface | PySide6 | Import, sélection, paramètres, statut, aperçu et export |
| Domaine | Dataclasses et validations explicites | Contrats, unités, versions et états |
| DSP | NumPy, SciPy | Calibration, filtres, convolution, métriques |
| Fichiers | SoundFile/libsndfile | Décodage et écriture WAV [S13] |
| Écoute | sounddevice/PortAudio | Lecture des rendus avec transport partagé |
| Inférence | Environnement Python séparé si nécessaire | Adaptateur spécifique au modèle, PyTorch si requis |
| Tests | pytest | Invariants audio, intégration et non-régression |

Cible initiale de l'application : Python 3.11, à confirmer par installation. L'environnement du modèle peut employer une autre version sans changer les contrats. Verrouiller les versions testées dans des fichiers de dépendances ; pas de mise à jour automatique à chaque lancement.

```text
REFERENCE ── import + sélection ── job d'analyse ── Estimateur interchangeable
                                                       │
                                                Estimation brute
                                                       │
                                        Canonicalisation + qualification
                                                       │
                                                 RoomProfile
                                                       │
ADR ── import + choix canal ── x ── convolution avec h_wet ── wet rendu
                              │                              │
                              └──────── aperçu x + wet ──────┘
                                                             │
                                            Export WAV + rapport JSON
```

Le DSP ne doit importer ni Qt ni une bibliothèque propre à un modèle de recherche. L'estimateur ne doit ni créer des widgets ni écrire directement le fichier final destiné à l'utilisateur.

## 6. Pipeline détaillé

### 6.1 Import et validation

1. Lire en-tête, fréquence, canaux, format, durée et taille avant décodage complet.
2. Rejeter fichiers vides, corrompus, valeurs non finies ou formats hors périmètre.
3. Décoder en tableaux flottants, convention interne `[frames, channels]`. Les conversions vers `[channels, frames]` restent confinées aux adaptateurs.
4. Calculer SHA-256 du contenu, pic, RMS indicatif et aperçu min/max de la forme d'onde. Le RMS n'est pas un loudness matching.
5. Signaler une suspicion d'écrêtage ; la proximité de 0 dBFS seule ne prouve pas un écrêtage. Les valeurs supérieures à 1 dans un WAV flottant sont possibles et ne doivent pas être écrêtées à la lecture.
6. Conserver les fichiers originaux en lecture seule. Un changement de contenu invalide le hash et tous les résultats dépendants.

Les caches vivent dans le dossier local de données de l'application, pas nécessairement sur le lecteur du projet. Utiliser des identifiants internes ; un nom de fichier utilisateur ne devient jamais une commande shell.

### 6.2 Préparation de la référence

La sélection utilise des indices d'échantillons de la référence originale : intervalle `[start_frame, end_frame)`. Les secondes servent à l'affichage. Prévoir un peu de contexte avant/après lorsque le backend l'exige ; enregistrer exactement la portion réellement analysée.

Un détecteur d'activité vocale peut suggérer une région ou signaler un manque de parole. Il ne doit pas concaténer les seuls segments parlés : cela détruirait les queues de reverb. Aucun débruitage ou gate automatique en amont.

Convertir le canal choisi à la fréquence native du modèle avec filtre anti-repliement. Appliquer seulement la normalisation attendue par ce modèle, noter le facteur et protéger le cas silence. Cette normalisation appartient à la copie d'analyse, jamais au fichier ADR.

Une sélection comportant plusieurs acoustiques, des déplacements importants, de la musique ou plusieurs locuteurs superposés peut être inadaptée. Signaler les indices détectés et permettre une autre sélection. Ne pas prétendre les identifier tous automatiquement.

### 6.3 Inférence et comparaison de fenêtres

`estimate()` retourne une estimation brute et des capacités explicites. Si plusieurs fenêtres sont autorisées, prendre au plus trois fenêtres contiguës représentatives pour commencer, avec leurs pauses et suffisamment de contexte. Respecter la durée d'entrée du modèle.

Ne pas moyenner les échantillons de plusieurs IR estimées : des phases différentes peuvent annuler la queue. Comparer plutôt leurs décroissances par bandes et DRR, puis choisir un profil représentatif, par exemple le médoïde selon ces paramètres. Conserver les résultats individuels pour diagnostiquer une référence instable. Une agrégation paramétrique sera une évolution distincte.

Le résultat distingue au minimum : `native_bandwidth_hz`, `rir_horizon_seconds`, `direct_representation`, `gain_calibration`, `supported_channels` et provenance. Un champ absent est inconnu, pas zéro.

### 6.4 Son direct et origine de l'IR

L'étape la plus délicate est de transformer `h_total` en `h_wet` sans doubler la voix directe ni effacer les premières réflexions.

Ordre de préférence :

1. Backend fournissant séparément direct et réflexions : utiliser cette décomposition après test.
2. Backend garantissant un direct de type impulsion à un indice connu : retirer cette composante précise et normaliser le profil selon son gain.
3. Backend fournissant seulement une IR totale : détecter l'arrivée directe dans une zone plausible selon sa convention, puis utiliser une fenêtre courte calibrée sur des IR connues pour séparer le paquet direct des réflexions.

Le maximum global n'est pas un détecteur fiable : une réflexion peut être plus forte que le direct. Retirer uniquement le plus grand échantillon n'enlève pas un direct coloré ou étalé. Supprimer arbitrairement les 20–50 premières millisecondes enlève une partie essentielle de l'acoustique.

Pour le troisième cas, ne pas fixer de fenêtre universelle dans le code générique. L'adaptateur stocke une méthode versionnée, son intervalle en échantillons et son statut de validation. Une fenêtre ou un masque doux est un compromis à mesurer : direct résiduel et réflexions supprimées. L'interface avancée peut afficher cette limite, sans exiger de l'utilisateur un réglage technique à chaque fichier.

Décaler ensuite le profil pour placer l'arrivée directe à `n = 0`, puis retirer le direct. Préserver les zéros jusqu'à la première réflexion. Si la séparation échoue, retourner `DIRECT_PATH_UNRESOLVED` ; aucun export présenté comme wet validé. Un résultat expérimental exige un statut explicite.

Ne pas tenter de calculer le wet en soustrayant l'ADR du signal de référence : ce sont deux performances différentes. Ne pas inverser aveuglément le filtre du direct, ce qui pourrait amplifier ses creux et créer un EQ involontaire.

### 6.5 Calibration du niveau de reverb

Le profil final est référencé à un direct unitaire, pour que l'utilisateur puisse conserver son ADR inchangé à gain 0 dB.

Cas simple validé : `h_total = a × delta[n-d] + r[n]`. Décaler de `d`, enlever le direct, puis obtenir `h_wet = r_aligned / a`. Le signe et la convention de phase sont conservés. Rejeter `a` trop faible ou instable au lieu de produire un gain énorme.

Si le direct est un paquet filtré, le rapport d'énergie fournit seulement une référence équivalente, sans retrouver son EQ. Une normalisation par la racine de l'énergie du paquet direct est une option expérimentale à calibrer ; elle ne transforme pas magiquement ce paquet en delta et ne doit pas être présentée comme une calibration exacte pour toute parole.

Pour une IR synthétique dont le direct vaut une impulsion unitaire :

```text
DRR_dB = 10 × log10(E_direct / E_reflections)
E_direct = 1
rho = E_reflections / E_direct = 10^(-DRR_dB / 10)
h_wet = raw_wet × sqrt(rho / sum(raw_wet²))
```

Les fenêtres d'énergie du DRR doivent être précisées et cohérentes avec celles du modèle et des tests. Le DRR n'est pas un pourcentage dry/wet de potentiomètre. Le rapport d'énergie d'une IR n'est pas non plus exactement le rapport RMS d'une phrase donnée.

Si le backend ne permet pas une estimation de niveau défendable : `gain_calibration = manual_required`, afficher « niveau de reverb à régler », fournir un réglage manuel et enregistrer ce statut. Ne jamais fabriquer un DRR automatique à partir du seul RT60, ni du rapport de volume entre deux performances.

### 6.6 Conversion de fréquence de l'IR

La copie ADR destinée au rendu reste à sa fréquence originale acceptée. L'IR est convertie vers cette fréquence une seule fois, après canonicalisation et avec traçabilité.

Attention : une IR est un filtre discret. Rééchantillonner ses coefficients comme une simple forme d'onde peut changer son gain de convolution. L'adaptateur de conversion doit préserver la réponse de transfert dans la bande commune. Pour un rééchantillonneur de forme d'onde conservant l'amplitude, le facteur `fs_source / fs_destination` est généralement nécessaire sur les coefficients ; vérifier cette convention sur l'implémentation choisie avec impulsions, filtres simples et sinus. Ne pas appliquer un facteur deux fois.

Compenser le délai du rééchantillonneur et tester l'origine temporelle, le gain et les retards des réflexions. Une interpolation peut étaler les impulsions : ne pas refaire une suppression aveugle de leur maximum après conversion. Mesurer également la fuite avant la première réflexion.

Si l'IR native est limitée à une bande inférieure, conserver cette information dans le profil et l'export. Aucun changement de fréquence d'en-tête ne doit masquer cette limite.

### 6.7 Paramètres utilisateur et synthèse manuelle

V1 automatique : niveau wet en dB et délai **supplémentaire** de 0 à 200 ms, valeur initiale 0. Le délai déjà contenu dans l'IR ne doit pas être ajouté une seconde fois. Ces réglages ne nécessitent pas une nouvelle inférence.

Pour le backend manuel de développement : RT60, DRR ou niveau manuel, délai des premières réflexions et seed explicite. Une queue par bandes peut suivre :

```text
late_b(t) = gain_b × filtered_noise_b(t) × exp(-ln(1000) × t / RT60_b)
```

Cette enveloppe d'amplitude atteint -60 dB à `RT60_b`. Garder des filtres et une graine déterministes, construire les réflexions séparément et calibrer l'énergie après synthèse. Vérifier les recouvrements des bandes et la décroissance obtenue. Aucun paramètre manuel n'est décrit comme issu de la référence s'il ne l'est pas.

Les réglages de decay, EQ du wet et largeur sont différés : ils impliquent une politique explicite de conservation d'énergie. Étirer naïvement les échantillons d'une IR change aussi sa couleur et ses réflexions.

### 6.8 Convolution et durée

Utiliser une convolution linéaire complète, par exemple `scipy.signal.oaconvolve(x, h_wet, mode="full")` pour les tailles V1 [S12]. L'axe temporel doit être explicite. Ne pas utiliser `mode="same"`, qui centre et tronque le résultat.

Avec `N` échantillons d'ADR et `M` coefficients effectifs d'IR, le rendu contient exactement `N + M - 1` échantillons, y compris les zéros acoustiques initiaux. Le délai supplémentaire fait partie de `M`. Zéro IR et ADR silencieux sont des cas explicites, sans division par zéro.

Prévoir une convolution par blocs avec état pour les longues durées lorsque le budget mémoire l'exige ; ne jamais traiter chaque bloc indépendamment en jetant les recouvrements. Pour V1, calculer le coût mémoire et rejeter proprement les tailles dépassant la limite plutôt que saturer la machine.

Ne pas raccourcir une queue inconnue avec un simple seuil sur les échantillons. Si le modèle la tronque alors qu'elle est encore significative, signaler `TAIL_TRUNCATED`. Un fondu final peut éviter un clic mais ne reconstitue pas la queue absente. Conserver la durée native par défaut ; toute extension synthétique future est documentée.

### 6.9 Préécoute et export

La lecture propose : référence, ADR, reverb seule, ADR + reverb. Le transport ADR/wet/mix partage la même position en échantillons. La référence possède sa propre position, puisqu'elle peut être une autre phrase. Ne pas superposer référence et ADR pour évaluer le matching.

Utiliser les mêmes tableaux de rendu pour l'écoute et l'export. Un gain de monitoring commun peut éviter de saturer la sortie ; il reste indépendant du fichier exporté et visible. Les éventuels fondus de commutation d'écoute ne modifient pas les stems.

Écrire `nom_ADR_REVERB.wav` en 32 bits flottants et un rapport `nom_ADR_REVERB.json`. Ce dernier contient profil, paramètres, versions, limites et mesures de pic. Ne pas écraser l'ADR ou une exportation existante silencieusement : proposer un nouveau nom ou le dialogue standard de remplacement.

Mesurer le pic du wet et celui de `ADR + wet`. Un dépassement de 0 dBFS dans le flottant est conservé et signalé ; pas de limiteur automatique. L'utilisateur peut atténuer ensemble ses pistes dans le DAW. Un export PCM24 ultérieur devra traiter explicitement marge et dithering, sans modifier silencieusement le dosage.

L'origine commune est garantie par les échantillons, même sans timecode. La copie du BWF `TimeReference`, à fréquence identique, est une amélioration distincte : SoundFile seul n'est pas supposé préserver tous les chunks BWF/iXML. Tant qu'elle n'est pas implémentée et testée, afficher « aligner les débuts des fichiers », sans promettre un placement automatique sur la timeline.

L'export optionnel d'IR utilise `nom_PROFILE_WET_IR.wav` et son manifeste. Il contient un filtre wet avec gain conservé. Indiquer de désactiver toute normalisation automatique dans le convolueur externe. Son comportement dans un DAW ne fait pas partie de la garantie sans vérification de ses conventions.

## 7. Contrats de données

Toutes les valeurs de temps opérationnelles sont des indices entiers ou des secondes avec fréquence associée ; jamais des millisecondes ambiguës. Les gains utilisateur sont en dB, les coefficients audio sont linéaires. JSON ne contient aucun NaN/Inf : utiliser `null` accompagné d'un motif pour une mesure indisponible.

### 7.1 Objets internes

| Objet | Champs obligatoires et sens |
|---|---|
| `AudioAsset` | `id`, `path`, `sha256`, `sample_rate_hz`, `frame_count`, `channels`, `subtype`, `selected_channel`, `peak`, métadonnées disponibles |
| `ReferenceSelection` | `asset_id`, `asset_sha256`, `start_frame`, `end_frame`, `channel_mode` |
| `AnalysisRequest` | sélection, `engine_id`, version, configuration, `seed`, révision projet |
| `RawRoomEstimate` | IR ou paramètres natifs, fréquence, bande, origine directe déclarée, normalisation connue, diagnostics et provenance |
| `RoomProfile` | manifeste ci-dessous + `wet_ir.npy` canonique, immuable |
| `RenderSettings` | `wet_gain_db`, `additional_predelay_ms`, fréquence et canal de sortie, révision |
| `RenderResult` | chemins de rendu, longueur, pics wet/mix, hash des dépendances, diagnostics |
| `JobStatus` | `job_id`, type, état, étape, progression si connue, révision, erreur structurée |

Exemple de manifeste **illustratif**, avec valeurs inconnues conservées comme telles :

```json
{
  "schema_version": 1,
  "profile_id": "example-profile",
  "status": "experimental",
  "engine": {
    "id": "recrir",
    "code_revision": "TO_BE_PINNED",
    "weights_sha256": null,
    "adapter_version": "0.1.0"
  },
  "reference": {
    "asset_sha256": "TO_BE_COMPUTED",
    "sample_rate_hz": 48000,
    "start_frame": 0,
    "end_frame": 480000,
    "channel_mode": "mono"
  },
  "native_sample_rate_hz": 16000,
  "wet_ir_sample_rate_hz": 16000,
  "wet_ir_path": "wet_ir.npy",
  "wet_ir_sha256": null,
  "time_origin": "estimated_direct_arrival",
  "direct_removal": {
    "method": "unvalidated",
    "window_frames": null,
    "validated": false
  },
  "gain_calibration": "manual_required",
  "drr_db": null,
  "usable_band_hz": [null, 8000],
  "tail_horizon_seconds": null,
  "band_parameters": [],
  "seed": 2026,
  "quality": {
    "level": "unknown",
    "reasons": ["DIRECT_PATH_UNRESOLVED", "BANDWIDTH_LIMITED"]
  }
}
```

Un profil non validé n'est pas automatiquement renderable : les capacités et raisons bloquantes sont contrôlées, indépendamment du champ descriptif `status`. L'exemple ne constitue pas un résultat prêt à l'export.

Le fichier NumPy ne contient que des tableaux numériques, chargé avec `allow_pickle=False`. Le JSON référence des chemins relatifs au dossier du profil. Vérifier leurs destinations avant lecture. Un import de profil ne charge jamais des objets Python arbitraires.

### 7.2 Interfaces conceptuelles

```text
Estimator.capabilities() -> EngineCapabilities
Estimator.estimate(AnalysisRequest, progress, cancellation) -> RawRoomEstimate
canonicalize(RawRoomEstimate, adapter_policy) -> RoomProfile
qualify(RoomProfile, diagnostics) -> Qualification
render(AudioAsset, RoomProfile, RenderSettings) -> RenderResult
export(RenderResult, ExportOptions) -> ExportManifest
```

Les fonctions DSP sont pures lorsque possible. Le choix de moteur est injecté, pas codé dans l'UI. `estimate` n'appelle pas `render`. Un profil peut être réutilisé avec un autre ADR.

## 8. Jobs, stockage et invalidation

L'UI doit rester réactive pendant l'analyse, le rendu, le hash et les lectures de grands fichiers. Utiliser un worker de calcul hors du thread graphique. Pour l'inférence, privilégier un sous-processus persistant démarré à la demande : environnement isolable et annulation possible sans tuer l'application.

Protocole minimal du worker : messages JSON par ligne sur stdin/stdout, journaux sur stderr. Chaque message porte `job_id` et `project_revision`. Les grands tableaux passent par fichiers de travail privés et chemins contrôlés, pas par JSON base64. Un seul job lourd à la fois en V1. Lancer le processus sans fenêtre console visible sur Windows.

Le worker reçoit une commande à arguments structurés, jamais une concaténation de chemin utilisateur dans un shell. Charger les poids uniquement depuis des sources connues et enregistrées ; ne pas proposer l'ouverture de checkpoints arbitraires dans l'UI.

États :

```text
EMPTY -> INPUTS_READY -> ANALYZING -> PROFILE_READY -> RENDERING -> READY
                              \-> FAILED/CANCELLED       \-> FAILED/CANCELLED
```

Un échec ne détruit pas le dernier résultat réussi, mais celui-ci est marqué obsolète si ses dépendances ont changé. L'export du résultat courant est désactivé lorsqu'il est obsolète. Une fin de job pour une ancienne révision ne remplace jamais un résultat plus récent.

| Modification | À invalider |
|---|---|
| Référence, canal, sélection, moteur, poids, paramètres d'analyse | Profil et rendus |
| ADR ou canal ADR | Rendus seulement |
| Niveau wet ou pré-délai supplémentaire | Rendus seulement |
| Volume d'écoute ou position de lecture | Rien dans les fichiers |
| Destination d'export | Rien dans le calcul |

Clés de cache : hash du contenu + sélection exacte + paramètres + version DSP/adaptateur + commit moteur + empreinte poids + seed + fréquence/canaux applicables. Ne pas utiliser uniquement nom, date ou taille.

Écrire les résultats dans un fichier temporaire, vérifier forme/valeurs/longueur, puis renommer atomiquement sur le même volume. À l'annulation, envoyer d'abord un signal logique ; si le modèle ne répond pas, terminer seulement son sous-processus après délai borné. Retirer les temporaires du job et garder les entrées.

Limiter le cache par taille ; purger uniquement les dossiers que l'application a créés sous sa racine résolue. Réinitialiser les jobs interrompus au redémarrage. Pas d'envoi de logs ou d'audio à distance par défaut.

## 9. Interface minimale

Deux onglets persistants, nommés exactement **REFERENCE** et **ADR**, avec une zone commune d'écoute/résultat accessible depuis les deux.

- REFERENCE : déposer/choisir WAV, forme d'onde, choix du canal si nécessaire, sélection d'une région, bouton « Analyser l'acoustique ».
- ADR : déposer/choisir WAV, choix du canal si nécessaire, forme d'onde et bouton « Générer la reverb » dès qu'un profil utilisable existe.
- Résultat : lecture ADR/reverb/mélange, niveau de reverb, délai supplémentaire, bouton « Exporter la reverb ».
- État utile : étape en cours, annuler, qualité/limites en français. Les informations techniques détaillées sont repliées.
- Si la fonction automatique n'est pas disponible, l'indiquer clairement. Les backends de test ne doivent pas se faire passer pour elle.

Ne pas afficher un pourcentage d'avancement inventé pendant une inférence opaque : utiliser une étape indéterminée. Ne pas annoncer « 98 % de précision » sans métrique calibrée. Le score qualitatif éventuel signifie seulement que les contrôles définis ont passé.

### Erreurs et avertissements stables

| Code | Comportement |
|---|---|
| `INVALID_AUDIO` / `UNSUPPORTED_FORMAT` | Bloquer l'import, expliquer le format attendu |
| `EMPTY_REFERENCE` / `INSUFFICIENT_SPEECH` | Demander une autre région ; ne pas produire de profil aléatoire |
| `MODEL_UNAVAILABLE` | Laisser l'UI et les outils manuels utilisables |
| `ENGINE_FAILURE` / `OUT_OF_MEMORY` | Arrêter le job, journal local, proposer un extrait plus court si pertinent |
| `DIRECT_PATH_UNRESOLVED` | Bloquer le wet validé ; diagnostic nécessaire |
| `GAIN_UNCALIBRATED` | Mode manuel explicitement identifié |
| `BANDWIDTH_LIMITED` / `TAIL_TRUNCATED` | Résultat expérimental identifié et limites dans le rapport |
| `REFERENCE_INCONSISTENT` | Recommander une sélection plus homogène |
| `MIX_OVER_0DBFS` | Conserver le flottant, afficher les pics, aucun limiteur caché |
| `STALE_RESULT` | Désactiver l'export courant jusqu'au nouveau rendu |

## 10. Validation : séparer justesse DSP et qualité du matching

### 10.1 Tests bloquants du moteur de rendu

Ces tests ne nécessitent aucun modèle ni donnée personnelle :

1. **IR connue** : `h_total = delta[0] + 0.25 delta[k]`. Le wet vaut `0.25 × ADR` retardé de `k`, sans duplication au temps zéro. Le mélange reconstruit la convolution par l'IR totale.
2. **Direct seul** : IR connue composée du direct uniquement ; wet nul, même avec ADR non silencieux.
3. **Réflexion dominante** : réflexion plus forte que le direct ; le détecteur ne confond pas leur origine dans ses cas validés.
4. **Direct étalé** : fixture synthétique avec paquet direct connu, vérifier le compromis suppression/conservation selon la méthode annoncée.
5. **Linéarité du gain** : doubler l'ADR double le wet ; +6,0206 dB double le wet ; le dry reste identique.
6. **Silence et bornes** : ADR nul, IR nulle, très petit gain direct, valeurs non finies, fichier vide ; aucun NaN ni crash.
7. **Temps** : silence initial, première réflexion connue, pré-délai supplémentaire et longueur `N+M-1` corrects, y compris aux frontières de blocs.
8. **Convolution** : comparer calcul par blocs et convolution directe float64 sur petits cas ; erreur absolue visée <= 1e-6 pour signaux de test d'amplitude <= 1.
9. **Conversion d'IR** : vérifier gain dans la bande commune et délai à 16/44,1/48 kHz. Objectif <= 0,1 dB et <= 1 échantillon cible hors transitions du filtre, avec mesure adaptée aux impulsions interpolées.
10. **Export flottant** : relire le WAV et comparer au tableau rendu ; longueurs/fréquences exactes, erreur <= 1e-6 sur fixtures normalisées. Préserver les valeurs > 1.
11. **Pas de contamination** : changer le contenu parlé de la référence sans réinjecter son audio dans le rendu ; le renderer ne reçoit que le profil et l'ADR.
12. **Canaux** : sélection L/R correcte ; downmix explicite ; aucune convolution accidentelle sur l'axe des canaux.

Un échec d'invariant bloque la livraison, même si le résultat semble agréable à écouter.

### 10.2 Qualification d'un estimateur

Construire un petit jeu reproductible de dialogues secs autorisés et d'IR mesurées autorisées. Générer `reference = speech_A * h + noise`, puis appliquer le profil estimé à **speech_B**, une autre phrase. La cible est `speech_B * h`. Utiliser aussi des vrais couples production/ADR, dont aucune IR exacte n'est connue.

Séparer pièces/IR d'entraînement et d'évaluation si un entraînement existe. Varier voix, langues dont français, niveaux de bruit, distances, pièces sèches et plus réverbérantes, premières réflexions fortes et queues dépassant l'horizon du modèle. Commencer par au moins 12 situations acoustiques et plusieurs phrases ; ce pilote n'est pas une validation statistique universelle.

Mesures utiles :

- RT20/RT30 et extrapolation RT60 par bandes lorsque la dynamique le permet ; sinon mesure indisponible.
- DRR et énergie précoce/tardive avec fenêtres explicitement identiques pour cible et estimation.
- Courbes de décroissance énergétique et coloration par bandes.
- Direct résiduel et réflexions précoces perdues sur cas dont la vérité est connue.
- Temps d'analyse, mémoire, reproductibilité, limites de bande et de durée.

Une mesure RT60 sur le simple signal de parole ne doit pas être confondue avec une mesure sur IR. Sur IR, une courbe de Schroeder nécessite de traiter le bruit et la troncature ; ne pas ajuster une droite à un plancher de bruit.

Écoutes à niveau de présentation contrôlé : ADR sec, reverb manuelle simple, résultat estimé et cible connue. Utiliser un même gain de monitoring pour les variantes d'un cas ; ne pas normaliser indépendamment les stems. Évaluer proximité, réflexions, coloration, queue, dosage et artefacts. Randomiser les étiquettes lors d'une comparaison formelle.

Objectifs provisoires pour le pilote, à réviser à partir des résultats : erreur médiane RT60 <= max(0,1 s, 20 % de la cible), erreur médiane DRR <= 3 dB sur cas mesurables, et préférence perceptive pour le résultat estimé face à une reverb générique dans la majorité des cas. Ces seuils sont des objectifs de projet, pas des performances constatées ni une garantie d'acceptabilité artistique.

Conserver également les pires cas. La qualité à 48 kHz, la fiabilité du niveau et la séparation du direct sont trois critères indépendants ; aucun ne peut être déclaré acquis à partir du seul nom du modèle.

### 10.3 Tests d'application

Import, sélection, génération et export ; annulation ; remplacement de référence pendant un job ; résultat obsolète ; modèle absent ; fichier renommé/modifié ; chemins Windows avec espaces/accents ; erreur de périphérique audio ; fermeture pendant rendu ; destination non accessible. Un test manuel dans le DAW vérifie la superposition wet + ADR et le prolongement de queue.

## 11. Organisation cible du dépôt

Créer progressivement cette structure, sans générer d'avance des dizaines de modules vides :

```text
architecture.md
README.md
pyproject.toml
src/mimetic/
  domain/          # objets, contrats, diagnostics, versions
  audio/           # lecture, fréquences, direct/wet, gain, convolution, export
  analysis/        # sélection, qualification, interface des estimateurs
  engines/         # known_ir, parametric_manual, adaptateurs expérimentaux
  jobs/            # orchestration, processus, annulation, cache
  playback/        # transport et monitoring
  ui/              # les deux onglets et le résultat
  cli.py           # accès de validation au même moteur, sans Qt
tests/
  unit/
  integration/
  fixtures/        # petits signaux synthétiques ; pas de rushes privés
benchmarks/
  manifests/       # provenance et découpage des données autorisées
docs/
  model-evaluation.md
  validation.md
  decisions.md
```

Poids, environnements, caches, captures audio privées et exports restent hors Git. Les jeux de tests volumineux sont référencés par manifestes avec empreintes et droits d'utilisation, pas copiés sans contrôle. Une CLI minimale permet d'analyser, rendre et mesurer sans interface graphique.

## 12. Plan de réalisation pour le modèle chargé du code

### Lot A — DSP fiable sans modèle

Créer projet Python, contrats, import/export, backend `known_ir`, séparation connue direct/wet, calibration, convolution et CLI. Ajouter les tests audio bloquants. Fournir des exemples synthétiques reproductibles. Livrable : rendu wet correct à partir d'une IR dont la convention est connue. Ce lot ne prétend pas analyser une pièce.

### Lot B — Audit et preuve d'estimation réelle

Inspecter le dépôt Rec-RIR et ses poids, épingler les versions, isoler son environnement, exécuter l'exemple officiel si disponible, puis une référence synthétique connue. Évaluer Windows/GPU/CPU réellement. Si l'environnement nécessite Linux/WSL, documenter le coût de cette dépendance ; ne pas l'installer comme prérequis caché de l'application.

Tester extraction du direct, gain relatif, durée et bande. Écrire le rapport d'évaluation même en cas d'échec. Un modèle incompatible n'empêche pas de terminer le renderer ; il empêche de déclarer le matching automatique livré. Ne pas remplacer une intégration échouée par un faux estimateur.

### Lot C — Premier workflow REFERENCE/ADR

Brancher les deux onglets au même moteur que la CLI, jobs annulables, aperçu cohérent, contrôle de niveau et export wet. Exposer clairement le statut expérimental si le backend est limité. Tester une superposition dans le DAW.

### Lot D — Validation de l'usage réel

Exécuter le pilote sur IR inconnues du modèle et vrais dialogues. Vérifier les seuils et écouter les échecs. Choisir le moteur retenu et documenter les corrections nécessaires pour le 48 kHz. Ce lot détermine si la V1 est utilisable pour l'objectif métier.

### Lot E — Livraison locale

Verrouiller les dépendances validées, fournir une procédure de lancement courte et les informations de licence applicables. Tester sur une installation propre. L'empaquetage en exécutable ne doit pas masquer une dépendance GPU/WSL ou un téléchargement de poids nécessaire.

Ordre recommandé : A puis B, avant de polir C. Si B bloque, poursuivre les parties indépendantes de C et produire un état précis des choix de moteur restants. Ne pas entamer un entraînement coûteux pour contourner le blocage sans décision de projet.

## 13. Évolutions prévues sans les coder maintenant

### EQ matching optionnel

Un futur module `ToneMatcher` produira un filtre et un `ADR_MATCHED_DRY.wav`. Le wet correspondant sera calculé à partir de ce même ADR corrigé. L'utilisateur remplacera l'ADR initial par le fichier corrigé, puis ajoutera le wet associé. Chaque export identifie exactement le dry dont il dépend.

Éviter un simple ratio spectral non régularisé entre deux phrases : différences de phonèmes, de jeu et de voix peuvent être prises pour un défaut de micro. Prévoir statistiques sur parole active, lissage, gains bornés et tests de non-dégradation. Les délais de l'EQ doivent être compensés dans les deux stems. Le matching EQ constitue un module et une validation distincts.

### Stéréo et lots

Une IR mono vers stéréo comporte deux réponses relatives au même direct. Elles demandent une calibration conjointe. Traiter les deux canaux avec deux estimateurs mono indépendants ne garantit pas un champ stéréo cohérent. Une entrée stéréo vers sortie stéréo peut nécessiter une matrice de quatre filtres ; préciser le routage avant extension.

Le traitement par lot réutilisera un `RoomProfile` immuable pour plusieurs ADR, avec rendus et rapports indépendants. Une évolution vers plugin pourra réutiliser le format de profil, mais demandera un moteur de convolution et des contraintes temps réel spécifiques.

## 14. Règles de passation

- Lire ce document avant de coder. Implémenter par lots vérifiables ; ne pas tout réécrire pour changer de modèle.
- Les décisions fixées sont : deux onglets, local, export wet parallèle, dry inchangé, origine préservée, gain traçable, mono V1, aucune API LLM dans le traitement.
- Les choix encore ouverts sont : backend réellement exécutable, méthode de séparation directe pour celui-ci, calibration de gain et solution pleine bande validée. Ils se résolvent par essais et preuves, pas par suppositions.
- Ne jamais qualifier de terminé le matching automatique avec seulement une UI, une reverb à RT60 fixe ou un convolueur.
- Ne pas retirer les premières réflexions pour rendre artificiellement facile le test d'absence de direct.
- Ne pas confondre normalisation d'entrée réseau, calibration du filtre et niveau de monitoring.
- Après chaque lot, documenter les commandes utiles, tests exécutés, limites restantes et prochain lot dans README/validation. Modifier cette architecture seulement si une décision change, avec motif dans `docs/decisions.md`.
- Ne pas réouvrir les choix d'interface à chaque étape ; les questions à l'utilisateur doivent concerner une ambiguïté réelle ou une écoute décisive.

## 15. Sources et portée des vérifications

Sources consultées le 16 septembre 2026. Les liens vers `main`/`master` peuvent changer : épingler les commits au moment de l'implémentation. Les propositions de stack, contrats, seuils et workflow de ce document sont propres au projet ; elles ne sont pas attribuées aux auteurs cités.

- [S1 — Manuel Chameleon](https://www.accentize.com/products/ChameleonManual.pdf) : fonctions documentées du plugin, contrôles et profil de réverbération.
- [S2 — SpectralBalance](https://www.accentize.com/product/spectral-balance/) : matching du timbre de dialogue distinct.
- [S3 — Rec-RIR, dépôt officiel](https://github.com/Audio-WestlakeU/Rec-RIR) : point de départ d'intégration et instructions publiques.
- [S4 — Licence du dépôt Rec-RIR](https://raw.githubusercontent.com/Audio-WestlakeU/Rec-RIR/main/LICENSE) : licence observée du code ; vérifier aussi la provenance et les conditions des artefacts distribués.
- [S5 — Rec-RIR, version 2 du papier](https://arxiv.org/html/2509.15628v2) : méthode, conditions expérimentales et limites temporelles/fréquentielles utilisées ici. Ne pas généraliser ses benchmarks à ce projet.
- [S6 — FiNS, page des auteurs et écoutes](https://facebookresearch.github.io/FiNS/) : architecture scientifique et exemples.
- [S7 — Réimplémentation non officielle FiNS](https://github.com/kyungyunlee/fins) : piste de code, distincte de la publication et des poids officiels.
- [S8 — BUDDy, dépôt officiel](https://github.com/sp-uhh/buddy) : méthode alternative et lien public vers un checkpoint.
- [S9 — Script d'inférence Rec-RIR](https://raw.githubusercontent.com/Audio-WestlakeU/Rec-RIR/main/inference.py) : normalisation de l'IR lors de sa sauvegarde.
- [S10 — Configuration Rec-RIR](https://raw.githubusercontent.com/Audio-WestlakeU/Rec-RIR/main/config/Rec-RIR.toml) : fréquence native et paramètres de configuration consultés.
- [S11 — Dépendances Rec-RIR](https://raw.githubusercontent.com/Audio-WestlakeU/Rec-RIR/main/requirements.txt) : dépendances à auditer, pas une installation testée.
- [S12 — SciPy, oaconvolve](https://docs.scipy.org/doc/scipy/reference/generated/scipy.signal.oaconvolve.html) : convolution overlap-add et modes de longueur.
- [S13 — SoundFile](https://python-soundfile.readthedocs.io/) : lecture/écriture audio et tableaux numériques.

Ce document n'atteste ni de performances mesurées, ni d'une installation réussie, ni d'une équivalence à Chameleon. Il définit comment obtenir, tester et livrer ces résultats sans confondre une hypothèse de recherche avec une fonctionnalité acquise.
