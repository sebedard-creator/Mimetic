# Validation — beta 0.1.0b1

## Mise à jour 2026-09-16 (2) — page unique + aigus hybrides

- `python -m pytest -q` : **31 passés, 1 sauté** (test Mamba, passé dans `.venv-engine`).
- **Nouveaux tests** :
  - flux automatique : la source seule ne lance rien ; source + destination déclenchent analyse et rendu ; un réglage ou une nouvelle destination relancent le rendu seulement ; un changement de canal de la source relance l'analyse ;
  - moteur absent → échec `MODEL_UNAVAILABLE` sans profil ni rendu ;
  - export relu identique à l'écoute ; traversée de chemin refusée ;
  - choix automatique des passages actifs ;
  - extension hybride : déterministe, bande estimée conservée, aigus présents mais jamais plus forts que 5–7 kHz, RT bornés, rien avant la première réflexion, marquage `HF_SYNTHESIZED`, 44,1 kHz.
- **Pilote pleine bande** (6 pièces) : voir [model-evaluation.md](model-evaluation.md).
- **Vérifié dans la page avec le vrai moteur** :
  - dépôt source (11 s, 48 kHz) + destination → analyse automatique → « Reverb prête », RT60 0,55 s / DRR 3,5 dB (vérité 0,60 s / 3,0 dB) ;
  - curseur de niveau → nouveau rendu sans nouvelle analyse ;
  - export sans écrasement (`_2`) ;
  - IR exportée avec aigus jusqu'à 20 kHz ;
  - destination pleine bande → reverb pleine bande ;
  - mise en page mobile et bureau.
- **Piège de test relevé** : une DESTINATION sans aigus (ex. synthèse vocale 16 kHz) donne une reverb sans aigus, extension ou non.
- **Toujours pas fait** : dialogue de tournage réel, écoute, DAW, accès depuis un autre poste.

---

## Mise à jour 2026-09-16 — moteur Rec-RIR intégré

- `python -m pytest -q` : **27 passés, 1 sauté**. Le test d'équivalence Mamba nécessite PyTorch ; il a été exécuté et passé dans `.venv-engine`.
- **Nouveaux tests** :
  - canonicalisation : alignement, retrait du direct, réflexions à leur retard et à leur gain relatif ;
  - direct antérieur préféré à une réflexion plus forte ;
  - direct non résolu → rendu bloqué ;
  - plan de fenêtres, médoïde, conversion 48 → 16 kHz ;
  - moteur absent → `MODEL_UNAVAILABLE`.
- **Pilote synthétique** de 18 cas : voir [model-evaluation.md](model-evaluation.md).
- **Vérifié dans la page** (service réel, référence de parole réverbérée à 48 kHz) :
  - analyse de 2 fenêtres (~66 s) ;
  - annulation : worker arrêté, profil précédent conservé ;
  - profil affiché avec ses limites ;
  - rendu et export ;
  - changement de sélection → profil et rendu obsolètes, rendu refusé (`STALE_RESULT`) ;
  - retour à la sélection d'origine → profil de nouveau valide.
- **Toujours pas fait** : dialogue de tournage réel, écoute, DAW, accès depuis un autre poste.

---

Date : 2026-09-16 · Machine : Windows 10, Python 3.11.9, numpy 2.2.6, scipy 1.13.1, soundfile 0.12.1, fastapi 0.111.0.

## Tests automatiques (`python -m pytest -q`) — 23 passés

Couverture des tests bloquants §10.1 :

| # | Test | Fichier |
|---|---|---|
| 1 | IR connue `delta[0] + 0.25 delta[k]` : wet = 0,25·ADR retardé, zéros exacts avant k, mélange = convolution totale | `tests/unit/test_dsp.py` |
| 2 | Direct seul → wet nul | idem |
| 3 | Réflexion dominante : l'indice déclaré fait foi | idem |
| 4 | Direct étalé : fenêtre retirée, réflexions conservées, calibration énergétique | idem |
| 5 | Linéarité (×2 ADR, +6,0206 dB), dry inchangé | idem |
| 6 | Silence, IR nulle, gain direct minuscule, NaN, ADR vide | idem |
| 7 | Silence initial, pré-délai, longueur `N+M-1` | idem |
| 8 | `oaconvolve` vs `np.convolve` float64 (≤ 1e-6) | idem |
| 9 | Conversion d'IR 16→48, 16→44,1, 48→44,1, 44,1→48 : gain à 1 kHz ≤ 0,1 dB, délai ≤ 1 échantillon | idem |
| 10 | Export float relu ≤ 1e-6, valeurs > 1 conservées, pas d'écrasement, nom avec accents/espaces | `tests/unit/test_engines_io.py` |
| 11 | Le renderer ne reçoit que profil + ADR (par construction de `pipeline.render`) ; pas de test de contenu dédié | — |
| 12 | Sélection L/R/moyenne explicite, refus de « mono » sur un fichier 2 canaux | `tests/unit/test_dsp.py` |

Autres : backend manuel déterministe, DRR calibré, T20 médium à ±20 % du RT60 demandé, `TAIL_TRUNCATED` ; `blind_estimator` refuse ; validation d'import (fréquence, FLAC renommé en .wav, fichier corrompu, durée max) ; workflow web complet (import, `MODEL_UNAVAILABLE`, profil, rendu, obsolescence après changement de profil, export refusé si obsolète, téléchargement = tableau d'écoute, traversée de chemin refusée).

**Non couvert** : rendu par blocs (non implémenté, rendu monobloc avec budget mémoire), BWF TimeReference (non copié), stéréo.

## Vérifications manuelles

- Service lancé, page ouverte via `localhost` et via l'IP LAN `10.0.0.30` depuis la machine hôte.
- Import référence stéréo + ADR, profil manuel, rendu, curseur de niveau → re-rendu automatique, transport partagé ADR / reverb seule / mélange (même position), export WAV float 48 kHz mono + JSON.
- **Pas encore fait** : accès depuis un autre poste (pare-feu), écoute réelle, superposition dans un DAW, fichiers de tournage réels.

## Limites connues

- Aucun matching automatique : les profils sont manuels ou issus d'une IR fournie.
- La suggestion d'indice direct (premier échantillon à 50 % du pic) n'est pas un détecteur validé.
- Le pic wet peut dépasser 0 dBFS sur des signaux tonaux de test : conservé et signalé (`MIX_OVER_0DBFS`), aucun limiteur.

## Prochain lot

Lot B : audit Rec-RIR (`docs/model-evaluation.md`), puis adaptateur `blind_estimator` en sous-processus.
