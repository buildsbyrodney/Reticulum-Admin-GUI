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
    def test_outbound_request_is_persisted_before_request_is_deleted_and_replay_is_idempotent(self):
        service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator"); service.router = types.SimpleNamespace(handle_outbound=lambda _: None)
        target = "c" * 32
        messaging.atomic_json(messaging.REQUESTS / "outbound-out.json", {"id": "out", "destination": target, "body": "hello"})
        service.process_requests()
        with messaging.connect() as con:
            row = con.execute("SELECT * FROM messages WHERE id='out'").fetchone()
            self.assertEqual(con.execute("SELECT count(*) FROM messages WHERE id='out'").fetchone()[0], 1)
        self.assertEqual(row["conversation_key"], target); self.assertEqual(row["recipient_destination"], target); self.assertEqual(row["body"], "hello"); self.assertEqual(row["delivery_state"], "Failed")
        self.assertFalse((messaging.REQUESTS / "outbound-out.json").exists())
        service.send({"id": "out", "destination": target, "body": "hello"})
        with messaging.connect() as con: self.assertEqual(con.execute("SELECT count(*) FROM messages WHERE id='out'").fetchone()[0], 1)

    def test_known_and_discovered_paths_send_without_false_delivery_claims(self):
        target = "d" * 32; sent = []
        class Identity:
            calls = 0
            @staticmethod
            def recall(_):
                Identity.calls += 1
                return object() if Identity.calls >= 2 else None
        class Transport:
            requested = []
            @staticmethod
            def request_path(value): Transport.requested.append(value)
        class Destination:
            OUT = SINGLE = 1
            def __init__(self, *args): self.args = args
        class LXMessage:
            DIRECT = "direct"
            def __init__(self, *args, **kwargs): self.delivered = self.failed = None
            def register_delivery_callback(self, callback): self.delivered = callback
            def register_failed_callback(self, callback): self.failed = callback
        old_rns, old_lxmf, old_wait = sys.modules.get("RNS"), sys.modules.get("LXMF"), messaging.PATH_LOOKUP_SECONDS
        sys.modules["RNS"] = types.SimpleNamespace(Identity=Identity, Transport=Transport, Destination=Destination); sys.modules["LXMF"] = types.SimpleNamespace(LXMessage=LXMessage); messaging.PATH_LOOKUP_SECONDS = 1
        try:
            service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator"); service.router = types.SimpleNamespace(handle_outbound=lambda item: sent.append(item))
            service.send({"id": "known", "destination": target, "body": "hello"})
            with messaging.connect() as con: self.assertEqual(con.execute("SELECT delivery_state FROM messages WHERE id='known'").fetchone()[0], "Queued")
            self.assertEqual(len(sent), 1); self.assertEqual(Transport.requested, [bytes.fromhex(target)])
            # Simulate a crash after the durable insert but before the worker
            # could unlink its request file. Replay removes the request but
            # must not create a second row or submit a second LXMF message.
            replay = messaging.REQUESTS / "outbound-known-replay.json"
            messaging.atomic_json(replay, {"id": "known", "destination": target, "body": "hello"})
            service.process_requests()
            self.assertFalse(replay.exists()); self.assertEqual(len(sent), 1)
            with messaging.connect() as con: self.assertEqual(con.execute("SELECT count(*) FROM messages WHERE id='known'").fetchone()[0], 1)
            sent[0].delivered(sent[0])
            with messaging.connect() as con: self.assertEqual(con.execute("SELECT delivery_state FROM messages WHERE id='known'").fetchone()[0], "Delivered")
        finally:
            messaging.PATH_LOOKUP_SECONDS = old_wait
            if old_rns is None: sys.modules.pop("RNS", None)
            else: sys.modules["RNS"] = old_rns
            if old_lxmf is None: sys.modules.pop("LXMF", None)
            else: sys.modules["LXMF"] = old_lxmf

    def test_delivery_and_failure_callbacks_update_the_correct_persistent_row(self):
        target = "a" * 32; submitted = []
        class Identity:
            @staticmethod
            def recall(_): return object()
        class Destination:
            OUT = SINGLE = 1
            def __init__(self, *args): pass
        class LXMessage:
            DIRECT = "direct"
            def __init__(self, *args, **kwargs): self.delivery = self.failed = None
            def register_delivery_callback(self, callback): self.delivery = callback
            def register_failed_callback(self, callback): self.failed = callback
        old_rns, old_lxmf = sys.modules.get("RNS"), sys.modules.get("LXMF")
        sys.modules["RNS"] = types.SimpleNamespace(Identity=Identity, Transport=types.SimpleNamespace(request_path=lambda _: None), Destination=Destination)
        sys.modules["LXMF"] = types.SimpleNamespace(LXMessage=LXMessage)
        try:
            service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator"); service.router = types.SimpleNamespace(handle_outbound=lambda item: submitted.append(item))
            service.send({"id": "delivery", "destination": target, "body": "first"})
            service.send({"id": "failure", "destination": target, "body": "second"})
            submitted[0].delivery(submitted[0]); submitted[1].failed(submitted[1])
            with messaging.connect() as con:
                self.assertEqual(con.execute("SELECT delivery_state FROM messages WHERE id='delivery'").fetchone()[0], "Delivered")
                failure = con.execute("SELECT delivery_state,error FROM messages WHERE id='failure'").fetchone()
            self.assertEqual(failure["delivery_state"], "Failed"); self.assertIn("LXMF delivery failed", failure["error"])
            self.assertIsNotNone(service.state["last_message_sent"])
        finally:
            if old_rns is None: sys.modules.pop("RNS", None)
            else: sys.modules["RNS"] = old_rns
            if old_lxmf is None: sys.modules.pop("LXMF", None)
            else: sys.modules["LXMF"] = old_lxmf

    def test_new_process_recovers_pending_and_queued_rows_but_not_terminal_rows(self):
        target = "b" * 32; submitted = []
        class Identity:
            @staticmethod
            def recall(_): return object()
        class Destination:
            OUT = SINGLE = 1
            def __init__(self, *args): pass
        class LXMessage:
            DIRECT = "direct"
            def __init__(self, *args, **kwargs): self.content = args[2]; self.delivery = self.failed = None
            def register_delivery_callback(self, callback): self.delivery = callback
            def register_failed_callback(self, callback): self.failed = callback
        old_rns, old_lxmf = sys.modules.get("RNS"), sys.modules.get("LXMF")
        sys.modules["RNS"] = types.SimpleNamespace(Identity=Identity, Transport=types.SimpleNamespace(request_path=lambda _: None), Destination=Destination)
        sys.modules["LXMF"] = types.SimpleNamespace(LXMessage=LXMessage)
        try:
            first_process = messaging.Messaging()
            stamp = 1
            with messaging.connect() as con:
                for message_id, state, body in [("pending", "Pending", "retry pending"), ("queued", "Queued", "retry queued"), ("delivered", "Delivered", "do not retry"), ("failed", "Failed", "do not retry")]:
                    con.execute("INSERT INTO messages(id,conversation_key,direction,sender_destination,recipient_destination,body,received_at,delivery_state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (message_id, target, "outbound", "operator", target, body, stamp, state, stamp, stamp))
            recovered_process = messaging.Messaging()
            self.assertNotEqual(first_process.session_id, recovered_process.session_id)
            recovered_process.destination = types.SimpleNamespace(hash=b"operator")
            recovered_process.router = types.SimpleNamespace(handle_outbound=lambda item: submitted.append(item))
            recovered_process.recover_interrupted_outbound()
            self.assertEqual([message.content for message in submitted], ["retry pending", "retry queued"])
            with messaging.connect() as con:
                rows = {row["id"]: row for row in con.execute("SELECT id,conversation_key,body,delivery_state FROM messages")}
            self.assertEqual(rows["pending"]["conversation_key"], target); self.assertEqual(rows["queued"]["body"], "retry queued")
            self.assertEqual(rows["pending"]["delivery_state"], "Queued"); self.assertEqual(rows["queued"]["delivery_state"], "Queued")
            self.assertEqual(rows["delivered"]["delivery_state"], "Delivered"); self.assertEqual(rows["failed"]["delivery_state"], "Failed")
            # Callback after a restart recovery updates the original durable row.
            submitted[1].delivery(submitted[1])
            with messaging.connect() as con: self.assertEqual(con.execute("SELECT delivery_state FROM messages WHERE id='queued'").fetchone()[0], "Delivered")
        finally:
            if old_rns is None: sys.modules.pop("RNS", None)
            else: sys.modules["RNS"] = old_rns
            if old_lxmf is None: sys.modules.pop("LXMF", None)
            else: sys.modules["LXMF"] = old_lxmf
    def test_settings_enforce_conservative_interval(self):
        messaging.atomic_json(messaging.REQUESTS / "settings.json", {"display_name": "W4OWE User 🤖", "interval_minutes": 1})
        value = messaging.settings(); self.assertEqual(value["display_name"], "W4OWE User 🤖"); self.assertEqual(value["interval_minutes"], 15)
    def test_disabled_service_does_not_store_inbound_messages(self):
        messaging.atomic_json(messaging.REQUESTS / "settings.json", {"enabled": False})
        service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator")
        service.receive(types.SimpleNamespace(source=types.SimpleNamespace(hash=b"sender"), source_hash=b"sender", content_as_string=lambda: "hello"))
        with messaging.connect() as con: self.assertIsNone(con.execute("SELECT * FROM messages").fetchone())
    def test_destination_is_exactly_32_hex_characters(self):
        self.assertTrue(messaging.DESTINATION_RE.fullmatch("a" * 32)); self.assertFalse(messaging.DESTINATION_RE.fullmatch("a" * 64))
    def test_path_resolution_timeout_records_route_unavailable(self):
        target = "e" * 32
        class Identity:
            @staticmethod
            def recall(_): return None
        class Transport:
            requested = []
            @staticmethod
            def request_path(value): Transport.requested.append(value)
        old_rns, old_lxmf, old_wait = sys.modules.get("RNS"), sys.modules.get("LXMF"), messaging.PATH_LOOKUP_SECONDS
        sys.modules["RNS"] = types.SimpleNamespace(Identity=Identity, Transport=Transport); sys.modules["LXMF"] = types.SimpleNamespace()
        messaging.PATH_LOOKUP_SECONDS = 0
        try:
            service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator")
            service.send({"id": "timeout", "destination": target, "body": "hello"})
            with messaging.connect() as con: row = con.execute("SELECT delivery_state,error FROM messages WHERE id='timeout'").fetchone()
            self.assertEqual(row["delivery_state"], "Failed"); self.assertIn("route", row["error"].lower()); self.assertEqual(Transport.requested, [bytes.fromhex(target)])
        finally:
            messaging.PATH_LOOKUP_SECONDS = old_wait
            if old_rns is None: sys.modules.pop("RNS", None)
            else: sys.modules["RNS"] = old_rns
            if old_lxmf is None: sys.modules.pop("LXMF", None)
            else: sys.modules["LXMF"] = old_lxmf

    def test_lxmf_submission_failure_is_not_reported_as_a_route_timeout(self):
        target = "f" * 32
        class Identity:
            @staticmethod
            def recall(_): return object()
        class Destination:
            OUT = SINGLE = 1
            def __init__(self, *args): pass
        class LXMessage:
            DIRECT = "direct"
            def __init__(self, *args, **kwargs): raise RuntimeError("not shown to the operator")
        old_rns, old_lxmf = sys.modules.get("RNS"), sys.modules.get("LXMF")
        sys.modules["RNS"] = types.SimpleNamespace(Identity=Identity, Transport=types.SimpleNamespace(request_path=lambda _: None), Destination=Destination)
        sys.modules["LXMF"] = types.SimpleNamespace(LXMessage=LXMessage)
        try:
            service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator"); service.router = types.SimpleNamespace(handle_outbound=lambda _: None)
            service.send({"id": "submission", "destination": target, "body": "hello"})
            with messaging.connect() as con: row = con.execute("SELECT delivery_state,error FROM messages WHERE id='submission'").fetchone()
            self.assertEqual(row["delivery_state"], "Failed"); self.assertEqual(row["error"], "LXMF submission failed.")
        finally:
            if old_rns is None: sys.modules.pop("RNS", None)
            else: sys.modules["RNS"] = old_rns
            if old_lxmf is None: sys.modules.pop("LXMF", None)
            else: sys.modules["LXMF"] = old_lxmf

class MessagingWebContractTests(unittest.TestCase):
    def test_navigation_and_privacy_contract_are_present(self):
        base = (ROOT / "reticulum-admin" / "templates" / "base.html").read_text(encoding="utf-8")
        inbox = (ROOT / "reticulum-admin" / "templates" / "messaging.html").read_text(encoding="utf-8")
        identity = (ROOT / "reticulum-admin" / "templates" / "messaging_identity.html").read_text(encoding="utf-8")
        self.assertIn("Messaging", base); self.assertIn("messaging_unread", base); self.assertIn("Messages are private operator content", inbox); self.assertIn("separate from the Bot", identity)

if __name__ == "__main__": unittest.main()
