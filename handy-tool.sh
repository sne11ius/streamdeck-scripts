#!/usr/bin/env bash
set -euo pipefail

TERMINALS="alacritty|konsole|xterm|kitty|foot|wezterm|gnome-terminal|tilix|terminator|yakuake|st-256color"

get_active_window_class() {
  local tmpscript marker script_id wclass
  tmpscript=$(mktemp /tmp/kwin_wclass_XXXX.js)
  marker="DOTOOL_WCLASS_$$"
  cat > "$tmpscript" << JSEOF
console.info("${marker}:" + workspace.activeWindow.resourceClass);
JSEOF
  script_id=$(qdbus6 org.kde.KWin /Scripting org.kde.kwin.Scripting.loadScript "$tmpscript" 2>/dev/null)
  qdbus6 org.kde.KWin "/Scripting/Script${script_id}" org.kde.kwin.Script.run 2>/dev/null
  sleep 0.1
  wclass=$(journalctl --user --since "3 seconds ago" --no-pager 2>/dev/null \
    | grep "$marker" | tail -1 | sed "s/.*${marker}://")
  qdbus6 org.kde.KWin "/Scripting/Script${script_id}" org.kde.kwin.Script.stop 2>/dev/null || true
  rm -f "$tmpscript"
  echo "$wclass"
}

is_terminal() {
  local wclass
  wclass=$(get_active_window_class)
  [[ "${wclass,,}" =~ ($TERMINALS) ]]
}

paste_text() {
  local text="$1"

  # Write text to temp file to avoid pipe keeping FDs open
  local tmpfile
  tmpfile=$(mktemp)
  printf '%s' "$text" > "$tmpfile"

  # Start wl-copy fully detached (new session, no inherited FDs)
  # wl-copy reads stdin fully before serving, so we wait briefly for it to consume the file
  setsid wl-copy --type 'text/plain;charset=utf-8' --trim-newline < "$tmpfile" >/dev/null 2>&1 &
  disown
  sleep 0.05
  rm -f "$tmpfile"

  # Paste: Ctrl+Shift+V for terminals, Ctrl+V for GUI apps
  if is_terminal; then
    ydotool key ctrl+shift+v
  else
    ydotool key ctrl+v
  fi
  sleep 0.02
  ydotool key enter
}

# Support both: arguments (handy) and stdin with "type " prefix (dotool)
if [[ $# -gt 0 ]]; then
  paste_text "$*"
else
  while IFS= read -r line; do
    if [[ "$line" =~ ^type[[:space:]]+(.*)$ ]]; then
      paste_text "${BASH_REMATCH[1]}"
    fi
  done
fi
