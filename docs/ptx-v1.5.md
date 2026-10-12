# Intégration PTX — Mimetic v1.5

## Parcours livré

La troisième boîte, à droite de SOURCE et DESTINATION, accepte **uniquement un ZIP**. Il contient un PTX principal et son dossier frère `Audio Files`, éventuellement dans un dossier de session enveloppant. Backups, WaveCache et métadonnées ZIP macOS sont ignorés. Un ZIP incomplet est refusé avec les noms des médias manquants ; le programme ne demande pas d'ajouts séparés et conserve la séquence précédente.

Le ZIP valide lance automatiquement l'analyse de pièce, le Match EQ, les rendus et l'assemblage. Le ZIP final se télécharge automatiquement une fois le traitement terminé, une seule fois par export dans le même onglet (y compris après rafraîchissement). Le bouton de téléchargement reste disponible. Il contient : PTX autonome, Audio Files et `Mimetic_report.json`. Une copie reste dans `data/exports`. Nouvelle séquence conserve les exports ; Delete Cache les supprime après le dialogue existant.

## Contrats et limites v1.5

- 48 kHz, 23,976 images/s, jusqu'à trois couples de pistes mono, dans l'ordre des playlists principales.
- Piste impaire = référence ; piste paire = ADR. Les noms ne déterminent jamais le rôle. Les couples entièrement vides restent vides.
- Une référence et un ADR par début exact, sans ambiguïté. Les longueurs peuvent différer.
- Clips consolidés, actifs, sans fades ni groupes. Les samples médias sont utilisés ; plugins, automation, Clip Gain et Track Volume d'entrée ne sont pas bouncés.
- Plages médias, fréquence, canaux, format et échantillons vérifiés. Le découpage lit `src_offset_samples` et `length_samples` depuis le WAV correspondant.
- Aucun rejet lié à la seule brièveté. Le sélecteur Rec-RIR reçoit un minimum propre au mode PTX de 32 ms ; son seuil normal de 2 s n'est pas muté globalement. Une référence plus courte/inexploitable reçoit, si nécessaire, une pièce paramétrique de secours RT60 0,3 s / DRR 12 dB, annoncée dans les rapports. Les erreurs d'installation/moteur et l'annulation ne sont pas converties en estimation fictive.
- Si Match EQ n'est pas mesurable, correction neutre de secours annoncée. Tous les ADR intègres restent rendus.
- Réglages PTX : EQ 100 %, wet supplémentaire 0 dB, prédélai supplémentaire 0 ms. Les sliders du parcours WAV ne changent pas ce lot.
- IR actifs, Clip Gain +3,5 dB stocké dans le PTX ; MIX mutés, Clip Gain 0 dB. Les samples WAV ne sont pas amplifiés par le gain de clip.
- Queues complètes ; les queues se chevauchant sont sommées en îlots. L'origine est conservée en samples absolus, sans conversion aller-retour en texte TC.
- Le profil natif UInt24 impose une longueur maximale de 16 777 215 samples par îlot exporté, soit environ 349,5 s. La limite d'import de région reste 10 min, mais un îlot dépassant la capacité du builder est refusé avec diagnostic, sans export partiel. Les tests réels de ce projet restent largement sous cette limite.
- ZIP reçu : 2 Go maximum ; médias décompressés : 4 Go maximum, 2 Go par fichier, 4096 entrées PTX/WAV maximum.

## Modules et dépendances

`src/mimetic/pro_tools.py` possède les fonctions de plan, découpage, rendu, BWF et assemblage. `src/mimetic/web/ptx.py` possède l'import ZIP, l'état du lot, l'annulation, le thread et la publication. Le serveur expose upload/cancel/retry/new et le téléchargement ZIP. Il sérialise les calculs WAV et PTX ; les fichiers importés utilisent des noms internes opaques. L'archive n'est jamais extraite avec ses chemins fournis : aucun `extractall`, aucun accès à un fichier local demandé par le client.

pt_api 1.6.3 est installé comme dépendance, depuis le commit `c31147bf427e763f7be900e6077368b615d47e24`. Son code et ses notices sont distribués par le paquet sous LGPL-2.1-or-later ; Mimetic reste sous sa licence MIT. Le dépôt `Y:/pt_api` n'est pas une dépendance d'exécution et n'est pas modifié par l'intégration.

Le template natif à six pistes est livré dans `src/mimetic/resources/Mimetic_Template.ptx`, inclus dans le wheel. SHA256 : `68b7339b601e9a425aabffe5f5e78c3e7fae93d58fb258a22c32ab6c54e3bfd3`. Sa Clip List contient seulement le prototype silencieux nécessaire au builder, remplacé par les médias réels à l'export. Le WAV correspondant est également livré dans `resources/Audio Files/Mimetic_Prototype_Float32_TC_60s.wav`, inclus dans Git, le wheel et la distribution source. Son SHA256 est `26f2a271b36a707d55e6e44cc875885afe1033f4efc505b6d1a1675b494462c9`. Le démarrage vérifie sa longueur, son TimeReference et son UMID contre le PTX. Le média est totalement silencieux ; aucun rush n'est livré.

Son Originator BWF `Mimetic` ajoute sept octets à l'en-tête natif : longueur 149, origine à +98, date précédente à +103, UUID à +133 ; première date à +29. Ce profil a été comparé octet par octet au profil natif 15/142, puis utilisé dans le PTX du test 05 ouvert avec succès dans Pro Tools par l'utilisateur le 12 octobre 2026. L'intégration utilise une copie isolée du module pt_api avec ce profil précis ; le module installé et ses registres globaux restent inchangés. La version et le hash du code API sont vérifiés (fins de ligne normalisées), ainsi que le hash du template.

## Vérification et publication

Les mutes, gains, positions, longueurs et copies WAV sont vérifiés après sauvegarde/relecture du PTX. La publication prépare les médias et le ZIP sur le volume de sortie, puis publie les noms définitifs après succès ; une annulation ou un échec ne fournit pas de package incomplet.

Les tests d'intégration couvrent un ZIP complet, les rôles indépendants des noms, des ADR de 50/100 ms, des références de 1,55 s, la correction EQ neutre explicite, les queues chevauchantes vérifiées contre une IR sparse connue, les gains non imprimés, les six pistes et slots vides, le téléchargement, la conservation des exports, la purge du cache, les ZIP incomplets/mal structurés, backups/métadonnées macOS, les chemins ZIP sortants et l'annulation pendant l'estimation. Ils utilisent un estimateur connu pour isoler le contrat DSP/IO.

Un test navigateur réel a importé un ZIP avec le premier couple de la session corrigée, exécuté Rec-RIR et Match EQ, produit deux placements et affiché le téléchargement, sans erreur navigateur. Les IR/MIX et les positions du test 05 complet ont déjà été validés par l'utilisateur dans Pro Tools. Les nouveaux exports du programme doivent conserver le même contrat ; l'écoute reste la validation de qualité du raccord.
