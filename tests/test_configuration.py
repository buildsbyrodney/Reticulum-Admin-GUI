import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "management-agent"))
import configuration as cfg

SAMPLE = """# keep this comment\n[reticulum]\nenable_transport = No\nloglevel = 4\ncustom_global = retain\n\n[[Public TCP]]\ntype = TCPServerInterface\nenabled = Yes\nmode = full\nlisten_ip = 0.0.0.0\nlisten_port = 4242\nnetwork_name = public\npassphrase = private-value\ncustom_key = preserve\n"""

class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); root=Path(self.temp.name)
        self.old=(cfg.CONFIG,cfg.ROOT,cfg.SNAPSHOTS,cfg.RESTART_REQUEST)
        cfg.ROOT=root; cfg.CONFIG=root/"config"; cfg.SNAPSHOTS=root/"admin-config-snapshots"; cfg.RESTART_REQUEST=root/"admin-control"/"restart-request"
        cfg.CONFIG.write_text(SAMPLE)
    def tearDown(self):
        cfg.CONFIG,cfg.ROOT,cfg.SNAPSHOTS,cfg.RESTART_REQUEST=self.old; self.temp.cleanup()
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
        old_health=cfg.health; cfg.health=lambda wait_for_restart=False:(True,"")
        try: result=cfg.restore_snapshot("admin","restore",first["id"])
        finally: cfg.health=old_health
        self.assertTrue(result["ok"]); self.assertGreaterEqual(len(cfg.list_snapshots()),2); self.assertIn("enable_transport = No",cfg.read_config())
    def test_apply_rolls_back_when_health_fails(self):
        old_health,old_runtime=cfg.health,cfg.apply_runtime
        cfg.apply_runtime=lambda change:(True,"interface_reload",""); cfg.health=lambda wait_for_restart=False:(False,"failed")
        try: result=cfg.apply_change("admin","test",{"kind":"interface","name":"Public TCP","changes":{"enabled":False}})
        finally: cfg.health, cfg.apply_runtime=old_health,old_runtime
        self.assertFalse(result["ok"]); self.assertIn("enabled = Yes",cfg.read_config())

if __name__ == "__main__": unittest.main()
