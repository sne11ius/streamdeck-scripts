#!/usr/bin/env bash
# Startet Firefox oder bringt ihn in den Vordergrund (KDE Wayland)
"$(dirname "$(readlink -f "$0")")/app-launch.sh" firefox-bin firefox firefox
