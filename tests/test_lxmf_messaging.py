import importlib.util
import json
import os
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
        self.temp = tempfile.TemporaryDirectory(); self.old_state, self.old_requests = messaging.STATE, messaging.REQUESTS; self.old_vapid_subject = os.environ.get("WEB_PUSH_VAPID_SUBJECT")
        messaging.STATE, messaging.REQUESTS = Path(self.temp.name) / "state", Path(self.temp.name) / "requests"
        os.environ["WEB_PUSH_VAPID_SUBJECT"] = "https://push.example"
    def tearDown(self):
        messaging.STATE, messaging.REQUESTS = self.old_state, self.old_requests
        if self.old_vapid_subject is None: os.environ.pop("WEB_PUSH_VAPID_SUBJECT", None)
        else: os.environ["WEB_PUSH_VAPID_SUBJECT"] = self.old_vapid_subject
        self.temp.cleanup()
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

    def test_clear_observed_announce_history_is_service_owned_and_preserves_messages_contacts(self):
        service, destination = messaging.Messaging(), "a" * 32
        with messaging.connect() as con:
            con.execute("INSERT INTO announced_peers(destination_hash,identity_hash,display_name,message_display_name,first_seen,last_seen,last_hops,announce_count) VALUES(?,?,?,?,?,?,?,?)", (destination, None, "Observed peer", "Message name", 1, 2, 3, 4))
            con.execute("INSERT INTO application_announces(destination_hash,aspect,identity_hash,display_name,first_seen,last_seen,last_hops,announce_count) VALUES(?,?,?,?,?,?,?,?)", (destination, "nomadnetwork.node", None, "Observed node", 1, 2, 3, 4))
            con.execute("INSERT INTO contacts(name,destination,notes,created_at,updated_at) VALUES(?,?,?,?,?)", ("Kept contact", destination, None, 1, 1))
            con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("kept-message", destination, "inbound", destination, "b" * 32, None, "private message", 1, None, "Received", None, 1, 1))
        request = messaging.REQUESTS / "clear-announces-test.json"
        messaging.atomic_json(request, {"id": "clear-test", "action": "clear_observed_announces"})
        service.process_requests()
        with messaging.connect() as con:
            self.assertEqual(con.execute("SELECT count(*) FROM announced_peers").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT count(*) FROM application_announces").fetchone()[0], 0)
            self.assertEqual(con.execute("SELECT count(*) FROM contacts").fetchone()[0], 1)
            self.assertEqual(con.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
        self.assertFalse(request.exists())
        # A replay after an interruption is harmless and cannot erase messages.
        messaging.atomic_json(request, {"id": "clear-test", "action": "clear_observed_announces"})
        service.process_requests()
        with messaging.connect() as con: self.assertEqual(con.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
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
    def test_web_push_preview_and_subscription_validation_are_safe(self):
        self.assertEqual(messaging.web_push_preview(" <b>Hello</b>\n [world](https://example.test) *today* "), "Hello world today")
        self.assertEqual(len(messaging.web_push_preview("x" * 500)), 140)
        valid = {"endpoint": "https://push.example/subscription", "keys": {"p256dh": "abc_123", "auth": "key-456"}}
        self.assertTrue(messaging.subscription_is_valid(valid))
        self.assertFalse(messaging.subscription_is_valid({"endpoint": "http://not-https", "keys": valid["keys"]}))
        self.assertFalse(messaging.subscription_is_valid({"endpoint": valid["endpoint"], "keys": {"p256dh": "x"}}))

    def test_vapid_subject_accepts_https_or_mailto_and_rejects_localhost_without_fallback(self):
        os.environ["WEB_PUSH_VAPID_SUBJECT"] = "https://fserver.tail.de88c.ts.net"
        self.assertEqual(messaging.vapid_subject(), "https://fserver.tail.de88c.ts.net")
        os.environ["WEB_PUSH_VAPID_SUBJECT"] = "mailto:operator@example.net"
        self.assertEqual(messaging.vapid_subject(), "mailto:operator@example.net")
        self.assertEqual(messaging.push_navigate_url("a" * 32), "/messaging/conversations/" + "a" * 32)
        for invalid in ("", "https://localhost", "https://node.localhost", "http://example.net", "mailto:not-an-address", "not-a-uri"):
            os.environ["WEB_PUSH_VAPID_SUBJECT"] = invalid
            self.assertIsNone(messaging.vapid_subject())

    def test_push_uses_configured_subject_and_keeps_existing_key_and_subscription(self):
        class WebPushException(Exception): pass
        old = sys.modules.get("pywebpush"); calls = []
        sys.modules["pywebpush"] = types.SimpleNamespace(WebPushException=WebPushException, webpush=lambda subscription_info, **kwargs: calls.append(kwargs))
        try:
            service = messaging.Messaging()
            key = messaging.STATE / "webpush-vapid.pem"
            # Substitute a pre-existing key file: send_push never writes or
            # rotates it, and a stored endpoint stays present after a send.
            key.parent.mkdir(parents=True, exist_ok=True); key.write_text("existing-key", encoding="utf-8")
            with messaging.connect() as con: con.execute("INSERT INTO push_subscriptions VALUES(?,?,?,?)", ("https://push.example/device", json.dumps({"endpoint": "https://push.example/device", "keys": {"p256dh": "abc", "auth": "def"}}), 1, 1))
            os.environ["WEB_PUSH_VAPID_SUBJECT"] = "https://fserver.tail.de88c.ts.net"
            service.send_push("Remote", "hello", "a" * 32)
            self.assertEqual(calls[0]["vapid_claims"], {"sub": "https://fserver.tail.de88c.ts.net"})
            payload = json.loads(calls[0]["data"])
            self.assertEqual(payload["web_push"], 8030)
            self.assertEqual(payload["notification"]["title"], "Remote")
            self.assertEqual(payload["notification"]["navigate"], "https://fserver.tail.de88c.ts.net/messaging/conversations/" + "a" * 32)
            self.assertEqual(payload["app_badge"], "0")
            self.assertEqual(service.state["last_provider_result"], "Accepted")
            self.assertEqual(service.state["last_provider_http_status"], 201)
            self.assertEqual(service.state["provider_accepts"], 1)
            service.status()
            status = json.loads((messaging.STATE / "status.json").read_text(encoding="utf-8"))
            self.assertNotIn("https://push.example/device", json.dumps(status))
            self.assertNotIn("existing-key", json.dumps(status))
            restarted = messaging.Messaging()
            self.assertEqual(restarted.state["provider_accepts"], 1)
            self.assertEqual(restarted.state["last_provider_result"], "Accepted")
            self.assertEqual(key.read_text(encoding="utf-8"), "existing-key")
            with messaging.connect() as con: self.assertEqual(con.execute("SELECT count(*) FROM push_subscriptions").fetchone()[0], 1)
            os.environ["WEB_PUSH_VAPID_SUBJECT"] = "https://localhost"
            service.send_push("Remote", "hello", "a" * 32)
            self.assertEqual(len(calls), 1)
            self.assertIn("WEB_PUSH_VAPID_SUBJECT", service.state["last_error"])
        finally:
            if old is None: sys.modules.pop("pywebpush", None)
            else: sys.modules["pywebpush"] = old

    def test_web_push_stale_subscription_is_removed_but_transient_failure_is_retained(self):
        class Response: status_code = 410
        class WebPushException(Exception):
            def __init__(self, status): self.response = types.SimpleNamespace(status_code=status)
        old = sys.modules.get("pywebpush")
        calls = []
        def webpush(subscription_info, **kwargs):
            calls.append(subscription_info["endpoint"])
            if subscription_info["endpoint"].endswith("gone"): raise WebPushException(410)
            if subscription_info["endpoint"].endswith("gone404"): raise WebPushException(404)
            raise WebPushException(503)
        sys.modules["pywebpush"] = types.SimpleNamespace(WebPushException=WebPushException, webpush=webpush)
        try:
            service = messaging.Messaging()
            for endpoint in ("https://push.example/gone", "https://push.example/gone404", "https://push.example/temporary"):
                messaging.atomic_json(messaging.REQUESTS / ("push-subscribe-" + endpoint.rsplit("/", 1)[1] + ".json"), {"subscription": {"endpoint": endpoint, "keys": {"p256dh": "abc", "auth": "def"}}})
            service.process_requests(); service.send_push("Remote", "hello", "a" * 32)
            with messaging.connect() as con: endpoints = {row[0] for row in con.execute("SELECT endpoint FROM push_subscriptions")}
            self.assertEqual(calls, ["https://push.example/gone", "https://push.example/gone404", "https://push.example/temporary"])
            self.assertNotIn("https://push.example/gone", endpoints)
            self.assertNotIn("https://push.example/gone404", endpoints)
            self.assertIn("https://push.example/temporary", endpoints)
            self.assertEqual(service.state["last_provider_result"], "Rejected")
            self.assertEqual(service.state["last_provider_http_status"], 503)
            self.assertEqual(service.state["stale_subscriptions_removed"], 2)
            self.assertEqual(service.state["provider_failures"], 3)
        finally:
            if old is None: sys.modules.pop("pywebpush", None)
            else: sys.modules["pywebpush"] = old

    def test_duplicate_hashed_inbound_message_pushes_once(self):
        pushed = []
        service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator")
        service.send_push = lambda *args: pushed.append(args)
        source = types.SimpleNamespace(hash=b"sender", display_name="Remote")
        message = types.SimpleNamespace(source=source, source_hash=b"sender", hash=b"stable-message", content_as_string=lambda: "hello")
        service.receive(message); service.receive(message)
        with messaging.connect() as con: self.assertEqual(con.execute("SELECT count(*) FROM messages").fetchone()[0], 1)
        self.assertEqual(len(pushed), 1)
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

    def test_lxmf_delivery_announce_is_passively_persisted_and_updated(self):
        target = "a" * 32
        class Transport:
            PATHFINDER_M = 99
            @staticmethod
            def hops_to(_): return 3
        old_rns, old_lxmf = sys.modules.get("RNS"), sys.modules.get("LXMF")
        sys.modules["RNS"] = types.SimpleNamespace(Transport=Transport)
        sys.modules["LXMF"] = types.SimpleNamespace(display_name_from_app_data=lambda _: "First announced name")
        try:
            service = messaging.Messaging()
            service.receive_announce(bytes.fromhex(target), types.SimpleNamespace(hash=bytes.fromhex("b" * 32)), b"supported app data")
            with messaging.connect() as con:
                row = con.execute("SELECT * FROM announced_peers WHERE destination_hash=?", (target,)).fetchone()
            self.assertEqual(row["display_name"], "First announced name")
            self.assertEqual(row["identity_hash"], "b" * 32)
            self.assertEqual(row["last_hops"], 3)
            self.assertEqual(row["announce_count"], 1)
            Transport.hops_to = staticmethod(lambda _: Transport.PATHFINDER_M)
            service.receive_announce(bytes.fromhex(target), None, b"app data without a route")
            with messaging.connect() as con:
                row = con.execute("SELECT * FROM announced_peers WHERE destination_hash=?", (target,)).fetchone()
            self.assertIsNone(row["last_hops"])
            self.assertEqual(row["announce_count"], 2)
            sys.modules["LXMF"].display_name_from_app_data = lambda _: "Updated announced name"
            Transport.hops_to = staticmethod(lambda _: 1)
            service.receive_announce(bytes.fromhex(target), None, b"new supported app data")
            with messaging.connect() as con:
                row = con.execute("SELECT * FROM announced_peers WHERE destination_hash=?", (target,)).fetchone()
            self.assertEqual(row["display_name"], "Updated announced name")
            self.assertEqual(row["identity_hash"], "b" * 32)
            self.assertEqual(row["last_hops"], 1)
            self.assertEqual(row["announce_count"], 3)
            self.assertEqual(service.state["lxmf_delivery_announces_observed"], 3)
            self.assertIsNotNone(service.state["last_lxmf_delivery_announce"])
            service.status()
            status = json.loads((messaging.STATE / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["lxmf_delivery_announces_observed"], 3)
            self.assertEqual(status["unique_lxmf_delivery_peers"], 1)
        finally:
            if old_rns is None: sys.modules.pop("RNS", None)
            else: sys.modules["RNS"] = old_rns
            if old_lxmf is None: sys.modules.pop("LXMF", None)
            else: sys.modules["LXMF"] = old_lxmf

    def test_received_sender_name_is_persisted_as_informational_peer_metadata(self):
        target = "d" * 32
        service = messaging.Messaging(); service.destination = types.SimpleNamespace(hash=b"operator")
        source = types.SimpleNamespace(hash=bytes.fromhex(target), display_name="VM Chat Name")
        service.receive(types.SimpleNamespace(source=source, source_hash=source.hash, content_as_string=lambda: "hello"))
        with messaging.connect() as con:
            row = con.execute("SELECT message_display_name,announce_count FROM announced_peers WHERE destination_hash=?", (target,)).fetchone()
        self.assertEqual(row["message_display_name"], "VM Chat Name")
        self.assertEqual(row["announce_count"], 0)

    def test_invalid_announce_metadata_is_safe_and_listener_is_aspect_limited(self):
        registered = []
        class Transport:
            PATHFINDER_M = 99
            @staticmethod
            def hops_to(_): return 99
            @staticmethod
            def register_announce_handler(handler): registered.append(handler)
        old_rns, old_lxmf = sys.modules.get("RNS"), sys.modules.get("LXMF")
        sys.modules["RNS"] = types.SimpleNamespace(Transport=Transport)
        sys.modules["LXMF"] = types.SimpleNamespace(display_name_from_app_data=lambda _: (_ for _ in ()).throw(ValueError("bad public metadata")))
        try:
            service = messaging.Messaging()
            service.register_announce_listener(sys.modules["RNS"])
            self.assertEqual({handler.aspect_filter for handler in registered}, {"lxmf.delivery", "lxmf.propagation", "nomadnetwork.node"})
            registered[0].received_announce(bytes.fromhex("c" * 32), None, b"bad", None)
            with messaging.connect() as con:
                row = con.execute("SELECT display_name,last_hops FROM announced_peers").fetchone()
            self.assertEqual(tuple(row), (None, None))
            self.assertIsNone(service.router)
            self.assertIsNone(service.destination)
        finally:
            if old_rns is None: sys.modules.pop("RNS", None)
            else: sys.modules["RNS"] = old_rns
            if old_lxmf is None: sys.modules.pop("LXMF", None)
            else: sys.modules["LXMF"] = old_lxmf

    def test_nomad_and_propagation_announces_are_passive_and_durable(self):
        nomad, propagation = "e" * 32, "f" * 32
        class Transport:
            PATHFINDER_M = 99
            @staticmethod
            def hops_to(value): return 2 if value == bytes.fromhex(nomad) else 99
        old_rns, old_lxmf = sys.modules.get("RNS"), sys.modules.get("LXMF")
        sys.modules["RNS"] = types.SimpleNamespace(Transport=Transport)
        sys.modules["LXMF"] = types.SimpleNamespace(display_name_from_app_data=lambda _: "LXMF Name")
        try:
            service = messaging.Messaging()
            service.receive_application_announce("nomadnetwork.node", bytes.fromhex(nomad), None, b"Alabama Mesh Node")
            service.receive_application_announce("lxmf.propagation", bytes.fromhex(propagation), None, b"not decoded")
            # Bad UTF-8 remains a useful observation without inventing a name.
            service.receive_application_announce("nomadnetwork.node", bytes.fromhex(nomad), None, b"\xff")
            with messaging.connect() as con:
                rows = {row["aspect"]: row for row in con.execute("SELECT * FROM application_announces WHERE destination_hash IN (?,?)", (nomad, propagation))}
            self.assertEqual(rows["nomadnetwork.node"]["display_name"], "Alabama Mesh Node")
            self.assertEqual(rows["nomadnetwork.node"]["last_hops"], 2)
            self.assertEqual(rows["nomadnetwork.node"]["announce_count"], 2)
            self.assertIsNone(rows["lxmf.propagation"]["display_name"])
            self.assertIsNone(rows["lxmf.propagation"]["last_hops"])
            self.assertIsNone(service.router); self.assertIsNone(service.destination)
            service.status()
            status = json.loads((messaging.STATE / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(status["application_announces_observed"], 3)
            self.assertEqual(status["application_announce_counts"]["nomadnetwork.node"], 2)
            self.assertEqual(status["application_announce_counts"]["lxmf.propagation"], 1)
            self.assertEqual(status["unique_application_announce_destinations"], 2)
        finally:
            if old_rns is None: sys.modules.pop("RNS", None)
            else: sys.modules["RNS"] = old_rns
            if old_lxmf is None: sys.modules.pop("LXMF", None)
            else: sys.modules["LXMF"] = old_lxmf

class MessagingWebContractTests(unittest.TestCase):
    def test_navigation_and_privacy_contract_are_present(self):
        base = (ROOT / "reticulum-admin" / "templates" / "base.html").read_text(encoding="utf-8")
        inbox = (ROOT / "reticulum-admin" / "templates" / "messaging.html").read_text(encoding="utf-8")
        compose = (ROOT / "reticulum-admin" / "templates" / "messaging_new.html").read_text(encoding="utf-8")
        identity = (ROOT / "reticulum-admin" / "templates" / "messaging_identity.html").read_text(encoding="utf-8")
        worker = (ROOT / "reticulum-admin" / "static" / "service-worker.js").read_text(encoding="utf-8")
        offline = (ROOT / "reticulum-admin" / "static" / "offline.html").read_text(encoding="utf-8")
        manifest = (ROOT / "reticulum-admin" / "static" / "manifest.webmanifest").read_text(encoding="utf-8")
        self.assertIn("Messaging", base); self.assertIn("messaging_unread", base); self.assertIn("Conversations", inbox); self.assertIn("Announces", inbox); self.assertIn("message-bubble", inbox); self.assertIn("maxlength=\"4000\"", inbox); self.assertNotIn("Add Files", inbox); self.assertNotIn("Add Image", inbox); self.assertNotIn("Add Voice", inbox); self.assertIn("separate from the Bot", identity)
        self.assertIn("Notifications", identity); self.assertIn("Enable Notifications", identity); self.assertIn("Disable Notifications", identity); self.assertIn("Web Push configured", identity); self.assertIn("WEB_PUSH_VAPID_SUBJECT", identity)
        self.assertIn('"display":"standalone"', manifest); self.assertIn("notificationclick", worker); self.assertIn("payload.notification", worker); self.assertIn("notification.navigate", worker); self.assertIn("showNotification", worker); self.assertIn("foreground-unread", worker); self.assertIn("setAppBadge", worker); self.assertNotIn("webpush-vapid.pem", worker)
        self.assertIn("Connect Tailscale, then tap Retry.", offline); self.assertIn("target", offline); self.assertIn("fetch(target", offline)

    def test_chat_layout_has_responsive_mobile_and_plain_text_contracts(self):
        style = (ROOT / "reticulum-admin" / "static" / "app.css").read_text(encoding="utf-8")
        inbox = (ROOT / "reticulum-admin" / "templates" / "messaging.html").read_text(encoding="utf-8")
        compose = (ROOT / "reticulum-admin" / "templates" / "messaging_new.html").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 700px)", style)
        self.assertIn(".chat-workspace.has-selection .chat-sidebar", style)
        self.assertIn(".chat-workspace.has-selection { display: flex; height: calc(100dvh - 10rem); min-height: 0; overflow: hidden; }", style)
        self.assertIn(".chat-workspace.has-selection .message-stream { flex: 1 1 auto; min-height: 0; max-height: none; overflow-y: auto; -webkit-overflow-scrolling: touch; }", style)
        self.assertIn(".chat-workspace.has-selection .chat-composer { position: relative; flex: 0 0 auto; }", style)
        self.assertIn(".chat-composer", style)
        self.assertIn("white-space: pre-wrap", style)
        self.assertIn("Announced and received names are informational, not proof of identity.", inbox)
        self.assertIn("stream.scrollTop = stream.scrollHeight", inbox)
        self.assertIn("const previousTop = stream.scrollTop", inbox)
        self.assertIn("stream.scrollTop = previousTop", inbox)
        self.assertIn("event.isComposing", inbox)
        self.assertIn("!event.shiftKey", inbox)
        self.assertIn("composer.requestSubmit()", inbox)
        self.assertIn("/messaging/api/conversations/", inbox)
        self.assertIn("reticulum-admin-pending-", inbox)
        self.assertIn("reticulum-admin-pending-", compose)
        self.assertIn("2000", inbox); self.assertIn("5000", inbox)
        self.assertIn("document.visibilityState", inbox)
        self.assertIn("/read", inbox)
        self.assertNotIn("column-reverse", style)

    def test_workspace_groups_conversations_and_prefers_contact_then_announce_name(self):
        old_env = {key: os.environ.get(key) for key in ("APP_DATA", "MESSAGING_DIR", "MESSAGING_REQUEST_DIR", "ADMIN_SECRET_KEY")}
        old_path = list(sys.path)
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "messaging"
            original_state, original_requests = messaging.STATE, messaging.REQUESTS
            messaging.STATE, messaging.REQUESTS = state, Path(temporary) / "requests"
            try:
                with messaging.connect() as con:
                    con.execute("INSERT INTO announced_peers(destination_hash,identity_hash,display_name,message_display_name,first_seen,last_seen,last_hops,announce_count) VALUES(?,?,?,?,?,?,?,?)", ("a" * 32, None, "Announced Alice", "Received Alice", 1, 1, 1, 1))
                    con.execute("INSERT INTO contacts(name,destination,notes,created_at,updated_at) VALUES(?,?,?,?,?)", ("Saved Alice", "a" * 32, None, 1, 1))
                    con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("first", "a" * 32, "inbound", "a" * 32, "operator", None, "older", 10, None, "Received", None, 10, 10))
                    con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("newest", "a" * 32, "outbound", "operator", "a" * 32, None, "newest preview", 30, None, "Queued", None, 30, 30))
                    con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("other", "b" * 32, "inbound", "b" * 32, "operator", None, "other conversation", 20, None, "Received", None, 20, 20))
                os.environ.update({"APP_DATA": str(Path(temporary) / "admin"), "MESSAGING_DIR": str(state), "MESSAGING_REQUEST_DIR": str(Path(temporary) / "requests"), "ADMIN_SECRET_KEY": "test-secret"})
                sys.path.insert(0, str(ROOT / "reticulum-admin"))
                app_spec = importlib.util.spec_from_file_location("messaging_workspace_app", ROOT / "reticulum-admin" / "app.py")
                app_module = importlib.util.module_from_spec(app_spec); app_spec.loader.exec_module(app_module)
                # The dynamic test import has no package root; point Flask at
                # the real static directory before exercising the PWA routes.
                app_module.app.static_folder = str(ROOT / "reticulum-admin" / "static")
                rows = app_module.messaging_conversations()
                self.assertEqual([row["conversation_key"] for row in rows], ["a" * 32, "b" * 32])
                self.assertEqual(rows[0]["body"], "newest preview")
                self.assertEqual(rows[0]["contact_name"], "Saved Alice")
                self.assertEqual(rows[0]["peer_name"], "Announced Alice")
                self.assertEqual(rows[1]["contact_name"], None)
                workspace = app_module.messaging_workspace_json()
                self.assertEqual(workspace["conversations"][0]["name"], "Saved Alice")
                # The local contact is strongest, followed by public announce
                # metadata, then an informational name supplied with a message.
                with messaging.connect() as con:
                    con.execute("DELETE FROM contacts WHERE destination=?", ("a" * 32,))
                self.assertEqual(app_module.messaging_workspace_json()["conversations"][0]["name"], "Announced Alice")
                with messaging.connect() as con:
                    con.execute("UPDATE announced_peers SET display_name=NULL WHERE destination_hash=?", ("a" * 32,))
                self.assertEqual(app_module.messaging_workspace_json()["conversations"][0]["name"], "Received Alice")
                anonymous = app_module.app.test_client()
                self.assertEqual(anonymous.post("/messaging/api/conversations/" + "a" * 32 + "/read").status_code, 302)
                client = app_module.app.test_client()
                self.assertEqual(client.get("/manifest.webmanifest").status_code, 200)
                self.assertEqual(client.get("/service-worker.js").status_code, 200)
                self.assertEqual(client.get("/static/offline.html").status_code, 200)
                self.assertEqual(client.get("/messaging/api/conversations/" + "a" * 32).status_code, 302)
                with client.session_transaction() as session:
                    session["admin"] = "test-admin"
                response = client.get("/messaging/api/conversations/" + "a" * 32)
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(len(response.get_json()["messages"]), 200)
                self.assertEqual([row["id"] for row in response.get_json()["messages"]], ["first", "newest"])
                self.assertEqual(response.get_json()["unread_inbound"], 1)
                # Polling GETs remain read-only. A visible conversation instead
                # asks the service-owned request path to mark just that thread.
                with messaging.connect() as con:
                    self.assertIsNone(con.execute("SELECT read_at FROM messages WHERE id='first'").fetchone()[0])
                    self.assertIsNone(con.execute("SELECT read_at FROM messages WHERE id='other'").fetchone()[0])
                self.assertEqual(client.post("/messaging/api/conversations/" + "a" * 32 + "/read").status_code, 400)
                with client.session_transaction() as session:
                    session["csrf"] = "read-csrf"
                subscription = {"endpoint": "https://push.example/current-device", "keys": {"p256dh": "abc_123", "auth": "def_456"}}
                self.assertEqual(client.post("/messaging/api/push-subscriptions", json={"subscription": subscription}).status_code, 400)
                self.assertEqual(client.post("/messaging/api/push-subscriptions", json={"subscription": subscription, "csrf_token": "read-csrf"}).status_code, 202)
                subscribe_files = list((Path(temporary) / "requests").glob("push-subscribe-*.json"))
                self.assertEqual(len(subscribe_files), 1)
                self.assertEqual(json.loads(subscribe_files[0].read_text())["subscription"]["endpoint"], subscription["endpoint"])
                self.assertEqual(client.delete("/messaging/api/push-subscriptions", json={"endpoint": subscription["endpoint"], "csrf_token": "read-csrf"}).status_code, 202)
                unsubscribe_files = list((Path(temporary) / "requests").glob("push-unsubscribe-*.json"))
                self.assertEqual(len(unsubscribe_files), 1)
                self.assertEqual(json.loads(unsubscribe_files[0].read_text())["endpoint"], subscription["endpoint"])
                self.assertEqual(client.post("/messaging/api/push-subscriptions", json={"subscription": {"endpoint": "https://x", "keys": {"p256dh": "", "auth": "x"}}, "csrf_token": "read-csrf"}).status_code, 400)
                self.assertEqual(client.post("/messaging/api/conversations/" + "a" * 32 + "/read", data={"csrf_token": "wrong"}).status_code, 400)
                self.assertEqual(client.post("/messaging/api/conversations/" + "a" * 32 + "/read", data={"csrf_token": "read-csrf"}).status_code, 204)
                request_file = Path(temporary) / "requests" / ("read-" + "a" * 32 + ".json")
                self.assertEqual(json.loads(request_file.read_text(encoding="utf-8")), {"conversation_key": "a" * 32})
                with messaging.connect() as con:
                    self.assertIsNone(con.execute("SELECT read_at FROM messages WHERE id='first'").fetchone()[0])
                messaging.Messaging().process_requests()
                with messaging.connect() as con:
                    self.assertIsNotNone(con.execute("SELECT read_at FROM messages WHERE id='first'").fetchone()[0])
                    self.assertIsNone(con.execute("SELECT read_at FROM messages WHERE id='other'").fetchone()[0])
                    con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", ("late-inbound", "a" * 32, "inbound", "a" * 32, "operator", "Late Alice", "appears through polling", 40, None, "Received", None, 40, 40))
                polled = client.get("/messaging/api/conversations/" + "a" * 32).get_json()
                self.assertTrue(any(row["id"] == "late-inbound" for row in polled["messages"]))
                self.assertEqual(polled["unread_inbound"], 1)
                # The polling payload is bounded even for a busy conversation,
                # and it reflects durable outbound state changes without a page reload.
                with messaging.connect() as con:
                    for index in range(205):
                        con.execute("INSERT INTO messages VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (f"cap-{index}", "c" * 32, "outbound", "operator", "c" * 32, None, f"queued {index}", index, None, "Queued", None, index, index))
                    con.execute("UPDATE messages SET delivery_state='Delivered' WHERE id='cap-204'")
                    con.execute("INSERT INTO announced_peers(destination_hash,identity_hash,display_name,message_display_name,first_seen,last_seen,last_hops,announce_count) VALUES(?,?,?,?,?,?,?,?)", ("e" * 32, None, "Polled announce", None, 1, 2, 1, 1))
                capped = client.get("/messaging/api/conversations/" + "c" * 32).get_json()
                self.assertEqual(len(capped["messages"]), 200)
                self.assertEqual(capped["messages"][-1]["delivery_state"], "Delivered")
                announces = client.get("/messaging/api/workspace?tab=announces").get_json()
                self.assertTrue(any(peer["name"] == "Polled announce" for peer in announces["peers"]))
            finally:
                messaging.STATE, messaging.REQUESTS = original_state, original_requests
                sys.path[:] = old_path
                for key, value in old_env.items():
                    if value is None: os.environ.pop(key, None)
                    else: os.environ[key] = value

if __name__ == "__main__": unittest.main()
