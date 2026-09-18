# Stream Deck Scripts

Scripte für Elgato Stream Deck Tasten, gesteuert über [OpenDeck](https://github.com/nekename/OpenDeck) unter KDE Wayland.

## Setup

In OpenDeck pro Taste eine "Run command"-Action auf "Key down" konfigurieren.

## Scripts

| Script | Beschreibung | Icon |
|--------|-------------|------|
| `firefox.sh` | Startet Firefox oder bringt ihn in den Vordergrund | `firefox.png` |
| `edge.sh` | Startet Edge oder bringt ihn in den Vordergrund | `edge.png` |
| `konsole-quake-toggle.sh` | Konsole im Quake-Stil: öffnet als randloses Dropdown von oben, toggled Sichtbarkeit per Stream Deck Taste | `konsole-quake.png` |
| `handy-tool.sh` | Paste-Helper für [Handy](https://handy.computer) Speech-to-Text — nimmt transkribierten Text entgegen und fügt ihn per Clipboard ins aktive Fenster ein | `microphone.png` |

## Konsole-Konfiguration

Das Quake-Fenster verwendet native Konsole-Schalter statt XML-GUI-Overrides:

- Beide Toolbars werden mit `--hide-toolbars` ausgeblendet. Aktionen und Rechtsklick-Menüs bleiben erhalten.
- Die Kopfzeilen aller Split-Panes werden über `SplitViewVisibility=AlwaysHideSplitHeader` ausgeblendet.
- `SplitDragHandleMedium` liefert stabile 5 logische Pixel, auch nach Änderungen in den Einstellungen. `konsole-config/quake.qss` macht beide Split-Richtungen durchscheinend und hebt den Griff beim Überfahren leicht hervor.
- Die unteren Tabs und das bestehende Quake-Toggle-/Session-Verhalten bleiben erhalten. Pane-Kopfzeilen und Griffbreite sind gemeinsame Konsole-Einstellungen; das Stylesheet und die Toolbar-Startoption gelten für den Quake-Prozess.

Nur das Styling installieren, ohne Konsole/KWin anzusprechen oder systemd zu ändern:

```bash
bash install-konsole-style.sh
```

**Laufende Terminals nicht beenden.** Der Installer sendet keine Reload-Benachrichtigung, startet keine Prozesse neu und verändert weder PID-Datei noch Session-Sicherung. Das komplette Styling greift beim nächsten regulären Start des Quake-Fensters, nicht beim Minimieren/Wiederherstellen. Ein späteres Anwenden der Konsole-Einstellungen kann die gemeinsamen Split-Einstellungen auch in einem bereits laufenden Prozess übernehmen.

Rückgängig machen:

```bash
bash install-konsole-style.sh --rollback
```

Die Sicherung liegt unter `${XDG_STATE_HOME:-$HOME/.local/state}/konsole-quake-style`. Wiederholte Installation erhält die ursprüngliche Sicherung. Rollback stellt nur die beiden Split-Einstellungen und ein zuvor vorhandenes Stylesheet wieder her; spätere, unabhängige Einstellungen bleiben erhalten. Es entfernt außerdem die zusätzlichen Startoptionen für künftige Quake-Starts. Bereits von Konsole gespeicherte Toolbar-/Fensterzustände werden absichtlich nicht überschrieben; Toolbars lassen sich im nativen Einstellungsmenü wieder einschalten.

Menüs und Bedienung ohne Kopfzeilen (Konsole-Standardbelegung):

- Rechtsklick öffnet das Terminal-Kontextmenü. Bei Programmen mit eigener Maussteuerung: `Shift` + Rechtsklick.
- `Ctrl+Shift+M` zeigt die Menüleiste wieder; `Shift+F10` öffnet das Hauptmenü.
- `Ctrl+Shift+C`, `Ctrl+Shift+V` und `Ctrl+Shift+F` bleiben für Kopieren, Einfügen und Suchen verfügbar.
- Split-, Maximieren-, Schließen- und Größenaktionen bleiben über Menüs/Tastenkürzel verfügbar. Pane-Griffe bleiben ziehbar; die Drag-Schaltfläche der ausgeblendeten Kopfzeile entfällt.

Die alten `konsoleui.rc`/`sessionui.rc` bleiben als historische Dateien im Repository, werden aber nicht mehr installiert. Commit `0c61968` dokumentiert einen früheren Rechtsklick-Absturz im Zusammenhang mit dem Session-Override. Bereits vorhandene eigene XML-Overrides werden nicht automatisch gelöscht.

Die vorhandenen KWin-Glitch-Effekte bleiben unverändert. Animationen einzelner Tabs/Panes sind zurückgestellt: Diese sind keine KWin-Fenster und benötigen eine eigene Konsole-Integration.

Vollständige Installation inklusive bestehender systemd-Session-Sicherung:

```bash
./install-konsole-config.sh
```

### Tests

```bash
python3 tests/konsole-style-test.py
bash tests/run-konsole-style-gui.sh
```

Der GUI-Test braucht Konsole, Qt6-Widgets-/Qt6-Test-Entwicklungsdateien, einen C++-Compiler, D-Bus, KWin Wayland und Kvantum/KvFlatRed. Er verwendet ausschließlich Wegwerf-Shells, ein eigenes HOME/XDG-Verzeichnis und einen privaten D-Bus unter `/tmp/opencode`. Die Testbibliothek wird nur in den neu gestarteten Testprozess geladen, niemals an einen laufenden Prozess angehängt. Offscreen prüft unter anderem Kontextmenüs; ein separater virtueller Wayland-Compositor prüft echte Alpha-Werte und die 5px-Griffe. Screenshots und Protokolle bleiben im ausgegebenen temporären Verzeichnis.

## Session-Sicherung

Die Wiederherstellung erzeugt **ausschließlich einen neuen, eigenen Konsole-Prozess**. Sie verändert keine bereits laufende Konsole. `restore <pid>` wird deshalb ausdrücklich abgelehnt; der Quake-Launcher verwendet stattdessen `launch`. Es werden keine `cd`-/`exit`-Befehle, Tastendrücke oder sonstigen Eingaben an Terminals gesendet, und die sicherheitskritische D-Bus-API bleibt deaktiviert.

Gesichert werden Tab-Reihenfolge, verschachtelte Splits, lokale Arbeitsverzeichnisse, Profile, lokale/entfernte Titelformate, aktive Panes und Größenverhältnisse. Laufende Programme, Shell-Variablen, Scrollback und ungespeicherter Anwendungszustand gehören **nicht** dazu. Ein Snapshot ersetzt keine Sicherung laufender Arbeit und ist kein Grund, eine wichtige Sitzung zu beenden.

- Beim Speichern werden Panes kurz ausgewählt, um ihre tatsächlichen Session-IDs zuzuordnen. Danach werden die gemerkten aktiven Panes und der ursprüngliche Tab wieder ausgewählt. `sessionList()` hat innerhalb eines Tabs eine andere Reihenfolge als die sichtbaren Panes und wird nicht mehr mit dem Layout zusammengezählt.
- Ist das Fenster inaktiv/minimiert oder ändert sich währenddessen seine Struktur, kann Konsole die Zuordnung nicht zuverlässig liefern. Dann wird die Sicherung abgelehnt, statt unvollständige Daten zu schreiben. Das Toggle funktioniert trotzdem weiter. Für einen verlässlichen Checkpoint das aktive Quake-Fenster über die Stream-Deck-Taste ausblenden; beim Einblenden kann eine abgelehnte Sicherung im Aktionsprotokoll erscheinen.
- Die JSON-Datei bleibt bei Fehlern unverändert. Erfolgreiches Speichern schreibt atomar und sichert die vorherige gültige Datei bytegetreu als `.bak`; beide erhalten Modus `0600`. Gleichzeitige Sicherung/Wiederherstellung wird durch Locks ausgeschlossen. Wiederholte Toggle-Aufrufe während einer laufenden Aktion werden ignoriert.
- Vor einer Wiederherstellung werden alle Daten geprüft. Die benötigten Profile müssen eindeutig vorhanden und alle Verzeichnisse zugänglich sein. Verzeichnisnamen mit wörtlichem `$` werden wegen Konsoles Variablenexpansion ausdrücklich abgelehnt, nicht durch das Home-Verzeichnis ersetzt. Remote-Verzeichnisse und laufende SSH-/Container-Sitzungen werden nicht rekonstruiert.
- Gewöhnliche alte JSON-Sicherungen ohne Versionsfeld werden weiterhin gelesen. Bereits falsch gespeicherte Pane-Zuordnungen oder ausgelassene Tabs lassen sich daraus nicht nachträglich ermitteln. Mehrdeutige alte Titel mit Konsole-Formatmarkern sowie ungültige Bäume werden mit einer Fehlermeldung abgelehnt.
- Größe wird über positive ganzzahlige Prozentwerte wiederhergestellt. Native Mindestgrößen können davon abweichen; der Helfer meldet größere Abweichungen. Nach einem Teilfehler bleiben neu angelegte Fenster zur Untersuchung offen und werden mit PID im Fehlerprotokoll genannt.

Die bestehenden Pfade bleiben erhalten:

```text
/tmp/konsole-quake.pid
~/.local/state/konsole-quake-session.json
~/.local/state/konsole-quake-session.json.bak
```

Der Helfer benötigt `python3-dbus`, der Launcher zusätzlich `flock` aus util-linux. Explizite Pfade für unabhängige Tests werden vor dem Unterbefehl angegeben:

```bash
python3 konsole-quake-session.py --pid-file /path/to/owned.pid --state-file /path/to/test.json save
python3 konsole-quake-session.py --state-file /path/to/test.json launch -- --hide-menubar --hide-toolbars
```

`launch` gibt nur die neue PID auf stdout aus; Diagnosemeldungen gehen an stderr. Erst nach erfolgreicher Wiederherstellung veröffentlicht der Launcher diese PID und setzt die Quake-Fensterregeln. Eine beschädigte vorhandene Sicherung wird nicht stillschweigend durch das statische Startlayout ersetzt. Bei Bedarf kann eine vorhandene `.bak` separat als `--state-file` geprüft und gestartet werden; die Originaldatei muss dafür nicht überschrieben werden.

Für einen direkten Helfer-Start ohne vorhandene Sicherung unterstützt `launch` zusätzlich `--tabs-file`:

```bash
python3 konsole-quake-session.py launch --tabs-file "$HOME/.config/konsole-quake-tabs" -- --hide-menubar
```

Die UTF-8-Datei enthält pro Tab `profile: Profilname ;; workdir: /absoluter/pfad` und optional `title: Titelformat`. Leerzeilen und Kommentarzeilen mit `#` werden ignoriert; Befehle, unbekannte oder doppelte Felder werden abgelehnt. Der Helfer erzeugt genau die angegebenen Tabs über dieselbe geprüfte Wiederherstellung. Eine vorhandene Sicherung hat Vorrang; beschädigte Sicherungen oder defekte Symlinks führen weiterhin zu einem Fehler. Dieser Start schreibt weder eine Sicherung noch eine PID-Datei.

Die Service-Vorlage nutzt `RemainAfterExit` und `ExecStop` am `graphical-session.target`, nicht einen als Shutdown-Sicherung missverstandenen `ExecStart` beim Login. Logout-Sicherung bleibt best effort: Konsole kann bereits beendet oder sein Fenster inaktiv sein. Maßgeblich ist der letzte erfolgreiche Checkpoint bei einer aktiven Sitzung, nicht ein versprochener Save nach einem Absturz. Den laufenden User-Service nicht zum Testen neu starten, denn das würde `ExecStop` ausführen.

Isolierte Tests ohne Zugriff auf die laufende Desktop-Sitzung:

```bash
python3 -B tests/konsole-session-test.py
python3 -B tests/konsole-launcher-test.py
bash tests/run-konsole-session-integration.sh
```

Der Integrationstest erzeugt einen privaten D-Bus und virtuellen KWin-Compositor. Er prüft mehrere Tabs mit neun Panes, verschiedene Profile/Verzeichnisse, gemischte Splits, Titelformate, aktive Auswahl, alte Sicherungen und Fehlerfälle mit eigenen Wegwerf-Shells. Die native Gruppierung und Tests sind auf Konsole 26.04.0 geprüft; unbekannte oder abweichende Layouts werden nicht geraten.

## Helfer

- `app-launch.sh <process-name> <window-class> <start-command>` — Generischer App-Launcher: prüft ob der Prozess läuft und fokussiert ihn, andernfalls wird die App gestartet. Wird von `firefox.sh`, `edge.sh` etc. genutzt.
- `kwin-focus.sh <window-class>` — Bringt ein Fenster per KWin-Scripting (DBus) in den Vordergrund. Wird von `app-launch.sh` genutzt und funktioniert unter Wayland.

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
