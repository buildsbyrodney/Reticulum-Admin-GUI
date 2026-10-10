import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


def load_helper(config):
    previous = sys.argv[:]
    try:
        sys.argv = ["control_helper.py", str(config)]
        spec = importlib.util.spec_from_file_location("control_helper_test", ROOT / "rnsd" / "control_helper.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module
    finally:
        sys.argv = previous


class ControlHelperTests(unittest.TestCase):
    def test_helper_accepts_only_predefined_local_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            helper = load_helper(Path(temporary))
            command = helper.command_for("reload", {"interface": "AutoInterface"})
            self.assertEqual(command[0], "rnstatus")
            self.assertIn("--reload", command)
            self.assertEqual(helper.command_for("drop_announces", {})[-1], "--drop-announces")
            with self.assertRaises(ValueError): helper.command_for("shell", {})
            with self.assertRaises(ValueError): helper.command_for("reload", {"interface": "bad\nname"})


if __name__ == "__main__": unittest.main()
