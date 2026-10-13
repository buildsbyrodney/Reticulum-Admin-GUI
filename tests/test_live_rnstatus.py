import importlib
import json
import os
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path


ROOT = Path(__file__).parents[1]
APP_DIR = ROOT / "reticulum-admin"
FIXTURE = ROOT / "tests" / "fixtures" / "rnstatus-1.5.7-pi.json"
TCP_CHILD_FIXTURE = ROOT / "tests" / "fixtures" / "rnstatus-1.5.7-tcp-child.json"


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

    def test_bot_is_a_top_level_navigation_item_not_a_diagnostics_child(self):
        page = self.client().get("/diagnostics/bot").get_data(as_text=True)
        navigation = page.split("<nav>", 1)[1].split("</nav>", 1)[0]
        self.assertIn('<a href="/diagnostics">Diagnostics</a>', navigation)
        self.assertIn('<a href="/diagnostics/bot">Bot</a>', navigation)
        self.assertEqual(navigation.count('href="/diagnostics/bot"'), 1)
        self.assertNotIn("Test Echo Bot", navigation)

    def test_bot_v2_page_explains_local_read_only_commands_and_tagline(self):
        page = self.client().get("/diagnostics/bot").get_data(as_text=True)
        for expected in ("Announce Tagline", "Send HELP for commands", "MENU", "VERSE John 3:16", "read-only", "does not grant remote Admin GUI access"):
            self.assertIn(expected, page)

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

    def test_interface_preview_shows_agent_validation_error_not_unknown_result(self):
        """A rejected preview must say why it was rejected; nothing was applied."""
        client = self.client()
        original_urlopen = self.app_module.urllib.request.urlopen
        original_token = os.environ.get("MANAGEMENT_TOKEN")
        original_url = os.environ.get("MANAGEMENT_URL")
        os.environ["MANAGEMENT_TOKEN"] = "test-management-token"
        os.environ["MANAGEMENT_URL"] = "http://management-agent:8081"
        response_body = json.dumps({
            "ok": False,
            "error": "Target Host must be the hostname or IP address of the remote Reticulum TCP server.",
        }).encode("utf-8")

        def rejected_preview(*args, **kwargs):
            raise urllib.error.HTTPError(
                "http://management-agent:8081/config/preview", 400,
                "Bad Request", {}, __import__("io").BytesIO(response_body),
            )

        self.app_module.urllib.request.urlopen = rejected_preview
        try:
            response = client.post("/interfaces/add?purpose=tcp_client", data={
                "csrf_token": "test-csrf", "purpose": "tcp_client",
                "interface_type": "TCPClientInterface", "name": "Test upstream",
                "enabled": "on", "mode": "full", "target_host": "bad host value", "target_port": "4243",
            }, follow_redirects=True)
        finally:
            self.app_module.urllib.request.urlopen = original_urlopen
            if original_token is None:
                os.environ.pop("MANAGEMENT_TOKEN", None)
            else:
                os.environ["MANAGEMENT_TOKEN"] = original_token
            if original_url is None:
                os.environ.pop("MANAGEMENT_URL", None)
            else:
                os.environ["MANAGEMENT_URL"] = original_url
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Target Host must be the hostname or IP address of the remote Reticulum TCP server.", page)
        self.assertNotIn("management operation did not return a usable result", page)

    def test_tcp_client_does_not_submit_server_listen_settings(self):
        """The shared beginner form must not leak its server defaults to a client."""
        change = self.app_module.make_interface_change({
            "purpose": "tcp_client", "interface_type": "TCPClientInterface",
            "name": "Known upstream", "enabled": "on", "mode": "full",
            "listen_ip": "0.0.0.0", "listen_port": "4242",
            "target_host": "mtgy-server", "target_port": "4243",
        })
        fields = change["interface"]["fields"]
        self.assertEqual(fields["target_host"], "mtgy-server")
        self.assertEqual(fields["target_port"], 4243)
        self.assertNotIn("listen_ip", fields)
        self.assertNotIn("listen_port", fields)

    def test_tcp_prerequisites_and_ifac_controls_are_visible(self):
        client = self.client()
        tcp_client = client.get("/interfaces/add?purpose=tcp_client").get_data(as_text=True)
        tcp_server = client.get("/interfaces/add?purpose=user_tcp").get_data(as_text=True)
        help_page = client.get("/help").get_data(as_text=True)
        self.assertIn("Before you connect to a remote Reticulum server, you need:", tcp_client)
        self.assertIn("Transport Mode can remain Off.", tcp_client)
        self.assertIn("Let other Reticulum nodes reach that remote network through this node", tcp_client)
        self.assertIn("Before another Reticulum node can connect to this server:", tcp_server)
        self.assertIn("fields.querySelectorAll('input').forEach(e => e.disabled = !ifac.checked)", tcp_client)
        self.assertIn("Using Tailscale", tcp_client + help_page)
        self.assertIn("must match that server’s Listen Port", tcp_client)
        self.assertIn("IFAC network name, passphrase, and IFAC size must match the remote server exactly", tcp_client)
        self.assertIn("Give connecting users the network name, passphrase, and IFAC size configured here. All three values must match.", tcp_server)
        self.assertIn("there is no universal default", tcp_client)
        self.assertNotIn("Cloudflare", tcp_client + tcp_server + help_page)

    def test_discovery_announce_status_does_not_fabricate_a_timestamp(self):
        client = self.client()
        self.app_module.CONFIG_VIEW_CACHE["config"]["discovery_capabilities"] = {"supported": True}
        page = client.get("/discovery").get_data(as_text=True)
        self.assertIn("Last discovery announce:", page)
        self.assertIn("Not exposed by this Reticulum runtime", page)
        self.assertIn("newly listening peer may need to wait until the next announcement", page)

    def test_ifac_must_be_enabled_and_is_not_inherited(self):
        base = {"purpose": "tcp_client", "interface_type": "TCPClientInterface", "name": "Upstream", "enabled": "on", "mode": "full", "target_host": "server.example", "target_port": "4242"}
        with self.assertRaisesRegex(ValueError, "IFAC is not enabled"):
            self.app_module.make_interface_change({**base, "network_name": "other-interface"})
        with self.assertRaisesRegex(ValueError, "IFAC requires both"):
            self.app_module.make_interface_change({**base, "ifac_enabled": "on", "network_name": "remote"})
        change = self.app_module.make_interface_change(base)
        self.assertNotIn("network_name", change["interface"]["fields"])
        self.assertNotIn("passphrase", change["interface"]["fields"])

    def test_route_for_others_review_warns_about_transport_and_second_interface(self):
        client = self.client()
        original = self.app_module.management
        self.app_module.CONFIG_VIEW_CACHE["config"] = {"global": {"enable_transport": "No"}, "interfaces": []}
        self.app_module.management = lambda path, body=None, timeout=None: {"ok": True, "diff": []}
        try:
            response = client.post("/interfaces/add?purpose=tcp_client", data={
                "csrf_token": "test-csrf", "purpose": "tcp_client", "interface_type": "TCPClientInterface",
                "name": "Upstream", "enabled": "on", "mode": "full", "target_host": "server.example",
                "target_port": "4242", "tcp_client_goal": "route_others",
            })
        finally:
            self.app_module.management = original
        page = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Transport Mode is currently: Off", page)
        self.assertIn("no second configured interface", page)

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

    def test_review_apply_returns_to_interfaces_and_discovery(self):
        client = self.client()
        original = self.app_module.management
        self.app_module.management = lambda path, body=None: {"ok": True}
        try:
            review = client.post("/interfaces/Default%20Interface/edit", data={
                "csrf_token": "test-csrf", "enabled": "yes", "mode": "full", "reason": "test",
            })
            self.assertEqual(review.status_code, 200)
            applied = client.post("/configuration/apply", data={"csrf_token": "test-csrf"}, follow_redirects=False)
            self.assertEqual(applied.headers["Location"], "/interfaces")

            review = client.post("/discovery", data={
                "csrf_token": "test-csrf", "discover_interfaces": "yes",
                "autoconnect_discovered_interfaces": "0", "autoconnect_unverified_implementations": "no",
            })
            self.assertEqual(review.status_code, 200)
            applied = client.post("/configuration/apply", data={"csrf_token": "test-csrf"}, follow_redirects=False)
            self.assertEqual(applied.headers["Location"], "/discovery")

            review = client.post("/configuration/preview", data={
                "csrf_token": "test-csrf", "kind": "global", "setting": "enable_transport", "value": "on",
            })
            self.assertEqual(review.status_code, 200)
            applied = client.post("/configuration/apply", data={"csrf_token": "test-csrf"}, follow_redirects=False)
            self.assertEqual(applied.headers["Location"], "/configuration")
        finally:
            self.app_module.management = original

    def test_invalid_configuration_return_destination_falls_back_to_configuration(self):
        client = self.client()
        original = self.app_module.management
        self.app_module.management = lambda path, body=None: {"ok": False, "error": "Rejected"}
        try:
            with client.session_transaction() as session:
                session["config_change"] = {"operation_id": "CFG-test", "reason": "test",
                                            "change": {"kind": "global", "setting": "enable_transport", "value": True},
                                            "return_destination": "https://untrusted.example"}
            response = client.post("/configuration/apply", data={"csrf_token": "test-csrf"}, follow_redirects=False)
        finally:
            self.app_module.management = original
        self.assertEqual(response.headers["Location"], "/configuration")

    def test_runtime_tcp_child_becomes_connected_client_and_history(self):
        """rns 1.5.7 exposes inbound peers as child interfaces, not clients[]."""
        status = json.loads(TCP_CHILD_FIXTURE.read_text())
        self.app_module.CONFIG_VIEW_CACHE["config"] = {"global": {}, "interfaces": [{"name": "VM Access", "type": "TCPServerInterface", "enabled": "Yes", "mode": "full", "fields": {}}]}
        (self.app_module.RETICULUM_DIR / "config").write_text("[interfaces]\n[[VM Access]]\ntype = TCPServerInterface\nenabled = Yes\n", encoding="utf-8")
        status_file = self.app_module.RETICULUM_DIR / "admin-status" / "snapshot.json"
        status_file.write_text(json.dumps({"status": status, "paths": []}), encoding="utf-8")
        with self.app_module.db() as con:
            con.execute("DELETE FROM connections"); con.execute("DELETE FROM events"); con.execute("DELETE FROM observer_state")

        _, records, active = self.app_module.ingest_snapshot()
        self.assertEqual(len(active), 1)
        client_record = active[0]
        self.assertEqual(client_record["remote_ip"], "192.168.1.145")
        self.assertEqual(client_record["remote_port"], 57032)
        self.assertEqual(client_record["interface"], "VM Access")
        self.assertEqual(client_record["rx"], 1039)
        self.assertEqual(client_record["tx"], 1804)
        self.assertEqual(client_record["ifac_netname"], "Local Access")
        self.assertTrue(client_record["ifac_active"])
        self.assertEqual(client_record["correlation"], "unknown")
        self.assertNotIn("Configured Upstream", [item["interface"] for item in active])

        page = self.client().get("/clients").get_data(as_text=True)
        self.assertIn("192.168.1.145:57032", page)
        self.assertIn("VM Access", page)
        self.assertIn("Local Access", page)
        self.assertIn("Reticulum Identity", page)
        self.assertIn("Not correlated", page)
        self.assertNotIn("signature-not-displayed", page)
        runtime_page = self.client().get("/interfaces").get_data(as_text=True)
        self.assertIn("Connected TCP peer · see Connected Clients", runtime_page)
        edit_page = self.client().get("/interfaces/VM%20Access/edit").get_data(as_text=True)
        self.assertIn("View Connected Clients", edit_page)

        # Two runtime children under the one server are distinct client records.
        second = dict(status["interfaces"][1]); second.update({"name": "TCPInterface[Client on VM Access/192.168.1.146:57033]", "hash": "second-child-hash", "rxb": 7, "txb": 8})
        two = {"interfaces": [dict(status["interfaces"][0], clients=2), status["interfaces"][1], second, status["interfaces"][2]]}
        self.assertEqual(len(self.app_module.current_clients(self.app_module.find_interface_records(two), self.app_module.config_interfaces())), 2)

        # Losing the runtime child closes exactly one active connection and
        # records one disconnect event; a later poll does not repeat it.
        status_file.write_text(json.dumps({"status": {"interfaces": [status["interfaces"][0]]}, "paths": []}), encoding="utf-8")
        self.app_module.ingest_snapshot(); self.app_module.ingest_snapshot()
        with self.app_module.db() as con:
            history = con.execute("SELECT * FROM connections").fetchall()
            disconnects = con.execute("SELECT * FROM events WHERE kind='client_disconnected'").fetchall()
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], "disconnected")
        self.assertIsNotNone(history[0]["disconnected_at"])
        self.assertEqual(len(disconnects), 1)

    def test_reported_tcp_client_count_without_runtime_detail_is_explained(self):
        self.app_module.CONFIG_VIEW_CACHE["config"] = {"global": {}, "interfaces": [{"name": "VM Access", "type": "TCPServerInterface", "enabled": "Yes", "mode": "full", "fields": {}}]}
        (self.app_module.RETICULUM_DIR / "config").write_text("[interfaces]\n[[VM Access]]\ntype = TCPServerInterface\n", encoding="utf-8")
        server = json.loads(TCP_CHILD_FIXTURE.read_text())["interfaces"][0]
        (self.app_module.RETICULUM_DIR / "admin-status" / "snapshot.json").write_text(json.dumps({"status": {"interfaces": [server]}, "paths": []}), encoding="utf-8")
        with self.app_module.db() as con: con.execute("DELETE FROM observer_state")
        page = self.client().get("/clients").get_data(as_text=True)
        self.assertIn("Reported clients: 1", page)
        self.assertIn("Identified TCP peers: 0", page)
        self.assertIn("does not expose enough per-client detail", page)

    def test_label_create_edit_clear_and_undo_are_sqlite_only(self):
        module = self.app_module
        original_management = module.management
        original_ingest = module.ingest_snapshot
        module.management = lambda *args, **kwargs: self.fail("labels must not call management")
        module.ingest_snapshot = lambda *args, **kwargs: self.fail("labels must not ingest observer data")
        client = self.client()
        try:
            response = client.post("/labels", data={"csrf_token": "test-csrf", "entity_type": "destination", "entity_value": "a" * 32, "label": "Known User", "notes": "first"})
            self.assertEqual(response.status_code, 302)
            response = client.post("/labels", data={"csrf_token": "test-csrf", "entity_type": "destination", "entity_value": "a" * 32, "label": "Known User 2", "notes": "second"})
            self.assertEqual(response.status_code, 302)
            with module.db() as con:
                self.assertEqual(con.execute("SELECT label FROM labels WHERE entity_value=?", ("a" * 32,)).fetchone()["label"], "Known User 2")
                self.assertEqual(con.execute("SELECT COUNT(*) AS n FROM label_history WHERE entity_value=?", ("a" * 32,)).fetchone()["n"], 2)
            client.post("/labels/undo", data={"csrf_token": "test-csrf", "entity_type": "destination", "entity_value": "a" * 32})
            self.assertEqual(module.labels()[("destination", "a" * 32)]["label"], "Known User")
            client.post("/labels/clear", data={"csrf_token": "test-csrf", "entity_type": "destination", "entity_value": "a" * 32, "confirm": "CLEAR LABEL"})
            self.assertNotIn(("destination", "a" * 32), module.labels())
        finally:
            module.management = original_management
            module.ingest_snapshot = original_ingest

    def test_paths_classify_by_context_and_prepare_safe_recovery(self):
        module = self.app_module
        destination, transport, identity = "a" * 32, "b" * 32, "c" * 32
        paths = [{"hash": destination, "via": transport, "interface": "VM Access", "hops": 2}, {"hash": "d" * 32, "identity": identity, "identity_verified": True}]
        models = module.path_models(paths, {("destination", destination): {"label": "Known User"}})
        self.assertEqual(models[0]["hash_type"], "Destination")
        self.assertIsNone(models[0]["identity"])
        self.assertEqual(models[1]["identity"], identity)
        self.assertTrue(any(kind == "Destination path" for kind, _ in module.hash_context(destination, models, {})))
        original_snapshot, original_management, original_ingest = module.snapshot, module.management, module.ingest_snapshot
        module.snapshot = lambda: {"paths": paths, "status": {"interfaces": []}}
        module.ingest_snapshot = lambda: ({"paths": paths, "path_error": None}, [], [])
        module.management = lambda *args, **kwargs: {"capabilities": {"blackhole": True, "unblackhole": True, "drop_path": True}}
        try:
            with module.db() as con:
                con.execute("INSERT INTO labels(entity_type,entity_value,label,notes,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(entity_type,entity_value) DO UPDATE SET label=excluded.label", ("destination", destination, "Known User", "", 0))
            page = self.client().get("/paths?q=Known").get_data(as_text=True)
            self.assertIn("Prepare Recovery", page)
            self.assertIn("Edit Label", page)
            self.assertIn('class="path-actions"', page)
            self.assertIn("Blackhole requires a verified identity hash", page)
            rejected = self.client().post("/recovery", data={"csrf_token": "test-csrf", "identity": destination, "blackhole": "on"}, follow_redirects=True)
            self.assertIn("known as a destination", rejected.get_data(as_text=True))
        finally:
            module.snapshot, module.management, module.ingest_snapshot = original_snapshot, original_management, original_ingest


if __name__ == "__main__":
    unittest.main()
