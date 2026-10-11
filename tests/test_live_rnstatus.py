import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]
APP_DIR = ROOT / "reticulum-admin"
FIXTURE = ROOT / "tests" / "fixtures" / "rnstatus-1.5.7-pi.json"


class LiveRnstatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        root = Path(cls.temp.name)
        os.environ["APP_DATA"] = str(root / "admin")
        os.environ["RETICULUM_DIR"] = str(root / "reticulum")
        os.environ["ADMIN_SECRET_KEY"] = "test-secret-key"
        os.environ["OBSERVATION_COLLECTOR"] = "false"
        sys.path.insert(0, str(APP_DIR))
        sys.modules.pop("app", None)
        cls.app_module = importlib.import_module("app")
        status_dir = root / "reticulum" / "admin-status"
        status_dir.mkdir(parents=True)
        (status_dir / "snapshot.json").write_text(json.dumps({"status": json.loads(FIXTURE.read_text()), "paths": []}), encoding="utf-8")
        (status_dir / "rnsd.heartbeat").write_text("0", encoding="utf-8")
        with cls.app_module.db() as con:
            con.execute("INSERT INTO admins VALUES (?, ?, ?)", ("admin", "hash", 0))
        cls.app_module.CONFIG_VIEW_CACHE["config"] = {
            "global": {"enable_transport": "No", "loglevel": "4"},
            "interfaces": [{"name": "Default Interface", "type": "AutoInterface", "enabled": "Yes", "mode": "full", "fields": {"network_name": "ASH", "passphrase": "ASHVILLE"}}],
        }

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def client(self):
        client = self.app_module.app.test_client()
        with client.session_transaction() as session:
            session["admin"] = "admin"
            session["csrf"] = "test-csrf"
        return client

    def setUp(self):
        self.app_module.CONFIG_VIEW_CACHE["config"] = {
            "global": {"enable_transport": "No", "loglevel": "4"},
            "interfaces": [{"name": "Default Interface", "type": "AutoInterface", "enabled": "Yes", "mode": "full", "fields": {"network_name": "ASH", "passphrase": "ASHVILLE"}}],
        }

    def test_adapter_normalizes_real_pi_fields(self):
        records = self.app_module.find_interface_records(json.loads(FIXTURE.read_text()))
        server, auto, local_client = records
        self.assertEqual(server["rx_bytes"], 0)
        self.assertEqual(server["tx_bytes"], 0)
        self.assertEqual(server["rx_rate"], 0)
        self.assertEqual(server["tx_rate"], 0)
        self.assertEqual(server["client_count"], 1)
        self.assertEqual(server["status_display"], "Up")
        self.assertEqual(server["mode_display"], "Full")
        self.assertFalse(server["ifac_enabled"])
        self.assertIsNone(auto["client_count"])
        self.assertEqual(auto["peer_count"], 0)
        self.assertEqual(local_client["parent_interface_name"], "Shared Instance[rns/default]")

    def test_missing_and_unknown_optional_fields_are_safe(self):
        records = self.app_module.find_interface_records({"interfaces": [{"name": "Sparse", "mode": 99, "status": False}]})
        self.assertEqual(records[0]["mode_display"], "Unknown (99)")
        self.assertEqual(records[0]["status_display"], "Down")
        self.assertIsNone(records[0]["rx_bytes"])
        self.assertIsNone(records[0]["client_count"])

    def test_observability_pages_render_live_fixture(self):
        client = self.client()
        for route in ("/", "/interfaces", "/clients", "/queues", "/announces", "/timeline"):
            response = client.get(route)
            self.assertEqual(response.status_code, 200, route)
        page = client.get("/interfaces").get_data(as_text=True)
        self.assertIn("Runtime / System Interfaces", page)
        self.assertIn("System Managed", page)
        self.assertIn("0 B / 0 B", page)
        self.assertIn(">1 / —</td>", page)
        self.assertIn("Full", page)
        self.assertIn("Up", page)
        self.assertIn("Default Interface", page)
        self.assertEqual(page.count("AutoInterface[Default Interface]"), 0)

    def test_configured_interface_is_merged_and_system_interfaces_are_read_only(self):
        client = self.client()
        page = client.get("/interfaces").get_data(as_text=True)
        self.assertEqual(page.count("Default Interface</td>"), 1)
        self.assertIn("Configured: Enabled", page)
        self.assertIn("Size: Use interface default", page)
        self.assertIn("/interfaces/Default%20Interface/edit", page)
        self.assertNotIn("/interfaces/Shared%20Instance%5Brns/default%5D/edit", page)
        self.assertNotIn("/interfaces/LocalClientInterface%5Btest-client%5D/clone", page)

        edit = client.get("/interfaces/Default%20Interface/edit")
        self.assertEqual(edit.status_code, 200)
        self.assertIn(b"Edit Interface", edit.data)
        self.assertIn(b"Default Interface", edit.data)

        blocked_edit = client.get("/interfaces/LocalClientInterface%5Btest-client%5D/edit", follow_redirects=True)
        self.assertEqual(blocked_edit.status_code, 200)
        self.assertIn(b"managed internally by Reticulum", blocked_edit.data)
        blocked_clone = client.get("/interfaces/LocalClientInterface%5Btest-client%5D/clone", follow_redirects=True)
        self.assertEqual(blocked_clone.status_code, 200)
        self.assertIn(b"managed internally by Reticulum", blocked_clone.data)
        blocked_delete = client.post("/interfaces/LocalClientInterface%5Btest-client%5D/delete", data={"csrf_token": "test-csrf"}, follow_redirects=True)
        self.assertEqual(blocked_delete.status_code, 200)
        self.assertIn(b"managed internally by Reticulum", blocked_delete.data)

    def test_tcp_and_tailscale_guidance_is_visible_without_network_automation(self):
        client = self.client()
        form = client.get("/interfaces/add?purpose=user_tcp").get_data(as_text=True)
        self.assertIn("Listen IP", form)
        self.assertIn("Do not normally enter your public Internet IP here", form)
        self.assertIn("Target Port", form)
        self.assertIn("must match that server’s Listen Port", form)
        help_page = client.get("/help").get_data(as_text=True)
        self.assertIn("Using Tailscale", help_page)
        self.assertIn("Public User TCP      0.0.0.0:4242", help_page)
        self.assertIn("Trusted Backbone     0.0.0.0:4243", help_page)
        self.assertNotIn("Cloudflare", form + help_page)

    def test_configuration_agent_timeout_is_recorded_not_http_500(self):
        client = self.client()
        original = self.app_module.management
        self.app_module.management = lambda path, body=None: {"ok": False, "unknown": True, "error": "The management operation did not return a result in time."}
        try:
            with client.session_transaction() as session:
                session["config_change"] = {"operation_id": "CFG-20261010-051834-A1B2C3", "reason": "test", "change": {"kind": "global", "setting": "enable_transport", "value": True}}
            response = client.post("/configuration/apply", data={"csrf_token": "test-csrf"}, follow_redirects=False)
        finally:
            self.app_module.management = original
        self.assertEqual(response.status_code, 302)
        with self.app_module.db() as con:
            row = con.execute("SELECT result, operation_id FROM config_changes ORDER BY id DESC LIMIT 1").fetchone()
        self.assertEqual(row["result"], "Result Unknown / Verification Timed Out")
        self.assertEqual(row["operation_id"], "CFG-20261010-051834-A1B2C3")


if __name__ == "__main__":
    unittest.main()
