import sys, tempfile, unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "management-agent"))
import features

class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old = features.ROOT, features.BOT_CONFIG, features.MESSAGING_REQUESTS, features.NOMAD_REQUESTS
        root = Path(self.temp.name)
        features.ROOT, features.BOT_CONFIG, features.MESSAGING_REQUESTS, features.NOMAD_REQUESTS = root / "state", root / "bot", root / "messaging", root / "nomad"
    def tearDown(self):
        features.ROOT, features.BOT_CONFIG, features.MESSAGING_REQUESTS, features.NOMAD_REQUESTS = self.old; self.temp.cleanup()
    def selection(self): return {name: meta["mandatory"] for name, meta in features.CATALOG.items()}
    def test_core_is_mandatory_and_planned_features_rejected(self):
        disabled = self.selection(); disabled["reticulum-core"] = False
        with self.assertRaises(ValueError): features.apply(disabled)
        planned = self.selection(); planned["rnode"] = True
        with self.assertRaises(ValueError): features.apply(planned)
    def test_apply_preserves_data_and_writes_service_activation_policy(self):
        proposed = self.selection(); proposed["messaging"] = True; proposed["nomad-browser"] = True
        result = features.apply(proposed, "operator")
        self.assertTrue(result["ok"]); self.assertTrue((features.ROOT / "backups" / result["rollback_snapshot"]).exists())
        self.assertTrue(features._read(features.MESSAGING_REQUESTS / "settings.json", {})["enabled"])
        self.assertTrue(features._read(features.NOMAD_REQUESTS / "settings.json", {})["enabled"])
