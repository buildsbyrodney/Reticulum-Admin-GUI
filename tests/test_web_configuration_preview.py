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
