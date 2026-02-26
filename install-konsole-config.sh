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

for file in konsoleui.rc; do
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

# Skripte ausführbar machen
chmod +x "$SCRIPT_DIR/konsole-quake-toggle.sh" \
         "$SCRIPT_DIR/konsole-quake-session.py" 2>/dev/null || true

# Systemd-User-Service installieren (speichert State beim Shutdown)
SERVICE_DIR="$HOME/.config/systemd/user"
SERVICE_SRC="$SCRIPT_DIR/konsole-quake-save.service"
SERVICE_DEST="$SERVICE_DIR/konsole-quake-save.service"

if [[ -f "$SERVICE_SRC" ]]; then
    mkdir -p "$SERVICE_DIR"
    # @SCRIPT_DIR@ durch echten Pfad ersetzen
    sed "s|@SCRIPT_DIR@|$SCRIPT_DIR|g" "$SERVICE_SRC" > "$SERVICE_DEST"
    systemctl --user daemon-reload
    systemctl --user enable konsole-quake-save.service 2>/dev/null || true
    echo "  konsole-quake-save.service — installiert und aktiviert"
fi

# State-Verzeichnis anlegen
mkdir -p "$HOME/.local/state"

echo ""
echo "Fertig. Konsole neu starten, damit die Änderungen greifen."
