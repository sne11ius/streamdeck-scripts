# Stream Deck Scripts

Scripte für Elgato Stream Deck Tasten, gesteuert über [OpenDeck](https://github.com/nekename/OpenDeck) unter KDE Wayland.

## Setup

In OpenDeck pro Taste eine "Run command"-Action auf "Key down" konfigurieren.

## Scripts

| Script | Beschreibung | Icon |
|--------|-------------|------|
| `firefox.sh` | Startet Firefox oder bringt ihn in den Vordergrund | `/usr/share/icons/hicolor/128x128/apps/firefox.png` |

## Helfer

- `kwin-focus.sh <window-class>` — Bringt ein Fenster per KWin-Scripting (DBus) in den Vordergrund. Wird von den App-Scripts genutzt und funktioniert unter Wayland.
