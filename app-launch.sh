#!/usr/bin/env bash
# Startet eine Anwendung oder bringt sie in den Vordergrund (KDE Wayland)
# Usage: app-launch.sh <process-name> <window-class> <start-command>

PROCESS="$1"
WINDOW_CLASS="$2"
COMMAND="$3"

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

if pgrep -x "$PROCESS" > /dev/null; then
    "$SCRIPT_DIR/kwin-focus.sh" "$WINDOW_CLASS"
else
    $COMMAND &
fi
