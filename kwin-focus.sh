#!/usr/bin/env bash
# Bringt ein Fenster anhand der Window-Class in den Vordergrund (KDE Wayland)
# Usage: kwin-focus.sh <window-class>

CLASS="$1"
TMPSCRIPT=$(mktemp /tmp/kwin-focus-XXXXXX.js)

cat > "$TMPSCRIPT" <<EOF
var clients = workspace.windowList();
for (var i = 0; i < clients.length; i++) {
    if (clients[i].resourceClass === "$CLASS") {
        workspace.activeWindow = clients[i];
        break;
    }
}
EOF

SCRIPT_ID=$(qdbus6 org.kde.KWin /Scripting loadScript "$TMPSCRIPT")
qdbus6 org.kde.KWin "/Scripting/Script${SCRIPT_ID}" run
qdbus6 org.kde.KWin "/Scripting/Script${SCRIPT_ID}" stop
rm -f "$TMPSCRIPT"
