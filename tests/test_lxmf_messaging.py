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
            self.assertEqual(len(registered), 1)
            self.assertEqual(registered[0].aspect_filter, "lxmf.delivery")
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

class MessagingWebContractTests(unittest.TestCase):
    def test_navigation_and_privacy_contract_are_present(self):
        base = (ROOT / "reticulum-admin" / "templates" / "base.html").read_text(encoding="utf-8")
        inbox = (ROOT / "reticulum-admin" / "templates" / "messaging.html").read_text(encoding="utf-8")
        compose = (ROOT / "reticulum-admin" / "templates" / "messaging_new.html").read_text(encoding="utf-8")
        identity = (ROOT / "reticulum-admin" / "templates" / "messaging_identity.html").read_text(encoding="utf-8")
        self.assertIn("Messaging", base); self.assertIn("messaging_unread", base); self.assertIn("Conversations", inbox); self.assertIn("Announces", inbox); self.assertIn("message-bubble", inbox); self.assertIn("maxlength=\"4000\"", inbox); self.assertNotIn("Add Files", inbox); self.assertNotIn("Add Image", inbox); self.assertNotIn("Add Voice", inbox); self.assertIn("separate from the Bot", identity)

    def test_chat_layout_has_responsive_mobile_and_plain_text_contracts(self):
        style = (ROOT / "reticulum-admin" / "static" / "app.css").read_text(encoding="utf-8")
        inbox = (ROOT / "reticulum-admin" / "templates" / "messaging.html").read_text(encoding="utf-8")
        compose = (ROOT / "reticulum-admin" / "templates" / "messaging_new.html").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 700px)", style)
        self.assertIn(".chat-workspace.has-selection .chat-sidebar", style)
        self.assertIn(".chat-composer", style)
        self.assertIn("white-space: pre-wrap", style)
        self.assertIn("Announced and received names are informational, not proof of identity.", inbox)
        self.assertIn("stream.scrollTop = stream.scrollHeight", inbox)
        self.assertIn("event.isComposing", inbox)
        self.assertIn("!event.shiftKey", inbox)
        self.assertIn("composer.requestSubmit()", inbox)
        self.assertIn("/messaging/api/conversations/", inbox)
        self.assertIn("reticulum-admin-pending-", inbox)
        self.assertIn("reticulum-admin-pending-", compose)
        self.assertIn("2000", inbox); self.assertIn("5000", inbox)
        self.assertIn("document.visibilityState", inbox)
        self.assertIn("/read", inbox)

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
                self.assertEqual(client.get("/messaging/api/conversations/" + "a" * 32).status_code, 302)
                with client.session_transaction() as session:
                    session["admin"] = "test-admin"
                response = client.get("/messaging/api/conversations/" + "a" * 32)
                self.assertEqual(response.status_code, 200)
                self.assertLessEqual(len(response.get_json()["messages"]), 200)
                self.assertEqual(response.get_json()["unread_inbound"], 1)
                # Polling GETs remain read-only. A visible conversation instead
                # asks the service-owned request path to mark just that thread.
                with messaging.connect() as con:
                    self.assertIsNone(con.execute("SELECT read_at FROM messages WHERE id='first'").fetchone()[0])
                    self.assertIsNone(con.execute("SELECT read_at FROM messages WHERE id='other'").fetchone()[0])
                self.assertEqual(client.post("/messaging/api/conversations/" + "a" * 32 + "/read").status_code, 400)
                with client.session_transaction() as session:
                    session["csrf"] = "read-csrf"
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
