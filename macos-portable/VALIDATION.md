# Validation Mac — 13 octobre 2026

La distribution actuelle est **Mimetic 1.5, build 1506**, Intel Monterey 12.7.6.
Le manifeste [releases/release.json](releases/release.json) identifie le ZIP exact.

Après nettoyage des sources : **22 tests Mac/worker passent**, **64 tests du
service web passent, un est ignoré**, et l'autotest d'inférence/export PTX passe.
Le compteur de cache web tolère désormais la disparition d'un dossier de job
pendant sa lecture; un test reproduit cette suppression pendant le parcours.

## Confirmé sur le Mac

Le build 1504 démarre après signature locale Apple et retrait de la quarantaine.
La fenêtre s'ouvre : les erreurs de signatures de protocole Cocoa sont corrigées.
Cette confirmation utilisateur ne constitue pas un audit de toutes les fonctions
de rendu, d'annulation et de file d'attente.

Le build 1505 a ajouté Téléchargements comme sortie par défaut. Le build 1506
sépare les modules Mac du worker Python; il doit être essayé sur le Mac pour
confirmer cette nouvelle disposition. Aucun compilateur n'est requis sur la cible.

## Contrôlé sur Windows

- **22 tests** du worker et de l'adaptation des sélecteurs Cocoa : régions ADR
  courtes, queues complètes, positions exactes, mutes/gains, Unicode, ZIP invalides,
  publication sans écrasement et annulation du processus. Les tests des sélecteurs
  utilisent les signatures runtime observées; ils n'exécutent pas AppKit.
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

## À confirmer sur la station cible

- Ouverture du build 1506 depuis un dossier utilisateur et import AppKit.
- Dépôt Finder/Dock, app fermée puis ouverte, et file de plusieurs ZIP.
- Autotest avec les bibliothèques **Mac** embarquées, via `Verifier_App_Mac.command`.
- Création du résultat dans Téléchargements et sélection par le Finder.
- Annulation, fermeture pendant un calcul et absence de résultat partiel.
- Import du PTX dans Pro Tools, timecodes, mutes/gains et écoute.
- Permissions et publication exclusive si une autre destination réseau est choisie.
