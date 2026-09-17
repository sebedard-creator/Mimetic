@echo off
REM Installation autonome de Mimetic : tout est créé dans ce dossier.
REM Prérequis : Python 3.11 (lanceur "py") et git dans le PATH.
setlocal
cd /d "%~dp0"

echo === 1/4 Environnement de l'application (.venv) ===
if not exist ".venv\Scripts\python.exe" py -3.11 -m venv .venv || goto :error
".venv\Scripts\python.exe" -m pip install --no-cache-dir -r requirements.txt || goto :error

echo.
echo === 2/4 Environnement du moteur d'analyse (.venv-engine, PyTorch CPU ~800 Mo) ===
if not exist ".venv-engine\Scripts\python.exe" py -3.11 -m venv .venv-engine || goto :error
".venv-engine\Scripts\python.exe" -m pip install --no-cache-dir torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu || goto :error
".venv-engine\Scripts\python.exe" -m pip install --no-cache-dir -r engine-requirements.txt || goto :error

echo.
echo === 3/4 Code et poids Rec-RIR (~48 Mo, licence MIT) ===
if not exist "third_party\Rec-RIR\.git" git clone https://github.com/Audio-WestlakeU/Rec-RIR.git third_party\Rec-RIR || goto :error
git -C third_party\Rec-RIR checkout --quiet b3ac6fc1421bd58e017022037feb14f30366b46f || goto :error

echo.
echo === 4/4 Vérification ===
".venv\Scripts\python.exe" -m pytest -q || goto :error
".venv\Scripts\python.exe" -c "import sys; sys.path.insert(0, 'src'); from mimetic.engines.recrir import adapter; c = adapter.capabilities(); print('Moteur d analyse :', 'OK' if c['available'] else 'INDISPONIBLE ' + str(c.get('detail')))" || goto :error

echo.
echo Installation terminee. Lancer le service avec : run.cmd
exit /b 0

:error
echo.
echo ECHEC de l'installation (voir le message ci-dessus).
exit /b 1
