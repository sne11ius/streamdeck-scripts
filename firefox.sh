#!/usr/bin/env bash
# Startet Firefox oder bringt ihn in den Vordergrund (KDE Wayland)

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"

if pgrep -x firefox-bin > /dev/null; then
    "$SCRIPT_DIR/kwin-focus.sh" firefox
else
    firefox &
fi
