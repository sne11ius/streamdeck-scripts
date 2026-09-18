#!/usr/bin/env python3
"""Validated Konsole layout persistence, without terminal input.

Global --pid-file/--state-file options precede save or launch. Launch owns a new
--separate process and prints only its PID after successful restoration. Save
briefly selects panes, restoring remembered selections even on failure. A hidden
window whose focus cannot be mapped safely is refused, never guessed.
"""
import argparse
from contextlib import contextmanager
import fcntl
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

PIDFILE = Path("/tmp/konsole-quake.pid")
STATE_FILE = Path.home() / ".local/state/konsole-quake-session.json"
MAX_BYTES = 1024 * 1024
AXES = ("left-right", "top-bottom")


class SessionError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise SessionError(message)


def identifier(value, zero=False):
    require((zero and str(value) == "0") or re.fullmatch(r"[1-9][0-9]{0,9}", str(value)) is not None,
            f"Noncanonical ID: {value!r}")
    result = int(value)
    require(result <= 2147483647, "ID exceeds D-Bus int32")
    return result


def leaves(node):
    if isinstance(node, int) or "cwd" in node:
        return [node]
    return [leaf for child in node["children"] for leaf in leaves(child)]


def branches(node):
    if isinstance(node, int) or "cwd" in node:
        return []
    return [node] + [branch for child in node["children"] for branch in branches(child)]


def parse_hierarchy(lines, allow_empty=False):
    require(isinstance(lines, (list, tuple)) and 1 <= len(lines) <= 128,
            "Missing or oversized window hierarchy")
    seen_views, seen_splits = set(), set()

    def parse(text, depth=0):
        require(depth <= 24 and bool(text), "Empty or overly deep hierarchy node")
        if text.isascii() and text.isdigit():
            view = identifier(text, zero=True)
            require(view not in seen_views, "Duplicate view ID")
            seen_views.add(view)
            require(len(seen_views) <= 128, "Too many panes")
            return view
        match = re.fullmatch(r"\((0|[1-9][0-9]*)\)(\[.*\]|\{.*\})", text)
        require(match is not None, f"Malformed hierarchy node: {text!r}")
        split_id = identifier(match[1], zero=True)
        require(split_id not in seen_splits, "Duplicate splitter ID")
        seen_splits.add(split_id)
        require(len(seen_splits) <= 512, "Too many splitters")
        block = match[2]
        parts, stack, start = [], [], 0
        content = block[1:-1]
        for index, char in enumerate(content):
            if char in "([{":
                stack.append(char)
            elif char in ")]}":
                require(bool(stack) and stack.pop() == {")": "(", "]": "[", "}": "{"}[char],
                        "Mismatched hierarchy brackets")
            elif char == "|" and not stack:
                parts.append(content[start:index])
                start = index + 1
        require(not stack, "Unclosed hierarchy brackets")
        if content:
            parts.append(content[start:])
        require(allow_empty or bool(parts), "Empty splitter in snapshot")
        return {"id": split_id, "type": AXES[block[0] == "{"],
                "children": [parse(part, depth + 1) for part in parts]}

    result = []
    for line in lines:
        require(isinstance(line, str) and len(line) <= 16384, "Invalid hierarchy string")
        node = parse(line)
        require(isinstance(node, dict), "Tab root must be a splitter")
        result.append(node)
    return result


def ratios(values, count):
    require(isinstance(values, list) and len(values) == count,
            "Split proportions must match children")
    require(all(type(n) in (int, float) and math.isfinite(n) and 0 < n <= 1e9 for n in values),
            f"Split proportions must be finite and positive: {values!r}")
    total = sum(values)
    return [100.0 * n / total for n in values]


def validate_state(value):
    """Validate the entire document; expand only the shipped unversioned schema."""
    def keys(obj, allowed, required):
        require(type(obj) is dict and required <= obj.keys() <= allowed, "Invalid state fields")

    def text(value, label, empty=False):
        require(isinstance(value, str) and (empty or bool(value)) and len(value) <= 16384
                and "\0" not in value and re.search(r"[\ud800-\udfff]", value) is None, f"Invalid {label}")
        return value

    keys(value, {"version", "tabs", "active_tab_index"}, {"tabs", "active_tab_index"})
    require("version" not in value or type(value["version"]) is int and value["version"] == 2,
            "Unsupported state version")
    legacy = "version" not in value
    tabs = value["tabs"]
    require(type(tabs) is list and 1 <= len(tabs) <= 32, "State must contain 1..32 tabs")
    active = value["active_tab_index"]
    require(type(active) is int and 0 <= active < len(tabs), "Invalid active tab index")
    count = 0

    def node(item, profile, depth=0, parent_axis=None):
        nonlocal count
        require(depth <= 24, "State is too deeply nested")
        require(type(item) is dict, "Invalid split node")
        if "cwd" in item:
            keys(item, {"cwd", "profile", "title_formats"}, {"cwd"})
            cwd = text(item["cwd"], "CWD")
            require(os.path.isabs(cwd), "CWD must be absolute")
            result = {"cwd": cwd, "profile": text(item.get("profile", profile), "profile")}
            if "title_formats" in item:
                formats = item["title_formats"]
                keys(formats, {"local", "remote"}, {"local", "remote"})
                result["title_formats"] = {k: text(v, "title format", empty=True)
                                           for k, v in formats.items()}
            count += 1
            require(count <= 128, "State contains too many panes")
            return result
        keys(item, {"type", "children", "ratios"}, {"type", "children"})
        axis = item["type"]
        require(axis in AXES, "Unknown split orientation")
        children = item["children"]
        require(type(children) is list and 2 <= len(children) <= 64,
                "Split must have 2..64 children; unary nodes are not a saved layout")
        require(axis != parent_axis, "Same-axis nested splits must be normalized")
        result = {"type": axis, "children": [node(c, profile, depth + 1, axis) for c in children]}
        if "ratios" in item:
            result["ratios"] = ratios(item["ratios"], len(children))
        return result

    result = {"version": 2, "tabs": [], "active_tab_index": active}
    for tab in tabs:
        keys(tab, {"title", "profile", "split", "active_path"}, {"split"})
        profile = text(tab["profile"], "tab profile") if "profile" in tab else None
        split = node(tab["split"], profile)
        path = tab.get("active_path")
        if path is None:
            require(legacy, "Versioned tab is missing active_path")
            path, current = [], split
            while "children" in current:
                path.append(0)
                current = current["children"][0]
        require(type(path) is list and len(path) <= 24, "Invalid active pane path")
        current = split
        for index in path:
            require(type(index) is int and "children" in current and
                    0 <= index < len(current["children"]), "Invalid active pane path")
            current = current["children"][index]
        require("cwd" in current, "Active pane path does not end at a pane")
        saved_tab = {"split": split, "active_path": list(path)}
        if "title" in tab:
            saved_tab["title"] = text(tab["title"], "legacy title", empty=True)
        result["tabs"].append(saved_tab)
    return result


def read_state(path, with_bytes=False):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, "State file exceeds 1 MiB")
    try:
        state = validate_state(json.loads(data, object_pairs_hook=unique,
                                          parse_constant=lambda v: require(False, f"Invalid JSON number: {v}")))
        return (state, data) if with_bytes else state
    except (ValueError, RecursionError) as error:
        raise SessionError(f"Invalid JSON state: {error}") from error


def read_tabs(path):
    """Read declarative startup tabs into the validated restoration schema."""
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    require(len(data) <= MAX_BYTES, "Tabs file exceeds 1 MiB")
    try:
        lines = data.decode("utf-8").splitlines()
    except UnicodeError as error:
        raise SessionError(f"Invalid UTF-8 tabs file: {error}") from error
    tabs = []
    for number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = {}
        for field in line.split(";;"):
            key, separator, value = field.partition(":")
            key = key.strip().lower()
            require(separator and key in {"title", "profile", "workdir"} and key not in fields,
                    f"Unsupported, malformed or duplicate tab field on line {number}: {key!r}")
            fields[key] = value.strip()
        require({"profile", "workdir"} <= fields.keys(),
                f"Tab on line {number} requires profile and workdir")
        pane = {"cwd": fields["workdir"], "profile": fields["profile"]}
        if "title" in fields:
            pane["title_formats"] = {"local": fields["title"], "remote": fields["title"]}
        tabs.append({"split": pane, "active_path": []})
        require(len(tabs) <= 32, "Tabs file contains more than 32 tabs")
    return validate_state({"version": 2, "tabs": tabs, "active_tab_index": 0})


@contextmanager
def state_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise SessionError("Another save/launch holds the state lock; retry later") from error
        yield
    finally:
        os.close(fd)


def atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write_snapshot(path, state):
    # Callers hold state_lock throughout collection and replacement.
    validated = validate_state(state)
    data = (json.dumps(validated, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode()
    require(len(data) <= MAX_BYTES, "Snapshot exceeds size limit")
    if path.exists():
        try:
            _, previous = read_state(path, with_bytes=True)
        except (SessionError, ValueError):
            print(f"Warning: invalid previous state; retaining existing {path}.bak", file=sys.stderr)
        else:
            atomic_write(Path(str(path) + ".bak"), previous)
    atomic_write(path, data)


def proc_identity(pid, executable=None, parent=None):
    path = Path(f"/proc/{identifier(pid)}")
    try:
        require(path.stat().st_uid == os.geteuid(), "Process belongs to another user")
        fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
        require(fields[0] != "Z", f"Process {pid} is a zombie")
        if executable:
            expected = shutil.which(executable)
            require(expected is not None and os.path.samefile(path / "exe", expected),
                    f"PID {pid} is not a {executable} executable")
        if parent:
            require(int(fields[1]) == parent, f"Session process {pid} is not a child of Konsole {parent}")
        return fields[19]
    except (OSError, IndexError, ValueError) as error:
        raise SessionError(f"Cannot validate process {pid}: {error}") from error


def wait_for(check, message, timeout=8.0):
    deadline, last = time.monotonic() + timeout, None
    while True:
        try:
            value = check()
            if value:
                return value
        except (SessionError, OSError) as error:
            last = error
        if time.monotonic() >= deadline:
            raise SessionError(f"{message}" + (f": {last}" if last else ""))
        time.sleep(0.025)


class Konsole:
    def __init__(self, pid, bus=None):
        import dbus
        self.dbus = dbus
        self.pid = identifier(pid)
        self.start = proc_identity(self.pid, executable="konsole")
        self.bus = bus if bus is not None else dbus.SessionBus()
        self.daemon = dbus.Interface(self.bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"),
                                     "org.freedesktop.DBus")
        self.service = f"org.kde.konsole-{self.pid}"
        try:
            self.owner = str(self.bus.get_name_owner(self.service))
            self.check_owner()
        except dbus.DBusException as error:
            raise SessionError(f"Konsole {self.pid} has no verified D-Bus owner: {error}") from error

    def check_owner(self):
        require(proc_identity(self.pid, executable="konsole") == self.start, "Konsole PID was reused")
        require(str(self.bus.get_name_owner(self.service)) == self.owner and
                int(self.daemon.GetConnectionUnixProcessID(self.owner, timeout=3)) == self.pid,
                "Konsole D-Bus owner/PID mismatch")

    def call(self, path, interface, method, *args):
        try:
            self.check_owner()
            obj = self.bus.get_object(self.owner, path, introspect=False)
            return obj.get_dbus_method(method, interface)(*args, timeout=5)
        except self.dbus.DBusException as error:
            raise SessionError(f"Konsole {self.pid} {method} failed: {error}") from error

    def win(self, method, *args):
        return self.call("/Windows/1", "org.kde.konsole.Window", method, *args)

    def session(self, sid, method, *args):
        return self.call(f"/Sessions/{identifier(sid)}", "org.kde.konsole.Session", method, *args)

    def pane(self, sid):
        pid = identifier(self.session(sid, "processId"))
        identity = (pid, proc_identity(pid, parent=self.pid), str(self.session(sid, "shellSessionId")))
        require(bool(identity[2]), "Missing shell session identity")
        try:
            cwd = os.readlink(f"/proc/{pid}/cwd")
            require(not cwd.endswith(" (deleted)") and os.path.isdir(cwd),
                    f"Session {sid} CWD no longer exists: {cwd}")
        except OSError as error:
            raise SessionError(f"Cannot read session {sid} CWD: {error}") from error
        result = {"cwd": cwd, "profile": str(self.session(sid, "profile")),
                  "title_formats": {key: str(self.session(sid, "tabTitleFormat", index))
                                    for index, key in enumerate(("local", "remote"))},
                  "identity": identity}
        require(identifier(self.session(sid, "processId")) == pid and
                proc_identity(pid, parent=self.pid) == identity[1], "Session process changed during read")
        return result


def current(konsole, sid):
    return int(konsole.win("currentSession")) == sid


def select_session(konsole, sid):
    konsole.win("setCurrentSession", sid)
    wait_for(lambda: current(konsole, sid), "Cannot restore selected session", timeout=1)


def snapshot(konsole):
    """Save runtime-only profiles using the persisted configured default as a
    recovery base. This deliberately loses unknown temporary overrides, never
    changes running profiles, and retains raw metadata for stability checks.
    """
    raw = list(map(str, konsole.win("viewHierarchy")))
    trees = parse_hierarchy(raw)
    require(len(trees) <= 32, "Too many tabs to save")
    sessions = list(map(identifier, konsole.win("sessionList")))
    require(len(sessions) == len(set(sessions)) == sum(len(leaves(t)) for t in trees)
            == int(konsole.win("sessionCount")), "Window session/view count mismatch")
    partitions, offset = [], 0
    for tree in trees:
        count = len(leaves(tree))
        partitions.append(set(sessions[offset:offset + count]))
        offset += count
    original = identifier(konsole.win("currentSession"))
    require(original in sessions, "Current session does not belong to /Windows/1")
    active_tab = next(i for i, part in enumerate(partitions) if original in part)
    remembered, mapping, metadata, measured = {}, {}, {}, {}

    def stable():
        require(list(map(str, konsole.win("viewHierarchy"))) == raw and
                list(map(identifier, konsole.win("sessionList"))) == sessions and
                int(konsole.win("sessionCount")) == len(sessions), "Window mutated during snapshot")

    try:
        # sessionList is useful only for tab membership, never pane/layout order.
        for step in range(len(trees)):
            sid = identifier(konsole.win("currentSession"))
            tab = (active_tab + step) % len(trees)
            require(sid in partitions[tab] and tab not in remembered,
                    "Cannot map remembered tabs (window may be hidden/inactive)")
            remembered[tab] = sid
            konsole.win("nextSession")
        require(current(konsole, original), "Tab selection did not cycle back")
        stable()
        for index, tree in enumerate(trees):
            for view in leaves(tree):
                require(bool(konsole.win("setCurrentView", view)), f"Cannot select view {view}")
                sid = identifier(konsole.win("currentSession"))
                require(sid in partitions[index] and sid not in mapping.values(),
                        "Stale/duplicate view mapping; window may be hidden/inactive; snapshot refused")
                mapping[view] = sid
                metadata[sid] = konsole.pane(sid)
        # A second, reversed pass rejects stale focus results rather than guessing.
        for view, sid in reversed(list(mapping.items())):
            require(bool(konsole.win("setCurrentView", view)), f"Cannot reselect view {view}")
            require(current(konsole, sid), "Unstable view-to-session mapping; snapshot refused")
        stable()

        def saved_node(node):
            if isinstance(node, int):
                return {k: v for k, v in metadata[mapping[node]].items() if k != "identity"} | {"view": node}
            children = [saved_node(child) for child in node["children"]]
            if len(children) == 1:
                return children[0]
            measured[node["id"]] = list(map(float, konsole.win("getSplitProportions", node["id"])))
            sizes = ratios(measured[node["id"]], len(children))
            flat, weights = [], []
            for child, weight in zip(children, sizes):
                if child.get("type") == node["type"]:
                    flat.extend(child["children"])
                    weights.extend(weight * part / 100 for part in child["ratios"])
                else:
                    flat.append(child)
                    weights.append(weight)
            return {"type": node["type"], "children": flat, "ratios": weights}

        def remove_views(node, active, path=()):
            if "view" in node:
                return list(path) if mapping[node.pop("view")] == active else None
            found = [remove_views(child, active, (*path, index))
                     for index, child in enumerate(node["children"])]
            return next((p for p in found if p is not None), None)

        tabs = []
        for index, tree in enumerate(trees):
            node = saved_node(tree)
            path = remove_views(node, remembered[index])
            require(path is not None, "Remembered pane disappeared")
            tabs.append({"split": node, "active_path": path})
        state = validate_state({"version": 2, "tabs": tabs, "active_tab_index": active_tab})
        available = list(map(str, konsole.win("profileList")))
        runtime_only = {pane["profile"] for pane in metadata.values() if pane["profile"] not in available}
        recovery_base = None
        if runtime_only:
            recovery_base = str(konsole.win("defaultProfile"))
            require(available.count(recovery_base) == 1, "Recovery default profile is not uniquely available")
            for tab in state["tabs"]:
                for pane in leaves(tab["split"]):
                    if pane["profile"] in runtime_only:
                        pane["profile"] = recovery_base
            # Listed profiles missing on disk are deliberately not substituted.
            preflight(state, [])
        for sid, before in metadata.items():
            require(konsole.pane(sid) == before, f"Session {sid} changed during snapshot")
        for split_id, before in measured.items():
            require(list(map(float, konsole.win("getSplitProportions", split_id))) == before,
                    "Split proportions changed during snapshot")
        require(list(map(str, konsole.win("profileList"))) == available and
                (recovery_base is None or str(konsole.win("defaultProfile")) == recovery_base),
                "Available/default profiles changed during snapshot")
        stable()
        if runtime_only:
            print(f"Warning: runtime-only profiles {', '.join(sorted(runtime_only))!r} use configured default "
                  f"{recovery_base!r} as the saved recovery base; temporary overrides beyond CWD/title formats "
                  "are not restorable. Running profiles are unchanged.", file=sys.stderr)
        return state
    finally:
        errors = []
        for sid in [*remembered.values(), original]:
            try:
                select_session(konsole, sid)
            except Exception as error:
                errors.append(str(error))
        if errors:
            raise SessionError("Could not restore original selection: " + "; ".join(errors))


def integer_percentages(values):
    normalized = ratios(values, len(values))
    require(len(values) <= 64, "Too many split proportions")
    result = [max(1, math.floor(n)) for n in normalized]
    while sum(result) < 100:
        index = max(range(len(result)), key=lambda i: normalized[i] - result[i])
        result[index] += 1
    while sum(result) > 100:
        index = max((i for i in range(len(result)) if result[i] > 1),
                    key=lambda i: result[i] - normalized[i])
        result[index] -= 1
    return result


def preflight(state, appearance):
    flags = {"--hide-menubar", "--show-menubar", "--hide-tabbar", "--show-tabbar",
             "--hide-toolbars", "--show-toolbars", "--fullscreen"}
    index = 0
    while index < len(appearance):
        arg = appearance[index]
        if arg == "--stylesheet":
            index += 1
            require(index < len(appearance), f"Missing value for {arg}")
            # Konsole scans raw argv for --force-reuse before parsing values.
            require(bool(appearance[index]) and not appearance[index].startswith("-"),
                    f"Invalid appearance value for {arg}: {appearance[index]!r}")
        else:
            require(arg in flags, f"Not a supported appearance argument: {arg}")
        index += 1
    wanted = {pane["profile"] for tab in state["tabs"] for pane in leaves(tab["split"])}
    for tab in state["tabs"]:
        for pane in leaves(tab["split"]):
            # Konsole's format substitution has no literal-percent escape.
            if "title_formats" not in pane:
                require(re.search(r"%[BuUnDdwh#Hc]", tab.get("title", "")) is None,
                        "Cannot represent literal legacy title containing format markers; "
                        "supply explicit title_formats instead")
            cwd = pane["cwd"]
            require("$" not in cwd, f"CWD contains '$', which Konsole expands; refusing: {cwd}")
            require(not cwd.endswith(" (deleted)") and os.path.isdir(cwd) and os.access(cwd, os.X_OK),
                    f"CWD is missing or inaccessible: {cwd}")
    home = Path.home()
    roots = [Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share")))]
    roots += [Path(p) for p in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":") if p]
    profiles = {}
    for root in dict.fromkeys(roots):
        for path in sorted((root / "konsole").glob("*.profile")):
            try:
                result = subprocess.run(["kreadconfig6", "--file", str(path.absolute()),
                                         "--group", "General", "--key", "Name"],
                                        stdin=subprocess.DEVNULL, capture_output=True,
                                        encoding="utf-8", timeout=5)
                require(result.returncode == 0, f"kreadconfig6 failed for {path}: {result.stderr.strip()}")
                # Remove only the tool's terminator, not escaped Name whitespace.
                name = result.stdout.removesuffix("\n")
            except (subprocess.SubprocessError, UnicodeError, OSError) as error:
                raise SessionError(f"Cannot read profile {path}: {error}") from error
            if name in wanted:
                require(name not in profiles, f"Ambiguous profile name: {name}")
                profiles[name] = str(path.absolute())
    require(wanted <= profiles.keys(), "Unknown/missing profiles: " + ", ".join(sorted(wanted - profiles.keys()))
            + "; missing/ephemeral profile parents cannot be inferred safely from D-Bus")
    return profiles


def restore_owned(konsole, state):
    """Only launch calls this, with the verified Popen child, never a caller PID."""
    dbus = konsole.dbus

    def hierarchy(empty=False):
        return parse_hierarchy(list(map(str, konsole.win("viewHierarchy"))), allow_empty=empty)

    trees = hierarchy()
    require(len(trees) == 1 and len(leaves(trees[0])) == 1 and
            int(konsole.win("sessionCount")) == 1, "Owned startup window is not a single pane")
    available = list(map(str, konsole.win("profileList")))
    wanted = {p["profile"] for t in state["tabs"] for p in leaves(t["split"])}
    require(wanted <= set(available), "Konsole profileList is missing: " + ", ".join(sorted(wanted - set(available))))
    require(all(available.count(name) == 1 for name in wanted), "Ambiguous native profile names")
    records, targets, hosts = {}, [], []
    first = True

    def create(node, title):
        nonlocal first
        if "children" in node:
            return {**node, "children": [create(child, title) for child in node["children"]]}
        before = hierarchy()
        old_views = {v for tree in before for v in leaves(tree)}
        old_sids = set(map(identifier, konsole.win("sessionList")))
        if first:
            sid = identifier(konsole.win("currentSession"))
            view = leaves(before[0])[0]
            first = False
        else:
            sid = identifier(konsole.win("newSession", node["profile"], node["cwd"]))
            after = hierarchy()
            new_views = {v for tree in after for v in leaves(tree)}
            require(old_views < new_views and len(new_views - old_views) == 1 and
                    set(map(identifier, konsole.win("sessionList"))) == old_sids | {sid} and sid not in old_sids,
                    "newSession did not create exactly one identifiable pane")
            view = (new_views - old_views).pop()
        select_session(konsole, sid)
        pane = wait_for(lambda: konsole.pane(sid), f"Session {sid} did not start")
        require(pane["profile"] == node["profile"], f"Session {sid} profile fallback: {pane['profile']!r}")
        require(os.path.samefile(pane["cwd"], node["cwd"]),
                f"Session {sid} started in wrong CWD: {pane['cwd']} (wanted {node['cwd']})")
        # newSession/--profile applied the profile before any title formats.
        if "title_formats" in node:
            for index, key in enumerate(("local", "remote")):
                konsole.session(sid, "setTabTitleFormat", index, node["title_formats"][key])
        elif title:
            konsole.session(sid, "setTitle", 1, title)
        pane = konsole.pane(sid)
        if "title_formats" in node:
            require(pane["title_formats"] == node["title_formats"], "Title formats were not applied")
        records[view] = {"sid": sid, "pane": pane}
        return {**node, "view": view}

    for tab in state["tabs"]:
        target = create(tab["split"], tab.get("title", ""))
        targets.append(target)
        anchor = leaves(target)[0]["view"]
        hosts.append(next(t["id"] for t in hierarchy() if anchor in leaves(t)))

    def shape(node):
        if isinstance(node, int):
            return node
        if "view" in node:
            return node["view"]
        children = [shape(c) for c in node["children"]]
        if len(children) == 1:
            return children[0]
        return (node["type"], *children)

    def branch_for(target, trees):
        found = [b for t in trees for b in branches(t)
                 if len(b["children"]) >= 2 and shape(b) == shape(target)]
        require(len(found) == 1, "Cannot resolve regrouped branch uniquely")
        return found[0]

    def intact():
        trees = hierarchy()
        require({v for t in trees for v in leaves(t)} == records.keys() and
                set(map(identifier, konsole.win("sessionList"))) == {r["sid"] for r in records.values()},
                "Regrouping changed live session/view membership")
        return trees

    def group(target, host_id):
        if "view" in target:
            return
        for child in target["children"]:
            group(child, host_id)
        before = hierarchy()
        host = next(t for t in before if t["id"] == host_id)
        require(bool(leaves(host)), "Host tab unexpectedly empty")
        old_ids = {b["id"] for t in before for b in branches(t)}
        # Opposite-axis empty anchor keeps the host alive while its pane moves.
        # A same-axis empty split silently merges away, despite returning true.
        require(bool(konsole.win("createSplitWithExisting", host_id, dbus.Array([], signature="s"),
                                 0, host["type"] != "left-right")), "Cannot create temporary splitter")
        with_empty = hierarchy(empty=True)
        host = next(t for t in with_empty if t["id"] == host_id)
        added = [b for b in branches(host) if b["id"] not in old_ids]
        require(len(added) == 1 and not added[0]["children"] and added[0] in host["children"],
                "Temporary splitter was not created as expected")
        widgets = [f"v-{c['view']}" if "view" in c else f"s-{branch_for(c, with_empty)['id']}"
                   for c in target["children"]]
        require(bool(konsole.win("createSplitWithExisting", added[0]["id"], dbus.Array(widgets, signature="s"),
                                 0, target["type"] == "left-right")), "Regrouping was rejected")
        branch_for(target, intact())

    for target, host in zip(targets, hosts):
        group(target, host)
    final = intact()
    require([shape(t) for t in final] == [shape(t) for t in targets], "Restored tab layout/order mismatch")
    for target in targets:
        select_session(konsole, records[leaves(target)[0]["view"]]["sid"])
        for node in branches(target):
            branch = branch_for(node, hierarchy())
            percentages = integer_percentages(node.get("ratios", [1] * len(node["children"])))
            require(bool(konsole.win("resizeSplits", branch["id"],
                                     dbus.Array(percentages, signature="d"))), "Split resize rejected")
            actual = list(map(float, konsole.win("getSplitProportions", branch["id"])))
            ratios(actual, len(percentages))
            if max(abs(a - b) for a, b in zip(actual, percentages)) > 5:
                print(f"Warning: native minimum pane sizes constrained ratios {percentages} to {actual}",
                      file=sys.stderr)
    selected = []
    for tab, target in zip(state["tabs"], targets):
        node = target
        for index in tab["active_path"]:
            node = node["children"][index]
        sid = records[node["view"]]["sid"]
        select_session(konsole, sid)
        selected.append(sid)
    select_session(konsole, selected[state["active_tab_index"]])
    for record in records.values():
        require(konsole.pane(record["sid"]) == record["pane"], "Session identity/CWD/profile/formats changed")
    # Also proves focus mapping and per-tab active selection before publishing PID.
    saved = snapshot(konsole)
    require(saved["active_tab_index"] == state["active_tab_index"] and
            [t["active_path"] for t in saved["tabs"]] == [t["active_path"] for t in state["tabs"]],
            "Restored active selection mismatch")


def launch(state, appearance):
    profiles = preflight(state, appearance)
    first = leaves(state["tabs"][0]["split"])[0]
    env = {k: v for k, v in os.environ.items() if not k.startswith("KONSOLE_DBUS_") and
           k not in ("XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID", "SESSION_MANAGER")}
    child = subprocess.Popen(["konsole", "--separate", "--profile", profiles[first["profile"]],
                              "--workdir", first["cwd"], *appearance], env=env,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        def ready():
            require(child.poll() is None, f"Owned Konsole exited with status {child.returncode}")
            konsole = Konsole(child.pid)
            return konsole if int(konsole.win("sessionCount")) == 1 else None
        konsole = wait_for(ready, "Owned Konsole did not become ready", timeout=12)
        restore_owned(konsole, state)
        require(child.poll() is None, "Owned Konsole exited during restore")
        return child.pid
    except Exception as error:
        # Never close shells or kill a partially restored window. It is recoverable.
        raise SessionError(f"Launch failed; created Konsole PID {child.pid} left open: {error}") from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid-file", type=Path, default=PIDFILE)
    parser.add_argument("--state-file", type=Path, default=STATE_FILE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("save")
    commands.add_parser("restore").add_argument("pid")
    launcher = commands.add_parser("launch")
    launcher.add_argument("--tabs-file", type=Path,
                          help="Bootstrap from declarative tabs only when the state file is absent")
    launcher.add_argument("appearance", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    if args.command == "restore":
        print("Refusing restore <pid>: existing/foreign processes are never restore targets; use launch.",
              file=sys.stderr)
        return 1
    try:
        with state_lock(args.state_file):
            if args.command == "save":
                pid = identifier(args.pid_file.read_text().strip())
                state = snapshot(Konsole(pid))
                preflight(state, [])
                write_snapshot(args.state_file, state)
                print(f"Saved {len(state['tabs'])} tabs to {args.state_file}", file=sys.stderr)
            else:
                try:
                    state = read_state(args.state_file)
                except FileNotFoundError:
                    # A dangling symlink is an existing, broken snapshot, not a
                    # first launch. Never hide that error with bootstrap tabs.
                    if args.tabs_file is None or os.path.lexists(args.state_file):
                        raise
                    state = read_tabs(args.tabs_file)
                appearance = args.appearance
                if appearance[:1] == ["--"]:
                    appearance = appearance[1:]
                pid = launch(state, appearance)
                print(pid, flush=True)
        return 0
    except (SessionError, OSError, ValueError, ImportError) as error:
        print(f"Konsole session {args.command} failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
