# Stream Deck Scripts

Scripte für Elgato Stream Deck Tasten, gesteuert über [OpenDeck](https://github.com/nekename/OpenDeck) unter KDE Wayland.

## Setup

In OpenDeck pro Taste eine "Run command"-Action auf "Key down" konfigurieren.

## Scripts

| Script | Beschreibung | Icon |
|--------|-------------|------|
| `firefox.sh` | Startet Firefox oder bringt ihn in den Vordergrund | `/usr/share/icons/hicolor/128x128/apps/firefox.png` |
| `handy-tool.sh` | Paste-Helper für [Handy](https://handy.computer) Speech-to-Text — nimmt transkribierten Text entgegen und fügt ihn per Clipboard ins aktive Fenster ein | `microphone.png` |

## Helfer

- `kwin-focus.sh <window-class>` — Bringt ein Fenster per KWin-Scripting (DBus) in den Vordergrund. Wird von den App-Scripts genutzt und funktioniert unter Wayland.

## Speech-to-Text via Stream Deck

Stream Deck Taste → Handy (STT) → `handy-tool.sh` → Text wird ins aktive Fenster gepastet.

### So funktioniert's

1. Eine Stream Deck Taste sendet per "Simulate Input" den Hotkey `Ctrl+Shift+Alt+F7` (Push-to-Talk)
2. [Handy](https://handy.computer) (muss separat laufen) fängt den Hotkey ab und startet die Aufnahme
3. Bei Loslassen der Taste stoppt die Aufnahme, Handy transkribiert lokal via STT
4. Der transkribierte Text wird an `handy-tool.sh` als Argument übergeben
5. Das Script kopiert den Text per `wl-copy` ins Wayland-Clipboard und simuliert Ctrl+V (bzw. Ctrl+Shift+V in Terminals) via `ydotool`
6. Abschließend wird Enter gesendet

### Voraussetzungen

- [Handy](https://handy.computer) mit `ExternalScript`-Paste-Methode, konfiguriert auf `handy-tool.sh`
- `wl-copy` (aus `wl-clipboard`) — Clipboard-Zugriff unter Wayland
- `ydotool` + `ydotoold` — Tastatur-Simulation (muss als root laufen)
- `qdbus6` — KWin-Scripting für Terminal-Erkennung

## Lizenz

[EUPL 1.2](./LICENSE)
