#!/usr/bin/env bash
# Konsole Quake-style dropdown toggle für KDE Plasma Wayland
# Nutzt PID-Tracking, da Konsole kein --class/--name Flag hat.
# Window-Rules (no border, position, size, etc.) werden per KWin-Script
# nach dem Start angewendet, da deklarative Regeln nicht zuverlässig
# auf Title-Matching reagieren (Timing-Problem).

PIDFILE="/tmp/konsole-quake.pid"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE_FILE="$HOME/.local/state/konsole-quake-session.json"

# Ignore repeat button presses while a launch/restore or snapshot is in flight.
exec 9>>"${XDG_RUNTIME_DIR:-/tmp}/konsole-quake-toggle-${UID}.lock"
flock -n 9 || exit 0

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
    local pid
    [[ -f "$PIDFILE" ]] || return 1
    read -r pid < "$PIDFILE"
    [[ $pid =~ ^[1-9][0-9]*$ && -O /proc/$pid && /proc/$pid/exe -ef $(command -v konsole) ]]
}

# Wenn Konsole-Quake nicht läuft: starten und State wiederherstellen
if ! is_running; then
    unset CLAUDECODE

    style="${XDG_DATA_HOME:-$HOME/.local/share}/konsole/quake.qss"
    style_state="${XDG_STATE_HOME:-$HOME/.local/state}/konsole-quake-style/active"
    appearance_args=()
    if [[ -f "$style_state" && -r "$style" ]]; then
        # Hide the widgets, not the XML definitions shared with right-click menus.
        appearance_args=(--hide-toolbars --stylesheet "$style")
    fi

    if [[ -f "$STATE_FILE" ]]; then
        # The helper owns the new process; it never restores into an existing PID.
        if ! PID=$(python3 "$SCRIPT_DIR/konsole-quake-session.py" launch -- \
            --hide-menubar "${appearance_args[@]}" 9>&-); then
            printf '%s\n' 'Quake restore failed; saved state and existing terminals were left untouched.' >&2
            exit 1
        fi
        [[ $PID =~ ^[1-9][0-9]*$ ]] || {
            printf '%s\n' 'Quake restore returned no valid PID; refusing window operations.' >&2
            exit 1
        }
    else
        # Kein State: Fallback auf statische Tab-Datei
        konsole --separate \
                --tabs-from-file ~/.config/konsole-quake-tabs \
                --profile Quake \
                --hide-menubar "${appearance_args[@]}" 9>&- &
        PID=$!
    fi
    printf '%s\n' "$PID" > "$PIDFILE"

    # Warten bis das Fenster erscheint, dann Window-Rules anwenden
    sleep 1
    PID=$(cat "$PIDFILE")
    run_kwin_script "
(function() {
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        var w = windows[i];
        if (w.pid === ${PID}) {
            workspace.activeWindow = w;
            break;
        }
    }
})();
"
    qdbus6 org.kde.kglobalaccel /component/kwin \
        org.kde.kglobalaccel.Component.invokeShortcut PoloniumToggleActiveTiling
    sleep 0.1
    run_kwin_script "
(function() {
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        var w = windows[i];
        if (w.pid === ${PID}) {
            w.noBorder = true;
            w.keepAbove = true;
            w.keepBelow = false;
            w.skipTaskbar = true;
            w.skipPager = true;
            w.skipSwitcher = true;
            var sg = workspace.activeScreen.geometry;
            w.frameGeometry = {x: sg.x, y: sg.y, width: sg.width, height: Math.round(sg.height * 0.755)};
            break;
        }
    }
})();
"
    exit 0
fi

PID=$(cat "$PIDFILE")

# Snapshot failures must remain visible, but must not strand a hidden window.
if ! python3 "$SCRIPT_DIR/konsole-quake-session.py" save 9>&-; then
    printf '%s\n' 'Quake snapshot was not updated; keeping the last saved state and continuing the toggle.' >&2
fi

# Toggle: minimize/focus
run_kwin_script "
(function() {
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        var w = windows[i];
        if (w.pid === ${PID}) {
            if (w.minimized || workspace.activeWindow !== w) {
                // Prüfen ob Fenster auf einem sichtbaren Screen liegt
                var visible = false;
                var screens = workspace.screens;
                var fg = w.frameGeometry;
                for (var s = 0; s < screens.length; s++) {
                    var sg = screens[s].geometry;
                    if (fg.x < sg.x + sg.width && fg.x + fg.width > sg.x &&
                        fg.y < sg.y + sg.height && fg.y + fg.height > sg.y) {
                        visible = true;
                        break;
                    }
                }
                if (!visible) {
                    var ag = workspace.activeScreen.geometry;
                    w.frameGeometry = {x: ag.x, y: ag.y, width: ag.width, height: Math.round(ag.height * 0.755)};
                }
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
