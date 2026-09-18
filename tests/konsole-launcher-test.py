#!/usr/bin/env python3
"""Real launcher control flow, isolated files and mocked desktop boundaries."""
import fcntl
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quake-launcher-", dir="/tmp/opencode")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.bin = self.home / "bin"
        self.bin.mkdir()
        self.runtime = self.home / "runtime"
        self.runtime.mkdir()
        (self.home / ".local/state").mkdir(parents=True)
        self.state = self.home / ".local/state/konsole-quake-session.json"
        self.state.write_text('{"test": "do not overwrite"}\n')
        self.pidfile = self.runtime / "quake.pid"
        source = (REPO / "konsole-quake-toggle.sh").read_text()
        self.assertEqual(source.count('PIDFILE="/tmp/konsole-quake.pid"'), 1)
        self.launcher = self.home / "launcher.sh"
        self.launcher.write_text(source.replace('PIDFILE="/tmp/konsole-quake.pid"',
                                               'PIDFILE="$HOME/runtime/quake.pid"', 1))
        self.env = {"HOME": str(self.home), "PATH": f"{self.bin}:/usr/bin:/bin", "LANG": "C.UTF-8",
                    "XDG_RUNTIME_DIR": str(self.runtime), "TMPDIR": str(self.runtime),
                    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/nonexistent-quake-test-bus"}
        self.fixture("qdbus6", 'printf "%s\\n" "$*" >> "$HOME/dbus-calls"; printf "0\\n"')
        self.fixture("sleep", "exit 0")
        self.fixture("konsole", 'touch "$HOME/native-launched"; test ! -e /proc/$$/fd/9 || touch "$HOME/leaked-lock"')
        self.fixture("python3", 'printf "%s\\n" "$*" >> "$HOME/helper-calls"; printf "99999999\\n"')

    def fixture(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body + "\n")
        path.chmod(0o755)

    def run_launcher(self):
        return subprocess.run(["/bin/bash", str(self.launcher)], env=self.env,
                              text=True, capture_output=True, timeout=10)

    def test_failed_restore_does_not_publish_pid_or_apply_window_rules(self):
        original = self.state.read_bytes()
        self.pidfile.write_text("99999998\n")
        self.fixture("python3", 'printf "restore fixture failed\\n" >&2; exit 1')
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("restore fixture failed", result.stderr)
        self.assertEqual(self.pidfile.read_text(), "99999998\n")
        self.assertEqual(self.state.read_bytes(), original)
        self.assertFalse((self.home / "dbus-calls").exists())
        self.assertFalse((self.home / "native-launched").exists())

    def test_non_pid_helper_output_is_never_interpolated_into_kwin_script(self):
        self.fixture("python3", 'printf "unexpected output\\n"')
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.pidfile.exists())
        self.assertFalse((self.home / "dbus-calls").exists())

    def test_in_flight_toggle_prevents_second_launch_or_save(self):
        lock = self.runtime / f"konsole-quake-toggle-{os.getuid()}.lock"
        with lock.open("w") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.home / "helper-calls").exists())
        self.assertFalse((self.home / "native-launched").exists())
        self.assertFalse(self.pidfile.exists())

    def test_native_start_does_not_inherit_toggle_lock(self):
        self.state.unlink()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.home / "native-launched").exists())
        self.assertFalse((self.home / "leaked-lock").exists())

    def test_reused_pid_of_non_konsole_process_is_not_toggled(self):
        # This test process is Python, not Konsole. No signal or GUI call may use it.
        self.pidfile.write_text(str(os.getpid()) + "\n")
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(" launch --", (self.home / "helper-calls").read_text())
        self.assertEqual(self.pidfile.read_text().strip(), "99999999")

    def test_save_failure_is_reported_but_still_allows_toggle(self):
        # Only this owned sleep process stands in for the tracked executable.
        (self.bin / "konsole").unlink()
        (self.bin / "konsole").symlink_to("/usr/bin/sleep")
        child = subprocess.Popen(["/usr/bin/sleep", "30"])
        self.addCleanup(child.wait)
        self.addCleanup(child.terminate)
        self.pidfile.write_text(str(child.pid) + "\n")
        self.fixture("python3", 'printf "snapshot fixture refused\\n" >&2; exit 1')
        before = self.state.read_bytes()
        result = self.run_launcher()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("snapshot fixture refused", result.stderr)
        self.assertTrue((self.home / "dbus-calls").exists())
        self.assertEqual(self.state.read_bytes(), before)
        self.assertEqual(self.pidfile.read_text().strip(), str(child.pid))
        self.assertIsNone(child.poll())


if __name__ == "__main__":
    unittest.main(verbosity=2)
