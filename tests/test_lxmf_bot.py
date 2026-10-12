import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
BOT_PATH = ROOT / "lxmf-test-bot" / "bot.py"
spec = importlib.util.spec_from_file_location("echo_bot", BOT_PATH)
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)

class EchoBotTests(unittest.TestCase):
    def test_commands_only_return_safe_content(self):
        processor = bot.CommandProcessor(lambda: {"transport":"On", "paths":42, "interfaces":3, "clients":1})
        self.assertEqual(processor.reply("PING"), "PONG")
        self.assertEqual(processor.reply("ECHO test"), "Echo: test")
        self.assertIn("PING", processor.reply("HELP"))
        status = processor.reply("STATUS")
        self.assertIn("Known paths: 42", status)
        self.assertNotIn("token", status.lower())
        self.assertEqual(processor.reply("hello"), "Echo: hello")

    def test_settings_persist_and_minimum_interval_is_enforced(self):
        with tempfile.TemporaryDirectory() as root:
            old = bot.CONFIG
            bot.CONFIG = Path(root)
            try:
                (bot.CONFIG / "settings.json").write_text(json.dumps({"display_name":"Stable Bot", "interval_minutes":1}))
                first, second = bot.read_settings(), bot.read_settings()
                self.assertEqual(first["display_name"], "Stable Bot")
                self.assertEqual(first["interval_minutes"], 15)
                self.assertEqual(first, second)
            finally: bot.CONFIG = old

    def test_identity_path_is_persistent_across_restarts(self):
        with tempfile.TemporaryDirectory() as root:
            identity = Path(root) / "identity"
            identity.write_bytes(b"persistent-test-identity")
            self.assertEqual(identity.read_bytes(), b"persistent-test-identity")

    def test_bot_requires_an_existing_shared_instance(self):
        source = BOT_PATH.read_text(encoding="utf-8")
        self.assertIn('RNS.Reticulum(configdir="/reticulum", require_shared_instance=True)', source)

    def test_waiting_status_is_not_reported_as_ready(self):
        with tempfile.TemporaryDirectory() as root:
            old = bot.STATUS
            bot.STATUS = Path(root)
            try:
                instance = bot.EchoBot()
                instance.write_status(False, "Waiting for managed Reticulum shared instance")
                state = json.loads((bot.STATUS / "status.json").read_text())
                self.assertFalse(state["running"])
                self.assertTrue(state["process_running"])
                self.assertEqual(state["state"], "waiting")
            finally:
                bot.STATUS = old

    def test_bot_uses_the_rnsd_host_network_namespace_without_exposing_ports(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        service = compose.split("  lxmf-test-bot:", 1)[1]
        self.assertIn("network_mode: host", service)
        self.assertNotIn("ports:", service)
        self.assertIn("./data/reticulum:/reticulum:ro", service)

if __name__ == "__main__": unittest.main()
