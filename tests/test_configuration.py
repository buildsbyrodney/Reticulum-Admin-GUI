import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "management-agent"))
import configuration as cfg

SAMPLE = """# keep this comment\n[reticulum]\nenable_transport = No\nloglevel = 4\ncustom_global = retain\n\n[[Public TCP]]\ntype = TCPServerInterface\nenabled = Yes\nmode = full\nlisten_ip = 0.0.0.0\nlisten_port = 4242\nnetwork_name = public\npassphrase = private-value\ncustom_key = preserve\n"""

class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); root=Path(self.temp.name)
        self.old=(cfg.CONFIG,cfg.ROOT,cfg.SNAPSHOTS,cfg.RESTART_REQUEST,cfg.STATUS_DIR,cfg.GENERATION_FILE,cfg.OPERATIONS)
        cfg.ROOT=root; cfg.CONFIG=root/"config"; cfg.SNAPSHOTS=root/"admin-config-snapshots"; cfg.RESTART_REQUEST=root/"admin-control"/"restart-request"; cfg.STATUS_DIR=root/"admin-status"; cfg.GENERATION_FILE=cfg.STATUS_DIR/"rnsd.generation"; cfg.OPERATIONS=root/"admin-config-operations"
        cfg.CONFIG.write_text(SAMPLE)
    def tearDown(self):
        cfg.CONFIG,cfg.ROOT,cfg.SNAPSHOTS,cfg.RESTART_REQUEST,cfg.STATUS_DIR,cfg.GENERATION_FILE,cfg.OPERATIONS=self.old; self.temp.cleanup()
    def test_known_change_preserves_unknown_lines_and_redacts_secret(self):
        candidate=cfg.edit_text(SAMPLE,{"kind":"interface","name":"Public TCP","changes":{"mode":"boundary","passphrase":"new-secret"}})
        self.assertIn("custom_key = preserve",candidate); self.assertIn("# keep this comment",candidate)
        diff=cfg.redacted_diff(SAMPLE,candidate); secret=[x for x in diff if x["key"] == "passphrase"][0]
        self.assertEqual(secret["old"],"Changed"); self.assertEqual(secret["new"],"Changed")
    def test_validation_and_atomic_write(self):
        with self.assertRaises(ValueError): cfg.edit_text(SAMPLE,{"kind":"interface","name":"Public TCP","changes":{"listen_port":70000}})
        candidate=cfg.edit_text(SAMPLE,{"kind":"global","setting":"enable_transport","value":True}); cfg.atomic_write(candidate)
        self.assertIn("enable_transport = Yes",cfg.read_config()); self.assertTrue((cfg.ROOT/"config.original").exists())
    def test_snapshot_restore_creates_safety_snapshot(self):
        first=cfg.create_snapshot("admin","before",{"manual":True},False)
        cfg.atomic_write(cfg.edit_text(SAMPLE,{"kind":"global","setting":"enable_transport","value":True}))
        old_health=cfg.health; cfg.health=lambda *args, **kwargs:(True,"")
        try: result=cfg.restore_snapshot("admin","restore",first["id"])
        finally: cfg.health=old_health
        self.assertTrue(result["ok"]); self.assertGreaterEqual(len(cfg.list_snapshots()),2); self.assertIn("enable_transport = No",cfg.read_config())
    def test_apply_rolls_back_when_health_fails(self):
        old_health,old_runtime=cfg.health,cfg.apply_runtime
        replies=iter([(False,"failed"),(True,"recovered")])
        cfg.apply_runtime=lambda change, operation_id=None:(True,"interface_reload",""); cfg.health=lambda *args, **kwargs:next(replies)
        try: result=cfg.apply_change("admin","test",{"kind":"interface","name":"Public TCP","changes":{"enabled":False}})
        finally: cfg.health, cfg.apply_runtime=old_health,old_runtime
        self.assertFalse(result["ok"]); self.assertTrue(result["rolled_back"]); self.assertIn("enabled = Yes",cfg.read_config())

    def test_restart_health_waits_for_new_generation_within_deadline(self):
        old_generation, old_probe, old_monotonic, old_sleep, old_budget = cfg.rnsd_generation, cfg.probe_health, cfg.time.monotonic, cfg.time.sleep, cfg.RESTART_HEALTH_TIMEOUT
        clock = [0]
        # A 28-second launch would have exceeded the old web timeout in the live
        # failure.  It remains inside the explicit 35-second restart budget.
        try:
            cfg.RESTART_HEALTH_TIMEOUT = 35
            cfg.time.monotonic = lambda: clock[0]
            cfg.time.sleep = lambda seconds: clock.__setitem__(0, clock[0] + 7)
            cfg.rnsd_generation = lambda: 8 if clock[0] >= 28 else 7
            cfg.probe_health = lambda: (True, "healthy")
            ok, detail, verification = cfg.health(True, 7, "CFG-20261010-051834-A1B2C3")
        finally:
            cfg.rnsd_generation, cfg.probe_health, cfg.time.monotonic, cfg.time.sleep, cfg.RESTART_HEALTH_TIMEOUT = old_generation, old_probe, old_monotonic, old_sleep, old_budget
        self.assertTrue(ok); self.assertEqual(detail, "healthy"); self.assertTrue(verification["restart_verified"]); self.assertEqual(verification["generation"], 8)

    def test_operation_id_prevents_duplicate_apply(self):
        operation = "CFG-20261010-051834-A1B2C3"
        old_runtime, old_health = cfg.apply_runtime, cfg.health
        try:
            cfg.apply_runtime = lambda change, operation_id=None: (True, "interface_reload", "")
            cfg.health = lambda *args, **kwargs: (True, "healthy")
            first = cfg.apply_change("admin", "test", {"kind":"interface","name":"Public TCP","changes":{"enabled":False}}, operation)
            second = cfg.apply_change("admin", "different request", {"kind":"interface","name":"Public TCP","changes":{"enabled":True}}, operation)
        finally:
            cfg.apply_runtime, cfg.health = old_runtime, old_health
        self.assertTrue(first["ok"]); self.assertEqual(first, second); self.assertIn("enabled = No", cfg.read_config())

if __name__ == "__main__": unittest.main()
