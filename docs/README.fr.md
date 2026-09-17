# Mimetic — beta LAN

Rendu de réverbération **wet seule** pour ADR (`nom_ADR_REVERB.wav`, 32 bits flottants), à placer sur une piste parallèle au même début que l'ADR. Voir [architecture.md](../architecture.md).

## Utilisation

Une seule page :

1. **SOURCE** : déposer le dialogue de tournage. La pièce y est analysée.
2. **DESTINATION** : déposer la voix ADR sèche. C'est elle qui reçoit la reverb.
3. Le calcul démarre tout seul : analyse (~30 s par passage de 6 s, jusqu'à 3 passages choisis automatiquement parmi les plus parlés), puis rendu. Le bouton « Annuler » arrête le calcul.
4. Écouter Destination / Reverb seule / Destination + reverb, ajuster « Niveau de reverb », puis **Exporter** : `nom_ADR_REVERB.wav` (WAV 32 bits flottants, 100 % wet), à caler au début exact du fichier DESTINATION.

Changer la DESTINATION ou le niveau ne relance que le rendu (quelques secondes). Changer la SOURCE relance l'analyse.

## Limites à connaître

- **Moteur expérimental** (Rec-RIR sur CPU) : pas encore validé sur du vrai dialogue de tournage. Sur les pilotes synthétiques, RT60 et DRR sont dans les objectifs, mais la reverb proposée est souvent **~1,5 dB trop faible** : remonter le niveau si besoin.
- **Aigus au-dessus de 7,8 kHz synthétisés** (mode hybride) : le modèle n'analyse que jusqu'à 8 kHz. Au-dessus, les aigus suivent la décroissance mesurée juste en dessous et l'absorption typique de l'air ; ils ne sont pas mesurés. Signalé dans la page et dans le JSON.
- **Queue de reverb limitée à ~1 s.**
- Détails : [docs/model-evaluation.md](model-evaluation.md), [docs/decisions.md](decisions.md).

## Lancer

Prérequis : Python 3.11 avec `numpy scipy soundfile fastapi uvicorn` (versions dans `pyproject.toml`).

```bash
python run_beta.py
```

Le terminal affiche les adresses, par exemple `http://10.0.0.30:8765`, à ouvrir depuis n'importe quel poste du LAN.

Options : `--port 8765`, `--host 127.0.0.1` (cette machine seulement), `--export-dir D:\Exports`, `--data-dir`.

Données et exports par défaut : `%LOCALAPPDATA%\Mimetic\beta\` (les imports et fichiers de travail sont purgés à chaque démarrage). Journal du moteur : `.engine-logs\recrir-worker.log`.

### Moteur d'analyse (déjà installé sur cette machine)

Environnement séparé `.venv-engine` et code/poids officiels dans `third_party/Rec-RIR` (commit épinglé, empreinte des poids vérifiée). Procédure de réinstallation : [engine-requirements.txt](../engine-requirements.txt). Sans ces éléments, la page affiche « moteur d'analyse non installé ».

### Accès depuis les autres postes

Le pare-feu Windows est actif : au premier lancement, Windows peut demander d'autoriser Python sur les réseaux privés. Sinon, dans un PowerShell **administrateur** :

```powershell
New-NetFirewallRule -DisplayName "Mimetic beta 8765" -Direction Inbound -Protocol TCP -LocalPort 8765 -Profile Private -Action Allow
```

⚠️ Pas d'authentification : toute personne du réseau peut utiliser la page et télécharger les exports. Une seule session de projet partagée et un seul calcul à la fois.

## CLI (outils manuels, sans analyse ni interface)

```bash
set PYTHONPATH=src
python -m mimetic.cli manual ADR.wav --rt60 0.8 --drr 6 --out exports
python -m mimetic.cli known-ir ADR.wav IR.wav --direct-index 120 --out exports --export-ir
```

## Tests et banc

```bash
python -m pytest -q
```

Pilotes du moteur (environ 10 min et 4 min) :

```bash
.venv-engine\Scripts\python.exe benchmarks\recrir_synthetic.py <parole_seche_16k> <sortie>
```

```bash
python benchmarks\recrir_evaluate.py <sortie>
```

```bash
.venv-engine\Scripts\python.exe benchmarks\recrir_fullband.py <parole_seche_16k> <sortie_pleine_bande>
```

```bash
python benchmarks\hybrid_evaluate.py <sortie_pleine_bande>
```

Voir [docs/validation.md](validation.md).
