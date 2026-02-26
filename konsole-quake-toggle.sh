#!/usr/bin/env bash
# Konsole Quake-style dropdown toggle für KDE Plasma Wayland
# Nutzt PID-Tracking, da Konsole kein --class/--name Flag hat.
# Window-Rules (no border, position, size, etc.) werden per KWin-Script
# nach dem Start angewendet, da deklarative Regeln nicht zuverlässig
# auf Title-Matching reagieren (Timing-Problem).

PIDFILE="/tmp/konsole-quake.pid"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$HOME/.local/state/konsole-quake-session.json"

run_kwin_script() {
    local tmpscript
    tmpscript=$(mktemp /tmp/kwin-konsole-XXXXXX.js)
    cat > "$tmpscript" <<< "$1"
    local script_id
    script_id=$(qdbus6 org.kde.KWin /Scripting loadScript "$tmpscript")
    qdbus6 org.kde.KWin "/Scripting/Script${script_id}" run
    qdbus6 org.kde.KWin "/Scripting/Script${script_id}" stop
    rm -f "$tmpscript"
}

is_running() {
    [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

# Wenn Konsole-Quake nicht läuft: starten und State wiederherstellen
if ! is_running; then
    unset CLAUDECODE

    if [[ -f "$STATE_FILE" ]]; then
        # State vorhanden: Konsole minimal starten, dann per D-Bus wiederherstellen
        konsole --separate \
                --profile Quake \
                --hide-menubar &
    else
        # Kein State: Fallback auf statische Tab-Datei
        konsole --separate \
                --tabs-from-file ~/.config/konsole-quake-tabs \
                --profile Quake \
                --hide-menubar &
    fi
    echo $! > "$PIDFILE"

    # Warten bis das Fenster erscheint, dann Window-Rules anwenden
    sleep 1
    PID=$(cat "$PIDFILE")
    run_kwin_script "
(function() {
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        var w = windows[i];
        if (w.pid === ${PID}) {
            w.noBorder = true;
            w.keepAbove = true;
            w.skipTaskbar = true;
            w.skipPager = true;
            w.skipSwitcher = true;
            w.frameGeometry = {x: 1707, y: 0, width: 2560, height: 1087};
            break;
        }
    }
})();
"
    # State wiederherstellen (im Hintergrund, nach Window-Rules)
    # Extra Wartezeit damit Konsole vollständig initialisiert ist
    if [[ -f "$STATE_FILE" ]]; then
        (sleep 1 && python3 "$SCRIPT_DIR/konsole-quake-session.py" restore "$PID" 2>/dev/null) &
    fi

    exit 0
fi

PID=$(cat "$PIDFILE")

# State speichern bei jedem Toggle (schneller D-Bus-Call)
python3 "$SCRIPT_DIR/konsole-quake-session.py" save 2>/dev/null || true

# Toggle: minimize/focus
run_kwin_script "
(function() {
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        var w = windows[i];
        if (w.pid === ${PID}) {
            if (w.minimized || workspace.activeWindow !== w) {
                w.minimized = false;
                workspace.activeWindow = w;
            } else {
                w.minimized = true;
            }
            break;
        }
    }
})();
"
