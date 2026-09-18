#!/usr/bin/env python3
"""Pure tests: no desktop bus, production PID, or production state access."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SANDBOX = tempfile.TemporaryDirectory(prefix="session-unit-env-", dir="/tmp/opencode")
os.environ.clear()
os.environ.update(PATH="/usr/bin:/bin", LANG="C.UTF-8",
                  DBUS_SESSION_BUS_ADDRESS="unix:path=" + SANDBOX.name + "/no-session-bus",
                  DBUS_SYSTEM_BUS_ADDRESS="unix:path=" + SANDBOX.name + "/no-system-bus")
for key in ("HOME", "XDG_CONFIG_HOME", "XDG_CONFIG_DIRS", "XDG_DATA_HOME", "XDG_DATA_DIRS",
            "XDG_STATE_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR", "TMPDIR"):
    path = Path(SANDBOX.name) / key.lower()
    path.mkdir(mode=0o700)
    os.environ[key] = str(path)

SOURCE = Path(__file__).resolve().parents[1] / "konsole-quake-session.py"
spec = importlib.util.spec_from_file_location("session_helper", SOURCE)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def state(cwd="/tmp"):
    return {"version": 2, "tabs": [{"split": {
        "cwd": cwd, "profile": "Test", "title_formats": {
            "local": "%d : %n", "remote": "%u@%h"}}, "active_path": []}],
        "active_tab_index": 0}


class Window:
    """External boundary double with reversed QObject traversal within tab 0."""
    def __init__(self, stale=False, mutate=False):
        self.active = [11, 33]
        self.tab = 1
        self.stale = stale
        self.mutate = mutate
        self.reads = 0
        self.profiles = ["P11", "P22", "P33"]
        self.default = "P33"

    def win(self, method, *args):
        if method == "viewHierarchy":
            self.reads += 1
            if self.mutate and self.reads > 2:
                return ["(1){7|8}", "(2)[10]"]
            return ["(1){7|8}", "(2)[9]"]
        if method == "sessionList":
            return ["22", "11", "33"]
        if method == "sessionCount":
            return 3
        if method == "profileList":
            return list(self.profiles)
        if method == "defaultProfile":
            return self.default
        if method == "currentSession":
            return self.active[self.tab]
        if method == "nextSession":
            self.tab = (self.tab + 1) % 2
            return
        if method == "setCurrentSession":
            sid = int(args[0])
            self.tab = 1 if sid == 33 else 0
            self.active[self.tab] = sid
            return
        if method == "setCurrentView":
            if not self.stale:
                self.win("setCurrentSession", {7: 11, 8: 22, 9: 33}[int(args[0])])
            return True
        if method == "getSplitProportions":
            return [40.0, 60.0] if int(args[0]) == 1 else [100.0]
        raise AssertionError(f"unapproved window call: {method}")

    def pane(self, sid):
        return {"cwd": "/tmp", "profile": f"P{sid}",
                "title_formats": {"local": f"{sid} %d", "remote": "%u@%h"},
                "identity": (sid, "start", f"shell-{sid}")}


class SessionTests(unittest.TestCase):
    def test_vertical_hierarchy_and_canonical_ids(self):
        self.assertEqual(helper.parse_hierarchy(["(0)[0]"]), [
            {"id": 0, "type": "left-right", "children": [0]}])
        self.assertEqual(helper.parse_hierarchy(["(1){7|(2)[8|9]}"]), [
            {"id": 1, "type": "top-bottom", "children": [7,
                {"id": 2, "type": "left-right", "children": [8, 9]}]}])
        for raw in ([], ["(1)[7|]"], ["(1)[7}"], ["(1)[7]garbage"],
                    ["(1)[07]"], ["(01)[7]"], ["(1)[7|7]"], ["(1)[]"],
                    ["(1)[7]", "(1)[8]"], ["(1)[7]", "(2)[7]"]):
            with self.subTest(raw=raw), self.assertRaises(helper.SessionError):
                helper.parse_hierarchy(raw)

    def test_full_validation_and_legacy_titles(self):
        legacy = {"tabs": [{"title": "old literal", "profile": "Test",
                            "split": {"cwd": "/tmp"}}], "active_tab_index": 0}
        valid = helper.validate_state(legacy)
        self.assertEqual(valid["tabs"][0]["split"]["profile"], "Test")
        self.assertEqual(valid["tabs"][0]["title"], "old literal")
        self.assertNotIn("title_formats", valid["tabs"][0]["split"])
        bad = [None, {}, {"tabs": []}, {**state(), "active_tab_index": True},
               {**state(), "active_tab_index": 1}, {**state(), "version": 90},
               {**state(), "unknown": 1}]
        for node in ({"cwd": "relative", "profile": "Test"},
                     {"cwd": "/tmp/\ud800", "profile": "Test"},
                     {"cwd": "/tmp", "profile": ""},
                     {"cwd": "/tmp", "children": []},
                     {"type": "diagonal", "children": [{"cwd": "/tmp"}]},
                     {"type": "top-bottom", "children": []}):
            item = state()
            item["tabs"][0]["split"] = node
            bad.append(item)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(helper.SessionError):
                helper.validate_state(value)
        item = state()
        leaf = item["tabs"][0]["split"]
        for ratios in ([0, 100], [float("nan"), 1], [float("inf"), 1], [30], [True, 2]):
            item["tabs"][0]["split"] = {"type": "left-right", "children": [leaf, leaf],
                                            "ratios": ratios}
            with self.subTest(ratios=ratios), self.assertRaises(helper.SessionError):
                helper.validate_state(item)

    def test_mapping_does_not_zip_session_list_and_restores_each_tab(self):
        window = Window()
        saved = helper.snapshot(window)
        self.assertEqual(saved["active_tab_index"], 1)
        self.assertEqual(saved["tabs"][0]["split"]["type"], "top-bottom")
        self.assertEqual([p["profile"] for p in saved["tabs"][0]["split"]["children"]],
                         ["P11", "P22"])
        self.assertEqual(saved["tabs"][0]["active_path"], [0])
        self.assertEqual(window.active, [11, 33])
        self.assertEqual(window.tab, 1)
        self.assertNotIn("title", saved["tabs"][0])

    def test_runtime_profile_recovery_changes_only_saved_output(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            directory = Path(tmp) / "konsole"
            directory.mkdir()
            (directory / "Test.profile").write_text("[General]\nName=Test\nCommand=/bin/sh -i\n")
            window = Window()
            original_pane = window.pane
            window.profiles, window.default = ["Test"], "Test"
            window.pane = lambda sid: {**original_pane(sid), "cwd": tmp,
                                       "profile": "Temporary" if sid != 33 else "Test"}
            before = {sid: window.pane(sid) for sid in (11, 22, 33)}
            with patch.dict(os.environ, {"XDG_DATA_HOME": tmp, "XDG_DATA_DIRS": tmp}), \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                saved = helper.snapshot(window)
            self.assertEqual([p["profile"] for t in saved["tabs"] for p in helper.leaves(t["split"])],
                             ["Test", "Test", "Test"])
            self.assertIn("runtime-only", error.getvalue())
            self.assertIn("overrides beyond CWD/title formats", error.getvalue())
            self.assertEqual({sid: window.pane(sid) for sid in (11, 22, 33)}, before)
            self.assertEqual(window.default, "Test")
            self.assertEqual(window.active, [11, 33])
            self.assertEqual(window.tab, 1)

    def test_runtime_profile_recovery_requires_unique_persisted_default(self):
        for profiles, default in ((["Other"], "Missing"), (["Test", "Test"], "Test"), (["Missing"], "Missing")):
            window = Window()
            window.profiles, window.default = profiles, default
            with self.subTest(profiles=profiles), self.assertRaises(helper.SessionError):
                helper.snapshot(window)

    def test_missing_persisted_profile_is_not_recovered(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path, pidfile = Path(tmp) / "state.json", Path(tmp) / "owned.pid"
            helper.write_snapshot(path, state(tmp))
            preserved = path.read_bytes()
            pidfile.write_text("123456789")
            window = Window()
            with patch.object(helper, "Konsole", return_value=window), \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                self.assertEqual(helper.main(["--pid-file", str(pidfile), "--state-file", str(path), "save"]), 1)
            self.assertIn("Unknown/missing profiles", error.getvalue())
            self.assertNotIn("runtime-only", error.getvalue())
            self.assertEqual(path.read_bytes(), preserved)

    def test_stale_or_mutating_mapping_refuses_and_restores_selection(self):
        for window in (Window(stale=True), Window(mutate=True)):
            with self.subTest(window=window), self.assertRaises(helper.SessionError):
                helper.snapshot(window)
            self.assertEqual(window.active, [11, 33])
            self.assertEqual(window.tab, 1)

    def test_changing_split_sizes_refuse_snapshot(self):
        window = Window()
        original = window.win
        reads = 0

        def changing_sizes(method, *args):
            nonlocal reads
            if method == "getSplitProportions" and args[0] == 1:
                reads += 1
                return [40.0, 60.0] if reads == 1 else [60.0, 40.0]
            return original(method, *args)

        window.win = changing_sizes
        with self.assertRaisesRegex(helper.SessionError, "proportions changed"):
            helper.snapshot(window)
        self.assertEqual(window.active, [11, 33])
        self.assertEqual(window.tab, 1)

    def test_atomic_failure_preserves_primary_and_last_good(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "state.json"
            backup = Path(str(path) + ".bak")
            first = state(tmp)
            helper.write_snapshot(path, first)
            original = path.read_bytes()
            second = copy.deepcopy(first)
            second["tabs"][0]["split"]["title_formats"]["local"] = "next %d"
            real_replace = os.replace

            def fail_primary(src, dst):
                if Path(dst) == path:
                    raise OSError("forced replacement failure")
                return real_replace(src, dst)

            with patch.object(helper.os, "replace", side_effect=fail_primary):
                with self.assertRaises(OSError):
                    helper.write_snapshot(path, second)
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(backup.read_bytes(), original)
            helper.write_snapshot(path, second)
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            path.write_text("{broken")
            with contextlib.redirect_stderr(io.StringIO()) as error:
                helper.write_snapshot(path, first)
            self.assertIn("retaining", error.getvalue())
            self.assertEqual(backup.read_bytes(), original)
            with self.assertRaises(helper.SessionError):
                helper.write_snapshot(path, {"tabs": []})
            self.assertEqual(path.read_bytes(), original)

    def test_backup_uses_the_exact_bytes_that_were_validated(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "state.json"
            helper.write_snapshot(path, state(tmp))
            original = path.read_bytes()
            # A noncooperating old writer changes the file after our validation.
            validate = helper.validate_state

            def replace_after_validation(value):
                result = validate(value)
                path.write_text("partial old writer")
                return result

            real_read = helper.read_state

            def racing_read(*args, **kwargs):
                with patch.object(helper, "validate_state", side_effect=replace_after_validation):
                    return real_read(*args, **kwargs)

            with patch.object(helper, "read_state", side_effect=racing_read):
                helper.write_snapshot(path, state(tmp))
            self.assertEqual(Path(str(path) + ".bak").read_bytes(), original)

    def test_lock_refuses_save_and_launch_before_reading_pid_or_spawning(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "state.json"
            helper.write_snapshot(path, state(tmp))
            original = path.read_bytes()
            with helper.state_lock(path):
                for command in ("save", "launch"):
                    with patch.object(Path, "read_text", side_effect=AssertionError("PID read")), \
                            patch.object(helper.subprocess, "Popen", side_effect=AssertionError("spawn")), \
                            contextlib.redirect_stderr(io.StringIO()) as error:
                        self.assertEqual(helper.main(["--state-file", str(path), command]), 1)
                    self.assertIn("lock", error.getvalue())
            self.assertEqual(path.read_bytes(), original)

    def test_cwd_read_failure_is_not_silently_home(self):
        konsole = object.__new__(helper.Konsole)
        konsole.pid = 99999999
        konsole.session = lambda sid, method: 99999998 if method == "processId" else "unique-shell"
        with patch.object(helper, "proc_identity", return_value="start"), \
                patch.object(helper.os, "readlink", side_effect=PermissionError("forced denial")):
            with self.assertRaisesRegex(helper.SessionError, "Cannot read.*CWD"):
                konsole.pane(1)

    def test_owner_pid_mismatch_and_reuse_refuse_before_window_call(self):
        konsole = object.__new__(helper.Konsole)
        konsole.pid, konsole.start, konsole.owner, konsole.service = 99999999, "start", ":1.9", "test"

        class Bus:
            def get_name_owner(self, service):
                return ":1.9"

            def GetConnectionUnixProcessID(self, owner, timeout):
                return 88888888

        konsole.bus = konsole.daemon = Bus()
        with patch.object(helper, "proc_identity", return_value="start"):
            with self.assertRaisesRegex(helper.SessionError, "owner/PID mismatch"):
                konsole.check_owner()
        with patch.object(helper, "proc_identity", return_value="new start"):
            with self.assertRaisesRegex(helper.SessionError, "reused"):
                konsole.check_owner()

    def test_konsole_basename_alone_is_not_executable_identity(self):
        stat = "99999999 (konsole) S 1 " + "0 " * 18
        with patch.object(Path, "stat", return_value=SimpleNamespace(st_uid=os.geteuid())), \
                patch.object(Path, "read_text", return_value=stat), \
                patch.object(helper.os, "readlink", return_value="/tmp/impostor/konsole"), \
                patch.object(helper.os.path, "samefile", return_value=False):
            with self.assertRaisesRegex(helper.SessionError, "executable"):
                helper.proc_identity(99999999, executable="konsole")

    def test_restore_foreign_pid_refuses_before_any_bus_or_process_access(self):
        with patch.object(helper.subprocess, "Popen", side_effect=AssertionError("spawn")), \
                patch.object(helper.os, "readlink", side_effect=AssertionError("proc read")), \
                patch.dict(os.environ, {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent"}), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            self.assertNotEqual(helper.main(["restore", "123456789"]), 0)
        self.assertIn("refus", error.getvalue().lower())

    def test_bad_state_unknown_profile_or_cwd_never_spawns(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "state.json"
            for text, message in (('{"tabs": []}', "Invalid state"),
                                  ('{"tabs":[],"tabs":[]}', "Duplicate JSON"),
                                  (json.dumps(state(tmp + "/missing")), "CWD is missing"),
                                  (json.dumps(state(tmp + "/$literal")), "Konsole expands"),
                                  (json.dumps(state(tmp)), "Unknown/missing profiles")):
                path.write_text(text)
                with patch.object(helper.subprocess, "Popen", side_effect=AssertionError("spawn")), \
                        patch.dict(os.environ, {"HOME": tmp, "XDG_DATA_HOME": tmp,
                                                "XDG_DATA_DIRS": tmp}), \
                        contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertNotEqual(helper.main(["--state-file", str(path), "launch"]), 0)
                self.assertIn(message, error.getvalue())

    def test_save_refuses_known_unrestorable_snapshot_before_replacing_last_good(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "state.json"
            pidfile = Path(tmp) / "owned.pid"
            pidfile.write_text("123456789\n")
            helper.write_snapshot(path, state(tmp))
            original = path.read_bytes()
            directory = Path(tmp) / "$literal"
            directory.mkdir()
            with patch.object(helper, "Konsole", return_value=object()), \
                    patch.object(helper, "snapshot", return_value=state(str(directory))), \
                    contextlib.redirect_stderr(io.StringIO()) as error:
                result = helper.main(["--pid-file", str(pidfile), "--state-file", str(path), "save"])
            self.assertNotEqual(result, 0)
            self.assertIn("Konsole expands", error.getvalue())
            self.assertEqual(path.read_bytes(), original)
            self.assertFalse(Path(str(path) + ".bak").exists())

    def test_option_looking_appearance_values_cannot_reach_konsole_or_dbus(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            directory = Path(tmp) / "konsole"
            directory.mkdir()
            (directory / "Test.profile").write_text("[General]\nName=Test\nCommand=/bin/sh -i\n")
            for option in ("--title", "--name", "--class", "--stylesheet"):
                for value in ("--force-reuse", "--new-tab", "-e"):
                    with self.subTest(option=option, value=value), \
                            patch.dict(os.environ, {"XDG_DATA_HOME": tmp, "XDG_DATA_DIRS": tmp}), \
                            patch.object(helper.subprocess, "Popen", side_effect=AssertionError("spawn")), \
                            patch.object(helper, "Konsole", side_effect=AssertionError("D-Bus")), \
                            self.assertRaisesRegex(helper.SessionError, "appearance"):
                        helper.launch(state(tmp), [option, value])

    def test_all_xdg_profile_paths_are_checked_even_with_same_basename(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            roots = [Path(tmp) / name for name in ("user", "system")]
            for root in roots:
                (root / "konsole").mkdir(parents=True)
                (root / "konsole/Same.profile").write_text("[General]\nName=Test\nCommand=/bin/sh -i\n")
            with patch.dict(os.environ, {"XDG_DATA_HOME": str(roots[0]), "XDG_DATA_DIRS": str(roots[1])}):
                with self.assertRaisesRegex(helper.SessionError, "Ambiguous profile"):
                    helper.preflight(state(tmp), [])
                (roots[1] / "konsole/Same.profile").write_text("[General]\nName=Distinct\nCommand=/bin/sh -i\n")
                fixture = state(tmp)
                fixture["tabs"][0]["split"]["profile"] = "Distinct"
                self.assertEqual(helper.preflight(fixture, []), {"Distinct": str(roots[1] / "konsole/Same.profile")})

    def test_duplicate_native_profile_names_refuse_before_session_creation(self):
        class DuplicateProfiles:
            dbus = None

            def win(self, method, *args):
                if method == "viewHierarchy":
                    return ["(0)[0]"]
                if method == "sessionCount":
                    return 1
                if method == "profileList":
                    return ["Test", "Test"]
                raise AssertionError(f"must refuse before {method}")

        with self.assertRaisesRegex(helper.SessionError, "Ambiguous.*profile"):
            helper.restore_owned(DuplicateProfiles(), state())

    def test_profile_names_use_native_kconfig_localization_and_escaping(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            directory = Path(tmp) / "konsole"
            directory.mkdir()
            path = directory / "Test.profile"
            for contents, expected in (("Name=English\nName[de]=Pr\u00fcfung\n", "Pr\u00fcfung"),
                                       (r"Name=\sLeading\tTabbed\\Name\s" + "\n", " Leading\tTabbed\\Name ")):
                path.write_text("[General]\n" + contents + "Command=/bin/sh -i\n", encoding="utf-8")
                fixture = state(tmp)
                fixture["tabs"][0]["split"]["profile"] = expected
                with patch.dict(os.environ, {"XDG_DATA_HOME": tmp, "XDG_DATA_DIRS": tmp,
                                              "LANG": "de_DE.UTF-8", "LC_ALL": "de_DE.UTF-8", "LANGUAGE": "de"}):
                    self.assertEqual(helper.preflight(fixture, []), {expected: str(path)})

    def test_tabs_file_reads_only_declared_tabs_and_title_formats(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "tabs"
            path.write_text(f"\n # comment\n title: codehaufen ;; workdir: {tmp} ;; profile: Test\n"
                            f"TITLE: gradlew: %d ;; profile: Second ;; workdir: {tmp}\n"
                            f"profile: Test ;; title: src ;; workdir: {tmp}\n")
            self.assertEqual(helper.read_tabs(path), {"version": 2, "active_tab_index": 0, "tabs": [
                {"active_path": [], "split": {"cwd": tmp, "profile": "Test",
                    "title_formats": {"local": "codehaufen", "remote": "codehaufen"}}},
                {"active_path": [], "split": {"cwd": tmp, "profile": "Second",
                    "title_formats": {"local": "gradlew: %d", "remote": "gradlew: %d"}}},
                {"active_path": [], "split": {"cwd": tmp, "profile": "Test",
                    "title_formats": {"local": "src", "remote": "src"}}}]})

    def test_tabs_file_rejects_unsupported_missing_duplicate_and_oversized_data(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path = Path(tmp) / "tabs"
            valid = f"profile: Test ;; workdir: {tmp}"
            for data in ("", "# comment\n", "title: Only a title", "profile: Test", f"workdir: {tmp}",
                         valid + " ;; command: unsupported", valid + " ;; tabcolor: red",
                         valid + " ;; profile: Other", valid + " ;; malformed",
                         "profile: Test ;; workdir: relative", "profile: ;; workdir: /tmp",
                         (valid + "\n") * 33, "x" * (helper.MAX_BYTES + 1)):
                path.write_text(data)
                with self.subTest(data=data[:80]), self.assertRaises(helper.SessionError):
                    helper.read_tabs(path)
            path.write_bytes(b"\xff")
            with self.assertRaises(helper.SessionError):
                helper.read_tabs(path)

    def test_launch_cli_bootstraps_only_when_state_is_absent(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path, tabs = Path(tmp) / "state.json", Path(tmp) / "tabs"
            tabs.write_text(f"title: Bootstrap ;; workdir: {tmp} ;; profile: Test\n")
            boot = {"version": 2, "active_tab_index": 0, "tabs": [{"active_path": [], "split": {
                "cwd": tmp, "profile": "Test", "title_formats": {"local": "Bootstrap", "remote": "Bootstrap"}}}]}
            cases = [(None, boot), (state(tmp), state(tmp))]
            for persisted, expected in cases:
                if persisted is not None:
                    helper.write_snapshot(path, persisted)
                    tabs.unlink()  # Existing state must not even require a bootstrap file.

                def engine(received, appearance):
                    self.assertEqual(received, expected)
                    self.assertEqual(appearance, ["--hide-menubar"])
                    return 123456789

                with patch.object(helper, "launch", side_effect=engine), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(helper.main(["--state-file", str(path), "launch", "--tabs-file", str(tabs),
                                                  "--", "--hide-menubar"]), 0)
                self.assertEqual(output.getvalue(), "123456789\n")
                self.assertEqual(path.exists(), persisted is not None)
                if persisted is not None:
                    self.assertEqual(json.loads(path.read_text()), persisted)

    def test_bootstrap_never_masks_missing_input_or_corrupt_saved_state(self):
        with tempfile.TemporaryDirectory(prefix="session-unit-", dir="/tmp/opencode") as tmp:
            path, tabs = Path(tmp) / "state.json", Path(tmp) / "tabs"
            command = ["--state-file", str(path), "launch"]
            with patch.object(helper, "launch", side_effect=AssertionError("must not launch")):
                for options in ([], ["--tabs-file", str(tabs)]):
                    with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()) as output:
                        self.assertEqual(helper.main(command + options), 1)
                    self.assertEqual(output.getvalue(), "")
                self.assertFalse(path.exists())
                tabs.write_text(f"profile: Test ;; workdir: {tmp}\n")
                path.write_text("{broken")
                with contextlib.redirect_stderr(io.StringIO()) as error:
                    self.assertEqual(helper.main(command + ["--tabs-file", str(tabs)]), 1)
                self.assertIn("Invalid JSON", error.getvalue())
                self.assertEqual(path.read_text(), "{broken")
                path.unlink()
                path.symlink_to(Path(tmp) / "missing-target")
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertEqual(helper.main(command + ["--tabs-file", str(tabs)]), 1)

    def test_ratio_rounding_is_positive_integer_percent_total(self):
        self.assertEqual(helper.integer_percentages([1, 1, 1]), [34, 33, 33])
        result = helper.integer_percentages([0.01, 99.98, 0.01])
        self.assertEqual(sum(result), 100)
        self.assertTrue(all(type(n) is int and n >= 1 for n in result))

    def test_legacy_literal_format_markers_refuse_instead_of_becoming_dynamic(self):
        for marker in ("%d", "%U"):
            legacy = {"tabs": [{"title": f"literal {marker}", "profile": "Test", "split": {"cwd": "/tmp"}}],
                      "active_tab_index": 0}
            with self.subTest(marker=marker), self.assertRaisesRegex(helper.SessionError, "literal legacy title"):
                helper.preflight(helper.validate_state(legacy), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
