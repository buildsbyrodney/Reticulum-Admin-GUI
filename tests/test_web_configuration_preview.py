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


if __name__ == "__main__":
    unittest.main()
