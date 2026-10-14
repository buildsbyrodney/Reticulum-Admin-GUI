"""Regression coverage for the conservative, read-only Network Map model."""
import os
import sys
import tempfile
import unittest
from pathlib import Path


class NetworkMapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        os.environ["ADMIN_SECRET_KEY"] = "network-map-test"
        os.environ["APP_DATA"] = cls.temp.name
        sys.path.insert(0, str(Path(__file__).parents[1] / "reticulum-admin"))
        import app as web_app
        cls.web = web_app

    def setUp(self):
        self.original = {name: getattr(self.web, name) for name in (
            "ingest_snapshot", "configuration_model", "labels", "messaging_announced_peers",
            "bot_status", "messaging_status", "transport_enabled")}
        self.web.ingest_snapshot = lambda: ({"running": True, "paths": [{"hash": "a" * 32, "hops": 2, "via": "b" * 32, "interface": "LAN"}]}, [
            {"name": "TCPServerInterface[LAN/192.168.1.85:4243]", "short_name": "LAN", "type": "TCPServerInterface", "status_display": "Up", "mode_display": "Gateway", "client_count": 1, "ifac_netname": "Local"}], [
            {"key": "client-key", "remote_ip": "192.168.1.145", "remote_port": 57032, "interface": "LAN", "status": "active", "rx": 1, "tx": 2}])
        self.web.configuration_model = lambda: {"interfaces": [{"name": "LAN", "type": "TCPServerInterface", "enabled": True, "mode": "gateway", "fields": {"network_name": "Local"}}]}
        self.web.labels = lambda: {("destination", "a" * 32): {"label": "Known peer"}}
        self.web.messaging_announced_peers = lambda: [{"destination_hash": "c" * 32, "display_name": "Announced peer", "message_display_name": None, "contact_name": None, "first_seen": 1, "last_seen": 2, "announce_count": 3, "last_hops": None}]
        self.web.bot_status = lambda: {"running": True, "destination": "d" * 32, "shared_instance_connected": True}
        self.web.messaging_status = lambda: {"running": True, "destination": "e" * 32, "shared_instance_connected": True}
        self.web.transport_enabled = lambda: True

    def tearDown(self):
        for name, value in self.original.items(): setattr(self.web, name, value)

    def test_model_has_grounded_nodes_edges_and_unknown_identity_fallback(self):
        graph = self.web.network_map_model()
        nodes = {node["id"]: node for node in graph["nodes"]}
        self.assertIn("local:shared-instance", nodes)
        self.assertIn("interface:LAN", nodes)
        self.assertIn("client:client-key", nodes)
        self.assertIn("service:bot", nodes); self.assertIn("service:messaging", nodes)
        peer = nodes["peer:" + "c" * 32]
        self.assertEqual(peer["name"], "Announced peer")
        self.assertEqual(peer["details"]["Identity"], "Unknown / not inferred")
        self.assertEqual(peer["details"]["Hops"], "Hops unavailable")
        self.assertTrue(any(edge["type"] == "tcp_client" for edge in graph["edges"]))
        self.assertTrue(any(edge["type"] == "lxmf_announce" for edge in graph["edges"]))

    def test_exact_path_and_announce_hashes_merge_with_name_and_path_details(self):
        destination, unrelated = "f" * 32, "9" * 32
        self.web.ingest_snapshot = lambda: ({"running": True, "paths": [
            {"hash": destination, "hops": 3, "via": "a" * 32, "interface": "LAN"},
            {"hash": unrelated, "hops": 1, "via": "b" * 32, "interface": "LAN"},
        ]}, [], [])
        self.web.configuration_model = lambda: {"interfaces": []}
        self.web.messaging_announced_peers = lambda: [{"destination_hash": destination,
            "contact_name": None, "display_name": "W4OWE-1", "message_display_name": "Old name",
            "first_seen": 1, "last_seen": 20, "announce_count": 4, "last_hops": 2}]
        graph = self.web.network_map_model()
        peers = [node for node in graph["nodes"] if node["class"] == "peer_identity"]
        self.assertEqual(len(peers), 2)
        matched = next(node for node in peers if node["id"] == "peer:" + destination)
        self.assertEqual(matched["name"], "W4OWE-1")
        self.assertEqual(matched["subtitle"], "ffffff…ffffff")
        self.assertEqual(matched["details"]["Interface"], "LAN")
        self.assertEqual(matched["details"]["Via transport"], "a" * 32)
        self.assertEqual(matched["details"]["Announce count"], 4)
        self.assertEqual(matched["details"]["Last seen"], self.web.format_time(20))
        plain = next(node for node in peers if node["id"] == "peer:" + unrelated)
        self.assertEqual(plain["name"], "999999…999999")

    def test_api_requires_login_and_authenticated_result_has_no_controls(self):
        with self.web.app.test_client() as client:
            response = client.get("/topology/api/graph")
            self.assertEqual(response.status_code, 302)
            with client.session_transaction() as state: state["admin"] = "operator"
            response = client.get("/topology/api/graph")
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("nodes", payload); self.assertIn("edges", payload)
        self.assertNotIn("actions", payload)

    def test_page_is_mobile_safe_and_routes_nodes_contextually(self):
        template = (Path(__file__).parents[1] / "reticulum-admin" / "templates" / "topology.html").read_text(encoding="utf-8")
        css = (Path(__file__).parents[1] / "reticulum-admin" / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn("window.location.assign(node.route)", template)
        self.assertIn("window.setTimeout(refresh,5000)", template)
        self.assertIn("settleFrames<180", template)
        self.assertIn("function startSettling", template)
        self.assertNotIn("function tick()", template)
        self.assertIn("@media (max-width: 700px)", css)
        self.assertNotIn("blackhole", template.lower())

    def test_mobile_navigation_is_accessible_and_progressively_enhanced(self):
        root = Path(__file__).parents[1]
        base = (root / "reticulum-admin" / "templates" / "base.html").read_text(encoding="utf-8")
        css = (root / "reticulum-admin" / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn('aria-controls="primary-navigation"', base)
        self.assertIn('aria-expanded="false"', base)
        self.assertIn('document.documentElement.classList.add("nav-ready")', base)
        self.assertIn('event.target.closest("a")', base)
        self.assertIn('.nav-ready .site-header .primary-navigation { display: none; }', css)
        self.assertIn('.nav-ready .site-header .primary-navigation.open { display: block;', css)


if __name__ == "__main__":
    unittest.main()
