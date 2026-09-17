# Évaluation du moteur Rec-RIR (lot B)

Date : 2026-09-16.

## Provenance

| Élément | Valeur |
|---|---|
| Code | https://github.com/Audio-WestlakeU/Rec-RIR, commit `b3ac6fc1421bd58e017022037feb14f30366b46f` (10 août 2026) |
| Licence du code | MIT (fichier `LICENSE` du dépôt). Les poids sont distribués dans le même dépôt, sans conditions séparées observées. |
| Poids | `ckpt/epoch35.tar`, 38 215 458 octets, SHA-256 `afe36e477f6fb3656d162d906a47da2d40a789634b476564266fc1ba36037478` |
| Chargement | `torch.load(weights_only=True)`, filtre des clés `ops`/`params` identique à `inference.py`, `load_state_dict(strict=True)` : 3 138 144 paramètres |
| Configuration | `config/Rec-RIR.toml` au même commit : 16 kHz, STFT 512/256 en fenêtre sqrt-Hann, CTF de 60 trames, `mamba(16,4)` |

## Environnement

| | |
|---|---|
| Matériel | Intel i5-11400 (6 cœurs), 7,9 Go de RAM, Quadro P600 2 Go (non utilisée) |
| OS | Windows 10 Home 19045, sans WSL |
| Environnement | `.venv-engine`, Python 3.11.9, torch 2.14.0+cpu, numpy 2.2.6, scipy 1.13.1, soundfile 0.12.1 (`engine-requirements.txt`) |
| Écarts au dépôt | `mamba_ssm.Mamba` remplacé par un portage PyTorch pur de `selective_scan_ref` ; `torchaudio` absent ; post-traitements de `PIM.process` non repris (décisions D5, D6) |

`requirements.txt` du dépôt (`python==2.9.0`, `mamba-ssm==2.2.5`, `causal-conv1d==1.5.2`, `torch==2.7.1`…) n'a **pas** été installé.

## Vérifications d'intégration

- Portage Mamba contre la boucle de référence : équivalent en float64 (≤ 1e-10). Sur le modèle complet, erreur relative de l'IR de 5,8e-8 entre le scan optimisé et la boucle littérale.
- **Non vérifié** : équivalence avec les noyaux CUDA `mamba_ssm` d'origine, faute de matériel compatible.

## Performance mesurée (CPU, 6 threads)

| Entrée | Calcul |
|---|---|
| 1 s | ~4 s (boucle littérale : 9,7 s) |
| 3 s | 14,8 s (boucle littérale : 33 s) |
| 6 s (fenêtre standard) | 31–34 s |
| Chargement du modèle | 0,4 s |

## Capacités

- Bande : 16 kHz natif, **rien au-dessus de 8 kHz**. Le rendu à 48 kHz reste valide, mais la reverb est vide au-dessus de 8 kHz (`BANDWIDTH_LIMITED`).
- Horizon : CTF de 60 × 256 / 16 000 = 0,96 s, profil tronqué à 1,0 s. `TAIL_TRUNCATED` si la queue reste au-dessus de −40 dB en fin d'horizon.
- Direct : non fourni séparément ; séparation par la politique `recrir-direct-v1` (non validée sur pièces réelles).
- Gain : `energy_equivalent`, relatif à l'énergie du paquet direct estimé.
- Canaux : mono.

## Pilote synthétique (`benchmarks/recrir_synthetic.py`, `benchmarks/recrir_evaluate.py`)

**Données** :
- Parole sèche : synthèse vocale Windows (voix anglaises David et Zira, 16 kHz), 10 phrases.
- 9 IR synthétiques : direct unitaire + 8 réflexions précoces (2–50 ms) + queue exponentielle 2 bandes (aigus à 0,7 × RT60). RT60 nominal 0,25 / 0,5 / 0,9 s × DRR +8 / +2 / −3 dB.
- Référence = parole × IR + bruit blanc à 30 dB de SNR, fenêtre de 6 s ; 2 phrases par pièce, soit 18 cas.

**Mesures** : fenêtres identiques pour la vérité et l'estimation, sur le wet (direct exclu).

Résultats avec la politique retenue (paquet direct ±1 ms) :

| Mesure | Résultat | Objectif provisoire (§10.2) |
|---|---|---|
| Erreur absolue médiane RT60 (T20) | 0,018 s | ≤ max(0,1 s ; 20 %) → **18/18** |
| Erreur médiane DRR | **+1,45 dB** (toujours positive, de +0,7 à +2,2) | ≤ 3 dB → **18/18** |
| C50 du wet | écart ≤ 0,4 dB | — |
| Écart moyen d'EDC 0–300 ms | 0,54 dB | — |
| Direct non résolu | 0/18 | — |

Tendances :
- **DRR surestimé de ~1,5 dB** : le wet estimé est un peu trop faible. Non corrigé automatiquement (calibration sur données synthétiques uniquement) ; le curseur de niveau compense.
- **Queues longues sous-estimées** : RT60 vrai 0,77 s → 0,67 s estimé.
- La taille de la fenêtre directe (0,5 à 5 ms) change le DRR de moins de 0,15 dB.

Contrôle de bout en bout par le service web (48 kHz, 11 s, 2 fenêtres, IR 48 kHz RT60 0,60 s / DRR +3,0 dB) :
- estimation RT60 0,55 s / DRR +3,55 dB ;
- écart entre fenêtres : RT60 ×1,01, DRR 0,03 dB.

## Extension hybride des aigus (`benchmarks/recrir_fullband.py`, `benchmarks/hybrid_evaluate.py`)

**Données** : 6 IR vérité à 48 kHz :
- RT60 médium 0,3 à 1,0 s, DRR −2 à +6 dB ;
- absorption des aigus RT_b = RT / (1 + k·(f/4 kHz)²) avec k de 0,1 à 2 ;
- pente de niveau tardif de 0 à −4 dB/oct ;
- premières réflexions filtrées passe-bas.

Référence = parole × IR + bruit à 35 dB de SNR, 6 s, analysée normalement (conversion 48 → 16 kHz). Comparaison par bande du RT60 (T20) et du niveau précoce 0–50 ms, relatif à la bande 5–7 kHz.

Estimation Rec-RIR dans les bandes 1–7 kHz : très proche de la vérité (RT60 et niveaux relatifs à ~10 % / ~1 dB près sur les cas inspectés).

| Bande synthétisée | Rapport RT60 (médiane, étendue) | Erreur de niveau (médiane abs., étendue) | Sans extension |
|---|---|---|---|
| 7,8–11,3 kHz | 0,88 (0,64 – 1,11) | 2,3 dB (+0,3 – +3,7) | −11 dB |
| 11,3–16 kHz | 1,01 (0,88 – 1,30) | 1,5 dB (−5,5 – +1,1) | −56 dB |
| 16–20 kHz | 0,94 (0,56 – 2,06) | 2,1 dB (−9,3 – +2,0) | −54 dB |

- **Bande estimée** : conservée (écart relatif < 1,6 % sous 6 kHz).
- **Pièce très absorbante** (k = 2) : aigus synthétisés trop sombres de 5 à 9 dB, erreur du côté prudent.
- **Pièce sans absorption** : aigus un peu trop brillants (+1 à +2 dB).
- **Contrôle de bout en bout** (pièce plate, bruit blanc) : IR exportée plate à 0,4 dB près entre 5 et 20 kHz (vérité : 0,2 dB).

Version précédente écartée (`hybrid-hf-v1`, pentes en octaves) : niveaux +4,6 à +10,6 dB trop forts, RT60 ×1,1 à ×4,7.

## Ce que ce pilote ne prouve pas

- Qualité sur **dialogue de tournage réel**, en français, avec vraies pièces, bruit de plateau, perche en mouvement.
- Qualité **perceptive** : aucune écoute formelle réalisée.
- Robustesse à des IR réelles plus complexes que le modèle synthétique utilisé (proche des données de simulation typiques de l'entraînement, ce qui peut flatter le résultat).
- Justesse des aigus synthétisés sur de vraies pièces : l'extension est jugée contre un modèle d'absorption synthétique, et son réglage de niveau a été choisi en voyant une partie de ce pilote.

## Décision

Rec-RIR est intégré comme `blind_estimator` **expérimental**. Il est complété au rendu par l'extension hybride des aigus (§4.4, étape 4), marquée « synthétisée ». Prochaines étapes : lot D sur de vrais couples production/ADR et IR mesurées, puis décision sur la solution pleine bande.
