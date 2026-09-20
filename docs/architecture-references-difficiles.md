# Mimetic — références bruitées ou très réverbérantes

Date : 2026-09-19. Recherche, audit de la V2 et architecture proposée pour la suite.

**Statut : proposition, pas fonctionnalité implémentée.** Le code de l'application n'a pas été modifié pour cet audit. Deux vérifications numériques ciblées ont été exécutées ; aucune écoute ni validation perceptive sur les rushes de l'utilisateur n'a été réalisée.

Ce document complète `architecture-match-eq.md` et `eq-validation.md`. Les décisions D11 et les réglages effectivement adoptés dans la V2 priment sur les recommandations initiales : lissage 5000, répliques courtes acceptées, comparaison globale, export `_EQ_IR_MIX`, dosage wet manuel.

## 1. Recommandation

Faire évoluer le raccord en séparant **le timbre de la voix, la réverbération et le fond sonore**. Une EQ plus énergique ne suffit pas à réparer leur confusion.

Ordre proposé :

1. Corriger la confiance de l'EQ, la sélection des trames et l'estimation du bruit. C'est le premier lot à coder.
2. Instrumenter les deux sorties auxiliaires de Rec-RIR déjà calculées mais abandonnées par l'application. Évaluer leur qualité avant d'en faire une nouvelle cible de raccord.
3. Tester un mode « Référence difficile » qui compare des estimations débruitées, **en conservant la réverbération** et le mélange virtuel de la V2.
4. Pour le manque de distance ou de queue, ajouter des réglages indépendants des réflexions précoces et tardives. Étudier séparément une calibration sur la performance de la référence elle-même.
5. Prévoir une écoute avec un vrai fond de plateau, fourni séparément lorsque nécessaire. Ne pas essayer de fabriquer ce fond en augmentant l'EQ ou le wet.

L'idée nouvelle la plus prometteuse est au point 2 : elle utilise le moteur existant et n'exige pas immédiatement un autre réseau. Sa disponibilité technique est vérifiée ; son bénéfice sonore reste à établir.

## 2. Pourquoi le raccord échoue dans ces cas

Modèle simplifié :

```text
Référence : r = s * m * (δ + h) + n
V2        : y = a · x * q * (δ + h_est)

s : performance de production ; x : autre performance, ADR
m : coloration relative de captation ; q : correction EQ estimée
h : réflexions réelles ; h_est : réflexions estimées et dosées
n : bruit / ambiance ; a : compensation de niveau
```

Cette décomposition est utile pour concevoir le programme, mais n'est pas identifiable exactement à partir d'un seul enregistrement. La voix, le micro et la pièce peuvent expliquer certaines mêmes différences spectrales. Une autre performance ajoute encore ses propres différences de phonèmes, de projection et de distance.

| Ce qui manque au rendu | Ce qu'une EQ commune peut faire | Ce qu'il faut traiter séparément |
|---|---|---|
| Voix trop sourde ou trop brillante | Modifier sa couleur générale | Fiabilité des bandes utilisées pour la mesurer |
| Réflexions insuffisantes ou mauvaises | En modifier la couleur en même temps que la voix | Niveau et structure temporelle de la pièce |
| Fond continu absent | Amplifier le bruit déjà présent dans l'ADR | Apport d'une ambiance indépendante |
| Performance différente | Modifier partiellement son équilibre spectral | Jeu, souffle, articulation et dynamique |
| Saturation ou compression de la prise | Approcher certaines conséquences spectrales | Traitement non linéaire, hors lot proposé |

Une EQ peut changer le DRR pondéré par fréquence ; elle ne constitue cependant pas un réglage indépendant du direct et de la queue. Et la convolution d'un silence suffisamment long reste silencieuse : elle ne crée pas une ambiance indépendante de la parole.

L'étude de Germain, Mysore et Fujioka décrit précisément le problème du raccord EQ en présence de bruit : une correction unique ne peut pas faire correspondre indépendamment le timbre de la voix et celui du bruit. Leur approche sépare ces composantes avant de les traiter et de les recombiner. C'est un appui au découpage du problème, pas une preuve que les choix proposés ici fonctionneront sur Mimetic. [Adobe Research, ICASSP 2016](https://research.adobe.com/publication/equalization-matching-of-speech-recordings-in-real-world-environments/)

## 3. Ce que la V2 montre déjà

Dans `docs/eq-validation.md`, la source réelle reste plus présente dans les 30–100 ms après les phrases. Le fond mesuré est également important, surtout entre 3 et 8 kHz. Le document signale que le rendu a été jugé trop sec ; il ne s'agit pas d'une écoute effectuée pendant cet audit.

Les deux tentatives de dosage automatique par les creux de parole ont été correctement retirées : elles suivaient le rythme des phrases ou ne réagissaient pas suffisamment au défaut connu. **Ne pas les remettre sous un nouveau nom.** Comparer une fin de phrase de production à une autre fin de phrase ADR ne donne pas une mesure isolée de la réverbération.

Le lissage fort et l'abandon des classes de phonèmes suivent des essais documentés. Les conserver comme référence de comparaison. Les exemples synthétiques existants ne constituent pas encore une validation sur bruit de plateau et forte réverbération.

### 3.1 Confiance absolue effacée par la normalisation des poids — vérifié

Fichier : `src/mimetic/analysis/eq_match.py`, fonction `estimate_curve`.

Les poids intègrent le SNR et l'incertitude, puis sont divisés par leur 90e centile. Ce choix résolvait un problème réel de sous-correction, mais peut transformer un ensemble de mesures toutes faibles en un ensemble de mesures toutes fortes. Le diagnostic `low_confidence` dépend ensuite seulement de l'incertitude, pas du niveau absolu de fiabilité acoustique.

Vérification isolée effectuée avec les fonctions de la V2 : mêmes spectres, même dispersion, 10 blocs ; seul le SNR déclaré change uniformément sur les deux entrées.

| SNR déclaré de chaque entrée | Poids médian après normalisation | Confiance réduite | Courbe min / max |
|---|---:|---|---|
| 18 dB | 1,0 | non | −2,946 / +5,058 dB |
| 6,2 dB | 1,0 | non | −2,946 / +5,058 dB |

Ce n'est pas une mesure d'un rush : c'est la démonstration d'un comportement du calcul. Elle ne prouve pas à elle seule la cause de chaque mauvais raccord.

### 3.2 « Pas de parole » n'est pas « bruit seul » — lecture du code

Fichier : `src/mimetic/analysis/speech_stats.py`, fonction `measure`.

Le code utilise `quiet = ~voiced`, puis le 20e centile des puissances de ces trames comme bruit. Malgré le commentaire « loin de la parole », il n'existe pas de garde temporelle après les mots. Une queue de pièce peut donc entrer dans le bruit estimé. Inversement, une voix continue sans vraie pause peut faire passer ses trames faibles pour du bruit.

Le SNR enregistré correspond à `10 log10(P_observé / P_bruit)`, donc signal-plus-bruit sur bruit, pas à la puissance de parole seule sur bruit. Renommer ce diagnostic ou recalculer sa définition avant de réutiliser aveuglément les seuils 6–18 dB.

### 3.3 L'incertitude des clips courts est largement conventionnelle — vérifié

Pour moins de 10 blocs, `measure` renvoie une dispersion `NaN`. `_difference_by_class`, dans son chemin global effectivement utilisé, remplace ce `NaN` par 1, puis divise par la racine du nombre de blocs. Le résultat dépend alors surtout du nombre de blocs, sans mesurer leur dispersion réelle.

Un signal de contrôle modulé de 1, 3 et 4 secondes, entouré de silence, produit respectivement 3, 7 et 9 blocs : dispersion inconnue dans les trois cas. Il s'agit d'un contrôle de branche algorithmique, pas d'un test de parole.

Ainsi, la mention actuelle « bornes par incertitude mesurée » est à nuancer pour les courtes répliques. Il faut améliorer l'estimation et son statut, sans réintroduire une interdiction générale des ADR courts.

### 3.4 Sélection et limites de pièce — risques identifiés

- L'activité énergétique utilisée pour sélectionner les fenêtres Rec-RIR peut préférer musique, choc ou bruit fort.
- L'EQ prend les 30 premières secondes et n'est pas nécessairement calculée sur le même passage de captation que l'IR retenue.
- La canonicalisation situe le direct par une heuristique de pic et normalise par l'énergie d'un paquet de ±1 ms. Une réflexion proche peut changer cette calibration ; sa contribution exacte sur les cas de l'utilisateur n'a pas été mesurée.
- Le profil utilise environ une seconde d'IR. Une pièce longue peut demander une queue supplémentaire, mais une limite d'une seconde n'implique pas automatiquement un défaut audible : cela dépend de l'énergie restante.

L'article Rec-RIR décrit une entrée à 16 kHz, un CTF d'environ 0,96 s et un apprentissage incluant du bruit à 5–20 dB de SNR et des RT60 de 0,2–1,5 s. Le modèle a donc été entraîné avec du bruit ; le débruiter systématiquement en amont n'est pas une correction évidente. Ses résultats publiés ne garantissent pas la calibration du direct réalisée par Mimetic. [Rec-RIR, sections IV-A à IV-D](https://arxiv.org/html/2509.15628v2)

## 4. Lot A — fiabiliser l'analyse existante

### 4.1 Trois catégories temporelles

Remplacer le couple « actif / inactif » par :

- **Parole probable** : utile pour le timbre ; peut contenir simultanément du bruit et de la réverbération.
- **Décroissance / transition** : après la parole ; à conserver pour la pièce, à exclure du bruit seul.
- **Fond seul probable** : assez éloigné de la parole et sans événement dominant.

Silero VAD en ONNX est un candidat pour la première catégorie. L'analyse à 16 kHz sert uniquement aux timestamps, remappés sur l'audio pleine bande ; elle ne remplace pas l'audio exporté. Le projet fournit des modèles 8/16 kHz et un usage ONNX. Un VAD détecte la présence de parole, pas sa pureté, ni l'identité de l'acteur, ni l'absence d'une autre voix. [Dépôt Silero VAD](https://github.com/snakers4/silero-vad)

Valeurs initiales à tester, **pas seuils validés** : exclure des candidats « bruit seul » les 100 ms avant et au moins 250 ms après une région parlée ; prolonger la garde selon la décroissance observée et, seulement s'il est crédible, le RT estimé. Ne pas plafonner à 250 ms une longue queue audible. Si aucune pause indépendante de la queue ne reste, déclarer le bruit inconnu.

Ne pas concaténer des segments pour analyser la pièce : conserver les fenêtres continues et leurs queues. Pour l'EQ, agréger des statistiques de segments distincts sans créer de raccords artificiels dans le signal.

Pour un même import, conserver un identifiant de passage/captation commun aux analyses de pièce et de timbre. L'EQ peut utiliser plus de parole de cette même captation ; elle ne doit pas inclure silencieusement un autre micro ou une autre scène. Donner la possibilité de choisir un passage de référence si la sélection automatique échoue.

### 4.2 Puissance de parole et bruit

Conserver les calculs en puissance linéaire jusqu'à l'agrégation. Pour un bruit supposé additif et non corrélé :

```text
P_parole_est(b) = max(P_observé(b) - P_bruit_est(b), epsilon)
SNR_est(b) = 10 log10(P_parole_est(b) / max(P_bruit_est(b), epsilon))
```

La soustraction est une approximation statistique, pas une séparation exacte trame par trame. Une puissance négative avant plancher signifie « non identifiable », pas « fréquence absente à corriger ». Propager un indicateur de bande dominée par le bruit, même après application du plancher numérique.

Estimer le fond par plusieurs pauses quand elles existent et mesurer sa variabilité. Un moteur de voiture intermittent ou un choc ne se représente pas bien par un seul spectre stationnaire. Si le fond change trop, baisser la confiance de la région ou choisir une autre région ; ne pas soustraire une valeur arbitraire avec une confiance normale.

### 4.3 Séparer forme relative et fiabilité absolue

Conserver deux notions :

```text
w_rel(b) : importance relative des bandes, éventuellement normalisée
c_abs(b) : fiabilité absolue, bornée dans [0, 1], jamais renormalisée à 1
```

La solution doit rester forte sur une différence nette et propre, tout en devenant prudente quand toutes les bandes sont mauvaises. Une première implémentation simple conserve le solveur actuel, calcule une courbe candidate avec `w_rel`, puis la réduit progressivement là où `c_abs` est faible ; refaire un ajustement lisse borné pour éviter des cassures de courbe. Une autre variante utilise `c_abs` pour renforcer localement la pénalité vers zéro. Comparer ces variantes au banc, ne pas cumuler par défaut tous les mécanismes de retrait.

Construire `c_abs` à partir du SNR réellement défini, de la variabilité du fond, de la stabilité de la différence spectrale et de la couverture de parole. Le nombre de bandes FFT voisines n'est pas autant d'observations indépendantes. Une bande inconnue reste inconnue ; une renormalisation relative ne la transforme pas en bande fiable.

Conserver initialement les bornes et le lissage validés par la V2 sur matériel propre. Un seuil de fiabilité ne doit pas couper brutalement l'EQ dès qu'une mesure fluctue autour de 6 dB. Les valeurs exactes de la nouvelle calibration sont à choisir sur le lot A de validation.

### 4.4 Incertitude sur réplique courte

Stocker les spectres par bloc, leurs poids et leurs coordonnées temporelles. Mesurer leur dispersion aussi sous 10 blocs, avec un statut `low_support` quand elle est peu documentée.

Utiliser un rééchantillonnage de blocs temporels contigus ou des répliques entières pour évaluer la stabilité, lorsque leur nombre le permet. Ne pas traiter les trames STFT qui se chevauchent comme des mesures indépendantes. Une incertitude statistique intra-clip ne couvre pas le biais systématique de phonèmes, de micro ou de séparation neuronale.

Pour un seul petit bloc exploitable, renvoyer « incertitude inconnue », pas une erreur type précise. Préserver la possibilité d'une correction large sur un ADR court lorsque le contraste est net ; ne pas remettre un seuil de 3 s ni des bornes serrées systématiques liées à la durée.

## 5. Lot B — récupérer les sorties déjà disponibles de Rec-RIR

### 5.1 Constat dans le code installé

Dans `third_party/Rec-RIR/model/RecRIR.py`, `BiSpatialNet.forward` renvoie :

```python
return y_spch, y_CTF, y_rev
```

Le code d'entraînement compare `y_spch` au signal de trajet direct et `y_rev` à la parole réverbérée sans bruit. Ce sont des sorties supervisées, pas seulement des embeddings internes.

Dans `third_party/Rec-RIR/method/pim.py`, `PIM.init_seg` fait :

```python
_, CTF_ft, _ = model(...)
```

Mimetic passe par cette fonction. Il abandonne donc deux résultats **déjà calculés pendant la même inférence** :

| Sortie | Interprétation correcte | Usage proposé |
|---|---|---|
| `y_spch` | Estimation du trajet direct, avec erreurs possibles | Diagnostic, essai de calibration de pièce |
| `y_rev` | Estimation de parole réverbérée sans bruit | Analyse EQ robuste, diagnostic du fond |
| `y_CTF` | Filtre de transfert estimé | Chemin IR actuel |

Ne pas appeler `y_spch` « voix sèche exacte », ni `r - y_rev` « bruit pur ». Ces résidus contiennent aussi les erreurs du réseau. Les sorties ne décrivent rien au-delà de 8 kHz.

### 5.2 Vérification technique effectuée

Chargement des poids installés et appel direct au réseau dans `.venv-engine`, sur un signal sinusoïdal modulé de 1 s à 16 kHz :

- `y_spch` et `y_rev` : forme `[1, 2, 257, 63]` ; `y_CTF` : `[1, 2, 257, 60]`.
- Les deux sorties audio reconstruites sont finies.
- Chargement + appel + conversion : environ 3,7 s dans cet essai ; ce n'est pas un benchmark sur dialogue.
- La conversion actuelle sans longueur explicite restitue 15 872 échantillons pour une entrée de 16 000. Il faut traiter ce détail avant toute soustraction ou comparaison temporelle.

Le signal de contrôle ne permet absolument pas d'évaluer la qualité du débruitage, de la déréverbération ou la fidélité du timbre.

### 5.3 Contrat d'intégration

Ajouter dans le worker un chemin d'inférence qui récupère les trois sorties en un seul appel. Ne pas éditer silencieusement le dépôt tiers : adapter le code de liaison dans Mimetic en documentant les opérations reprises du commit épinglé.

Préserver exactement les opérations CTF existantes : normalisation d'entrée, `preprocess`, `postprocess`, inversion temporelle du CTF pour le PIM. **Ne pas appliquer cette inversion temporelle aux spectrogrammes de voix.**

Pour reconstruire les voix : `postprocess`, iSTFT avec les conventions d'origine, longueur explicitement maîtrisée, puis multiplication par **la même échelle d'entrée**. Ne pas normaliser les deux sorties séparément au pic : leurs niveaux relatifs servent au diagnostic.

L'outil iSTFT tiers ne transmet actuellement pas `length` à `torch.istft` ; son argument `wav_len` ne fait que tronquer. Tester une reconstruction explicitement dimensionnée dans l'adaptateur. Vérifier l'alignement avec des signaux connus et consigner les marges de bord non fiables ; ne pas simplement ajouter des zéros puis prétendre que ces échantillons ont été estimés.

Stocker pour chaque fenêtre : bornes dans le fichier original, fréquence 16 kHz, échelle, longueur valide, version et empreinte des poids, proxies direct/réverbéré, CTF et profil existant. Les proxies restent des données d'analyse locales dans `data/`.

**Condition de livraison du lot B :** extraction disponible, aucune modification de l'IR issue du même passage au-delà de la tolérance numérique de référence, aucune modification d'un export V2 quand le nouveau mode est désactivé. Aucune sortie neuronale n'est utilisée comme voix audible de l'ADR.

## 6. Lot C — Match EQ robuste au bruit

### 6.1 Conserver la comparaison au mélange virtuel

La cible de la V2 est correcte dans son principe : comparer la référence au rendu avant EQ, `y0 = x * (δ + h_eff)`. Elle évite de mesurer une couleur de pièce dans l'EQ, puis de réappliquer cette couleur en ajoutant la pièce.

Pour le prototype neuronal, noter `D` la sortie `y_rev` du moteur : débruitage en conservant la réverbération. Comparer sous 7 kHz :

```text
d(b) = LTAS_dB(D(r))(b) - LTAS_dB(D(y0))(b)
```

Utiliser des fenêtres de même captation, le même modèle et la même méthode d'agrégation des deux côtés, avec niveau global retiré. Garder les références originales pour mesurer le bruit, contrôler les omissions et calculer les diagnostics.

Cette symétrie réduit un biais de traitement unilatéral ; **elle ne garantit pas son annulation**. Un réseau non linéaire peut changer différemment une référence sale et une simulation plus propre, et `D(q*y0)` n'est pas forcément égal à `q*D(y0)`.

Comparer trois ablations : statistiques originales améliorées ; `D(r)` contre `y0` ; `D(r)` contre `D(y0)`. La troisième est la candidate privilégiée, pas un résultat déjà prouvé. Ne pas choisir une méthode uniquement parce que son spectre se rapproche davantage du spectre débruité.

### 6.2 Pleine bande et coût

Sous 7 kHz, utiliser la courbe candidate seulement dans les bandes validées et stables. Faire une transition douce vers la mesure pleine bande de la V2 entre 7 et 8 kHz ; au-delà, conserver l'approche pleine bande avec sa confiance absolue. Si ces bandes sont dominées par le bruit, réduire la correction vers zéro. **Ne pas extrapoler le résultat 16 kHz comme une mesure des aigus.**

Le signal audible reste à sa fréquence native. Le FIR causal continue d'être appliqué une fois à l'ADR d'origine, puis la réverbération est rendue depuis cet ADR corrigé.

Les proxies de référence coûtent peu en calcul supplémentaire puisqu'ils existent dans l'inférence de pièce. En revanche, `D(y0)` requiert une nouvelle inférence sur le rendu virtuel : potentiellement plusieurs dizaines de secondes sur le CPU documenté. Mettre en cache par ADR, fenêtre, IR, wet, prédélai et modèle. Un changement de wet invalide cette analyse. Ne pas annoncer un mode interactif instantané.

L'analyse de `y0` ne doit pas redéfinir la pièce en boucle : on récupère ses proxies, on ignore son nouveau CTF, puis on applique une seule correction candidate. Garder ce mode explicitement expérimental jusqu'aux résultats du lot C.

### 6.3 Ce qu'il faut éviter

- Débruiter et déréverbérer la référence, comparer cette voix au mélange réverbéré ADR, puis ajouter encore de la reverb : les cibles ne correspondent plus.
- Prendre `LTAS(y_spch(r)) / LTAS(x)` comme correction directe parfaite : le réseau peut conserver des réflexions, supprimer du timbre ou modifier les consonnes.
- Réinjecter `r - y_rev` dans le rendu : cela peut réintroduire la réplique originale et des queues de mots.
- Utiliser une référence débruitée pour réestimer automatiquement la pièce : cela peut retirer précisément les indices de pièce recherchés.
- Renforcer une EQ incertaine simplement pour réduire l'erreur LTAS globale.

## 7. Pièces très réverbérantes : le chantier distinct

### 7.1 Premier levier pratique : réflexions précoces et queue

Si le wet global monte, les réflexions précoces et la queue montent ensemble. Le rendu peut devenir flou ou nasal avant que la queue ait la présence voulue.

Proposer en réglages avancés deux composantes du wet existant :

```text
h_early = m(t) · h_wet
h_late  = (1 - m(t)) · h_wet
h_eff   = g_global · (g_early · h_early + g_late · h_late)
```

`m(t)` vaut 1 au début puis descend doucement à 0. Point de départ expérimental : transition de 40 à 60 ms après le direct. Ce découpage est un contrôle de rendu, pas une découverte du temps de mélange exact de la salle.

Les masques sont complémentaires en amplitude : à gains unitaires, leur somme doit retrouver exactement le profil initial. Ne pas utiliser une paire de fondus à puissance constante qui amplifierait leur somme. Le direct reste dans la branche ADR.

Au départ, exposer seulement « Présence de la queue » autour de 0 dB, avec une plage indicative de ±6 dB, en conservant le niveau wet général existant. Cela permet un ajustement à l'oreille sans prétendre avoir retrouvé un DRR exact. L'EQ doit être recalculée sur le nouveau mélange virtuel ; elle n'est plus valable pour l'ancien dosage.

Ce réglage aide si la forme de la queue est plausible mais son niveau insuffisant. Il ne recrée pas une réflexion absente ni une longue queue coupée.

### 7.2 Calibration automatique : utiliser la même performance, en expérimentation

Les proxies de référence ouvrent une expérience différente des deux heuristiques abandonnées : resynthétiser **la performance de production elle-même**, puis comparer à sa version réverbérée débruitée.

```text
s_hat = proxy direct de r
v_hat = proxy réverbéré sans bruit de r
z(theta) = s_hat * (δ + h_theta)
```

Dans cette comparaison, le rythme et les mots proviennent du même enregistrement. Ne pas utiliser l'ADR pour estimer le dosage de pièce dans cette expérience.

Protocole de prototype :

1. Fixer l'EQ à l'identité pendant cette calibration. Ne pas optimiser simultanément une EQ libre et le wet : ils se compensent.
2. Garder l'IR et son prédélai fixes ; commencer par un seul paramètre, gain wet relatif de −6 à +12 dB par pas de 1 dB. Les bornes sont un espace de recherche provisoire.
3. Comparer des enveloppes d'énergie par grandes bandes, avec une résolution temporelle de l'ordre de 20–40 ms, sur les mêmes timestamps valides. Retirer les zones où le réseau ou le bruit domine et les bords de reconstruction.
4. Ajuster séparément un gain global nuisance pour ne pas confondre niveau d'enregistrement et dosage wet. Garder ce gain hors du profil de pièce.
5. Utiliser une perte robuste sur les log-puissances et un faible a priori vers le gain actuel. Évaluer toutes les fenêtres disponibles séparément avant de combiner leurs propositions.
6. Afficher la courbe de coût dans les diagnostics. Une courbe plate, plusieurs minima, un optimum à la borne ou des fenêtres contradictoires donnent « dosage indéterminé ».
7. Tester ensuite, seulement si le paramètre global est identifiable, un gain de queue distinct. Ne pas ajouter simultanément RT, EQ et prédélai à l'optimiseur initial.

**Limite majeure : les proxies et l'IR viennent du même réseau.** Une bonne reconstruction interne peut simplement reproduire ses propres biais. Une réverbération résiduelle dans `s_hat`, une perte de consonnes ou une erreur de gain de `y_rev` peuvent pousser le wet dans la mauvaise direction. L'erreur de reconstruction interne n'est donc ni une vérité terrain ni un score de confiance suffisant.

Ce lot doit rester une expérience de banc tant qu'il n'a pas retrouvé des écarts de dosage connus sur des signaux et des pièces indépendants. Sur une seule réplique courte, l'absence de validation entre fenêtres doit être visible. En cas d'échec, conserver le réglage manuel plutôt que remplacer une heuristique fragile par une autre.

### 7.3 Direct et queue longue

Avant de conclure qu'il faut un autre modèle, auditer la sensibilité de la canonicalisation à la fenêtre directe, aux réflexions proches et au pic choisi. Comparer le même CTF avant/après conversion et le profil wet utilisé réellement au rendu. Les définitions de DRR doivent utiliser les mêmes fenêtres pour vérité et estimation.

Pour `TAIL_TRUNCATED`, agrandir simplement le tableau de zéros n'ajoute aucune information. Une extension tardive par bruit filtré et enveloppes décroissantes peut être une future option, distincte de l'extension HF existante. Elle doit être marquée comme synthétisée, préserver le début de l'IR et n'être activée qu'après validation de continuité et de décroissance. Ce n'est pas une correction à inclure silencieusement dans le lot A.

## 8. Fond sonore : séparer raccord de voix et raccord de montage

Le raccord doit aussi être écouté dans son montage, sur le fond de production existant. Un export isolé sans ambiance peut paraître artificiellement propre même si la voix et la pièce sont raisonnables.

Première solution produit : option d'écoute « Avec fond de plateau », utilisant un extrait sans parole choisi par l'utilisateur. Ne pas modifier automatiquement `_EQ_IR_MIX`. Si un futur export complet avec ambiance est demandé, le rendre explicite ; éviter de doubler le fond déjà présent dans la station de montage.

Un extrait doit être assez long et sans mots, musique identifiable, choc ou queue parlée résiduelle. Un échec de détection de voix ne suffit pas à le certifier. Pour une durée supérieure au fond disponible, utiliser des raccords discrets évalués à l'écoute ; une boucle très courte crée une répétition audible. Le résidu d'un séparateur n'est pas un substitut sûr à un fond réellement isolé.

Le niveau d'ambiance doit être choisi relativement au niveau du dialogue après traitement, puis rester continu, sans pompage mot par mot. Une synthèse de bruit stationnaire à partir d'une densité spectrale pourrait reproduire un souffle ; elle ne recrée pas un plateau vivant. Garder cette possibilité hors du premier lot.

## 9. Alternatives recherchées et choix

| Piste | Intérêt | Décision proposée |
|---|---|---|
| Sorties auxiliaires Rec-RIR | Déjà dans le modèle installé, pas de nouvel apprentissage | Premier prototype, limité à l'analyse |
| Silero VAD | Meilleure sélection de parole que l'énergie seule | Candidat du lot A ; mesurer les erreurs sur les références difficiles |
| DeepFilterNet | Débruitage pleine bande 48 kHz, projet avec poids et prise en charge Windows annoncée | Solution de comparaison si les proxies 16 kHz limitent le résultat |
| WPE / NARA-WPE | Déréverbération par prédiction linéaire, implémentation NumPy | Ablation de recherche ; pas une extraction garantie du timbre direct |
| BERP | Autre estimateur aveugle de paramètres en présence de bruit, code et liens de poids publiés | Référence de recherche ; pas un remplaçant automatique du moteur |
| Réseau complet de transfert acoustique | Peut traiter davantage que le spectre moyen | Plus gros chantier, pas nécessaire avant les ablations ci-dessus |

DeepFilterNet fournit un débruiteur ; cela ne garantit ni un timbre inchangé ni une conservation parfaite de la réverbération. L'installation et le coût dans les environnements Mimetic n'ont pas été testés. Son dépôt annonce Windows et fournit les poids, avec code sous MIT/Apache-2.0. [Dépôt officiel DeepFilterNet](https://github.com/Rikorose/DeepFilterNet)

NARA-WPE implémente plusieurs variantes de prédiction linéaire pondérée pour la déréverbération. Son intérêt ici serait une comparaison expérimentale ; ne pas supposer qu'une courte prise mono bruyante offre assez d'information pour une séparation fiable. [Dépôt NARA-WPE](https://github.com/fgnt/nara_wpe)

BERP est destiné aux paramètres acoustiques et physiques à partir de parole mono bruitée. Le dépôt publie des liens de poids et indique GPL-3.0 ; c'est un choix d'intégration différent du projet Mimetic actuel. Son modèle de direct et ses paramètres ne doivent pas être assimilés sans vérification au DRR de Mimetic. Aucun test local n'a été fait. [Dépôt BERP](https://github.com/Alizeded/BERP), [article](https://arxiv.org/html/2405.04476v3)

## 10. Validation qui peut départager les solutions

### 10.1 Corpus contrôlé

Conserver les essais V2, mais ajouter de la parole naturelle locale autorisée. Séparer les locuteurs, les pièces et les bruits du réglage des paramètres et de l'évaluation finale. Le nombre de rendus issus d'une même phrase n'est pas le nombre de cas indépendants.

Matrice minimale, construite progressivement :

- Même performance pour vérifier le mécanisme, puis performances différentes du même acteur ; même texte et texte différent évalués séparément.
- EQ connue : identité, proximité, atténuation d'aigus, bosse large.
- Pièce : faible / moyenne / forte, avec des IR mesurées si disponibles ; RT60 et DRR variés indépendamment, premières réflexions fortes incluses.
- Bruit : aucun, stationnaire, évolutif, transitoires ; SNR indicatifs 30, 20, 10 et 5 dB.
- Références de 1, 3 et 10 s, avec et sans vraie pause ; musique ou autre voix comme cas d'échec à détecter, pas comme garanties de prise en charge.
- IR connue, IR estimée, puis IR volontairement mal dosée : distinguer erreur d'EQ et erreur de pièce.

Générer les signaux de vérité en conservant séparément parole directe, parole réverbérée et bruit. Les métriques sur ces composantes ne doivent pas utiliser la séparation neuronale comme vérité terrain.

### 10.2 Critères du lot A

1. À spectres et dispersion identiques, abaisser fortement tous les SNR ne peut plus produire une confiance identique par simple renormalisation.
2. Une queue seule après les mots ne sert pas de bruit seul. Si aucune pause valable n'existe, `noise_unknown` est déclaré.
3. Une augmentation de bruit à EQ inchangée ne doit pas créer systématiquement une correction d'aigus plus forte.
4. Mesurer le vrai biais d'EQ et l'erreur sur EQ connue, pas seulement le rapprochement du LTAS total bruité.
5. Aucune régression perceptible sur les cas propres déjà bons. Seuil d'alerte de banc proposé : hausse supérieure à 0,25 dB de l'erreur médiane d'EQ sur le jeu propre, à interpréter avec sa variabilité.

### 10.3 Critères des lots B et C

Tester séparément : fidélité du timbre de `y_rev`, préservation des décroissances, réverbération résiduelle dans `y_spch`, niveaux relatifs, alignement et longueur. Une voix plus intelligible peut être une moins bonne référence de couleur : ne pas confondre les objectifs.

Comparer V2, lot A, puis chacune des variantes du lot C sur exactement les mêmes cas. Pour les références sales, objectif provisoire : réduction médiane d'au moins 20 % de l'erreur d'EQ connue, sans hausse nette des mauvais cas et sans détérioration du jeu propre. Publier aussi la dispersion et les échecs. Ces seuils sont des décisions de livraison proposées, pas des performances obtenues.

Faire une écoute avec ordre masqué et niveau comparable, en séparant trois questions : timbre de la voix, distance/pièce, continuité du fond. Juger le dialogue dans le contexte du montage puis isolé pour repérer les artefacts. Aucun score de « qualité de parole propre » ne doit choisir automatiquement le rendu le plus sec.

### 10.4 Critères de calibration de pièce

Avec une IR initiale correcte, puis volontairement atténuée de 6 et 10 dB, mesurer le dosage proposé. Faire varier débit, pauses, bruit et EQ indépendamment. Inclure le cas où le profil est trop humide.

Objectifs provisoires avant toute suggestion automatique : erreur médiane inférieure à 2 dB, faible biais sur un profil déjà juste, et abstention sur les cas ambigus. Vérifier que le résultat n'est pas toujours ramené vers un même dosage malgré des vérités différentes. Sur vraie production, valider l'utilité à l'écoute ; un minimum de coût de reconstruction ne suffit pas.

### 10.5 Invariants audio et exports

- Mode désactivé : même chemin V2 et mêmes échantillons.
- Extraction des proxies : même CTF et même IR que le chemin précédent.
- EQ appliquée une seule fois, même gain de compensation sur dry et wet.
- Préservation des timestamps, silence initial et queue complète au rendu.
- `_IR_ONLY`, `_EQ_IR_MIX`, `_IR_PROFILE` conservent leur sens ; Match EQ reste exigé pour le clip traité selon D11.
- Échec de l'analyse robuste : statut explicite et accès au résultat V2, sans faire passer un repli pour un nouveau raccord réussi.
- Identité EQ : tester explicitement qu'un profil EQ actif mais plat n'est pas confondu avec une absence de Match EQ dans la logique d'export.
- Aucune ambiance ajoutée ni voix neuronale substituée à l'ADR sans option explicite.

## 11. Découpage pour l'agent de programmation

| Livraison | Fichiers principalement concernés | Résultat attendu |
|---|---|---|
| A1 — audit reproductible | nouveau banc bruit/réverbération, diagnostics | Capturer la V2 et ses échecs avant changement |
| A2 — statistiques robustes | `analysis/speech_stats.py`, `analysis/eq_match.py` | Trois masques, bruit inconnu explicite, confiance absolue, incertitude honnête |
| A3 — passages cohérents | `engines/recrir/adapter.py`, serveur, interface | Fenêtres de parole et captation communes, choix de passage en secours |
| B — proxies Rec-RIR | `engines/recrir/worker.py`, adaptateur, stockage local | Trois sorties récupérées, aucune modification sonore par défaut |
| C — EQ expérimentale | analyse, caches, serveur, interface | Comparaison débruitée symétrique avec ablations et limites de bande |
| D — présence de la queue | DSP, profil, caches, contrôles avancés | Réglage early/late à identité exacte au neutre |
| E — calibration | banc séparé, diagnostics | Étude sur même performance ; livraison conditionnelle aux résultats |
| F — contexte d'ambiance | préécoute et import optionnel | Écoute avec fond choisi, export actuel inchangé par défaut |

Les noms de modules additionnels sont à choisir selon le dépôt au moment du codage. Les nouveaux paramètres et versions entrent dans les clés de cache. Les modèles, leurs poids et leurs dépendances restent dans le projet, conformément à D10.

L'interface peut rester simple : deux fichiers comme aujourd'hui, un mode « Référence difficile » après validation, un indicateur compréhensible de fiabilité, puis « Présence de la queue » en avancé. Les diagnostics de SNR, proxies et coûts restent dans le rapport technique.

### Consigne de reprise

> Lire ce document, `docs/decisions.md` et `docs/eq-validation.md`. Implémenter d'abord les lots A1/A2 avec une comparaison reproductible à la V2. Conserver les bons résultats existants, le lissage 5000 et l'acceptation des répliques courtes. Séparer fiabilité absolue et poids relatifs ; exclure les queues du bruit seul ; représenter les informations inconnues comme telles. Ensuite, instrumenter les trois sorties du Rec-RIR installé, sans changer le rendu. Évaluer les proxies avant d'activer leur usage pour l'EQ. Ne pas réintroduire les anciens dosages automatiques fondés sur les pauses, ne pas modifier le timbre par un réseau dans l'audio exporté et ne pas installer un autre gros modèle avant les ablations. Rapporter les gains, les régressions et les cas non identifiables.

## 12. Reproduire le contrôle de normalisation des poids

À exécuter depuis la racine avec l'environnement de l'application. Ce contrôle utilise des statistiques construites, sans lire de fichier utilisateur ; il permet de reproduire le tableau du §3.1 sur la V2 auditée.

```python
import sys
sys.path.insert(0, "src")
import numpy as np
from mimetic.analysis import eq_match, speech_stats

f, _ = speech_stats.band_edges(48000)
p = (f / 1000) ** -1.0
shape = 4.0 * np.tanh(np.log2(f / 1500))
for snr in (18.0, 6.2):
    common = dict(
        ok=True, active_seconds=3.0, centers_hz=f,
        dispersion=np.full(f.size, 0.2), blocks=10,
        classes={}, snr_db=np.full(f.size, snr),
    )
    ref = dict(common, power=p * 10 ** (shape / 10))
    base = dict(common, power=p)
    c = eq_match.estimate_curve(ref, base, hf_synthesized=False)
    print(snr, np.median(c["weights"]), c["low_confidence"],
          c["gain_db"].min(), c["gain_db"].max())
```

Après correction, cette propriété doit changer de manière documentée. Ce contrôle numérique ne remplace pas les validations sur parole, bruit, pièces et écoute.
