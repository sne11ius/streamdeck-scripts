#!/usr/bin/env bash
# Startet Edge oder bringt ihn in den Vordergrund (KDE Wayland)

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

if pgrep -x msedge > /dev/null; then
    "$SCRIPT_DIR/kwin-focus.sh" microsoft-edge
else
    microsoft-edge &
fi
