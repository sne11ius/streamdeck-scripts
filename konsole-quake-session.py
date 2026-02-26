#!/usr/bin/env python3
"""
Konsole-Quake Session-Persistence: Save & Restore.

Speichert und stellt Konsole-Tabs, Splits (beliebig verschachtelt),
Verzeichnisse und Titel per D-Bus wieder her.

Usage:
    konsole-quake-session.py save
    konsole-quake-session.py restore <pid>

viewHierarchy Format:
    (tab_idx)[content]      — [] = left-right splitter (horizontal=true)
    (splitter_id){content}  — {} = top-bottom splitter (horizontal=false)
    view_ids separated by |
    Beispiel: (1)[1|(5){5|6}] = links View 1, rechts oben View 5, rechts unten View 6
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

PIDFILE = Path("/tmp/konsole-quake.pid")
STATE_FILE = Path.home() / ".local" / "state" / "konsole-quake-session.json"


def qdbus(service, path, method, *args, check=True):
    """Ruft qdbus6 auf und gibt stdout zurück."""
    cmd = ["qdbus6", service, path, method, *[str(a) for a in args]]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    if check and r.returncode != 0:
        raise RuntimeError(f"qdbus6 failed: {' '.join(cmd)}\n{r.stderr.strip()}")
    return r.stdout.strip()


def qdbus_literal(service, path, method, *args):
    """qdbus6 --literal für Array-Rückgaben."""
    cmd = ["qdbus6", "--literal", service, path, method, *[str(a) for a in args]]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    return r.stdout.strip()


# ─── SAVE ────────────────────────────────────────────────────────────────────


def get_cwd_for_session(service, sid):
    """Liest das aktuelle Verzeichnis einer Session über /proc."""
    try:
        shell_pid = qdbus(service, f"/Sessions/{sid}",
                          "org.kde.konsole.Session.processId")
        if shell_pid and os.path.isdir(f"/proc/{shell_pid}"):
            return os.readlink(f"/proc/{shell_pid}/cwd")
    except Exception:
        pass
    return str(Path.home())


def split_top_level(content):
    """Splittet content an | auf oberster Klammerebene."""
    parts, depth, current = [], 0, ""
    for ch in content:
        if ch in "([{":
            depth += 1
            current += ch
        elif ch in ")]}":
            depth -= 1
            current += ch
        elif ch == "|" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    if current:
        parts.append(current)
    return parts


def parse_node(node_str, session_ids, view_counter):
    """
    Parst einen Knoten der viewHierarchy rekursiv.
    Gibt (json_node, neuer_view_counter) zurück.
    """
    # Fall 1: einfache View-ID
    if re.fullmatch(r"\d+", node_str):
        sid = session_ids[view_counter]
        return {"sid": sid}, view_counter + 1

    # Fall 2: (id)[content] = left-right
    m = re.fullmatch(r"\((\d+)\)\[(.+)\]", node_str)
    if m:
        return parse_split_content(m.group(2), "left-right", session_ids, view_counter)

    # Fall 3: (id){content} = top-bottom
    m = re.fullmatch(r"\((\d+)\)\{(.+)\}", node_str)
    if m:
        return parse_split_content(m.group(2), "top-bottom", session_ids, view_counter)

    # Fallback
    sid = session_ids[view_counter]
    return {"sid": sid}, view_counter + 1


def parse_split_content(content, split_type, session_ids, view_counter):
    """Parst den Inhalt eines Splitter-Blocks."""
    parts = split_top_level(content)
    if len(parts) == 1:
        return parse_node(parts[0], session_ids, view_counter)

    children = []
    for part in parts:
        child, view_counter = parse_node(part, session_ids, view_counter)
        children.append(child)
    return {"type": split_type, "children": children}, view_counter


def enrich_tree(node, service):
    """Ersetzt sid-Referenzen durch cwd im Baum."""
    if "sid" in node:
        return {"cwd": get_cwd_for_session(service, node["sid"])}
    enriched_children = [enrich_tree(c, service) for c in node["children"]]
    return {"type": node["type"], "children": enriched_children}


def count_views_in_node(node):
    """Zählt die Anzahl der Leaf-Views in einem Knoten."""
    if "sid" in node or "cwd" in node:
        return 1
    return sum(count_views_in_node(c) for c in node["children"])


def do_save():
    if not PIDFILE.exists():
        print("Konsole-Quake läuft nicht (kein PID-File).", file=sys.stderr)
        sys.exit(1)

    pid = PIDFILE.read_text().strip()
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        print(f"Konsole-Quake PID {pid} läuft nicht mehr.", file=sys.stderr)
        sys.exit(1)

    service = f"org.kde.konsole-{pid}"
    window = "/Windows/1"

    try:
        qdbus(service, "/", "org.freedesktop.DBus.Peer.Ping")
    except RuntimeError:
        print(f"D-Bus-Service {service} nicht erreichbar.", file=sys.stderr)
        sys.exit(1)

    hierarchy_raw = qdbus(service, window, "org.kde.konsole.Window.viewHierarchy")
    hierarchy = [line for line in hierarchy_raw.splitlines() if line.strip()]

    session_ids_raw = qdbus(service, window, "org.kde.konsole.Window.sessionList")
    session_ids = [s.strip() for s in session_ids_raw.splitlines() if s.strip()]

    active_session = qdbus(service, window, "org.kde.konsole.Window.currentSession")

    tabs = []
    view_counter = 0

    for line in hierarchy:
        m = re.fullmatch(r"\((\d+)\)\[(.+)\]", line)
        if not m:
            continue

        tab_content = m.group(2)
        first_sid = session_ids[view_counter]

        # Titel und Profil der ersten Session
        try:
            title = qdbus(service, f"/Sessions/{first_sid}",
                          "org.kde.konsole.Session.title", "1", check=False)
        except Exception:
            title = ""
        try:
            profile = qdbus(service, f"/Sessions/{first_sid}",
                            "org.kde.konsole.Session.profile", check=False)
        except Exception:
            profile = ""
        if not profile:
            try:
                profile = qdbus(service, window,
                                "org.kde.konsole.Window.defaultProfile", check=False)
            except Exception:
                profile = ""

        # Baum parsen
        split_node, view_counter = parse_split_content(
            tab_content, "left-right", session_ids, view_counter
        )

        # sid → cwd auflösen
        split_enriched = enrich_tree(split_node, service)

        tabs.append({
            "title": title,
            "profile": profile,
            "split": split_enriched,
        })

    # Aktiven Tab-Index ermitteln
    active_tab_index = 0
    vc = 0
    for i, line in enumerate(hierarchy):
        m = re.fullmatch(r"\((\d+)\)\[(.+)\]", line)
        if not m:
            continue
        # View-IDs zählen (Splitter-IDs in () rausfiltern)
        clean = re.sub(r"\(\d+\)", "", m.group(2))
        clean = re.sub(r"[{}\[\]]", "", clean)
        view_ids = re.findall(r"\d+", clean)
        for vi in range(len(view_ids)):
            if vc + vi < len(session_ids) and session_ids[vc + vi] == active_session:
                active_tab_index = i
        vc += len(view_ids)

    state = {"tabs": tabs, "active_tab_index": active_tab_index}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))

    print(f"Konsole-Quake State gespeichert: {STATE_FILE} ({len(tabs)} Tabs)")


# ─── RESTORE ─────────────────────────────────────────────────────────────────


def wait_for_dbus(service, timeout=5.0):
    """Wartet bis der D-Bus-Service erreichbar ist."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            qdbus(service, "/", "org.freedesktop.DBus.Peer.Ping")
            return True
        except Exception:
            time.sleep(0.1)
    return False


def get_session_set(service, window):
    raw = qdbus(service, window, "org.kde.konsole.Window.sessionList", check=False)
    return set(s.strip() for s in raw.splitlines() if s.strip())


def get_view_set(service, window):
    raw = qdbus(service, window, "org.kde.konsole.Window.viewHierarchy", check=False)
    clean = re.sub(r"\(\d+\)", "", raw)
    clean = re.sub(r"[{}\[\]\n]", " ", clean)
    return set(re.findall(r"\d+", clean))


def get_first_cwd(node):
    """Erstes cwd im Baum (rekursiv)."""
    if "cwd" in node:
        return node["cwd"]
    return get_first_cwd(node["children"][0])


def restore_node(node, current_view, current_sid, tab_title, service, window):
    """Rekursive Split-Wiederherstellung. Keine bash-Scoping-Probleme mehr."""
    if "cwd" in node:
        # Leaf: cwd setzen
        cwd = node["cwd"]
        if not os.path.isdir(cwd):
            cwd = str(Path.home())
        try:
            qdbus(service, f"/Sessions/{current_sid}",
                  "org.kde.konsole.Session.runCommand",
                  f"cd {cwd} && clear", check=False)
        except Exception:
            pass
        if tab_title:
            try:
                qdbus(service, f"/Sessions/{current_sid}",
                      "org.kde.konsole.Session.setTitle", "1", tab_title, check=False)
            except Exception:
                pass
        return

    # Split-Knoten
    children = node["children"]
    horizontal = "true" if node["type"] == "left-right" else "false"

    # ERST alle Splits erstellen, Views+Sessions sammeln
    child_infos = [(current_view, current_sid)]  # Kind 0 = aktueller Pane

    for _ in range(1, len(children)):
        before_sids = get_session_set(service, window)
        before_views = get_view_set(service, window)

        try:
            qdbus(service, window,
                  "org.kde.konsole.Window.createSplit",
                  current_view, horizontal, check=False)
        except Exception:
            pass
        time.sleep(0.3)

        after_sids = get_session_set(service, window)
        after_views = get_view_set(service, window)

        new_sids = after_sids - before_sids
        new_views = after_views - before_views

        new_sid = new_sids.pop() if new_sids else ""
        new_view = new_views.pop() if new_views else ""

        child_infos.append((new_view, new_sid))

    # DANN rekursiv in alle Kinder — Python hat echtes Scoping, kein Clobbering
    for i, child in enumerate(children):
        view_id, sid = child_infos[i]
        if view_id and sid:
            restore_node(child, view_id, sid, tab_title, service, window)


def do_restore(konsole_pid):
    service = f"org.kde.konsole-{konsole_pid}"
    window = "/Windows/1"

    if not wait_for_dbus(service):
        print(f"D-Bus-Service {service} nicht erreichbar.", file=sys.stderr)
        sys.exit(1)

    # Warten bis Window existiert
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            qdbus(service, window, "org.kde.konsole.Window.sessionCount")
            break
        except Exception:
            time.sleep(0.1)

    if not STATE_FILE.exists():
        print(f"Kein gespeicherter State ({STATE_FILE}).", file=sys.stderr)
        sys.exit(0)

    state = json.loads(STATE_FILE.read_text())
    tabs = state.get("tabs", [])
    if not tabs:
        print("State enthält keine Tabs.", file=sys.stderr)
        sys.exit(0)

    # Initiale Session merken
    initial_session = qdbus(service, window,
                            "org.kde.konsole.Window.currentSession", check=False)

    created_sessions = []

    for tab in tabs:
        title = tab.get("title", "")
        profile = tab.get("profile", "")
        split = tab["split"]

        first_cwd = get_first_cwd(split)
        if not os.path.isdir(first_cwd):
            first_cwd = str(Path.home())

        # Tab erstellen
        new_sid = qdbus(service, window,
                        "org.kde.konsole.Window.newSession", profile, first_cwd)
        new_sid = new_sid.strip()

        # Titel setzen
        if title:
            for method in ["setTitle", "setTabTitleFormat"]:
                args = ["1", title] if method == "setTitle" else []
                if method == "setTabTitleFormat":
                    for fmt in ["0", "1"]:
                        try:
                            qdbus(service, f"/Sessions/{new_sid}",
                                  f"org.kde.konsole.Session.{method}",
                                  fmt, title, check=False)
                        except Exception:
                            pass
                else:
                    try:
                        qdbus(service, f"/Sessions/{new_sid}",
                              f"org.kde.konsole.Session.{method}",
                              *args, check=False)
                    except Exception:
                        pass

        created_sessions.append(new_sid)

        # Splits wiederherstellen
        if "type" in split:
            try:
                qdbus(service, window,
                      "org.kde.konsole.Window.setCurrentSession", new_sid, check=False)
            except Exception:
                pass
            time.sleep(0.3)

            # View-ID des neuen Tabs finden
            hier = qdbus(service, window,
                         "org.kde.konsole.Window.viewHierarchy", check=False)
            last_line = hier.strip().splitlines()[-1]
            clean = re.sub(r"\(\d+\)", "", last_line)
            clean = re.sub(r"[{}\[\]]", "", clean)
            view_ids = re.findall(r"\d+", clean)
            new_view = view_ids[0] if view_ids else "0"

            restore_node(split, new_view, new_sid, title, service, window)

    # Initiale Default-Session schließen
    if initial_session and created_sessions:
        try:
            qdbus(service, window,
                  "org.kde.konsole.Window.setCurrentSession",
                  created_sessions[0], check=False)
        except Exception:
            pass
        time.sleep(0.5)
        try:
            qdbus(service, f"/Sessions/{initial_session}",
                  "org.kde.konsole.Session.sendText", "exit\n", check=False)
        except Exception:
            pass
        time.sleep(0.5)

    # Aktiven Tab wiederherstellen
    active_idx = state.get("active_tab_index", 0)
    if 0 <= active_idx < len(created_sessions):
        try:
            qdbus(service, window,
                  "org.kde.konsole.Window.setCurrentSession",
                  created_sessions[active_idx], check=False)
        except Exception:
            pass

    print(f"Konsole-Quake State wiederhergestellt: {len(tabs)} Tabs")


# ─── MAIN ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} save | restore <pid>", file=sys.stderr)
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "save":
        do_save()
    elif cmd == "restore":
        if len(sys.argv) < 3:
            print(f"Usage: {sys.argv[0]} restore <pid>", file=sys.stderr)
            sys.exit(1)
        do_restore(sys.argv[2])
    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)
