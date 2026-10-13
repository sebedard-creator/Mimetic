#!/bin/bash
set -eu
TASK_DIR="$(cd "$(dirname "$0")" && pwd)"
TASK_APP="${1:-$TASK_DIR/Mimetic.app}"
if [ ! -d "$TASK_APP" ] && [ -d "$HOME/Applications/Mimetic.app" ]; then
    TASK_APP="$HOME/Applications/Mimetic.app"
elif [ ! -d "$TASK_APP" ] && [ -d /Applications/Mimetic.app ]; then
    TASK_APP="/Applications/Mimetic.app"
fi
TASK_RESOURCES="$TASK_APP/Contents/Resources"
if [ ! -x "$TASK_RESOURCES/bin/python3.11" ]; then
    echo "Mimetic.app introuvable. Passer son chemin en argument ou garder le script à côté de l'application."
    read -r -p "Entrée pour fermer… " TASK_REPLY
    exit 1
fi
unset PYTHONHOME PYTHONPATH DYLD_LIBRARY_PATH DYLD_FRAMEWORK_PATH
echo "Chargement du pont AppKit et validation des classes Cocoa…"
"$TASK_RESOURCES/bin/python3.11" -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import mimetic_macos.cocoa_app; print("Classes AppKit et protocoles Cocoa chargés.")' "$TASK_RESOURCES/app"
echo "Autotest du moteur et de l'export PTX…"
if "$TASK_RESOURCES/bin/python3.11" -I -B "$TASK_RESOURCES/app/app_entry.py" --self-test; then
    echo "Autotest réussi. Aucun Python système ni compilateur n'a été utilisé."
else
    echo "Échec. Consulter ~/Library/Logs/Mimetic."
fi
read -r -p "Entrée pour fermer… " TASK_REPLY
