# Architecture Mac

## Frontières du code

`src/mimetic/` reste le cœur audio Python et le service web. Les sources Mac
sont isolées sous `macos-portable/`; elles n'ajoutent aucune dépendance Cocoa
au package principal ni à ses installations Windows.

Dans `app/`, `mimetic_macos` contient la fenêtre AppKit et les sélecteurs des
protocoles Cocoa. `mimetic_portable` contient le worker ZIP/PTX, la publication
atomique, le protocole de processus et les autotests. Le worker s'utilise dans
les contrôles Windows sans importer `mimetic_macos` ni PyObjC.

Le payload est un snapshot **généré et ignoré par Git** du cœur, des composants
tiers et des ressources. `prepare_snapshot.py` produit un manifeste SHA256 des
fichiers et des sources originales. L'adaptation du cœur ne touche que les
chemins du runtime, le lancement du worker et la fermeture de ses handles.
Le DSP, l'analyse EQ et la construction PTX restent ceux de `src/mimetic`.

## Lanceur et runtime

Le Mach-O Intel précompilé de py2app **0.28.8** est l'exécutable principal.
Il charge le CPython portable **3.11.10** de python-build-standalone
**20241016**, installé sous `Contents/Resources`. L'auxiliaire `MacOS/python`
est un trampoline shell. `mac_boot.py`, copié en `Resources/__boot__.py`,
établit les chemins internes, fixe `sys.executable` au Python embarqué et ouvre
`~/Library/Logs/Mimetic/app.log`. Aucune installation Python externe n'est utilisée.

L'interface utilise **PyObjC 10.3.2**. Chaque callback lit sa signature dans
le protocole Cocoa réel via `objc.protocolNamed(...).instanceMethods()`, puis
la déclare avec `objc.namedSelector`. Cela évite les différences entre les
métadonnées de la wheel et le runtime Monterey (`I`/`Q` et `Z`/`c`).

Les événements Finder et le dépôt dans la fenêtre alimentent une file
séquentielle. Le dossier de sortie vient de Foundation : `NSDownloadsDirectory`
dans `NSUserDomainMask`. Le chemin est créé au besoin et résolu avant de lancer
le worker. Un sélecteur propose une autre destination s'il n'est pas accessible.

## Processus et annulation

`desktop_runner.py` lance le Python embarqué avec `-I -B app_entry.py`,
`--process <ZIP>` et `--output-dir <dossier>`. stdout est réservé aux événements
JSON UTF-8; stderr va dans un journal de traitement. Un thread lit la sortie et
transmet les événements au thread AppKit avec `AppHelper.callAfter`.
Il vide stdout avant de notifier `exit`, afin de conserver l'événement `ready`.

Le job et son moteur appartiennent à un groupe de processus dédié. Annuler
envoie SIGTERM au groupe, puis SIGKILL après trois secondes s'il vit encore.
La fermeture attend cette annulation. Les nettoyages concernent uniquement le
workspace `.mimetic-*` du job sous sa destination, jamais le ZIP d'entrée.

## Pipeline et publication

L'import ZIP vérifie les chemins, doublons, liens et limites de taille, puis
résout le PTX principal et ses médias. Le cœur lit les couples impairs/pairs,
estime la salle et l'EQ, rend l'ADR et construit la session à six pistes.
IR actifs à +3,5 dB de Clip Gain et MIX mutés sont stockés comme métadonnées PTX.
Les queues complètes sont conservées; les recouvrements sont sommés en îlots.

Le ZIP est d'abord construit dans un dossier temporaire sur le volume de
destination. macOS le publie avec `renamex_np(RENAME_EXCL)` : il apparaît complet,
sans écraser un résultat existant. Les noms suivants utilisent `_02`, `_03`, etc.
Une erreur ou annulation ne publie pas de ZIP partiel.

## Assemblage et distribution

`prepare_snapshot.py` vérifie pt_api **1.6.3**, commit
`c31147bf427e763f7be900e6077368b615d47e24`, et Rec-RIR, commit
`b3ac6fc1421bd58e017022037feb14f30366b46f`. Les poids ont le SHA256
`afe36e477f6fb3656d162d906a47da2d40a789634b476564266fc1ba36037478`.
Les licences et le source LGPL de pt_api sont inclus dans l'app.

`assemble_windows.py` télécharge les composants et wheels vérifiés, assemble
le bundle et inspecte les architectures Intel, versions minimales et chemins
de bibliothèques. Le runtime audio est NumPy **1.26.4** / PyTorch **2.2.2** :
la wheel Intel Torch précède l'ABI NumPy 2. La template et son WAV silencieux
requis sont copiés depuis les ressources du cœur.

La signature est **ad hoc**, via rcodesign **0.29.0**, sans certificat Apple
Developer ID ni notarisation. `signature_audit.py` vérifie les hashes des pages
CodeDirectory et les ressources. Ces audits Windows ne remplacent pas Apple
`codesign` ni les essais natifs. La procédure locale de lancement est dans le README.

Le ZIP contient uniquement `Mimetic.app`, avec les modes Unix exécutables.
`package_release.py` vérifie le CRC, les modes, la template et l'empreinte du ZIP,
puis produit `releases/release.json`. Seule cette archive validée est versionnée
via Git LFS. Caches, environnements, snapshots, essais et médias restent ignorés.

Les anciens essais Swift/PyInstaller restent dans le dossier local
`build/legacy/` et ne font pas partie des sources publiées.

Références : [CPython portable](https://github.com/astral-sh/python-build-standalone),
[py2app](https://py2app.readthedocs.io/en/latest/),
[PyObjC](https://pyobjc.readthedocs.io/en/latest/),
[rcodesign](https://gregoryszorc.com/docs/apple-codesign/stable/).
