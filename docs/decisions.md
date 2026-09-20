# Décisions

## D1 — 2026-09-16 — Interface web locale au lieu de PySide6 pour la beta

**Architecture §5** : application de bureau PySide6, « aucun serveur web ».

**Décision** : la beta est un service Python (FastAPI + uvicorn) servant une page web sur le LAN, à la demande explicite de l'utilisateur.

**Motif** : accès depuis plusieurs postes du studio sans installation côté client.

**Ce qui ne change pas** : traitement 100 % local sur la machine hôte (aucun service distant), DSP sans dépendance à l'UI (`mimetic.audio`, `mimetic.engines`, `mimetic.pipeline` n'importent ni FastAPI ni le navigateur), CLI sur le même moteur, contrats de données, invariants audio.

**Conséquences** :
- Sécurité : pas d'authentification ; usage réservé à un LAN de confiance. Noms de fichiers utilisateur jamais utilisés comme chemins (identifiants internes), téléchargements confinés au dossier d'export.
- Session unique partagée en mémoire ; un seul calcul lourd à la fois (409 `BUSY` sinon).
- L'écoute passe par Web Audio : les tableaux float32 envoyés sont exactement ceux du rendu, et le navigateur rééchantillonne à la sortie de la carte son (monitoring seulement).
- Pas d'annulation de job : les calculs de la beta (synthèse, convolution ≤ 10 min) sont de l'ordre de la seconde. À revoir avec un estimateur réel (sous-processus, §8).

## D2 — 2026-09-16 — Contenu de la beta

Lot A (DSP sans modèle) + interface du lot C. Le backend `blind_estimator` expose seulement `MODEL_UNAVAILABLE` : aucune analyse simulée (§4.1, §14). Le lot B (audit Rec-RIR) reste à faire.

## D3 — 2026-09-16 — Choix de conception du backend manuel

- 3 bandes (crossovers 500 Hz / 4 kHz, Butterworth ordre 4 en `sosfiltfilt`), bruit indépendant par bande normalisé à la même énergie avant enveloppe.
- 8 premières réflexions éparses dans les 40 ms suivant la première réflexion, signe aléatoire (graine), amplitude liée à l'enveloppe médium.
- Montée de 20 ms de la queue diffuse ; énergie totale calibrée au DRR demandé (direct unitaire) ; zéros exacts avant la première réflexion.
- Durée = première réflexion + RT60 de la bande la plus longue, plafonnée à 10 s (`TAIL_TRUNCATED`).
- Le recouvrement des filtres n'est pas plat ; la couleur n'est pas un EQ calibré. La décroissance obtenue est mesurée (T20 par bande) et affichée.

## D5 — 2026-09-16 — Rec-RIR sur CPU Windows via un portage PyTorch pur de Mamba

**Contexte** : Rec-RIR (commit `b3ac6fc`) importe `mamba_ssm`, des noyaux CUDA/Triton sans support Windows natif. La machine cible a une Quadro P600 (2 Go, Pascal), 8 Go de RAM et pas de WSL.

**Décision** : exécuter le code et les poids officiels, non modifiés, dans un environnement séparé `.venv-engine` (PyTorch CPU). `mamba_ssm.Mamba` y est remplacé par `mimetic/engines/recrir/mamba_ref.py`, transcription de `selective_scan_ref` avec les mêmes noms de paramètres. `torchaudio` est remplacé par un module vide, car seules les E/S WAV et la convolution finale en dépendent, et elles sont refaites dans le worker.

**Preuves** :
- chargement `strict=True` des 3,1 M de paramètres ;
- équivalence du scan optimisé avec la boucle de référence (erreur relative sur l'IR finale 5,8e-8).

La comparaison avec les noyaux CUDA d'origine n'a **pas** été faite, faute de matériel compatible.

**Écarté** :
- scan parallèle en log-espace : exact, mais ~10× plus lent sur CPU ;
- route WSL2 + CUDA : lourde, et GPU probablement non pris en charge ;
- BUDDy : déréverbération par diffusion, trop coûteuse ici.

**Coût mesuré** : environ 5 s de calcul par seconde d'audio (i5-11400, 6 threads), soit ~30 s par fenêtre de 6 s.

## D6 — 2026-09-16 — Post-traitements de Rec-RIR non repris

`PIM.process` découpe l'IR à « maximum global − 2,5 ms », tronque à 2 s et divise par le pic, et `inference.py` renormalise au pic. L'adaptateur récupère l'IR complète avant ces étapes (§4.3). La séparation du direct suit la politique versionnée `recrir-direct-v1` :
- choix du pic fort le plus précoce dans les 5 ms précédant le maximum ;
- paquet direct de ±1 ms, retiré avec une remontée de 0,5 ms ;
- gain par l'énergie du paquet (`energy_equivalent`) ;
- horizon de 1 s.

`validated: false` : la politique est réglée sur un pilote synthétique, pas validée sur des pièces réelles.

## D7 — 2026-09-16 — Analyse asynchrone dans le service web

L'analyse tourne dans un thread qui pilote le worker (sous-processus persistant, sans fenêtre console). Un seul calcul lourd à la fois. L'annulation arrête le sous-processus. Un résultat dont la sélection, le canal ou le contenu de la référence ont changé est ignoré. Un profil Rec-RIR devient obsolète, avec rendu et export bloqués, si la référence change après l'analyse.

## D8 — 2026-09-16 — Une seule page : SOURCE + DESTINATION, calcul automatique

**Architecture §9** : deux onglets REFERENCE / ADR, sélection de région, boutons séparés.

**Décision** (demande de l'utilisateur, jugeant l'interface « beaucoup trop compliquée ») :
- une seule page, deux fichiers obligatoires ;
  - SOURCE : dialogue de tournage analysé ;
  - DESTINATION : voix ADR qui reçoit la reverb ;
- dès que les deux sont présents, un job fait ce qui manque :
  - analyse si le contenu ou le canal de la source a changé ;
  - rendu si la destination, son canal, le profil ou les réglages ont changé ;
- plus de sélection manuelle : jusqu'à 3 passages de 6 s sans recouvrement, les plus actifs de la source (trames de 20 ms à moins de 35 dB du maximum), pauses et queues incluses (pas de concaténation, §6.2). Les passages retenus sont encadrés sur la forme d'onde ;
- outils manuels (`parametric_manual`, `known_ir`) retirés de la page, conservés dans la CLI et les tests DSP.

**Limite** : l'indice d'activité n'est pas un détecteur de parole ; musique ou bruit fort peuvent être choisis.

## D9 — 2026-09-16 — Extension hybride des aigus (`hybrid-hf-v2`)

Rec-RIR ne décrit rien au-dessus de 8 kHz. Au rendu (fréquence de la destination) :
- sous 7,8 kHz : IR estimée, inchangée ;
- au-dessus : 3 bandes synthétisées (7,8–11,3 / 11,3–16 / 16–20 kHz). Chacune est modulée par l'enveloppe de la bande estimée 5–7 kHz, qui porte le placement des réflexions ;
- décroissance : 1/RT = a + b·f² ajusté sur les bandes 1–7 kHz, forme de l'absorption de l'air, b ≥ 0 ;
- niveau précoce : prolongé linéairement en Hz ;
- bornes : jamais plus brillant ni plus long que la bande 5–7 kHz.

Marquage `HF_SYNTHESIZED` dans la page et le rapport JSON (`render_ir.synthesized_bands`), jamais « estimé ».

`hybrid-hf-v1`, écarté, prolongeait RT et niveau linéairement en octaves : aigus trop brillants (+5 à +10 dB) et trop longs (×1,3 à ×2,8) sur le pilote pleine bande.

Réserve méthodologique : la forme f² de la décroissance est un choix physique a priori, mais le passage du niveau en dB/kHz a été choisi **après** avoir vu 3 des 6 pièces du pilote, lui-même synthétique. Ce n'est pas une validation indépendante.

## D10 — 2026-09-17 — Installation autonome dans le dossier du projet

**Architecture §6.1** : caches dans le dossier local de données de l'application (`%LOCALAPPDATA%`).

**Décision** (demande de l'utilisateur, qui gère ses services depuis un disque de projets) : tout vit sous la racine du projet et rien n'est écrit ailleurs.

| Élément | Emplacement |
|---|---|
| Environnement de l'application | `.venv/` (`requirements.txt`) |
| Environnement du moteur | `.venv-engine/` (`engine-requirements.txt`) |
| Code et poids Rec-RIR | `third_party/Rec-RIR/` (commit épinglé) |
| Imports, jobs, exports | `data/` (`--data-dir`, `--export-dir` pour en changer) |
| Journaux service et worker | `.engine-logs/` |

`setup.cmd` recrée l'ensemble, `run.cmd` lance le service. Le dossier reste déplaçable : tous les chemins sont résolus depuis la racine du projet.

Seule dépendance extérieure : l'interpréteur Python 3.11 de la machine, dont les venvs héritent (fonctionnement normal d'un venv). Les paquets, eux, sont installés dans le projet, avec `--no-cache-dir` pour éviter le cache pip du profil utilisateur.

## D11 — 2026-09-19 — Match EQ optionnel et deux modes d'export

Suit `architecture-match-eq.md`. **Option, pas remplacement** : sans Match EQ, le rendu et les échantillons sont exactement ceux d'avant (test bloquant `test_bypass_is_bit_identical`).

Deux exports au choix :
- **Reverb seule** `…_IR_ONLY.wav` : comportement historique, piste parallèle ;
- **Clip traité** `…_EQ_IR_MIX.wav` : un seul fichier, voix corrigée + reverb, qui **remplace** l'ADR d'origine.

L'export facultatif de l'IR de pièce s'appelle `…_IR_PROFILE.wav`.

Les suffixes de la première version (`_ADR_REVERB`, `_ADR_MATCHED`, `_PROFILE_WET_IR`) ont été renommés à la demande de l'utilisateur ; les anciens fichiers déjà exportés gardent évidemment leur nom.

Écarts assumés par rapport au document d'architecture :
- **Silero VAD différé** : détecteur maison énergie/SNR (`energy_snr_v1`), déclaré comme indice d'activité. La dépendance ONNX sera ajoutée seulement si la sélection devient le facteur limitant.
- **Paramètres de régularisation ajustés** après mesure : `λ0 = 0,05 + 0,25·(1−w)` au lieu de `2·(1−w)`, et poids remis à l'échelle relative. Avec les valeurs du document, la correction mesurée n'était appliquée qu'à ~30 % et le raccord ne progressait pas (voir `docs/eq-validation.md`).

- **Minimum de parole abaissé à 0,8 s** après échec sur une vraie réplique d'ADR (le seuil de 2 s du document écartait un cas courant).
- **Bornes fixées par l'incertitude mesurée, jamais par la durée** : un ADR est court par nature, lier la force de correction à sa durée briderait la fonction en permanence.
- **Lissage porté à λ = 5000** et **comparaison par classes de phonèmes essayée puis écartée** (elle double la fausse correction). Mesures : `docs/eq-validation.md`.
- **Match EQ actif par défaut**, calculé dans la foulée de l'analyse de pièce, sans second clic.
- **Calage automatique du niveau de reverb : essayé, mesuré, retiré.** Deux estimateurs (tranches après les fins de phrases, temps audible dans les creux) se sont révélés dominés par le rythme de parole ou insensibles au défaut. Sur un même cas réel ils proposaient −3 dB et +12 dB. Le dosage reste manuel ; détails et chiffres dans `docs/eq-validation.md`.
- **Le clip traité exige le Match EQ** (`EQ_REQUIRED`) : un mélange voix + reverb sans raccord de timbre n'a pas d'usage, la reverb seule le couvre déjà. Demande explicite de l'utilisateur.

Conservé tel quel : cible = mélange virtuel avec la reverb rendue, application unique du filtre à l'ADR, FIR causal par cepstre réel, compensation de niveau commune bornée, bornes de courbe, refus explicites sans casser la reverb.

## D12 — 2026-09-19 — Analyse robuste au bruit de plateau (lots A1/A2)

Suit `docs/architecture-references-difficiles.md`. Trois défauts de la V2 vérifiés puis corrigés : confiance absolue effacée par la normalisation des poids, queue de réverbération comptée comme du bruit, incertitude conventionnelle sur réplique courte. Détails et chiffres : `docs/eq-validation.md`.

- **`c_abs` (fiabilité absolue, jamais renormalisée) séparée des poids relatifs.** C'est le cœur du correctif : un extrait bruité ne peut plus produire la même courbe qu'un extrait propre.
- **Plus de falaise ni de refus surprise** : la correction décroît continûment de 24 dB à 0 dB de SNR ; le refus est réservé à l'absence réelle d'information.
- **Fond mesuré loin des mots et de leur décroissance** ; fond négligeable (silence numérique) distingué de fond inconnu.
- **Rapport signal/bruit redéfini** en parole seule sur bruit.
- Aucun changement du rendu quand tout est propre : régression de +0,05 dB sur le jeu propre du banc, sous le seuil de 0,25 dB fixé par l'audit.
- **Aucune correction du bruit lui-même** : ces mesures pilotent uniquement la confiance accordée à chaque bande. Mimetic n'ajoute, ne retire et ne recopie aucun fond sonore.

Lots B (récupérer `y_spch` et `y_rev` de Rec-RIR) et suivants : non entamés.

## D13 — 2026-09-20 — Enchaîner deux couples, et interface jamais mise en cache

- **Bouton « Nouveau duo »** à côté du statut : vide les deux fenêtres et l'analyse, conserve exports et réglages. Sans lui, remplacer la seule SOURCE relançait aussitôt un calcul avec l'ancienne DESTINATION, avant d'avoir pu la remplacer. Le traitement reste automatique (D11) ; c'est l'intention de changer de couple qui devient explicite.
- **La page HTML porte `Cache-Control: no-store`**, comme le CSS et le JavaScript depuis D11. Sans cela, un navigateur gardait l'ancienne interface après mise à jour et affichait d'anciens boutons.

## D14 — 2026-09-20 — Fichiers entrelacés : deux micros, deux corrections

Sur un plateau, le boom et le lavalier d'une même scène n'ont ni le même timbre ni le même rapport direct/réverbéré. Une correction unique appliquée aux deux est fausse pour au moins l'un des deux.

- **Deux canaux des deux côtés = deux traitements indépendants**, appariés par ordre de canal : A1 source avec A1 destination, A2 avec A2. Profil de pièce, courbe d'EQ et rendu sont calculés séparément pour chaque piste.
- **La cohérence de l'ordre des canaux appartient à l'utilisateur.** Aucune détection automatique du type de micro : elle serait devinée, donc parfois fausse, et silencieusement.
- **Les exports respectent le même ordre** et sortent entrelacés : `…_IR_ONLY.wav` porte l'IR_ONLY de A1 sur A1 et celui de A2 sur A2, `…_EQ_IR_MIX.wav` de même.
- **Complément de zéros à la fin, jamais au début** : l'échantillon zéro reste l'origine commune, le calage sur la tête de fichier vaut pour toutes les pistes (voir D4 et la règle de placement).
- **Mono inchangé**, et **nombres de canaux différents** entre source et destination : retour au choix manuel d'un canal de chaque côté, avec un avertissement dans la page.
- **Un seul rapport JSON** (`schema_version: 2`) décrivant l'ordre des canaux et une entrée par piste, plutôt que deux rapports à recoller.
- **Les trois exports partent ensemble** : tant qu'une piste n'est pas à jour, les boutons restent inactifs. Un fichier entrelacé à moitié recalculé n'aurait aucun sens.
- **Curseurs communs, analyses séparées.** Niveau de reverb, délai supplémentaire et intensité du Match EQ s'appliquent aux deux pistes : ce sont des choix de mixage sur une même scène, pas des mesures. Les deux micros captent la même pièce au même moment, un dosage divergent serait un défaut de raccord plus souvent qu'une intention. C'est écrit dans la page pour que le partage ne se devine pas.
- **Une piste en échec ne fait pas disparaître l'autre** : le sélecteur reste affiché, la piste saine reste consultable et écoutable, et le corps du résultat est remplacé par la raison de l'absence. Seul l'export est bloqué, avec le nom de la piste fautive et la cause — un bouton qui ne fait rien sans rien dire est un bug, pas une protection.

## D4 — 2026-09-16 — Zéros exacts dans la convolution

`oaconvolve` (FFT) laisse un bruit ~1e-17 avant la première réflexion. Les zéros initiaux de l'ADR et de l'IR sont retirés avant convolution, puis le résultat est replacé à son indice : zéros exacts, même longueur `N+M-1`.
