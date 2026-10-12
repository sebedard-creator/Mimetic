# Mimetic — import de session et export Pro Tools avec pt_api

Date : 2026-10-12. Statut : architecture proposée, preuve technique locale et retour de sauvegarde native Pro Tools ; intégration à l'interface non implémentée.

Demande : isoler deux pistes d'une session, **Source** et **Destination**, analyser/rendre avec Mimetic, puis obtenir une session PTX contenant la reverb wet et le mix traité aux positions du montage. L'utilisateur confirme un usage principalement en **48 kHz / 23,976 images/s**.

## 1. Faisabilité vérifiée

Oui, dans ce périmètre. La version auditée de `pt_api` est **1.6.2**, commit `e0a7cb6ab645ee1a763ecea875f88344c496868d`, disponible localement dans `Y:/pt_api` et sur [GitHub](https://github.com/sebedard-creator/pt_api). Son code lit les pistes et les événements audio, expose leurs positions en échantillons et construit une session depuis un template compatible. Il ne rend pas le son et ne crée pas arbitrairement des pistes.

Sources : [README de pt_api](https://github.com/sebedard-creator/pt_api/blob/master/README.md), [implémentation](https://github.com/sebedard-creator/pt_api/blob/e0a7cb6ab645ee1a763ecea875f88344c496868d/pt_api.py), [contrats du format](https://github.com/sebedard-creator/pt_api/blob/master/pt_format_specs.md).

### Preuve locale

`benchmarks/ptx_export_probe.py` construit un ADR synthétique et une pièce connue, utilise le rendu Mimetic existant, produit deux WAV compatibles et appelle réellement le builder.

Résultats de la vérification finale :

- Template natif validé : `native_float_15_142`, deux pistes existantes `PFX 01` et `PFX 02`.
- Placement des deux sorties : **178 906 865 échantillons**, affiché `01:02:03:12`, avec **137 échantillons supplémentaires** après cette frontière d'image.
- Longueur de chaque rendu : **108 000 échantillons**, queue incluse.
- WAV copiés par le builder identiques à l'octet ; échantillons relus identiques aux rendus float32 de Mimetic.
- Placement wet muté par défaut, placement mix audible : éviter de sommer deux fois la reverb.
- Rechargement PTX et sauvegarde API sans modification identiques à l'octet.

Livrable : `data/ptx-probe-20261012-final/Session/Mimetic_PTX_probe.ptx`, avec son dossier `Audio Files` et un rapport `verification.json` dans le dossier parent.

**Ce que ce test ne prouve pas :** extraction d'un montage réel, fidélité des fades/automations, analyse Rec-RIR ou Match EQ. Le MIX de démonstration est dry + wet sans EQ ; ce n'est pas un export `_EQ_IR_MIX` de production. Les pistes du template conservent leurs noms de fixture.

### Retour natif Pro Tools — vérifié le 12 octobre 2026

L'utilisateur a ouvert la copie du partage puis sauvegardé par-dessus le PTX. La relecture avec pt_api confirme des timelines identiques : positions **178 906 865**, durées **108 000**, offsets zéro, IR_ONLY muté et MIX audible, 48 kHz et enum `0x09` conservés. Les deux chunks WAV `data` et leurs échantillons sont identiques à la copie générée ; Pro Tools a ajouté des métadonnées `DGDA`, `minf` et `regn` sans modifier le son. Les TimeReference BWF sont conservés. Le PTX sauvegardé nativement supporte un cycle API de sauvegarde identique à l'octet.

L'archive et le rapport sont dans `Y:/SHARE TO NETWORK/Mimetic_Interoperabilite/2026-10-12_Test_01_PTX/Retours_Pro_Tools`. Ce résultat valide le placement et le package de démonstration sur ce template ; il ne certifie pas les autres profils ou l'extraction d'un montage réel. La version de Pro Tools et l'évaluation d'écoute n'ont pas été communiquées.

### Premier import de montage réel — blocage confirmé

Le 12 octobre 2026, `pour_test.ptx` a été testé dans le dossier partagé `2026-10-12_Test_02_Import`. Le conteneur, la fréquence/cadence et les playlists `Audio 1` puis `1PS` sont lus ; les 7 médias mono PCM24/48 kHz sont présents et la sauvegarde API sans édition est identique à l'octet. Selon la convention utilisateur, Audio 1 est la référence et 1PS l'ADR.

L'import audio complet est néanmoins bloqué : `get_clips()` et `get_timeline_clips()` refusent le sélecteur `0x22` du payload `0x2628` du clip ID 9, `SIM_020_06-17.L`, réellement placé sur 1PS. Ne pas contourner ce refus en devinant sa durée. Le montage comporte aussi des fondus et son profil Track Volume n'est pas reconnu par le lecteur d'automation. Le compte rendu et le rapport détaillé sont dans `2026-10-12_Test_02_Import/Resultats`. Une extension vérifiée du lecteur est nécessaire avant de présenter cette session comme prise en charge par l'import Mimetic.

### Retest avec les correctifs locaux — lecture désormais validée

Le retest du 12 octobre utilise le module local `Y:/pt_api/pt_api.py`, SHA-256 `c6a72a6b39ec8a8a6fedd678e11e91b069ec248bc5abe6b87c86236dc8e5dee6`, toujours déclaré 1.6.2 mais modifié et non commité. Sur la session partagée actuelle (SHA-256 `643815b371f1aa52e20204be7a23e0cabd9d71aaacefff31c924af90348f1112`), le lecteur public retourne 13 définitions, 14 placements audio et 13 fades. Les 14 régions se lisent dans les 7 WAV aux offsets/longueurs exacts. Track Volume est lu à 0 dB sur les deux pistes. La sauvegarde API sans édition est byte-identique et les 689 régressions locales passent.

Le sélecteur `0x22` est un profil Sync Point vérifié : le début audible de `SIM_020_06-17.L` est **1 740 341 136**, et non l'ancre brute **1 740 374 500**. La durée **43 697** et la fin **1 740 384 833** concordent avec la capture native `3.png`. Le crossfade compact de 337 échantillons concorde avec `4.png`. La limite de lecture du premier rapport est donc levée avec ces correctifs locaux ; les restrictions d'écriture de ces profils restent distinctes.

Compte rendu : `2026-10-12_Test_02_Import/Resultats/Retest_Mimetic_API_locale/RESULTAT_RETEST.md`. L'extraction testée est brute : elle n'imprime pas encore les fades/crossfades ou le Clip Gain dans le son. Plusieurs fragments ADR étant courts, la prochaine étape est la reconstruction d'une région continue de montage avant l'analyse EQ, avec conservation des trous et des positions. Aucun traitement Mimetic complet de cette session n'est encore déclaré validé.

## 2. Parcours utilisateur proposé

Ajouter un mode **Session Pro Tools** à côté de l'import WAV actuel.

1. Importer le PTX et ses médias, ou indiquer un dossier de session accessible au service.
2. Choisir deux pistes par leur nom : rôle **Source**, rôle **Destination**. Les noms littéraux « Source »/« Destination » peuvent être proposés automatiquement s'ils sont uniques, mais le choix doit rester explicite.
3. Afficher les clips, les plages temporelles et les médias trouvés/manquants. Choisir la séquence à traiter, ou utiliser les clips de la Destination dans la sélection.
4. Sélectionner une référence représentative de cette captation ; proposer les régions Source présentes dans cette séquence. Ne pas considérer une scène entière comme une seule acoustique par défaut.
5. Analyser la référence, rendre la Destination et proposer l'écoute avec les réglages Mimetic existants.
6. **Exporter une session Pro Tools** : un dossier/ZIP contenant PTX, `Audio Files` et rapport, avec deux pistes `MIMETIC_IR` et `MIMETIC_MIX` aux positions de la Destination.

La piste Source sert à l'analyse ; son audio n'est pas inséré dans les rendus. La position de sortie vient de la Destination. La Source peut contenir une autre réplique utilisée comme référence : elle n'a pas à commencer au même TC ni à avoir la même longueur.

Il faut distinguer ici **deux rôles de pistes d'une session** et le mode Mimetic actuel **deux canaux dans chacun de deux WAV**. Sélectionner une piste Source mono et une piste Destination mono forme un seul couple de traitement. Cela ne demande pas de les entrelacer ni de lancer A1/A2.

### Médias locaux et navigateur

Un PTX ne contient pas les formes d'onde : un import PTX seul ne suffit pas. Le serveur doit pouvoir lire tous les WAV référencés par les placements sélectionnés.

Deux modes d'accès possibles :

- Service et session sur la même machine : sélection d'un dossier configuré accessible au service.
- Navigateur sur une autre machine du LAN : envoi du PTX et des médias, par dossier importé ou archive de session. Un chemin disque du client n'est pas un chemin du serveur.

Pour le premier lot, retenir une seule méthode d'import complète. Conserver les chemins relatifs et un manifeste de résolution. Deux médias homonymes dans différents dossiers sont une ambiguïté à résoudre, pas une raison de choisir le premier.

## 3. Contrat temporel — aucune conversion par TC affiché

Utiliser les **entiers en échantillons de la session** comme unité interne. Le timecode est une présentation pour l'utilisateur.

`get_timeline_clips()` fournit notamment :

```text
track, clip_name
start_samples, length_samples, end_samples
src_offset_samples, physical_filename
muted, is_fade
```

Pour un placement simple, sans traitement temporel :

```text
audio_utilisé = média[src_offset_samples : src_offset_samples + length_samples]
origine_du_rendu = start_samples
fin_du_rendu = origine_du_rendu + longueur_rendue
```

Ne pas replacer le clip au TimeReference du fichier source : son TC de prise peut être différent de sa position dans le montage. Ne pas convertir `start_samples → HH:MM:SS:FF → samples` : on perd les positions entre images.

Pour une extraction tronquée par une sélection `[a, b)` :

```text
début_effectif = max(start_samples, a)
fin_effective = min(end_samples, b)
offset_effectif = src_offset_samples + début_effectif - start_samples
```

Les intervalles sont fermés à gauche, ouverts à droite. La référence et l'ADR peuvent avoir des origines d'extraction différentes, conservées dans leurs métadonnées. Ne pas ajouter automatiquement une heure ni soustraire le début de la séquence au placement final.

Avec `TimecodeEngine`, l'usage vérifié est :

```python
engine = TimecodeEngine(48000, 0x09)
n = engine.timecode_to_samples(hh, mm, ss, ff)
display = engine.samples_to_timecode(n)
```

Le template de sortie doit avoir une grille/affichage temporel cohérent avec la session importée. Les réglages d'origine d'affichage particuliers doivent faire partie du préflight et d'un contrôle natif : ne pas supposer que le seul enum de cadence couvre tous les réglages de session. Le placement d'origine reste l'entier lu dans le PTX.

## 4. Extraire le son : pt_api lit le montage, Mimetic doit le rendre

La lecture d'une session n'est pas un bounce Pro Tools. Des médias et leurs placements ne reproduisent pas automatiquement les fades, Clip Gain, Track Volume, plugins, inserts, time-stretch ou traitement Elastic Audio.

### Premier périmètre recommandé

- Deux pistes audio **mono** sélectionnées.
- Session et médias à **48 kHz**, session à **23,976** (enum exact `0x09`).
- Clips audio ordinaires, placements et offsets résolus sans ambiguïté.
- Pas de fade/crossfade, Clip Group, chevauchement ambigu ou transformation temporelle dans les régions traitées.
- Son consolidé sans ces traitements, ou validation explicite du mode « médias bruts » : ne pas annoncer la reproduction du mix de la piste.
- Source exploitable pour Rec-RIR et EQ selon les règles Mimetic existantes.

Lorsque le montage contient des éléments non reproduits, demander une version consolidée de la séquence à son TC correct, avec le traitement souhaité déjà imprimé. Ne pas contourner un fondu en appelant seulement `get_timeline_clips(include_fades=False)` : cela masque le renseignement sans rendre l'audio équivalent.

Le préflight doit inspecter les fades avec le lecteur complet et les Clip Groups avec leur lecteur dédié. Il doit aussi définir comment détecter les traitements hors périmètre ; toute propriété non vérifiée apparaît comme telle dans le résumé d'import. Les clips mutés sont exclus du signal sauf choix explicite.

### Un clip ou plusieurs clips

Pour la première livraison, une unité de traitement correspond à un clip Destination ou une région Destination consolidée, associé à un extrait Source choisi. On conserve ainsi l'origine de chaque rendu, ses statistiques EQ et sa queue.

Pour plusieurs ADR de la même captation, réutiliser le profil de pièce, puis calculer une EQ par réplique. Lier la référence à une unité/zone de scène explicite. Ne pas apparier Source et Destination simplement par numéro de clip, ni supposer que le clip le plus proche en TC convient acoustiquement.

Les queues wet de répliques successives peuvent se chevaucher. Pour une première session à deux pistes, sommer les rendus dans un stem continu de la séquence à partir d'une origine commune, avec la queue finale conservée. Cela évite qu'une priorité de playlist Pro Tools masque une queue sous le clip suivant. Vérifier les pics du stem après somme.

Une optimisation ultérieure peut écrire des stems par îlots séparés, en garantissant que les plages exportées sur une même piste ne se chevauchent pas. Le builder trie les placements chronologiquement ; cette fonction ne garantit pas qu'un chevauchement soit mixé comme une somme.

## 5. Écrire les sorties PTX

### Signification des deux pistes

| Sortie | Audio | Placement | Écoute prévue |
|---|---|---|---|
| `MIMETIC_IR` | Reverb 100 % wet de l'ADR | Origine Destination, queue incluse | En parallèle de l'ADR voulu |
| `MIMETIC_MIX` | ADR avec EQ + reverb | Même origine | Remplace l'ADR |

« IR » désigne ici le stem `IR_ONLY`, pas l'impulsion `IR_PROFILE`. Une impulsion de pièce n'est pas une réplique et ne doit pas être présentée comme un rendu à poser au TC du dialogue. Son export peut rester disponible séparément.

Les deux sorties sont des alternatives : MIX contient déjà la reverb. Le package doit prévenir leur lecture simultanée ; muter les placements de `MIMETIC_IR` par défaut avec `mute_clip()` est une solution déjà vérifiée dans la preuve locale. Le manifest et l'interface doivent expliquer cette convention.

L'EQ doit être réellement calculée et valable pour le MIX de production. Conserver la règle existante d'échec explicite quand elle est indisponible. Un rendu à l'identité accepté par l'analyse ne doit pas être confondu avec une analyse absente ; couvrir ce cas dans les tests du nouveau chemin.

### Template

L'API remplit des slots existants ; elle ne crée pas deux pistes à partir de n'importe quelle session. Utiliser un template natif vide possédant au moins deux pistes mono, un prototype audio compatible dans la Clip List et aucune région sur la timeline.

Exécuter `ProToolsSession(template).validate_audio_import_template()` avant tout rendu coûteux. Choisir/renommer deux slots existants à l'aide des méthodes publiques vérifiées, puis revalider le template modifié. Conserver sa provenance et son empreinte.

Templates locaux vérifiés pendant cet audit :

- `Y:/pt_api/pfx_import_reference/01_A_imported_cliplist/01_A_imported_cliplist.ptx` : `native_float_15_142`, pistes `PFX 01`, `PFX 02`. Utilisé pour la preuve courte.
- `Y:/pt_api/tests/fixtures/walla_template_native_float_31_151_u32.ptx` : `native_float_31_151_u32`, une seule piste `Walla Gen`. Compatible pour écrire de longues durées, insuffisant pour les deux sorties dans une même session.

Pour des séquences longues, préparer un template natif **UInt32 à deux pistes**, puis le faire valider par l'API et dans Pro Tools. Le profil historique UInt24 limite un média à `16 777 215` échantillons, soit environ **5 min 49,5 s** à 48 kHz, queue comprise. La durée maximale de Mimetic, 10 min, ne peut pas être transmise silencieusement à ce profil.

### WAV BWF compatibles

L'écriture actuelle `io.write_float_wav()` produit un WAV float ordinaire ; elle ne fournit pas à elle seule le contrat du builder. Ajouter un writer dédié au package PTX :

- WAV mono, 48 kHz, float32 **WAVE_EXTENSIBLE**, GUID IEEE float correct.
- `fact` contenant le nombre d'échantillons, cohérent avec `data`.
- `bext` suffisamment long, TimeReference et basic UMID non nul selon le contrat vérifié.
- Identifiants distincts pour chaque média généré ; politique d'allocation UMID documentée et validée.
- Audio float32 identique aux échantillons exportés, sans normalisation ou limiteur ajouté.

L'API exige un TimeReference BWF UInt32 pour ses headers physiques vérifiés, alors que `placement_start_samples` accepte UInt64. Pour le premier mode, utiliser l'origine de rendu pour les deux et refuser explicitement les valeurs dépassant la borne du BWF ; ne pas croire que le UInt64 de timeline étend automatiquement tous les champs.

Ne pas réutiliser l'UMID du média ADR : les rendus sont de nouveaux médias. Le writer de démonstration utilise un UMID synthétique non nul accepté par le validateur ; son acceptation n'est pas une certification complète d'une politique de production.

### Appel public du builder

```python
specs = [
    {"audio_path": wet_bwf_path, "track_name": "MIMETIC_IR",
     "clip_name": "SEQ01_IR", "placement_start_samples": sequence_origin_samples},
    {"audio_path": mix_bwf_path, "track_name": "MIMETIC_MIX",
     "clip_name": "SEQ01_MIX", "placement_start_samples": sequence_origin_samples},
]
bundle = build_audio_session(template_path, specs, new_output_directory,
                             session_name="SEQ01_Mimetic")
```

Le nom des pistes doit réellement exister dans le template. Le builder copie les WAV sans convertir leur audio et crée le PTX/`Audio Files` dans un nouveau dossier. Ne pas écraser la session importée. Le package autonome peut être importé dans la session de montage via le parcours Pro Tools habituel.

## 6. Contrats internes et séparation du moteur

Ajouter des données d'import de session plutôt que détourner `AudioAsset` :

```text
SessionImport
  session_hash, api_version, sample_rate, frame_rate_enum
  source_track, destination_track, media_manifest, selection, warnings

TimelineRegion
  placement_key, playlist_name, clip_name
  start_samples, length_samples, source_offset_samples
  resolved_media_path, media_hash, channel, muted, unsupported_features

ProcessingUnit
  reference_region_ids, destination_region_ids
  render_origin_samples, room_profile_id, EQ settings, rendered_assets
```

Un nom de clip n'est pas une identité unique : un même clip peut être placé plusieurs fois. Utiliser une identité de placement incluant la playlist, la position, l'offset et un ordinal lorsque nécessaire. Toute ambiguïté doit être résolue avant traitement.

Modules proposés :

- `integrations/pro_tools/session_reader.py` : pt_api → données de session, résolution des médias, préflight.
- `integrations/pro_tools/audio_extract.py` : lecture des régions autorisées, extraction aux offsets exacts.
- `integrations/pro_tools/bwf_writer.py` : emballage des rendus conforme au builder.
- `integrations/pro_tools/session_export.py` : template, manifest, création, relecture et publication du package.

Le DSP existant ne dépend pas de pt_api. Le mode PTX produit des assets audio qui passent par les mêmes analyses/rendus, puis ajoute leurs origines de montage à l'export. Les parcours WAV et A1/A2 existants doivent garder leur comportement.

Épingler pt_api au commit audité dans une dépendance optionnelle/documentée. Ne pas copier sans version le fichier depuis un checkout qui peut changer. Conserver les notices LGPL de la dépendance distribuée et le code de liaison séparé. Le dépôt API local a une modification préexistante de `handoff.md`, laissée intacte pendant cet audit.

## 7. Lots et validation

1. **Import en lecture seule** : PTX + médias, choix des deux pistes, liste des placements et préflight. Un chemin manquant ou ambigu bloque l'unité concernée avant inférence.
2. **Extraction simple** : mono, offsets, sélection, exclusion des clips mutés, règles explicites des éléments non rendus.
3. **Rendu et aperçu** : réutilisation de Mimetic, stockage de l'origine indépendante de la longueur de référence, queue complète.
4. **Package PTX** : template compatible, writer BWF, deux sorties alternatives, relecture du PTX et comparaison des médias.
5. **Multiples répliques d'une séquence** : associations de référence explicites, EQ par réplique, stems sommés aux positions exactes.

Tests essentiels :

- Placement exact avec position entre images, offset média non nul, sélection à l'intérieur d'un clip et position supérieure à une heure.
- Source à un autre TC et d'une autre durée : le résultat suit uniquement l'origine de Destination.
- Silence/gap dans le montage : aucune concaténation ne supprime les trous temporels.
- Queue débordant la sélection : conservée ; queues de clips voisins sommées, pas masquées par la playlist.
- Fades, groupes, plugins/transformations non reproduits : comportement de refus ou de mode brut explicite, sans prétendre au bounce natif.
- Média absent, homonyme, changé pendant le job, plusieurs occurrences du même clip : résolution et invalidation correctes.
- Export à plus de 5 min 49,5 s : refus du profil UInt24 ou utilisation du profil UInt32 certifié ; jamais troncature.
- WAV PCM relu identique au rendu float32, CTF/IR/EQ du chemin WAV inchangés, aucune nouvelle normalisation.
- Session originale et médias d'entrée inchangés ; aucune publication d'un dossier partiel.

Validation native requise pour qualifier la fonction comme disponible : ouvrir le package final dans Pro Tools, vérifier l'absence de médias manquants, les TC et les positions entre images, écouter MIX seul puis IR avec l'ADR adapté, sauvegarder nativement et relire via pt_api. Une relecture API seule confirme une cohérence interne, pas la compatibilité de toute session PTX.

## 8. Reproduire la preuve

Depuis la racine Mimetic, avec un **nouveau** dossier de sortie :

```powershell
.venv\Scripts\python.exe benchmarks/ptx_export_probe.py `
  --api-root Y:\pt_api `
  --template Y:\pt_api\pfx_import_reference\01_A_imported_cliplist\01_A_imported_cliplist.ptx `
  --out data\ptx-probe-nouveau
```

Le script refuse de réutiliser un dossier existant. Il n'extrait pas de rushes et n'appelle pas Rec-RIR. Il vérifie le chemin **rendu Mimetic → WAV BWF → builder PTX → relecture**, sur des données synthétiques.

La prochaine étape de programmation est le lot 1, puis une première unité consolidée au TC conservé. Les deux prérequis de production à finaliser sont le template à deux pistes adapté à la durée des séquences et le mode d'accès aux médias de la session.

## 9. Test réel à quatre pistes et politique des durées courtes

### Test du 12 octobre 2026

`Copy of pasmorte-1_006_dial_pr_mix copie.ptx` est accepté en lecture par **pt_api 1.6.3**, commit `c31147bf427e763f7be900e6077368b615d47e24`, identique au HEAD public vérifié. Ses quatre pistes forment les couples **Audio 1 → Audio 2** et **Audio 3 → Audio 4**, huit régions chacune. Les 32 placements et les 34 médias mono PCM24/48 kHz se lisent ; les plages médias sont valides et le cycle de sauvegarde API est identique à l'octet. Aucun fade ou groupe n'est retourné. Le lecteur Track Volume refuse toutefois les identités/largeurs de cette session ; ne pas supposer des réglages neutres.

Les débuts correspondent dans chaque couple, mais certaines longueurs diffèrent. Conserver les ADR complets, indépendamment de la longueur de référence. L'étendue de timeline est de 25 min 21 s : exporter des îlots/régions à leur TC absolu, sans former un gigantesque asset d'analyse rempli de silences. Un package réunissant les deux couples nécessite quatre slots de sortie IR/MIX.

Rapport : `Y:/SHARE TO NETWORK/Mimetic_Interoperabilite/2026-10-12_Test_03_Session_reelle/Resultats/RESULTAT_SESSION_REELLE.md`.

### Règle utilisateur : aucun rejet sur la seule durée

L'utilisateur précise que les ADR courts seront fréquents et doivent être rendus même avec une IR moins précise. Cette exigence complète et, si nécessaire, remplace les anciens seuils de durée comme conditions bloquantes du futur mode PTX. Elle concerne la conservation de tous les placements : aucune réplique ne disparaît d'un export à cause de sa brièveté.

**Distinguer les deux rôles :** l'IR est estimée depuis la référence Source ; l'ADR est le signal à traiter. Un ADR court n'exige pas une nouvelle IR estimée depuis lui-même. La brièveté de l'ADR limite surtout les statistiques de Match EQ. Dans le test réel, les deux régions refusées par le sélecteur actuel sont les références `Audio 1_04` et `Audio 3_04` de 1,55 s ; leurs ADR sont plus longs.

Politique à implémenter :

1. **ADR court avec une IR disponible :** appliquer cette IR à l'ADR entier et conserver la queue complète. Aucun seuil minimal de durée ADR pour la convolution.
2. **EQ peu documentée sur un ADR court :** utiliser le contexte de la même captation lorsque celui-ci est explicitement établi, ou réduire la correction vers l'identité avec un statut clair. Ne pas inventer une mesure fiable. Un profil EQ à l'identité constitue un résultat conservateur explicite, pas une absence de traitement à confondre avec une erreur d'export.
3. **Référence courte :** essayer une inférence sur la durée réellement disponible, avec confiance réduite et diagnostic. Le seuil de 2 s du sélecteur actuel doit devenir une indication de qualité dans ce chemin, pas un refus automatique. Les éventuels zéros de complément nécessaires au calcul ne sont pas de la parole supplémentaire et n'améliorent pas sa confiance.
4. **Estimation de pièce inutilisable :** garder le clip dans le résultat et employer un repli annoncé : profil d'une même captation explicitement liée, ou rendu de pièce conservateur sous un mode de secours documenté. Définir et valider ce mode avant livraison ; ne pas prétendre que son IR est mesurée. Ne pas attribuer automatiquement la pièce d'une autre scène sous prétexte de proximité du TC ou de numéro de piste.
5. **Export de toutes les répliques :** le rapport décrit par placement `measured`, `low_confidence` ou `fallback`, et le motif. Le parcours normal permet de publier les courts clips au lieu de les omettre silencieusement ou de bloquer tout un couple. Les erreurs d'intégrité, média absent/non décodable ou plage invalide restent des erreurs distinctes de la durée.

Cette politique concerne la future intégration ; elle n'est pas encore implémentée dans le moteur actuel. Elle impose notamment de revoir les refus `EQ_INSUFFICIENT_SPEECH`/`EQ_REQUIRED` liés aux clips courts : préserver l'activation du raccord et un résultat conservateur explicite, sans afficher une estimation exacte lorsque la correction est neutre ou réutilisée.

Tests de livraison : ADR de 50/100/300 ms et 1 s avec IR connue ; référence de 1,55 s réellement rencontrée ici ; référence plus courte que l'ADR ; échec d'estimation déclenchant le mode de secours ; absence de clipping/troncature de queue ; égalité des positions et du nombre de répliques avant/après export. Vérifier qu'aucun emprunt de profil traverse silencieusement une captation et que les avertissements de faible confiance n'empêchent pas l'export attendu.

## 10. Première génération de rendus réels — test hors interface

Le 12 octobre 2026, `benchmarks/ptx_real_render.py` et `benchmarks/ptx_package_renders.py` ont produit les deux couples de la session réelle. Les 16 ADR ont reçu une IR Rec-RIR et un Match EQ effectivement calculés, sans repli synthétique ou EQ neutre de secours. Les références de 1,55 s ont été inférées directement après abaissement du seuil de sélection dans le seul processus de test ; elles sont signalées à confiance réduite. Le moteur installé reste inchangé.

Les échantillons de médias consolidés servent d'entrée ; les réglages de mix de la session originale ne sont pas bouncés. Les paramètres sont wet supplémentaire 0 dB, prédélai supplémentaire 0 ms, EQ 100 %, compensation de niveau habituelle. Le rendu conserve l'ADR complet, le FIR et la queue.

Le template natif à deux pistes a été renommé par les méthodes publiques vérifiées puis utilisé pour deux PTX : `pasmorte_Mimetic_P01` et `pasmorte_Mimetic_P02`. Le builder actuel ne crée pas de pistes ; quatre sorties dans un seul PTX exigent quatre slots compatibles. La visibilité du template à deux slots a été conservée : son profil `set_visible_tracks` est refusé, et cette mutation superflue n'est pas utilisée.

Les premières queues se chevauchant, les rendus ont été sommés localement en îlots non chevauchants. Chaque couple contient 7 îlots par sortie couvrant ses 8 ADR ; les positions restent absolues et tous les clips sont couverts une seule fois par sortie. Les clips wet sont mutés par défaut. Le MIX est la sortie complète et ne doit pas être lu avec le wet simultanément.

Packages : `Y:/SHARE TO NETWORK/Mimetic_Interoperabilite/2026-10-12_Test_04_Rendus_Reels/PTX`. Relecture, positions, longueurs, mutes, copies WAV et cycles de sauvegarde API sont vérifiés. L'ouverture et l'écoute natives de ces deux nouveaux PTX restent à valider. Ce test constitue une génération de livrables hors interface, pas une intégration complète au service web.

### Correction des rôles et nouvelles conventions d'export

Après le test 04, l'utilisateur a corrigé l'ordre et les noms du fichier de session. Le nouvel ordre est `source 1`, `destination 1`, `source 2`, `destination 2`. Les médias de référence sont maintenant `Audio 2_*.wav` et `Audio 4_*.wav` ; les ADR à traiter sont `Audio 1_*.wav` et `Audio 3_*.wav`. Le nom historique du média ne remplace jamais le rôle lu dans l'ordre actuel des pistes. Les rendus du test 04 restent des observations historiques et sont à refaire, sans réutiliser leurs IR/EQ établies avec l'ordre précédent.

Nouvelles exigences explicites : **clips MIX toujours mutés**, **Clip Gain de chaque clip IR à +3,5 dB**, gain stocké dans le PTX et non imprimé dans les samples WAV. Elles remplacent la convention de mute IR du premier prototype. Le template utilisateur `Mimetic_Template.ptx` contient trois paires IR/MIX : conserver les six slots, même si seules deux paires sont utilisées.

Une copie du template reçu est conservée localement sous `data/pro_tools_templates/Mimetic_Template_received_20261012.ptx`. Sa version initiale est entièrement vide et ne passe pas le builder 1.6.3 : absence de Clip List et de prototype importé. Le builder exige encore un prototype mono float32 natif, une seule définition et un seul média, aucune timeline. Le WAV silencieux de préparation native est dans `2026-10-12_Test_05_Ordre_Corrige/Preparation_Template`. Le template doit être revalidé après cette préparation, sans greffer de blocs binaires d'une autre session pour contourner le contrat.

Les nouveaux calculs du test 05 sont terminés : 16 ADR, 32 WAV, toutes les IR estimées par Rec-RIR et toutes les EQ calculées. Les ADR de 1,55 s sont conservés. `EQ_LOW_CONFIDENCE` est retourné pour P01_R03, P02_R03 et P02_R04 ; `EQ_ROOM_CONTEXT_UNCERTAIN` et `HF_SYNTHESIZED` restent documentés. Aucun repli de pièce ou d'EQ n'a été déclenché. Les hashes, rôles, débuts, longueurs et échantillons finis des sorties ont été vérifiés dans `verification_renders.json`. L'assemblage du PTX attend encore un template natif compatible ; ne pas confondre les WAV prêts avec un PTX publié.

Le script de packaging utilise désormais par défaut MIX muté et Clip Gain IR +3,5 dB. Il vérifie aussi le dictionnaire de gains avant de créer le package et impose explicitement les mutes aux deux types de sortie. Pour le template de six pistes et deux couples, passer `--keep-unused-tracks` afin de conserver P03 vide et de ne pas demander une mutation de visibilité superflue. Les paramètres de gain sont stockés dans le PTX ; les samples WAV ne sont pas multipliés par le gain de clip.

Le premier import natif utilisateur est présent, mais n'est pas compatible : préfixe `0x2628` `0000400000`, média float 15 octets, en-tête `0x2106` 145 octets, catalogue dans le dossier `Preparation_Template`. Le dictionnaire Clip Gain natif existe désormais (zéro point). La seule durée de six minutes n'a donc pas suffi à obtenir le profil 31/151 UInt32 ; les instructions de préparation initiales étaient incomplètes. Une copie de cette version est conservée sous `Mimetic_Template_imported_20261012.ptx`. Le diagnostic et les nouvelles instructions sont dans `Preparation_Template/diagnostic_import_template.json` et `LIRE_TEMPLATE_CORRECTION.md`. Un prototype de 60 s avec origine BWF 01:00:00:00, placé dans un dossier `Audio Files`, vise le profil 15/142 connu. Il faut revalider son import natif avant de considérer le blocage résolu ; aucun nouveau profil opaque n'est accepté par simple relaxation du validateur.

### Assemblage du test 05 avec le template préparé

Le second import natif possède le bon préfixe `0000300400`, un média float de 15 octets, un catalogue `Audio Files` valide et le dictionnaire Clip Gain attendu. Son en-tête `0x2106` mesure toutefois **149 octets**, car le champ Originator contient la chaîne `Mimetic` : tag à +48, longueur UInt32 égale à 7 à +49, texte à +53. Le profil 15/142 codé dans pt_api 1.6.3 avait un Originator vide. Après retrait des sept octets de texte et normalisation de la longueur à zéro, tous les autres octets fixes concordent avec le fixture natif 15/142, hors champs variables de dates, méta BWF, origine et UUID.

Le script hors interface propose `--mimetic-originator-compat`, limité au hash exact du template préparé (`68b7339b601e9a425aabffe5f5e78c3e7fae93d58fb258a22c32ab6c54e3bfd3`), à pt_api 1.6.3 et au fixture natif de comparaison inchangé. Il conserve l'en-tête natif du template et ses chaînes ; le profil de ce processus utilise longueur 149, origine à +98, date précédente à +103 et UUID à +133. La première date reste à +29. Il ne greffe aucun bloc d'une autre session et ne modifie pas le checkout pt_api. Les validations habituelles restent appliquées aux clips, médias, longueurs, origines, playlists et gains. Cette adaptation est une compatibilité expérimentale du test, pas un support générique publié ; sa qualification nécessite l'ouverture et la sauvegarde natives.

Un seul package est maintenant généré sous `2026-10-12_Test_05_Ordre_Corrige/PTX/pasmorte_Mimetic_P01_P02`. Il contient six pistes, deux couples utilisés, P03 vide, 28 placements et 28 médias. Les 16 ADR corrigés sont couverts exactement une fois par type de sortie. Les 14 clips MIX sont mutés et les 14 clips IR actifs portent +3,5 dB de Clip Gain ; les WAV ne sont pas amplifiés par ce gain. `verification_final.json` confirme les positions exactes, la concordance des TimeReference BWF/en-tête PTX/timeline, les gains, mutes et les samples float32 après sommation des queues chevauchantes. Le cycle API conserve le PTX à l'octet. SHA256 du PTX produit avant sauvegarde native : `24ff38496f325b16fa8e00bdc276ce6a76c7c6881003f82aa0c1503f6c95af94`. L'ouverture et l'écoute Pro Tools restent à effectuer.
