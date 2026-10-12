import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location("rnsd_health", ROOT / "rnsd" / "check_rnsd_health.py")
health = importlib.util.module_from_spec(spec); spec.loader.exec_module(health)

class RnsdHealthTests(unittest.TestCase):
    def test_launcher_retries_shared_readiness_inside_one_managed_lifecycle(self):
        launcher = (ROOT / "rnsd" / "start-rnsd.sh").read_text(encoding="utf-8")
        probe = (ROOT / "rnsd" / "check_shared_instance.py").read_text(encoding="utf-8")
        # A failed candidate must be stopped before retrying. This avoids the
        # previous Docker-restart loop, where a Pi could outlast the management
        # agent's verification deadline before its third candidate was ready.
        self.assertIn('CANDIDATE_READY_TIMEOUT="${RNSD_CANDIDATE_READY_TIMEOUT:-25}"', launcher)
        self.assertIn('STARTUP_READINESS_BUDGET="${RNSD_STARTUP_READINESS_BUDGET:-45}"', launcher)
        self.assertIn('CANDIDATE_ATTEMPTS="${RNSD_CANDIDATE_ATTEMPTS:-2}"', launcher)
        self.assertIn("wait_for_no_shared_instance()", launcher)
        self.assertIn("stop_candidate()", launcher)
        self.assertIn('SHARED_INSTANCE_PROBE="${RNS_SHARED_PROBE_COMMAND:-/usr/local/bin/check-shared-instance}"', launcher)
        self.assertIn('Retrying shared-instance startup', launcher)
        self.assertIn('RNS_SHARED_PROBE_TIMEOUT", "2"', probe)

    def snapshot(self, root, generation=7, ready=True, error=None, collected="2026-10-10T05:00:00+00:00"):
        status=root/"admin-status"; status.mkdir(exist_ok=True); (status/"rnsd.generation").write_text(str(generation))
        (status/"snapshot.json").write_text(json.dumps({"collected_at":collected,"rnsd_generation":generation,"reticulum_ready":ready,"status_error":error,"status":{"interfaces":[{"name":"Shared Instance[rns/default]"}]}}))
    def test_missing_or_stale_snapshot_is_unhealthy_even_with_generation_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); (root/"admin-status").mkdir(); (root/"admin-status"/"rnsd.generation").write_text("7")
            self.assertFalse(health.healthy(root, clock=lambda:1760072400)[0])
            self.snapshot(root, collected="2020-01-01T00:00:00+00:00")
            self.assertFalse(health.healthy(root, clock=lambda:1760072400)[0])
    def test_error_or_generation_mismatch_is_unhealthy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); self.snapshot(root, error="No shared RNS instance available")
            self.assertFalse(health.healthy(root, clock=lambda:1760072400)[0])
            self.snapshot(root, generation=7); payload=json.loads((root/"admin-status"/"snapshot.json").read_text()); payload["rnsd_generation"]=6; (root/"admin-status"/"snapshot.json").write_text(json.dumps(payload))
            self.assertFalse(health.healthy(root, clock=lambda:1760072400)[0])
    def test_fresh_matching_ready_snapshot_is_healthy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); self.snapshot(root)
            self.assertTrue(health.healthy(root, clock=lambda:1760072400)[0])

if __name__ == "__main__": unittest.main()
