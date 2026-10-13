import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
BOT_PATH = ROOT / "lxmf-test-bot" / "bot.py"
sys.path.insert(0, str(BOT_PATH.parent))
spec = importlib.util.spec_from_file_location("echo_bot", BOT_PATH)
bot = importlib.util.module_from_spec(spec); spec.loader.exec_module(bot)

class EchoBotTests(unittest.TestCase):
    def test_commands_only_return_safe_content(self):
        processor = bot.CommandProcessor(lambda: {"transport":"On", "paths":42, "interfaces":3, "clients":1, "uptime":"2h 14m", "interface_summary":"Interfaces\n- VM Access: Up", "client_summary":"TCP clients: 1", "path_summary":"Known paths: 42"})
        self.assertEqual(processor.reply("PING"), "PONG")
        self.assertEqual(processor.reply(" help "), processor.reply("MENU"))
        self.assertIn("PING", processor.reply("HELP"))
        status = processor.reply("STATUS")
        self.assertIn("Known paths: 42", status)
        self.assertNotIn("token", status.lower())
        self.assertEqual(processor.reply("hello"), "I don't know that one yet. Send HELP for the command list.")

    def test_fun_commands_and_verse_library_are_local(self):
        processor = bot.CommandProcessor(lambda: {"transport":"Off", "paths":0, "interfaces":0, "clients":0, "uptime":"0m"})
        self.assertIn(processor.reply("TEST"), bot.TEST)
        self.assertIn(processor.reply("JOKE"), bot.JOKES)
        self.assertIn(processor.reply("DADJOKE"), bot.DAD_JOKES)
        self.assertIn(processor.reply("8BALL Will this work?"), bot.EIGHT_BALL)
        self.assertEqual(processor.reply("8BALL"), "Ask me a question after 8BALL.")
        self.assertIn(processor.reply("FACT"), bot.FACTS)
        self.assertIn(processor.reply("COFFEE"), bot.COFFEE)
        self.assertIn(processor.reply("RADIO"), bot.RADIO)
        self.assertIn("John 3:16 KJV", processor.reply("verse john 3:16"))
        self.assertIn("not in my local KJV", processor.reply("VERSE Genesis 1:1"))
        self.assertIn("KJV", processor.reply("VERSE"))

    def test_replies_are_bounded_and_input_is_case_insensitive(self):
        processor = bot.CommandProcessor(lambda: {"transport":"Off", "paths":0, "interfaces":0, "clients":0, "uptime":"0m"})
        self.assertEqual(processor.reply(" Help"), processor.reply("HELP"))
        self.assertLessEqual(len(processor.reply("x" * 5000)), bot.MAX_REPLY)

    def test_settings_persist_and_minimum_interval_is_enforced(self):
        with tempfile.TemporaryDirectory() as root:
            old = bot.CONFIG
            bot.CONFIG = Path(root)
            try:
                (bot.CONFIG / "settings.json").write_text(json.dumps({"display_name":"Stable Bot", "interval_minutes":1}))
                first, second = bot.read_settings(), bot.read_settings()
                self.assertEqual(first["display_name"], "Stable Bot")
                self.assertEqual(first["interval_minutes"], 15)
                self.assertEqual(first["tagline"], "Send HELP for commands")
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

    def test_unicode_display_name_and_tagline_are_preserved(self):
        with tempfile.TemporaryDirectory() as root:
            old = bot.CONFIG; bot.CONFIG = Path(root)
            try:
                settings = {"display_name": "W4OWE Test Bot 🤖", "tagline": "Send HELP for commands"}
                (bot.CONFIG / "settings.json").parent.mkdir(parents=True)
                (bot.CONFIG / "settings.json").write_text(json.dumps(settings, ensure_ascii=False), encoding="utf-8")
                self.assertEqual(bot.read_settings()["display_name"], "W4OWE Test Bot 🤖")
                self.assertEqual(bot.read_settings()["tagline"], "Send HELP for commands")
            finally: bot.CONFIG = old

    def test_per_sender_rate_limit_and_self_messages_are_not_replied_to(self):
        with tempfile.TemporaryDirectory() as root:
            old = bot.CONFIG; bot.CONFIG = Path(root)
            try:
                (bot.CONFIG / "settings.json").parent.mkdir(parents=True)
                (bot.CONFIG / "settings.json").write_text(json.dumps({"rate_limit_per_minute": 1}), encoding="utf-8")
                instance = bot.EchoBot()
                self.assertTrue(instance.allowed_sender(b"sender"))
                self.assertFalse(instance.allowed_sender(b"sender"))
                self.assertEqual(instance.counters["rate_limited_count"], 1)
            finally: bot.CONFIG = old

    def test_disabled_bot_does_not_process_or_reply(self):
        class Message:
            source_hash = b"other-sender"
            def content_as_string(self): return "PING"
        with tempfile.TemporaryDirectory() as root:
            old = bot.CONFIG; bot.CONFIG = Path(root)
            try:
                (bot.CONFIG / "settings.json").parent.mkdir(parents=True)
                (bot.CONFIG / "settings.json").write_text(json.dumps({"enabled": False}), encoding="utf-8")
                instance = bot.EchoBot(); instance.received(Message())
                self.assertEqual(instance.counters["messages_received"], 0)
                self.assertEqual(instance.counters["replies_sent"], 0)
            finally: bot.CONFIG = old

    def test_bot_uses_the_rnsd_host_network_namespace_without_exposing_ports(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        service = compose.split("  lxmf-test-bot:", 1)[1]
        self.assertIn("network_mode: host", service)
        self.assertNotIn("ports:", service)
        self.assertIn("./data/reticulum:/reticulum:ro", service)

    def test_bot_has_no_management_or_configuration_write_capability(self):
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        service = compose.split("  lxmf-test-bot:", 1)[1]
        self.assertNotIn("management-agent", service)
        self.assertNotIn("MANAGEMENT_TOKEN", service)

if __name__ == "__main__": unittest.main()
