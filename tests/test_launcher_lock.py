import subprocess
import tempfile
import unittest
from pathlib import Path


class LauncherLockTests(unittest.TestCase):
    def test_second_owner_is_blocked_and_lock_releases_on_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            lock = Path(temporary) / "rnsd.lock"
            first = subprocess.Popen(["flock", "-n", str(lock), "sleep", "30"])
            try:
                self.assertNotEqual(subprocess.run(["flock", "-n", str(lock), "true"]).returncode, 0)
            finally:
                first.terminate(); first.wait(timeout=5)
            self.assertEqual(subprocess.run(["flock", "-n", str(lock), "true"]).returncode, 0)


if __name__ == "__main__": unittest.main()
