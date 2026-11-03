# Validation Mac — 17 octobre 2026

La distribution actuelle est **Mimetic 1.5.1, build 1508**, Intel Monterey 12.7.6.
Le manifeste [releases/release.json](releases/release.json) identifie le ZIP exact.

Après optimisation : **25 tests Mac/worker passent**, **64 tests du
service web passent, un est ignoré**, et l'autotest d'inférence/export PTX passe.
Le compteur de cache web tolère désormais la disparition d'un dossier de job
pendant sa lecture; un test reproduit cette suppression pendant le parcours.

## Confirmé sur le Mac

Le build 1504 démarre après signature locale Apple et retrait de la quarantaine.
La fenêtre s'ouvre : les erreurs de signatures de protocole Cocoa sont corrigées.
Cette confirmation utilisateur ne constitue pas un audit de toutes les fonctions
de rendu, d'annulation et de file d'attente.

Le build 1505 a ajouté Téléchargements comme sortie par défaut. Le build 1506
sépare les modules Mac du worker Python. Le build 1507 ajuste le parallélisme
CPU et réutilise les calculs fixes du moteur. Le 17 octobre, l'utilisateur
confirme que ce build fonctionne et semble légèrement plus rapide; aucun
chronométrage Mac contrôlé n'est disponible. Le build 1508 reprend le même
traitement avec les métadonnées de version 1.5.1. Aucun compilateur n'est
requis sur la cible.

## Contrôlé sur Windows

- **25 tests** du worker et de l'adaptation des sélecteurs Cocoa : régions ADR
  courtes, queues complètes, positions exactes, mutes/gains, Unicode, ZIP invalides,
  publication sans écrasement et annulation du processus. Les tests des sélecteurs
  utilisent les signatures runtime observées; ils n'exécutent pas AppKit.
  La calibration est testée avec Torch réel : générateur aléatoire inchangé,
  cache réutilisé sans recalcul, configuration invalide recalibrée et cache
  inaccessible toléré.
- Inférence intégrale Torch 2.2.2 comparée à une entrée synthétique identique
  sous Torch 2.14.0 : erreur relative L2 **7,63 × 10⁻⁷**, tolérance **0,002**.
- Séquence privée de 16 ADR : 28 placements, six pistes, sans salle synthétique
  de secours. Comparaison aux rendus Windows : L2 max **1,68 × 10⁻⁶**, différence
  max **3,54 × 10⁻⁸**. Les médias et sorties de cet essai restent hors Git.
- **178 binaires Mach-O Intel**, compatibilité Monterey et dépendances embarquées;
  **231 202 pages de code** et hashes de ressources contrôlés après signature.
- ZIP relu par CRC, permissions exécutables, template PTX et prototype WAV inclus.
- Source pt_api et poids Rec-RIR vérifiés par SHA256, composants et wheels
  vérifiés contre leurs distributions officielles.

Les preuves détaillées d'assemblage restent dans `dist/ASSEMBLY_REPORT.json`
et `build/`, hors Git. La référence versionnée `validation/engine_baseline.npy`
vient d'une entrée synthétique de deux secondes, sans audio de production.

## Comparaison avant/après optimisation

La récurrence Mamba d'origine est conservée. Modèle, poids, FP32, fenêtres
d'analyse, EQ et queues ne sont pas réduits. Une variante de calcul évaluée
localement n'a pas été retenue pour la distribution.

Sur Windows, Torch 2.2.2, 12 processeurs logiques, même entrée synthétique de
deux secondes et modèle chargé : médiane de deux inférences **9,36 s** avec
12 threads contre **7,73 s** avec 8 threads, soit **17,4 % de temps en moins**.
L'écart relatif L2 entre IR est **4,90 × 10⁻⁸**, et l'échantillon du pic est
identique. Ce résultat ne prédit pas le gain du build Mac ni celui d'un ZIP entier.

Le test complet de 16 ADR compare les 28 WAV du nouveau pipeline aux sorties
portables précédentes sous le même Torch 2.2.2 : erreur L2 max
**4,47 × 10⁻⁸**, différence d'échantillon max **1,49 × 10⁻⁸**. Les longueurs,
placements, mutes, gains, fenêtres sélectionnées, arrivées directes, pics et
avertissements sont identiques. Ce sont de minuscules écarts d'arrondi;
l'identité bit à bit n'est pas revendiquée.

Cet essai prend **413,9 s** au total, dont **411,9 s** pour analyse/EQ/rendu,
**0,43 s** pour import/validation et **1,43 s** pour assemblage/vérification
PTX. D'autres contrôles et l'assemblage de l'app étaient exécutés en parallèle;
ce temps total sert à localiser le coût, pas à mesurer un gain avant/après.
Les données détaillées restent dans `build/performance/`, hors Git.

## À confirmer sur la station cible

- Ouverture du build 1508 depuis un dossier utilisateur et import AppKit.
- Dépôt Finder/Dock, app fermée puis ouverte, et file de plusieurs ZIP.
- Autotest avec les bibliothèques **Mac** embarquées, via `Verifier_App_Mac.command`.
- Création du résultat dans Téléchargements et sélection par le Finder.
- Annulation, fermeture pendant un calcul et absence de résultat partiel.
- Import du PTX dans Pro Tools, timecodes, mutes/gains et écoute.
- Permissions et publication exclusive si une autre destination réseau est choisie.
- Même ZIP avant/après sur le Mac, durée affichée et écoute des IR/MIX.
  La première calibration ajoute un petit coût; elle est réutilisée ensuite.
