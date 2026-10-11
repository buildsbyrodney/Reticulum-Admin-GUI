"""Static and adapter tests for the native-Linux rnsd host-network boundary."""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]


class HostNetworkingTests(unittest.TestCase):
    def test_only_rnsd_uses_host_networking_and_agent_stays_private(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        rnsd = compose.split("  management-agent:", 1)[0]
        agent = compose.split("  management-agent:", 1)[1].split("  reticulum-admin:", 1)[0]
        web = compose.split("  reticulum-admin:", 1)[1]
        self.assertIn("network_mode: host", rnsd)
        self.assertNotIn("ports:", rnsd)
        self.assertNotIn("networks:", rnsd)
        self.assertIn("./data/reticulum:/root/.reticulum", rnsd)
        self.assertIn("networks: [management]", agent)
        self.assertNotIn("ports:", agent)
        self.assertIn("internal: true", compose)
        self.assertIn("MANAGEMENT_URL: http://management-agent:8081", web)

    def test_observer_reports_host_ipv4_addresses_without_exposing_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_argv = sys.argv
            try:
                sys.argv = ["observer.py", directory]
                spec = importlib.util.spec_from_file_location("host_network_observer", ROOT / "rnsd" / "observer.py")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            finally:
                sys.argv = previous_argv
            module.run = lambda *command: (json.dumps([
                {"ifname": "eth0", "addr_info": [{"family": "inet", "local": "192.168.1.85"}]},
                {"ifname": "tailscale0", "addr_info": [{"family": "inet", "local": "100.64.10.30"}]},
                {"ifname": "lo", "addr_info": [{"family": "inet6", "local": "::1"}]},
            ]), None)
            network = module.host_network()
        self.assertEqual(network["ipv4_addresses"], ["100.64.10.30", "192.168.1.85"])
        self.assertEqual(network["interfaces"][1], {"name": "tailscale0", "ipv4_addresses": ["100.64.10.30"]})
        self.assertIsNone(network["error"])

    def test_installer_requires_native_linux_for_host_networked_rnsd(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('"$(uname -s)" != "Linux"', installer)
        self.assertIn("Docker Desktop and WSL are not supported", installer)


if __name__ == "__main__":
    unittest.main()
