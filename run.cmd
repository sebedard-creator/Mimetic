@echo off
REM Lance le service Mimetic avec l'environnement du projet.
cd /d "%~dp0"
".venv\Scripts\python.exe" run.py %*
