#!/usr/bin/env bash
# Installiert die Konsole-Konfiguration (Toolbar ausblenden).
#
# Die XML-GUI-Dateien überschreiben die in Konsole eingebetteten Defaults
# und entfernen beide Toolbars (mainToolBar + sessionToolbar).
# Konsole muss danach neu gestartet werden.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TARGET_DIR="$HOME/.local/share/kxmlgui5/konsole"

mkdir -p "$TARGET_DIR"

for file in konsoleui.rc sessionui.rc; do
    src="$SCRIPT_DIR/konsole-config/$file"
    dest="$TARGET_DIR/$file"

    if [[ -f "$dest" ]]; then
        if cmp -s "$src" "$dest"; then
            echo "  $file — bereits aktuell"
            continue
        fi
        echo "  $file — überschreibe (Backup: ${dest}.bak)"
        cp "$dest" "${dest}.bak"
    else
        echo "  $file — installiere"
    fi

    cp "$src" "$dest"
done

echo ""
echo "Fertig. Konsole neu starten, damit die Änderungen greifen."
