import os
import sys
import tempfile
import unittest
from pathlib import Path


class WebConfigurationPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_SECRET_KEY"] = "test-secret"
        os.environ["APP_DATA"] = cls.temp.name
        os.environ["OBSERVATION_COLLECTOR"] = "false"
        sys.path.insert(0, str(Path(__file__).parents[1] / "reticulum-admin"))
        import app as web_app
        cls.web_app = web_app

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.web_app.CONFIG_VIEW_CACHE["config"] = {
            "global": {"enable_transport": "No", "loglevel": "4"},
            "interfaces": [{
                "name": "Public TCP",
                "enabled": "Yes",
                "mode": "full",
                "fields": {"network_name": "public", "passphrase": "Configured"},
            }],
        }

    def test_transport_preview_uses_local_cached_model(self):
        preview = self.web_app.local_config_preview({"kind": "global", "setting": "enable_transport", "value": True})
        self.assertTrue(preview["ok"])
        self.assertEqual(preview["diff"], [{"section": "reticulum", "key": "enable_transport", "old": "No", "new": "Yes"}])

    def test_interface_preview_redacts_passphrase_without_agent_request(self):
        preview = self.web_app.local_config_preview({"kind": "interface", "name": "Public TCP", "changes": {"mode": "boundary", "passphrase": "new-secret"}})
        self.assertTrue(preview["local_review"])
        self.assertIn({"section": "Public TCP", "key": "mode", "old": "full", "new": "boundary"}, preview["diff"])
        self.assertIn({"section": "Public TCP", "key": "passphrase", "old": "Changed", "new": "Changed"}, preview["diff"])

    def test_discovery_batch_preview_is_local_and_immediate(self):
        preview = self.web_app.local_config_preview({"kind": "global_batch", "changes": {"discover_interfaces": True, "autoconnect_discovered_interfaces": 1}})
        self.assertTrue(preview["local_review"])
        self.assertIn({"section": "reticulum", "key": "discover_interfaces", "old": None, "new": "Yes"}, preview["diff"])

    def test_discovered_interface_model_redacts_ifac_key_and_marks_trusted_source(self):
        source = "0123456789abcdef0123456789abcdef"
        snap = {"discovered_interfaces": [{"name": "BHM", "type": "TCPServerInterface", "network_id": source, "reachable_on": "node.example.net", "port": 4242, "ifac_netname": "Trusted", "ifac_netkey": "must-not-leak"}]}
        config = {"global": {"interface_discovery_sources": source}}
        rows = self.web_app.discovered_interface_models(snap, [], config)
        self.assertEqual(rows[0]["reachable_address"], "node.example.net:4242")
        self.assertTrue(rows[0]["trusted"])
        self.assertNotIn("ifac_netkey", rows[0])

    def test_review_route_does_not_ingest_or_write_observer_state(self):
        old_ingest = self.web_app.ingest_snapshot
        self.web_app.ingest_snapshot = lambda: (_ for _ in ()).throw(AssertionError("Review must not ingest observer state"))
        try:
            with self.web_app.app.test_client() as client:
                with client.session_transaction() as state:
                    state["admin"] = "test-admin"
                    state["csrf"] = "csrf-test"
                response = client.post("/configuration/preview", data={
                    "csrf_token": "csrf-test", "kind": "global", "setting": "enable_transport", "value": "on", "reason": "test",
                })
        finally:
            self.web_app.ingest_snapshot = old_ingest
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Review Configuration Change", response.data)


if __name__ == "__main__":
    unittest.main()
