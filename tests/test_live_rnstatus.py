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

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def client(self):
        client = self.app_module.app.test_client()
        with client.session_transaction() as session:
            session["admin"] = "admin"
            session["csrf"] = "test-csrf"
        return client

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
        self.assertIn("Shared Instance[rns/default]", page)
        self.assertIn("0 B / 0 B", page)
        self.assertIn(">1</td>", page)
        self.assertIn("Full", page)
        self.assertIn("Up", page)
        self.assertIn("AutoInterface[Default Interface]", page)


if __name__ == "__main__":
    unittest.main()
