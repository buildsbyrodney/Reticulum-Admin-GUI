import os, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "management-agent"))
import configuration as cfg

SAMPLE = """# keep this comment\n[reticulum]\nenable_transport = No\nloglevel = 4\ncustom_global = retain\n\n[[Public TCP]]\ntype = TCPServerInterface\nenabled = Yes\nmode = full\nlisten_ip = 0.0.0.0\nlisten_port = 4242\nnetwork_name = public\npassphrase = private-value\ncustom_key = preserve\n"""

class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); root=Path(self.temp.name)
        self.old=(cfg.CONFIG,cfg.ROOT,cfg.SNAPSHOTS,cfg.RESTART_REQUEST,cfg.STATUS_DIR,cfg.GENERATION_FILE,cfg.SNAPSHOT_FILE,cfg.OPERATIONS)
        cfg.ROOT=root; cfg.CONFIG=root/"config"; cfg.SNAPSHOTS=root/"admin-config-snapshots"; cfg.RESTART_REQUEST=root/"admin-control"/"restart-request"; cfg.STATUS_DIR=root/"admin-status"; cfg.GENERATION_FILE=cfg.STATUS_DIR/"rnsd.generation"; cfg.SNAPSHOT_FILE=cfg.STATUS_DIR/"snapshot.json"; cfg.OPERATIONS=root/"admin-config-operations"
        cfg.CONFIG.write_text(SAMPLE); cfg.STATUS_DIR.mkdir(); cfg.GENERATION_FILE.write_text("5")
        # Discovery is gated by the rnsd-owned LXMF import check in production.
        (cfg.STATUS_DIR / "capabilities.json").write_text('{"lxmf_available": true, "lxmf_version": "0.9.6"}')
    def tearDown(self):
        cfg.CONFIG,cfg.ROOT,cfg.SNAPSHOTS,cfg.RESTART_REQUEST,cfg.STATUS_DIR,cfg.GENERATION_FILE,cfg.SNAPSHOT_FILE,cfg.OPERATIONS=self.old; self.temp.cleanup()
    def test_known_change_preserves_unknown_lines_and_redacts_secret(self):
        candidate=cfg.edit_text(SAMPLE,{"kind":"interface","name":"Public TCP","changes":{"mode":"boundary","passphrase":"new-secret"}})
        self.assertIn("custom_key = preserve",candidate); self.assertIn("# keep this comment",candidate)
        diff=cfg.redacted_diff(SAMPLE,candidate); secret=[x for x in diff if x["key"] == "passphrase"][0]
        self.assertEqual(secret["old"],"Changed"); self.assertEqual(secret["new"],"Changed")

    def test_authenticated_configuration_model_includes_ifac_values(self):
        model = cfg.public_model(SAMPLE)
        interface = model["interfaces"][0]
        self.assertEqual(interface["fields"]["passphrase"], "private-value")

    def test_configuration_model_recognises_reticulum_ifac_field_names(self):
        text = """[reticulum]\n\n[[Shared TCP]]\ntype = TCPServerInterface\nifac_netname = shared\nifac_passphrase = shared-credential\nifac_size = 8\n"""
        interface = cfg.public_model(text)["interfaces"][0]
        self.assertEqual(interface["fields"]["ifac_netname"], "shared")
        self.assertEqual(interface["fields"]["ifac_passphrase"], "shared-credential")
        self.assertEqual(interface["fields"]["ifac_size"], "8")
    def test_validation_and_atomic_write(self):
        with self.assertRaises(ValueError): cfg.edit_text(SAMPLE,{"kind":"interface","name":"Public TCP","changes":{"listen_port":70000}})
        candidate=cfg.edit_text(SAMPLE,{"kind":"global","setting":"enable_transport","value":True}); cfg.atomic_write(candidate)
        self.assertIn("enable_transport = Yes",cfg.read_config()); self.assertTrue((cfg.ROOT/"config.original").exists())
    def test_snapshot_restore_creates_safety_snapshot(self):
        first=cfg.create_snapshot("admin","before",{"manual":True},False)
        cfg.atomic_write(cfg.edit_text(SAMPLE,{"kind":"global","setting":"enable_transport","value":True}))
        old_health=cfg.health; cfg.health=lambda *args, **kwargs:(True,"")
        try: result=cfg.restore_snapshot("admin","restore",first["id"])
        finally: cfg.health=old_health
        self.assertTrue(result["ok"]); self.assertGreaterEqual(len(cfg.list_snapshots()),2); self.assertIn("enable_transport = No",cfg.read_config())
    def test_apply_rolls_back_when_health_fails(self):
        old_health,old_runtime=cfg.health,cfg.apply_runtime
        replies=iter([(False,"failed"),(True,"recovered")])
        cfg.apply_runtime=lambda change, operation_id=None:(True,"interface_reload",""); cfg.health=lambda *args, **kwargs:next(replies)
        try: result=cfg.apply_change("admin","test",{"kind":"interface","name":"Public TCP","changes":{"enabled":False}})
        finally: cfg.health, cfg.apply_runtime=old_health,old_runtime
        self.assertFalse(result["ok"]); self.assertTrue(result["rolled_back"]); self.assertIn("enabled = Yes",cfg.read_config())

    def test_restart_health_waits_for_new_generation_within_deadline(self):
        old_generation, old_probe, old_monotonic, old_sleep, old_budget = cfg.rnsd_generation, cfg.probe_health, cfg.time.monotonic, cfg.time.sleep, cfg.RESTART_HEALTH_TIMEOUT
        clock = [0]
        # A Pi can need one failed candidate plus a Docker relaunch before the
        # verified generation advances. This 56-second case would have rolled
        # back under the old 35-second budget.
        try:
            cfg.RESTART_HEALTH_TIMEOUT = 70
            cfg.time.monotonic = lambda: clock[0]
            cfg.time.sleep = lambda seconds: clock.__setitem__(0, clock[0] + 7)
            cfg.rnsd_generation = lambda: 8 if clock[0] >= 56 else 7
            cfg.probe_health = lambda *args: (True, "healthy")
            ok, detail, verification = cfg.health(True, 7, "CFG-20261010-051834-A1B2C3")
        finally:
            cfg.rnsd_generation, cfg.probe_health, cfg.time.monotonic, cfg.time.sleep, cfg.RESTART_HEALTH_TIMEOUT = old_generation, old_probe, old_monotonic, old_sleep, old_budget
        self.assertTrue(ok); self.assertEqual(detail, "healthy"); self.assertTrue(verification["restart_verified"]); self.assertEqual(verification["generation"], 8)

    def test_health_uses_new_rnsd_observer_snapshot_not_agent_rnstatus(self):
        old_generation, old_monotonic, old_sleep, old_budget = cfg.rnsd_generation, cfg.time.monotonic, cfg.time.sleep, cfg.RESTART_HEALTH_TIMEOUT
        clock=[0]
        def generation(): return 6 if clock[0] >= 2 else 5
        def tick(_):
            clock[0] += 1
            # Old generation snapshot remains briefly after the new daemon starts.
            if clock[0] == 1:
                cfg.SNAPSHOT_FILE.write_text('{"collected_at":"2100-01-01T00:00:00+00:00","rnsd_generation":5,"reticulum_ready":true,"status":{"name":"old"},"status_error":null}')
            if clock[0] == 3:
                cfg.SNAPSHOT_FILE.write_text('{"collected_at":"2100-01-01T00:00:01+00:00","rnsd_generation":6,"observer_sequence":9,"reticulum_ready":true,"status":{"name":"new"},"status_error":null}')
        try:
            cfg.RESTART_HEALTH_TIMEOUT=10; cfg.rnsd_generation=generation; cfg.time.monotonic=lambda:clock[0]; cfg.time.sleep=tick
            ok, _, verification=cfg.health(True, 5, "CFG-20261010-051834-A1B2C3")
        finally:
            cfg.rnsd_generation,cfg.time.monotonic,cfg.time.sleep,cfg.RESTART_HEALTH_TIMEOUT=old_generation,old_monotonic,old_sleep,old_budget
        self.assertTrue(ok); self.assertEqual(verification["generation"],6)

    def test_unhealthy_new_observer_snapshot_causes_rollback(self):
        old_runtime, old_health=cfg.apply_runtime,cfg.health
        responses=iter([(True,"preflight"), (False,"observer status error: unavailable",{"restart_verified":False}), (True,"recovered",{"restart_verified":True})])
        try:
            cfg.apply_runtime=lambda change, operation_id=None:(True,"rnsd_restart","",5)
            cfg.health=lambda *args, **kwargs:next(responses)
            result=cfg.apply_change("admin","test",{"kind":"global","setting":"enable_transport","value":True},"CFG-20261010-051834-A1B2C3")
        finally: cfg.apply_runtime,cfg.health=old_runtime,old_health
        self.assertFalse(result["ok"]); self.assertTrue(result["rolled_back"]); self.assertIn("enable_transport = No",cfg.read_config())

    def test_unhealthy_runtime_refuses_restart_change_without_mutating_config(self):
        old_health=cfg.health
        try:
            cfg.health=lambda *args, **kwargs:(False,"observer reports Reticulum shared instance unavailable")
            result=cfg.apply_change("admin","test",{"kind":"global","setting":"enable_transport","value":True},"CFG-20261010-051835-A1B2C3")
        finally: cfg.health=old_health
        self.assertFalse(result["ok"]); self.assertTrue(result["preflight_failed"]); self.assertIn("enable_transport = No",cfg.read_config())

    def test_operation_id_prevents_duplicate_apply(self):
        operation = "CFG-20261010-051834-A1B2C3"
        old_runtime, old_health = cfg.apply_runtime, cfg.health
        try:
            cfg.apply_runtime = lambda change, operation_id=None: (True, "interface_reload", "")
            cfg.health = lambda *args, **kwargs: (True, "healthy")
            first = cfg.apply_change("admin", "test", {"kind":"interface","name":"Public TCP","changes":{"enabled":False}}, operation)
            second = cfg.apply_change("admin", "different request", {"kind":"interface","name":"Public TCP","changes":{"enabled":True}}, operation)
        finally:
            cfg.apply_runtime, cfg.health = old_runtime, old_health
        self.assertTrue(first["ok"]); self.assertEqual(first, second); self.assertIn("enabled = No", cfg.read_config())

    def test_create_tcp_server_preserves_existing_configuration(self):
        change = {"kind":"interface_create", "interface":{"name":"User TCP", "type":"TCPServerInterface", "enabled":True, "mode":"boundary", "fields":{"listen_ip":"0.0.0.0", "listen_port":4243, "network_name":"users", "passphrase":"secret", "ifac_size":8}}}
        candidate = cfg.edit_text(SAMPLE, change)
        self.assertIn("custom_key = preserve", candidate)
        self.assertIn("[[User TCP]]", candidate)
        self.assertIn("listen_port = 4243", candidate)
        self.assertIn("passphrase = secret", candidate)
        self.assertIn("Changed", [row["new"] for row in cfg.redacted_diff(SAMPLE, candidate) if row["key"] == "passphrase"])

    def test_create_tcp_client_and_auto_interface(self):
        tcp = {"kind":"interface_create", "interface":{"name":"Upstream", "type":"TCPClientInterface", "enabled":True, "mode":"full", "fields":{"target_host":"example.org", "target_port":4242}}}
        auto = {"kind":"interface_create", "interface":{"name":"LAN", "type":"AutoInterface", "enabled":True, "mode":"full", "fields":{}}}
        candidate = cfg.edit_text(cfg.edit_text(SAMPLE, tcp), auto)
        self.assertIn("target_host = example.org", candidate); self.assertIn("[[LAN]]", candidate)

    def test_create_rejects_duplicate_name_and_server_port(self):
        duplicate = {"kind":"interface_create", "interface":{"name":"Public TCP", "type":"TCPServerInterface", "enabled":True, "mode":"full", "fields":{"listen_ip":"0.0.0.0", "listen_port":4243}}}
        with self.assertRaisesRegex(ValueError, "already exists"): cfg.edit_text(SAMPLE, duplicate)
        used = {"kind":"interface_create", "interface":{"name":"Other", "type":"TCPServerInterface", "enabled":True, "mode":"full", "fields":{"listen_ip":"0.0.0.0", "listen_port":4242}}}
        with self.assertRaisesRegex(ValueError, "already configured"): cfg.edit_text(SAMPLE, used)

    def test_create_rejects_known_unassigned_host_listen_address(self):
        cfg.SNAPSHOT_FILE.write_text('{"host_network":{"ipv4_addresses":["127.0.0.1","192.168.1.85","100.64.10.30"]}}')
        valid = {"kind":"interface_create", "interface":{"name":"LAN TCP", "type":"TCPServerInterface", "enabled":True, "mode":"full", "fields":{"listen_ip":"192.168.1.85", "listen_port":4243}}}
        self.assertIn("listen_ip = 192.168.1.85", cfg.edit_text(SAMPLE, valid))
        with self.assertRaisesRegex(ValueError, "not currently assigned to this server"):
            cfg.edit_text(SAMPLE, {"kind":"interface", "name":"Public TCP", "changes":{"listen_ip":"192.168.1.99"}})
        invalid = {"kind":"interface_create", "interface":{"name":"Missing IP", "type":"TCPServerInterface", "enabled":True, "mode":"full", "fields":{"listen_ip":"192.168.1.99", "listen_port":4243}}}
        with self.assertRaisesRegex(ValueError, "not currently assigned to this server"):
            cfg.edit_text(SAMPLE, invalid)

    def test_host_address_validation_is_advisory_when_observer_data_is_unavailable(self):
        candidate = {"kind":"interface_create", "interface":{"name":"Address Later", "type":"TCPServerInterface", "enabled":True, "mode":"full", "fields":{"listen_ip":"192.168.1.99", "listen_port":4243}}}
        self.assertIn("listen_ip = 192.168.1.99", cfg.edit_text(SAMPLE, candidate))

    def test_delete_interface_preserves_other_sections(self):
        candidate = cfg.edit_text(SAMPLE, {"kind":"interface_delete", "name":"Public TCP"})
        self.assertNotIn("[[Public TCP]]", candidate); self.assertIn("custom_global = retain", candidate)

    def test_discovery_global_settings_are_allowlisted_and_preserve_unknown_config(self):
        change = {"kind":"global_batch", "changes":{"discover_interfaces":True, "autoconnect_discovered_interfaces":2, "interface_discovery_sources":"0123456789abcdef0123456789abcdef\n0123456789abcdef0123456789abcdef", "autoconnect_interface_mode":"boundary"}}
        candidate = cfg.edit_text(SAMPLE, change)
        self.assertIn("discover_interfaces = Yes", candidate)
        self.assertIn("autoconnect_discovered_interfaces = 2", candidate)
        self.assertIn("interface_discovery_sources = 0123456789abcdef0123456789abcdef", candidate)
        self.assertIn("custom_global = retain", candidate)

    def test_discovery_cannot_be_enabled_without_runtime_lxmf(self):
        (cfg.STATUS_DIR / "capabilities.json").write_text('{"lxmf_available": false}')
        with self.assertRaisesRegex(ValueError, "requires LXMF"):
            cfg.edit_text(SAMPLE, {"kind":"global_batch", "changes":{"discover_interfaces":True}})

    def test_discovery_capability_comes_from_rnsd_status(self):
        (cfg.STATUS_DIR / "capabilities.json").write_text('{"lxmf_available": false}')
        self.assertFalse(cfg.public_model(SAMPLE)["discovery_capabilities"]["supported"])
        (cfg.STATUS_DIR / "capabilities.json").write_text('{"lxmf_available": true, "lxmf_version": "0.9.6"}')
        capability = cfg.public_model(SAMPLE)["discovery_capabilities"]
        self.assertTrue(capability["supported"])
        self.assertEqual(capability["lxmf_version"], "0.9.6")

    def test_discovery_interface_controls_are_validated(self):
        change = {"kind":"interface", "name":"Public TCP", "changes":{"discoverable":True, "discovery_name":"Trusted Site", "reachable_on":"node.example.net", "announce_interval":60, "publish_ifac":True}}
        candidate = cfg.edit_text(SAMPLE, change)
        self.assertIn("discoverable = Yes", candidate)
        self.assertIn("reachable_on = node.example.net", candidate)
        self.assertIn("publish_ifac = Yes", candidate)
        with self.assertRaisesRegex(ValueError, "hostname"):
            cfg.edit_text(SAMPLE, {"kind":"interface", "name":"Public TCP", "changes":{"reachable_on":"/tmp/not-allowed"}})
        no_ifac = SAMPLE.replace("network_name = public\npassphrase = private-value\n", "")
        with self.assertRaisesRegex(ValueError, "Publish IFAC"):
            cfg.edit_text(no_ifac, {"kind":"interface", "name":"Public TCP", "changes":{"discoverable":True, "publish_ifac":True}})

if __name__ == "__main__": unittest.main()
