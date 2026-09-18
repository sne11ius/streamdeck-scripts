#!/usr/bin/python3
"""Command-free Konsole round trip, under private D-Bus and virtual KWin only."""
import importlib.util
import json
import os
from pathlib import Path
import resource
import re
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
HELPER = HERE.parent / "konsole-quake-session.py"
DIRECTORIES = {"HOME": "home", "XDG_CONFIG_HOME": "config", "XDG_CONFIG_DIRS": "config-dirs",
               "XDG_DATA_HOME": "data", "XDG_DATA_DIRS": "data-dirs", "XDG_STATE_HOME": "state",
               "XDG_CACHE_HOME": "cache", "XDG_RUNTIME_DIR": "runtime"}
PROFILE_NAMES = {"J": "Pr\u00fcfung", "K": " Leading\tTabbed\\Name "}


def child(root, helper_args=None):
    assert root.parent == Path("/tmp/opencode") and root.name.startswith("konsole-session-test.")
    assert not root.is_symlink() and (root / "guard").read_text() == "isolated-session-v1"
    for key, value in DIRECTORIES.items():
        assert os.environ[key] == str(root / value), key
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"].startswith(f"unix:path={root}/runtime/")
    assert os.environ["DBUS_SYSTEM_BUS_ADDRESS"] == f"unix:path={root}/runtime/no-system-bus"
    for key in ("DISPLAY", "QT_QPA_PLATFORMTHEME", "LD_PRELOAD", "SESSION_MANAGER",
                "KONSOLE_DBUS_SERVICE", "KONSOLE_DBUS_WINDOW", "KONSOLE_DBUS_SESSION"):
        assert key not in os.environ, key
    for key in ("XDG_ACTIVATION_TOKEN", "DESKTOP_STARTUP_ID"):
        os.environ.pop(key, None)
    os.environ.update(TMPDIR=str(root / "tmp"), WAYLAND_DISPLAY="konsole-session-test",
                      QT_QPA_PLATFORM="wayland")
    assert (root / "runtime/konsole-session-test").is_socket()
    import dbus
    bus = dbus.bus.BusConnection(os.environ["DBUS_SESSION_BUS_ADDRESS"])
    spec = importlib.util.spec_from_file_location("session_helper", HELPER)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    # Runtime guard on every helper D-Bus call, not a source-text assertion.
    safe_win = {"viewHierarchy", "sessionList", "sessionCount", "currentSession", "nextSession",
                "setCurrentSession", "setCurrentView", "newSession", "profileList",
                "createSplitWithExisting", "getSplitProportions", "resizeSplits", "defaultProfile"}
    safe_session = {"processId", "shellSessionId", "profile", "tabTitleFormat",
                    "setTabTitleFormat", "setTitle"}
    original_call = helper.Konsole.call

    def guarded_call(self, path, interface, method, *args):
        assert method in (safe_win if path == "/Windows/1" else safe_session), method
        return original_call(self, path, interface, method, *args)

    helper.Konsole.call = guarded_call
    if helper_args is not None:
        return helper.main(helper_args)
    assert not [n for n in bus.list_names() if n.startswith("org.kde.konsole")]

    def leaf(label):
        return {"cwd": str(root / "cwd" / f"pane {label}; literal & space"),
                "profile": PROFILE_NAMES.get(label, f"Session {label}"),
                "title_formats": {"local": f"{label} %d : %n", "remote": f"{label} %u@%h"}}

    def group(axis, *children, ratios=None):
        result = {"type": axis, "children": list(children)}
        if ratios:
            result["ratios"] = ratios
        return result

    sample = {"version": 2, "active_tab_index": 1, "tabs": [
        {"split": group("top-bottom", leaf("A"),
                        group("left-right", leaf("B"), leaf("C"), leaf("D"), ratios=[30, 40, 30]),
                        leaf("E"), ratios=[30, 40, 30]), "active_path": [1, 2]},
        {"split": group("left-right", leaf("F"),
                        group("top-bottom", leaf("G"), leaf("H")), ratios=[45, 55]),
         "active_path": [1, 0]},
        {"split": leaf("I"), "active_path": []},
        {"split": group("left-right", leaf("J"), leaf("K")), "active_path": [1]}]}
    source = root / "state/input.json"
    output = root / "state/output.json"
    pidfile = root / "state/owned.pid"
    bootstrap_tabs = root / "state/bootstrap.tabs"
    bootstrap_tabs.write_text("".join(f"title: {title} ;; workdir: {leaf(label)['cwd']} ;; profile: Session A\n"
                                      for title, label in (("codehaufen", "A"), ("gradlew", "B"), ("src", "A"))))
    source.write_text(json.dumps(sample))
    owned = []
    try:
        # This sentinel is disposable but deliberately uses the native singleton
        # route. A raw --force-reuse hidden as an option value must never reach it.
        sentinel = subprocess.Popen(["konsole", "--profile", str(root / "data/konsole/A.profile"),
                                     "--workdir", str(root / "home")],
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        sentinel_fd = os.pidfd_open(sentinel.pid)
        owned.append((sentinel.pid, sentinel_fd))
        daemon = dbus.Interface(bus.get_object("org.freedesktop.DBus", "/org/freedesktop/DBus"),
                                "org.freedesktop.DBus")

        def sentinel_windows():
            assert sentinel.poll() is None
            try:
                owner = str(bus.get_name_owner("org.kde.konsole"))
                assert int(daemon.GetConnectionUnixProcessID(owner)) == sentinel.pid
                windows = dbus.Interface(bus.get_object(owner, "/Windows", introspect=False),
                                         "org.freedesktop.DBus.Introspectable").Introspect()
                return {node.attrib["name"]: int(dbus.Interface(
                    bus.get_object(owner, "/Windows/" + node.attrib["name"], introspect=False),
                    "org.kde.konsole.Window").sessionCount()) for node in ET.fromstring(str(windows)).findall("node")}
            except dbus.DBusException as error:
                raise helper.SessionError(f"{error}; private bus names: {bus.list_names()}") from error

        sentinel_before = helper.wait_for(sentinel_windows, "Disposable singleton not ready")
        assert sentinel_before == {"1": 1}, sentinel_before
        shadow_directory = root / "data-dirs/konsole"
        shadow_directory.mkdir()
        shadow = shadow_directory / "A.profile"
        shadow.write_text("[General]\nName=Session A\nCommand=/bin/sh -i\nParent=FALLBACK/\n")
        result = subprocess.run([sys.executable, str(HELPER), "--state-file", str(source), "launch"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode != 0 and not result.stdout.strip() and "Ambiguous profile" in result.stderr, result
        assert sentinel_windows() == sentinel_before
        shadow.unlink()
        print("PASS cross-XDG duplicate profile names refused before spawn")
        for option in ("--title", "--stylesheet"):
            result = subprocess.run([sys.executable, str(HELPER), "--state-file", str(source),
                                     "launch", "--", option, "--force-reuse"],
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode != 0 and not result.stdout.strip()
            assert sentinel_windows() == sentinel_before, (option, result.stderr, sentinel_windows())
            assert "appearance" in result.stderr, result.stderr
        signal.pidfd_send_signal(sentinel_fd, signal.SIGTERM)
        sentinel.wait(timeout=5)
        os.close(sentinel_fd)
        owned.pop()
        print("PASS raw appearance options cannot mutate existing UseSingleInstance=true window")
        # Exercise the actual command-substitution stdout contract.
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--helper", str(root),
                                 "--state-file", str(source),
                                 "--pid-file", str(pidfile), "launch", "--tabs-file", str(root / "state/nonexistent.tabs"),
                                 "--", "--hide-menubar",
                                 "--hide-toolbars", "--fullscreen"],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        print("LAUNCH", result.returncode, repr(result.stdout), result.stderr, flush=True)
        if result.returncode:
            match = re.search(r"created Konsole PID ([0-9]+)", result.stderr)
            if match:
                failed_pid = int(match[1])
                failed = helper.Konsole(failed_pid, bus=bus)
                owned.append((failed_pid, os.pidfd_open(failed_pid)))
                hierarchy = helper.parse_hierarchy(list(map(str, failed.win("viewHierarchy"))))
                print("FAILED HIERARCHY", hierarchy)
                for tree in hierarchy:
                    for branch in helper.branches(tree):
                        print("FAILED RATIOS", branch["id"], failed.win("getSplitProportions", branch["id"]))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip().isdigit()
        pid = int(result.stdout.strip())
        owned.append((pid, os.pidfd_open(pid)))
        assert not pidfile.exists(), "helper must not publish PID"
        pidfile.write_text(str(pid))
        konsole = helper.Konsole(pid, bus=bus)
        before = {int(s): konsole.pane(int(s))["identity"] for s in konsole.win("sessionList")}
        # Use real CLI save; also exercise guarded native calls in-process.
        saved = helper.snapshot(konsole)
        result = subprocess.run([sys.executable, str(HELPER), "--state-file", str(output),
                                 "--pid-file", str(pidfile), "save"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        saved = json.loads(output.read_text())

        def compare(want, actual):
            if "cwd" in want:
                for key in ("cwd", "profile", "title_formats"):
                    assert actual[key] == want[key], (key, want, actual)
                return
            assert actual["type"] == want["type"], (want, actual)
            assert len(actual["children"]) == len(want["children"])
            if "ratios" in want:
                assert max(abs(a - b) for a, b in zip(want["ratios"], actual["ratios"])) <= 5, (want, actual)
            for a, b in zip(want["children"], actual["children"]):
                compare(a, b)

        assert saved["active_tab_index"] == 1
        assert len(saved["tabs"]) == 4
        for want, actual in zip(sample["tabs"], saved["tabs"]):
            assert actual["active_path"] == want["active_path"], (want, actual)
            compare(want["split"], actual["split"])
        assert before == {int(s): konsole.pane(int(s))["identity"] for s in konsole.win("sessionList")}
        print("PASS nested H/V, >2 siblings, localized/escaped profiles, CWDs, formats, selection, ratios, shell identity")
        preserved = output.read_bytes()
        original = konsole.win
        calls = 0

        def mutation(method, *args):
            nonlocal calls
            if method == "viewHierarchy":
                calls += 1
                if calls > 1:
                    return ["(1)[999999]"]
            return original(method, *args)

        konsole.win = mutation
        try:
            helper.write_snapshot(output, helper.snapshot(konsole))
        except helper.SessionError:
            pass
        else:
            raise AssertionError("mutation accepted")
        assert output.read_bytes() == preserved
        print("PASS forced mutation leaves saved snapshot untouched")
        names = set(bus.list_names())
        source.write_text('{"tabs": []}')
        result = subprocess.run([sys.executable, str(HELPER), "--state-file", str(source), "launch",
                                 "--tabs-file", str(bootstrap_tabs)],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and not result.stdout.strip()
        assert {n for n in bus.list_names() if n.startswith("org.kde.konsole")} == {
            n for n in names if n.startswith("org.kde.konsole")}
        print("PASS malformed state refuses before spawn")
        legacy = {"tabs": [{"title": "Legacy literal ; &", "profile": "Session A",
                             "split": {"type": "top-bottom", "children": [
                                 {"cwd": leaf("A")["cwd"]}, {"cwd": leaf("B")["cwd"]}]}}],
                  "active_tab_index": 0}
        source.write_text(json.dumps(legacy))
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--helper", str(root),
                                 "--state-file", str(source), "launch", "--", "--fullscreen"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        legacy_pid = int(result.stdout.strip())
        owned.append((legacy_pid, os.pidfd_open(legacy_pid)))
        restored = helper.snapshot(helper.Konsole(legacy_pid, bus=bus))
        panes = restored["tabs"][0]["split"]["children"]
        assert restored["tabs"][0]["split"]["type"] == "top-bottom"
        assert [p["cwd"] for p in panes] == [leaf("A")["cwd"], leaf("B")["cwd"]]
        assert all(p["profile"] == "Session A" for p in panes)
        assert all(p["title_formats"] == {"local": "Legacy literal ; &", "remote": "Legacy literal ; &"}
                   for p in panes), panes
        print("PASS unversioned legacy titles/profile/CWD and default split proportions")
        konsole.win = original
        try:
            inactive = helper.snapshot(konsole)
        except helper.SessionError as error:
            print("PASS inactive-window save safely refused:", error)
        else:
            for want, actual in zip(sample["tabs"], inactive["tabs"]):
                compare(want["split"], actual["split"])
            assert inactive["active_tab_index"] == 1
            assert [t["active_path"] for t in inactive["tabs"]] == [t["active_path"] for t in sample["tabs"]]
            print("PASS inactive-window mapping verified without fallback")
        assert output.read_bytes() == preserved
        real_win = helper.Konsole.win

        def reject_group(self, method, *args):
            if method == "createSplitWithExisting":
                return False
            return real_win(self, method, *args)

        helper.Konsole.win = reject_group
        try:
            helper.launch(helper.validate_state(legacy), ["--fullscreen"])
        except helper.SessionError as error:
            match = re.search(r"created Konsole PID ([0-9]+)", str(error))
            assert match and "temporary splitter" in str(error), error
            failed_pid = int(match[1])
            failed = helper.Konsole(failed_pid, bus=bus)
            owned.append((failed_pid, os.pidfd_open(failed_pid)))
            assert int(failed.win("sessionCount")) == 2
        else:
            raise AssertionError("false D-Bus grouping result accepted")
        finally:
            helper.Konsole.win = real_win
        assert output.read_bytes() == preserved
        assert json.loads(source.read_text()) == legacy
        print("PASS false D-Bus result fails with created PID, leaves owned sessions open, preserves state")
        bootstrap_state = root / "state/absent-bootstrap.json"
        bootstrap_pidfile = root / "state/bootstrap.pid"
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--helper", str(root),
                                 "--state-file", str(bootstrap_state), "--pid-file", str(bootstrap_pidfile),
                                 "launch", "--tabs-file", str(bootstrap_tabs), "--", "--hide-menubar", "--fullscreen"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and result.stdout.strip().isdigit(), result
        assert "runtime-only" not in result.stderr
        bootstrap_pid = int(result.stdout.strip())
        owned.append((bootstrap_pid, os.pidfd_open(bootstrap_pid)))
        assert not bootstrap_state.exists() and not bootstrap_pidfile.exists()
        bootstrap_pidfile.write_text(str(bootstrap_pid))
        bootstrap_saved = root / "state/bootstrap-saved.json"
        result = subprocess.run([sys.executable, str(HELPER), "--state-file", str(bootstrap_saved),
                                 "--pid-file", str(bootstrap_pidfile), "save"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and "runtime-only" not in result.stderr, result
        restored = json.loads(bootstrap_saved.read_text())
        assert len(restored["tabs"]) == 3 and restored["active_tab_index"] == 0
        for tab, (title, label) in zip(restored["tabs"], (("codehaufen", "A"), ("gradlew", "B"), ("src", "A"))):
            assert tab == {"active_path": [], "split": {"cwd": leaf(label)["cwd"], "profile": "Session A",
                           "title_formats": {"local": title, "remote": title}}}, tab
        bootstrap_window = helper.Konsole(bootstrap_pid, bus=bus)
        assert int(bootstrap_window.win("sessionCount")) == 3
        assert all(bootstrap_window.session(int(s), "profile") == "Session A" for s in bootstrap_window.win("sessionList"))
        assert not bootstrap_state.exists()
        print("PASS absent-state bootstrap creates exactly three declared persistent-profile tabs, no state write or recovery warning")
        # Different real base profiles can produce identical hidden profile names
        # and title formats. D-Bus provides no parent/effective-profile getter.
        tabfile = root / "state/ephemeral.tabs"
        tabfile.write_text("".join(f"title: Same title;; profile: {label};; workdir: {root / 'home'}\n"
                                   for label in "AB"))
        ephemeral = subprocess.Popen(["konsole", "--separate", "--fullscreen", "--tabs-from-file", str(tabfile),
                                      "--profile", str(root / "data/konsole/A.profile"),
                                      "--workdir", str(root / "home")],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        owned.append((ephemeral.pid, os.pidfd_open(ephemeral.pid)))

        def ephemeral_ready():
            assert ephemeral.poll() is None
            window = helper.Konsole(ephemeral.pid, bus=bus)
            return window if int(window.win("sessionCount")) == 3 else None

        window = helper.wait_for(ephemeral_ready, "Disposable tabs-from-file not ready")
        original_profiles = {int(s): window.pane(int(s)) for s in window.win("sessionList")}
        original_default = str(window.win("defaultProfile"))
        assert original_default == "Session B"
        candidate = helper.wait_for(lambda: helper.snapshot(window), "Disposable tabs-from-file not selectable")
        generated = [tab["split"] for tab in candidate["tabs"][:2]]
        assert generated[0] == generated[1], generated
        assert original_profiles[1]["profile"] not in set(map(str, window.win("profileList")))
        assert generated[0]["profile"] == original_default
        assert generated[0]["title_formats"] == {"local": "Same title", "remote": "Same title"}
        ephemeral_pidfile = root / "state/ephemeral.pid"
        ephemeral_pidfile.write_text(str(ephemeral.pid))
        result = subprocess.run([sys.executable, str(HELPER), "--pid-file", str(ephemeral_pidfile),
                                 "--state-file", str(output), "save"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and "runtime-only" in result.stderr, result
        recovered = json.loads(output.read_text())
        assert [tab["split"]["profile"] for tab in recovered["tabs"]] == ["Session B", "Session B", "Session A"]
        assert all(tab["split"]["cwd"] == str(root / "home") for tab in recovered["tabs"])
        assert all(tab["split"]["title_formats"] == {"local": "Same title", "remote": "Same title"}
                   for tab in recovered["tabs"][:2])
        assert Path(str(output) + ".bak").read_bytes() == preserved
        assert original_profiles == {int(s): window.pane(int(s)) for s in window.win("sessionList")}
        assert str(window.win("defaultProfile")) == original_default
        assert int(window.win("sessionCount")) == 3
        print("PASS runtime-only profiles saved with configured recovery base, CWD/title formats and running profiles preserved")
        saved_recovery = output.read_bytes()
        # A still-listed persistent profile must never be mistaken for a hidden
        # runtime profile just because its file was removed after startup.
        (root / "data/konsole/A.profile").unlink()
        assert "Session A" in set(map(str, window.win("profileList")))
        result = subprocess.run([sys.executable, str(HELPER), "--pid-file", str(ephemeral_pidfile),
                                 "--state-file", str(output), "save"],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode != 0 and "Unknown/missing profiles: Session A" in result.stderr, result
        assert output.read_bytes() == saved_recovery
        assert Path(str(output) + ".bak").read_bytes() == preserved
        print("PASS listed persistent profile with missing file is not substituted")
        (root / "complete").write_text("PASS")
    finally:
        for pid, fd in owned:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            os.close(fd)


def main():
    if len(sys.argv) >= 3 and sys.argv[1] in ("--child", "--helper"):
        try:
            return child(Path(sys.argv[2]), sys.argv[3:] if sys.argv[1] == "--helper" else None) or 0
        except BaseException:
            traceback.print_exc()
            return 1
    assert len(sys.argv) == 1
    assert Path("/tmp/opencode").is_dir() and not Path("/tmp/opencode").is_symlink()
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    root = Path(tempfile.mkdtemp(prefix="konsole-session-test.", dir="/tmp/opencode"))
    for name in (*DIRECTORIES.values(), "tmp", "cwd"):
        (root / name).mkdir()
    (root / "guard").write_text("isolated-session-v1")
    (root / "data/konsole").mkdir()
    for label in "ABCDEFGHIJK":
        (root / "cwd" / f"pane {label}; literal & space").mkdir()
        (root / "data/konsole" / f"{label}.profile").write_text(
            f"[General]\nName=Session {label}\nCommand=/bin/sh -i\nParent=FALLBACK/\n"
            "[Scrolling]\nHistoryMode=0\n")
    (root / "data/konsole/J.profile").write_text(
        "[General]\nName=English\nName[de]=Pr\u00fcfung\nCommand=/bin/sh -i\nParent=FALLBACK/\n", encoding="utf-8")
    (root / "data/konsole/K.profile").write_text(
        "[General]\n" + r"Name=\sLeading\tTabbed\\Name\s" + "\nCommand=/bin/sh -i\nParent=FALLBACK/\n")
    (root / "config/konsolerc").write_text(
        "[General]\nConfigVersion=1\n[Desktop Entry]\nDefaultProfile=B.profile\n"
        "[KonsoleWindow]\nUseSingleInstance=true\nRememberWindowSize=false\n")
    (root / "session.conf").write_text(
        f'<busconfig><type>session</type><listen>unix:tmpdir={root}/runtime</listen>'
        '<auth>EXTERNAL</auth><policy context="default"><allow send_destination="*"/>'
        '<allow receive_sender="*"/><allow own="*"/></policy></busconfig>')
    env = {key: str(root / value) for key, value in DIRECTORIES.items()}
    env.update(PATH="/usr/bin:/bin", LANG="de_DE.UTF-8", LC_ALL="de_DE.UTF-8", LANGUAGE="de", TMPDIR=str(root / "tmp"),
               DBUS_SYSTEM_BUS_ADDRESS=f"unix:path={root}/runtime/no-system-bus",
               QT_STYLE_OVERRIDE="Fusion", QT_SCALE_FACTOR="1", KWIN_COMPOSE="Q",
               LIBGL_ALWAYS_SOFTWARE="1", QT_QUICK_BACKEND="software",
               QT_WAYLAND_DISABLE_WINDOWDECORATION="1", PYTHONDONTWRITEBYTECODE="1")
    command = ["/usr/bin/timeout", "--signal=TERM", "--kill-after=5s", "150s",
               "/usr/bin/dbus-run-session", "--config-file", str(root / "session.conf"), "--",
               "/usr/bin/kwin_wayland", "--virtual", "--width", "1800", "--height", "1200",
               "--scale", "1", "--socket", "konsole-session-test", "--no-lockscreen",
               "--no-global-shortcuts", "--exit-with-session",
               f'/usr/bin/python3 "{Path(__file__).resolve()}" --child "{root}"']
    print(f"ARTIFACTS={root}", flush=True)
    with (root / "session.log").open("x") as log:
        result = subprocess.run(command, env=env, cwd=root / "home", stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, timeout=165,
                                start_new_session=True)
    print((root / "session.log").read_text())
    passed = (root / "complete").exists()
    print(f"RESULT={'PASS' if passed else 'FAIL'} PROCESS_EXIT={result.returncode}")
    return 0 if passed and result.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
