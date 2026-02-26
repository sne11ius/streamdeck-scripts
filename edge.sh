#!/usr/bin/env bash
# Startet Edge oder bringt ihn in den Vordergrund (KDE Wayland)
"$(dirname "$(readlink -f "$0")")/app-launch.sh" msedge microsoft-edge microsoft-edge
