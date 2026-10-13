# Mimetic pour Mac Intel

Application autonome pour **Mac Intel sous macOS 12.7.6 Monterey**, Mimetic
**1.5**, build **1506**. Elle transforme une séquence Pro Tools en ZIP en un
ZIP contenant les rendus IR/MIX dans un PTX. Aucun Python, Git, compilateur
ou Pro Tools n'est nécessaire pour calculer le résultat.

Le lancement du build 1504 est confirmé sur Monterey. Le build 1505 a ajouté
la sortie dans Téléchargements; le build 1506 sépare les modules AppKit du
worker Python et prépare la distribution dans le dépôt. Windows ne permet
pas d'exécuter l'interface Mac de cette révision.

## Télécharger et lancer

Le [ZIP précompilé](releases/Mimetic-Mac-Intel-Monterey.zip) contient uniquement
**Mimetic.app**, avec son runtime, son modèle et sa template. Il est stocké via
**Git LFS** : utiliser le bouton de téléchargement du fichier sur GitHub, ou
`git lfs pull` après un clone. Le [manifeste](releases/release.json) donne la
taille, le build et le SHA256. Le petit pointeur LFS n'est pas l'application.

Extraire le ZIP sur le disque local du Mac, dans un dossier accessible en
écriture, par exemple le Bureau ou Téléchargements. `/Applications` n'est pas
requis et peut demander un compte administrateur.
Lors d'une mise à jour, quitter l'ancienne app et extraire la nouvelle copie
dans un dossier distinct avant de l'ouvrir.

Cette distribution utilise une signature personnelle **ad hoc**, sans Developer
ID ni notarisation Apple. Ouvrir Terminal, taper `MIMETIC_APP=`, glisser
`Mimetic.app` après le signe égal et appuyer sur Entrée. Exécuter ensuite :

```bash
codesign --force --deep --sign - "$MIMETIC_APP" &&
codesign --verify --deep --strict "$MIMETIC_APP" &&
xattr -rd com.apple.quarantine "$MIMETIC_APP" &&
open "$MIMETIC_APP"
```

Cela utilise les outils intégrés au Mac et ne compile rien. La commande
s'applique uniquement à Mimetic, une fois par nouvelle copie installée. Les
ouvertures et traitements suivants se font normalement depuis le Finder/Dock.
Si une commande échoue, conserver son message pour le diagnostic.

## Utilisation

Déposer un ZIP sur l'icône de l'app, dans sa fenêtre, ou utiliser **Choisir un
ZIP…**. Le ZIP d'entrée doit contenir la hiérarchie de session habituelle :

```text
MaSequence.zip
└── MaSequence/                     # dossier englobant facultatif
    ├── MaSequence.ptx
    └── Audio Files/
        ├── reference.wav
        └── adr.wav
```

Le résultat est créé dans **Téléchargements**, sous le nom
`MaSequence_Mimetic.zip`. Les résultats précédents sont conservés avec `_02`,
`_03`, etc. Le Finder sélectionne le résultat terminé. Si Téléchargements
n'est pas accessible en écriture, un sélecteur propose une autre destination.
Plusieurs ZIP sont traités dans une file; chaque traitement est annulable.

Le résultat contient un PTX, Audio Files et `Mimetic_report.json` :

- Session 48 kHz / 23,976 images/s, WAV mono PCM 16/24 bits ou float32.
- Pistes impaires = référence, pistes paires = ADR, quel que soit leur nom.
- Chaque ADR et sa référence commencent au même échantillon; jusqu'à trois couples.
- IR actifs avec **+3,5 dB de Clip Gain** dans le PTX, sans gain gravé dans le WAV.
- MIX mutés, Clip Gain 0 dB; queues complètes et queues qui se chevauchent sommées.
- Match EQ 100 %, wet supplémentaire 0 dB, prédélai supplémentaire 0 ms.
- ADR courts conservés, avec avertissements si l'analyse est incertaine.
- Consolider fades/groupes; plugins, automation et gains de mix d'entrée
  ne sont pas imprimés par Mimetic.
- Un PTX principal hors backups; métadonnées macOS ignorées.
- ZIP ≤ 2 Go, médias décompressés ≤ 4 Go, fichier individuel ≤ 2 Go.
- Région ≤ 10 min; îlot de sortie ≤ 16 777 215 échantillons (~349,5 s à 48 kHz).

Le traitement fonctionne hors ligne. Pro Tools est requis pour ouvrir et
écouter le PTX. L'app autonome propose le traitement de séquences ZIP; le
workflow SOURCE/DESTINATION interactif reste dans le service web.

Les journaux sont dans `~/Library/Logs/Mimetic` : `app.log` pour le démarrage,
`job-<identifiant>.log` par traitement et `engine.log` pour l'inférence.
Le bouton **Voir le journal** ouvre le journal courant.

## Organisation des sources

```text
../src/mimetic/                 # cœur audio Python et service web
macos-portable/
├── app/
│   ├── mimetic_macos/          # interface AppKit/PyObjC spécifique au Mac
│   ├── mimetic_portable/       # worker ZIP/PTX, processus et runtime embarqué
│   ├── mac_boot.py            # entrée du lanceur Mac
│   └── app_entry.py           # entrée du worker Python
├── tools/                     # snapshot, audits, empaquetage et contrôles
├── tests/                     # worker et adaptation Cocoa
├── validation/                # référence synthétique, sans dialogue
├── releases/                  # ZIP précompilé LFS et manifeste SHA256
├── assemble_windows.py        # assemblage depuis Windows
├── payload/                   # généré, ignoré par Git
├── build/                     # caches et essais privés, ignorés
└── dist/                      # sortie d'assemblage, ignorée
```

Le cœur audio n'est pas maintenu en double : `tools/prepare_snapshot.py` le
copie depuis `../src/mimetic` au moment de l'assemblage et adapte uniquement
les chemins, le lancement et la fermeture du processus d'inférence. Les poids
Rec-RIR, pt_api **1.6.3** et leurs licences sont récupérés à des révisions
épinglées, puis inclus dans l'app. Template PTX et WAV silencieux proviennent
des ressources du cœur. Aucun média de production n'est versionné.

## Reconstruire depuis Windows

Python 3.11 et Git sont requis sur la machine d'assemblage. Depuis la racine :

```powershell
py -3.11 -m venv macos-portable/.build-venv
macos-portable/.build-venv/Scripts/python.exe -m pip install -r macos-portable/requirements-assembly.txt
macos-portable/.build-venv/Scripts/python.exe macos-portable/tools/prepare_snapshot.py
macos-portable/.build-venv/Scripts/python.exe macos-portable/assemble_windows.py
macos-portable/.build-venv/Scripts/python.exe macos-portable/tools/package_release.py
```

Le snapshot utilise le Rec-RIR local de `third_party/` s'il est disponible;
sinon il clone la révision validée dans `build/vendor/`. pt_api est cloné dans
ce cache sans modifier un éventuel dépôt de travail séparé. Les SHA256 des
poids, du source pt_api, des composants et des wheels sont vérifiés.

Le builder télécharge CPython Intel 3.11.10, les wheels du runtime Mac, PyObjC
10.3.2 et le lanceur précompilé py2app 0.28.8. Il assemble l'app, contrôle ses
binaires et ressources, signe localement et écrit le ZIP dans `dist/`.
`package_release.py` vérifie le ZIP, puis copie l'app et son manifeste dans
`releases/`. Avec `--destination <dossier>`, il peut copier le ZIP dans un
dossier de livraison sans y ajouter d'autres fichiers.

## Vérifier

Pour les tests du worker sur Windows :

```powershell
py -3.11 -m venv macos-portable/.test-venv
macos-portable/.test-venv/Scripts/python.exe -m pip install torch==2.2.2 --index-url https://download.pytorch.org/whl/cpu
macos-portable/.test-venv/Scripts/python.exe -m pip install -r macos-portable/requirements-test.txt
macos-portable/.test-venv/Scripts/python.exe -m pytest macos-portable/tests -q
macos-portable/.test-venv/Scripts/python.exe -I -B macos-portable/app/app_entry.py --self-test
```

Préparer le snapshot avant ces contrôles. Sur le Mac, après autorisation de
l'app, `Verifier_App_Mac.command` accepte son chemin en argument, importe les
classes AppKit et lance le même autotest avec le Python embarqué.

```bash
bash macos-portable/Verifier_App_Mac.command "/chemin/vers/Mimetic.app"
```

Les anciens essais Swift/PyInstaller sont conservés localement dans
`build/legacy/`, hors Git. Ils ne participent pas à cette distribution.
Voir [ARCHITECTURE.md](ARCHITECTURE.md) et [VALIDATION.md](VALIDATION.md).
