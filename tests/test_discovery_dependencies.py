import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("discovery_dependencies", ROOT / "rnsd" / "check_discovery_dependencies.py")
dependency = importlib.util.module_from_spec(spec); spec.loader.exec_module(dependency)


class DiscoveryDependencyTests(unittest.TestCase):
    def test_rnsd_image_pins_and_import_checks_lxmf(self):
        dockerfile = (ROOT / "rnsd" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ARG LXMF_VERSION=0.9.6", dockerfile)
        self.assertIn('"lxmf==${LXMF_VERSION}"', dockerfile)
        self.assertIn("import RNS, LXMF", dockerfile)

    def test_launcher_rejects_discovery_without_lxmf_before_starting_rnsd(self):
        launcher = (ROOT / "rnsd" / "start-rnsd.sh").read_text(encoding="utf-8")
        self.assertIn("verify_discovery_dependency()", launcher)
        self.assertIn("check-discovery-dependencies", launcher)
        self.assertIn("wait_for_discovery_dependency()", launcher)
        self.assertIn("Discovery requires LXMF in the managed rnsd runtime", launcher)
        self.assertIn("endless restart storm", launcher)

    def test_runtime_capability_reports_missing_lxmf(self):
        original = sys.modules.pop("LXMF", None)
        try:
            with mock.patch.object(dependency.importlib.metadata, "version", side_effect=dependency.importlib.metadata.PackageNotFoundError):
                result = dependency.capability()
        finally:
            if original is not None: sys.modules["LXMF"] = original
        self.assertFalse(result["lxmf_available"])


if __name__ == "__main__":
    unittest.main()
