#!/usr/bin/env bash
# Installiert Konsole-Styling und die bestehende Session-Sicherung.
# Nur Styling ohne systemd-Aktionen: bash install-konsole-style.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
bash "$SCRIPT_DIR/install-konsole-style.sh"

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
echo "Fertig. Laufende Konsole-Sitzungen bleiben offen; Styling gilt beim nächsten Start."
