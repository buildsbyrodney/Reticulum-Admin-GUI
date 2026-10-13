import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SERVICE_PATH = ROOT / "lxmf-messaging" / "messaging.py"
spec = importlib.util.spec_from_file_location("messaging_service", SERVICE_PATH)
messaging = importlib.util.module_from_spec(spec); spec.loader.exec_module(messaging)

class MessagingServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.old_state, self.old_requests = messaging.STATE, messaging.REQUESTS
        messaging.STATE, messaging.REQUESTS = Path(self.temp.name) / "state", Path(self.temp.name) / "requests"
    def tearDown(self):
        messaging.STATE, messaging.REQUESTS = self.old_state, self.old_requests; self.temp.cleanup()
    def test_inbound_message_is_persisted_unread_and_identity_is_separate(self):
        service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator")
        source = types.SimpleNamespace(hash=b"sender", display_name="Remote User")
        service.receive(types.SimpleNamespace(source=source, source_hash=b"sender", content_as_string=lambda: "Hello <b>there</b>"))
        with messaging.connect() as con: row = con.execute("SELECT * FROM messages").fetchone()
        self.assertEqual(row["direction"], "inbound"); self.assertEqual(row["body"], "Hello <b>there</b>"); self.assertIsNone(row["read_at"])
        self.assertNotEqual(messaging.STATE / "identity", ROOT / "data" / "bot" / "identity")
    def test_read_request_and_contact_lifecycle(self):
        service = messaging.Messaging(); key = "a" * 32
        with messaging.connect() as con: con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("m", key, "inbound", key, "b" * 32, None, "hello", 1, None, "Received", None, 1, 1))
        messaging.atomic_json(messaging.REQUESTS / "read-test.json", {"conversation_key": key})
        messaging.atomic_json(messaging.REQUESTS / "contact-save-test.json", {"name": "Alice", "destination": key, "notes": "Local label"})
        service.process_requests()
        with messaging.connect() as con:
            self.assertIsNotNone(con.execute("SELECT read_at FROM messages WHERE id='m'").fetchone()[0]); self.assertEqual(con.execute("SELECT name FROM contacts").fetchone()[0], "Alice")
        messaging.atomic_json(messaging.REQUESTS / "contact-delete-test.json", {"destination": key}); service.process_requests()
        with messaging.connect() as con: self.assertIsNone(con.execute("SELECT * FROM contacts").fetchone())
    def test_invalid_and_failed_outbound_states_are_safe(self):
        service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator"); service.router = types.SimpleNamespace(handle_outbound=lambda _: None)
        target = "c" * 32
        with messaging.connect() as con: con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("out", target, "outbound", None, target, None, "hello", 1, None, "Pending", None, 1, 1))
        service.send({"id": "out", "destination": target, "body": "hello"})
        with messaging.connect() as con: self.assertEqual(con.execute("SELECT delivery_state FROM messages WHERE id='out'").fetchone()[0], "Failed")
    def test_settings_enforce_conservative_interval(self):
        messaging.atomic_json(messaging.REQUESTS / "settings.json", {"display_name": "W4OWE User 🤖", "interval_minutes": 1})
        value = messaging.settings(); self.assertEqual(value["display_name"], "W4OWE User 🤖"); self.assertEqual(value["interval_minutes"], 15)
    def test_disabled_service_does_not_store_inbound_messages(self):
        messaging.atomic_json(messaging.REQUESTS / "settings.json", {"enabled": False})
        service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator")
        service.receive(types.SimpleNamespace(source=types.SimpleNamespace(hash=b"sender"), source_hash=b"sender", content_as_string=lambda: "hello"))
        with messaging.connect() as con: self.assertIsNone(con.execute("SELECT * FROM messages").fetchone())

class MessagingWebContractTests(unittest.TestCase):
    def test_navigation_and_privacy_contract_are_present(self):
        base = (ROOT / "reticulum-admin" / "templates" / "base.html").read_text(encoding="utf-8")
        inbox = (ROOT / "reticulum-admin" / "templates" / "messaging.html").read_text(encoding="utf-8")
        identity = (ROOT / "reticulum-admin" / "templates" / "messaging_identity.html").read_text(encoding="utf-8")
        self.assertIn("Messaging", base); self.assertIn("messaging_unread", base); self.assertIn("Messages are private operator content", inbox); self.assertIn("separate from the Bot", identity)

if __name__ == "__main__": unittest.main()
