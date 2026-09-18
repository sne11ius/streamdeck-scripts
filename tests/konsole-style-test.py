#!/usr/bin/env python3
"""Exercise installation and launch boundaries without contacting the live desktop."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


REPO = Path(__file__).resolve().parents[1]


class KonsoleStyleTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="konsole-style-test-")
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name) / "home with spaces"
        self.env = os.environ.copy()
        self.env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        self.env["HOME"] = str(self.home)
        for name in ("CONFIG", "DATA", "STATE", "CACHE", "RUNTIME"):
            path = self.home / name.lower()
            path.mkdir(parents=True)
            self.env[f"XDG_{name}_HOME" if name != "RUNTIME" else "XDG_RUNTIME_DIR"] = str(path)
        self.config = self.home / "config/konsolerc"
        self.config.write_text("[Desktop Entry]\nDefaultProfile=Quake.profile\n\n[TabBar]\nTabBarPosition=Bottom\n")
        self.style = self.home / "data/konsole/quake.qss"
        self.style.parent.mkdir()
        self.pidfile = self.home / "runtime/quake.pid"

    def install(self, *args):
        script = REPO / "install-konsole-style.sh"
        self.assertTrue(script.is_file(), "the scoped styling installer is missing")
        result = subprocess.run(["bash", str(script), *args], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def read_key(self, group, key):
        return subprocess.check_output([
            "kreadconfig6", "--file", str(self.config), "--group", group,
            "--key", key, "--default", "ABSENT",
        ], env=self.env, text=True).strip()

    def test_install_hides_headers_sets_medium_handles_and_leaves_session_assets_alone(self):
        untouched = [
            self.home / "data/kxmlgui5/konsole/sessionui.rc",
            self.home / "data/kxmlgui6/konsole/konsoleui.rc",
            self.home / "data/konsole/Quake.profile",
            self.home / "state/konsole-quake-session.json",
            self.home / "state/konsolestaterc",
            self.pidfile,
        ]
        for path in untouched:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"do not change {path.name}\n")
        self.install()
        self.assertEqual(self.read_key("SplitView", "SplitViewVisibility"), "AlwaysHideSplitHeader")
        self.assertEqual(self.read_key("SplitView", "SplitDragHandleSize"), "SplitDragHandleMedium")
        self.assertEqual(self.read_key("TabBar", "TabBarPosition"), "Bottom")
        self.assertEqual(self.read_key("Desktop Entry", "DefaultProfile"), "Quake.profile")
        self.assertEqual(self.style.read_bytes(), (REPO / "konsole-config/quake.qss").read_bytes())
        for path in untouched:
            self.assertEqual(path.read_text(), f"do not change {path.name}\n")
        self.assertFalse((self.home / "data/kxmlgui5/konsole/konsoleui.rc").exists())

    def test_reinstall_keeps_original_backup_and_rollback_preserves_later_preferences(self):
        with self.config.open("a") as config:
            config.write("\n[SplitView]\nSplitViewVisibility=AlwaysShowSplitHeader\nSplitDragHandleSize=SplitDragHandleLarge\n")
        self.style.write_text("previous stylesheet\n")
        self.install()
        self.install()
        subprocess.run([
            "kwriteconfig6", "--file", str(self.config), "--group", "General",
            "--key", "UnrelatedPreference", "keep-new-value",
        ], env=self.env, check=True)
        self.install("--rollback")
        self.assertEqual(self.read_key("SplitView", "SplitViewVisibility"), "AlwaysShowSplitHeader")
        self.assertEqual(self.read_key("SplitView", "SplitDragHandleSize"), "SplitDragHandleLarge")
        self.assertEqual(self.read_key("General", "UnrelatedPreference"), "keep-new-value")
        self.assertEqual(self.style.read_text(), "previous stylesheet\n")

    def test_rollback_removes_previously_absent_settings_and_stylesheet(self):
        self.install()
        self.install("--rollback")
        self.assertEqual(self.read_key("SplitView", "SplitViewVisibility"), "ABSENT")
        self.assertEqual(self.read_key("SplitView", "SplitDragHandleSize"), "ABSENT")
        self.assertEqual(self.read_key("TabBar", "TabBarPosition"), "Bottom")
        self.assertFalse(self.style.exists())

    def test_failed_install_can_rollback_or_retry_without_losing_original_settings(self):
        binaries = self.home / "failing-bin"
        binaries.mkdir()
        writer = binaries / "kwriteconfig6"
        writer.write_text(
            '#!/bin/sh\ncase "$*" in *SplitDragHandleSize*) exit 37;; esac\n'
            f'exec "{shutil.which("kwriteconfig6")}" "$@"\n'
        )
        writer.chmod(0o755)
        original_path = self.env["PATH"]
        for retry in (False, True):
            with self.subTest(retry=retry):
                self.config.write_text("[SplitView]\nSplitViewVisibility=AlwaysShowSplitHeader\nSplitDragHandleSize=SplitDragHandleLarge\n")
                self.style.write_text("original stylesheet\n")
                self.env["PATH"] = str(binaries) + os.pathsep + original_path
                result = subprocess.run(["bash", str(REPO / "install-konsole-style.sh")],
                                        env=self.env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
                self.assertEqual(self.read_key("SplitView", "SplitViewVisibility"), "AlwaysHideSplitHeader")
                self.assertFalse((self.home / "state/konsole-quake-style/active").exists())
                self.env["PATH"] = original_path
                if retry:
                    self.install()
                self.install("--rollback")
                self.assertEqual(self.read_key("SplitView", "SplitViewVisibility"), "AlwaysShowSplitHeader")
                self.assertEqual(self.read_key("SplitView", "SplitDragHandleSize"), "SplitDragHandleLarge")
                self.assertEqual(self.style.read_text(), "original stylesheet\n")

    def test_both_launch_paths_receive_styling_without_touching_the_real_pidfile(self):
        # These boundaries launch applications and control KWin; replace only those
        # external processes. Run the real launcher and examine its argument output.
        source = (REPO / "konsole-quake-toggle.sh").read_text()
        # Substitute only the hard-coded external PID path in a disposable copy,
        # rather than add a production test hook or ever open the live PID file.
        self.assertEqual(source.count('PIDFILE="/tmp/konsole-quake.pid"'), 1)
        launcher = self.home / "launcher.sh"
        launcher.write_text(source.replace('PIDFILE="/tmp/konsole-quake.pid"',
                                           'PIDFILE="$HOME/runtime/quake.pid"', 1))
        binaries = self.home / "bin"
        binaries.mkdir()
        fixtures = {
            "konsole": '#!/bin/sh\nprintf "%s\\n" "$@" > "$HOME/launch-args"\n',
            "qdbus6": '#!/bin/sh\nprintf "0\\n"\n',
            "sleep": '#!/bin/sh\nexit 0\n',
            "python3": '#!/bin/sh\nprintf "%s\\n" "$@" > "$HOME/session-args"\nprintf "99999999\\n"\n',
        }
        for name, contents in fixtures.items():
            target = binaries / name
            target.write_text(contents)
            target.chmod(0o755)
        self.env["PATH"] = str(binaries) + os.pathsep + self.env["PATH"]
        self.env["TMPDIR"] = str(self.home / "runtime")
        self.install()
        # The legacy saved-session location deliberately remains unchanged.
        state = self.home / ".local/state/konsole-quake-session.json"
        for has_state in (False, True):
            with self.subTest(saved_state=has_state):
                if has_state:
                    state.parent.mkdir(parents=True)
                    state.write_text("{}\n")
                self.pidfile.unlink(missing_ok=True)
                result = subprocess.run(["bash", str(launcher)], env=self.env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                if has_state:
                    args = (self.home / "session-args").read_text().splitlines()
                    self.assertEqual(args[1:3], ["launch", "--"])
                    self.assertNotIn("restore", args)
                    self.assertEqual(self.pidfile.read_text().strip(), "99999999")
                else:
                    args = (self.home / "launch-args").read_text().splitlines()
                    self.assertIn("--separate", args)
                    self.assertEqual(args[args.index("--profile") + 1], "Quake")
                self.assertIn("--hide-menubar", args)
                self.assertIn("--hide-toolbars", args)
                self.assertEqual(args[args.index("--stylesheet") + 1], str(self.style))
                self.assertEqual("--tabs-from-file" in args, not has_state)

        self.install("--rollback")
        self.pidfile.unlink(missing_ok=True)
        subprocess.run(["bash", str(launcher)], env=self.env, check=True,
                       capture_output=True, timeout=10)
        args = (self.home / "session-args").read_text().splitlines()
        self.assertNotIn("--stylesheet", args)
        self.assertNotIn("--hide-toolbars", args)


if __name__ == "__main__":
    unittest.main(verbosity=2)
