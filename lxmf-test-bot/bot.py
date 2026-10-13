"""Small, deliberately non-administrative LXMF destination."""
import json
import os
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from responses import COFFEE, DAD_JOKES, EIGHT_BALL, FACTS, JOKES, KJV, KJV_TOPICS, RADIO, TEST

MIN_INTERVAL = 15
STATE = Path(os.environ.get("BOT_STATE_DIR", "/bot"))
STATUS = Path(os.environ.get("BOT_STATUS_DIR", "/status"))
CONFIG = Path(os.environ.get("BOT_CONFIG_DIR", "/config"))
RUNTIME_STATUS = Path(os.environ.get("RETICULUM_STATUS_PATH", "/reticulum/admin-status/snapshot.json"))

DEFAULTS = {"enabled": True, "display_name": "Reticulum Admin Test Bot", "tagline": "Send HELP for commands", "announce_on_startup": True, "periodic_announce": True, "interval_minutes": 60, "rate_limit_per_minute": 12}
MAX_INPUT = 500
MAX_REPLY = 900
MENU = """Reticulum Admin Bot

Diagnostics
PING
STATUS
DIAGNOSTICS
INTERFACES
CLIENTS
PATHS

Fun
TEST
JOKE
DADJOKE
8BALL <question>
FACT
COFFEE
RADIO

Bible
VERSE
VERSE <reference>

Send MENU or HELP anytime to see this list."""

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temp, path)

def read_settings():
    try:
        value = json.loads((CONFIG / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    settings = {**DEFAULTS, **{key: value[key] for key in DEFAULTS if key in value}}
    try: settings["interval_minutes"] = max(MIN_INTERVAL, int(settings["interval_minutes"]))
    except (TypeError, ValueError): settings["interval_minutes"] = DEFAULTS["interval_minutes"]
    try: settings["rate_limit_per_minute"] = min(120, max(1, int(settings["rate_limit_per_minute"])))
    except (TypeError, ValueError): settings["rate_limit_per_minute"] = DEFAULTS["rate_limit_per_minute"]
    return settings

class CommandProcessor:
    """Pure replies make command safety and test coverage independent of LXMF."""
    def __init__(self, status):
        self.status = status
        self.last_choice = {}
    def choose(self, category):
        options = tuple(category)
        candidates = [value for value in options if value != self.last_choice.get(id(options))] or list(options)
        value = random.choice(candidates); self.last_choice[id(options)] = value
        return value
    def normalise_reference(self, reference):
        value = " ".join(reference.lower().replace(".", "").split())
        aliases = {"ps ": "psalm ", "psalms ": "psalm ", "prov ": "proverbs ", "pr ": "proverbs ", "jn ": "john ", "rom ": "romans ", "phil ": "philippians ", "rev ": "revelation ", "1 cor ": "1 corinthians ", "2 cor ": "2 corinthians ", "i corinthians ": "1 corinthians ", "ii corinthians ": "2 corinthians "}
        for short, full in aliases.items():
            if value.startswith(short): return full + value[len(short):]
        return value
    def verse(self, reference):
        if not reference:
            reference, text = self.choose(tuple(KJV.values()))
        else:
            key = self.normalise_reference(reference)
            if key in KJV_TOPICS:
                key = self.choose(KJV_TOPICS[key])
            item = KJV.get(key)
            if not item: return "That reference is not in my local KJV library."
            reference, text = item
        return f'“{text}”\n— {reference} KJV'
    def reply(self, text):
        command = (text or "").strip()[:MAX_INPUT]
        upper = command.upper()
        if upper == "PING": return "PONG"
        if upper in ("HELP", "MENU"): return MENU
        if upper == "STATUS":
            value = self.status()
            return "Transport: {transport}\nInterfaces: {interfaces} up\nTCP clients: {clients}\nKnown paths: {paths}\nBot uptime: {uptime}".format(**value)
        if upper in ("DIAGNOSTICS", "DIAG"):
            value = {"reticulum": "Unavailable", "bot": "Ready", **self.status()}
            return "Diagnostics\nReticulum: {reticulum}\nTransport: {transport}\nInterfaces up: {interfaces}\nTCP clients: {clients}\nKnown paths: {paths}\nBot: {bot}\nBot uptime: {uptime}".format(**value)
        if upper == "INFO": return self.reply("STATUS")
        if upper == "IFACES": return self.reply("INTERFACES")
        if upper == "CLIENT": return self.reply("CLIENTS")
        if upper == "ROUTES": return self.reply("PATHS")
        if upper == "?": return self.reply("HELP")
        if upper == "INTERFACES": return self.status().get("interface_summary", "No runtime interface information is available.")
        if upper == "CLIENTS": return self.status().get("client_summary", "TCP clients: 0")
        if upper == "PATHS": return self.status().get("path_summary", f"Known paths: {self.status().get('paths', 0)}")
        if upper == "TEST": return self.choose(TEST)
        if upper == "JOKE": return self.choose(JOKES)
        if upper == "DADJOKE": return self.choose(DAD_JOKES)
        if upper == "FACT": return self.choose(FACTS)
        if upper == "COFFEE": return self.choose(COFFEE)
        if upper == "RADIO": return self.choose(RADIO)
        if upper == "VERSE": return self.verse("")
        if upper.startswith("VERSE "): return self.verse(command[6:])
        if upper == "8BALL": return "Ask me a question after 8BALL."
        if upper.startswith("8BALL "): return self.choose(EIGHT_BALL)
        return "I don't know that one yet. Send HELP for the command list."

class EchoBot:
    def __init__(self):
        self.router = self.destination = None
        self.counters = {"messages_received": 0, "replies_sent": 0, "command_count": 0, "rate_limited_count": 0, "last_command": None, "last_announce": None, "last_message_received": None, "last_reply_sent": None, "last_reply_error": None, "activity": []}
        self.started_at = int(time.time()); self.sender_windows = {}; self.processor = CommandProcessor(self.safe_status)
    def activity(self, text):
        self.counters["activity"] = [{"at": int(time.time()), "message": text}] + self.counters["activity"][:19]
    def safe_status(self):
        try: snapshot = json.loads(RUNTIME_STATUS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): snapshot = {}
        status = snapshot.get("status") or {}; interfaces = status.get("interfaces") or []
        active = [row for row in interfaces if row.get("status") is True]
        names = [f"- {row.get('short_name') or row.get('name')}: Up" for row in active[:8]]
        # LocalServerInterface.clients counts shared-instance programs, not
        # inbound TCP peers. Only a TCP server reports the latter directly.
        clients = sum((row.get("clients") or 0) for row in interfaces if row.get("type") == "TCPServerInterface")
        transport_active = bool(status.get("transport_id") or status.get("transport_instance") or status.get("transport_enabled"))
        return {"reticulum": "Ready" if status else "Unavailable", "bot": "Ready", "transport": "On" if transport_active else "Off", "paths": len(snapshot.get("paths") or []), "interfaces": len(active), "clients": clients, "uptime": self.uptime(), "interface_summary": "Interfaces\n" + ("\n".join(names) if names else "No active interfaces reported."), "client_summary": f"TCP clients: {clients}", "path_summary": f"Known paths: {len(snapshot.get('paths') or [])}"}
    def uptime(self):
        seconds = int(time.time()) - self.started_at
        hours, minutes = divmod(seconds // 60, 60)
        return f"{hours}h {minutes}m" if hours else f"{minutes}m"
    def write_status(self, ready=False, error=None):
        """Report readiness separately from the supervised process being alive.

        A container can be running while it is deliberately waiting for rnsd.
        Calling that state "Running" in the GUI was misleading during startup.
        """
        atomic_json(STATUS / "status.json", {
            "running": bool(ready),
            "process_running": True,
            "state": "ready" if ready else "waiting",
            "shared_instance_connected": ready,
            "lxmf_router_ready": bool(self.router),
            "destination": self.destination.hash.hex() if self.destination else None,
            "identity": self.identity.hash.hex() if getattr(self, "identity", None) else None,
            "display_name": read_settings()["display_name"], "tagline": read_settings()["tagline"], "uptime": self.uptime(),
            "error": error,
            **self.counters,
        })
    def announce(self, reason):
        # LXMF 0.9.6 announces accept only a destination hash/interface.
        # Keep the local tagline for a future compatible runtime; never fake
        # unsupported announce payload formatting on the wire.
        self.router.announce(self.destination.hash)
        self.counters["last_announce"] = int(time.time()); self.activity(f"{reason} announce sent")
    def allowed_sender(self, source_hash):
        key = source_hash.hex() if hasattr(source_hash, "hex") else str(source_hash)
        now_time = time.time(); window = [stamp for stamp in self.sender_windows.get(key, []) if now_time - stamp < 60]
        limit = read_settings()["rate_limit_per_minute"]
        if len(window) >= limit:
            self.sender_windows[key] = window; self.counters["rate_limited_count"] += 1; self.activity("Message rate limited"); return False
        window.append(now_time); self.sender_windows[key] = window; return True
    def received(self, message):
        # LXMF attaches the sender's replyable outbound destination directly
        # to received messages. Reconstructing it from an identity hash is
        # both unnecessary and unreliable when that identity is not cached.
        source_destination = getattr(message, "source", None)
        source_hash = getattr(message, "source_hash", None) or getattr(source_destination, "hash", None)
        if not source_hash or (self.destination and source_hash == self.destination.hash) or not self.allowed_sender(source_hash): return
        if not read_settings()["enabled"]: return
        self.counters["messages_received"] += 1; self.counters["command_count"] += 1; self.counters["last_message_received"] = int(time.time()); self.activity("Message received")
        text = message.content_as_string() if hasattr(message, "content_as_string") else str(getattr(message, "content", ""))
        reply = self.processor.reply(text)[:MAX_REPLY]
        self.counters["last_command"] = (text or "").strip().split(maxsplit=1)[0].upper()[:24] or "TEXT"
        if source_destination is None:
            self.counters["last_reply_error"] = "Reply destination unavailable"
            self.activity("Reply destination unavailable")
            return
        try:
            import LXMF
            outbound = LXMF.LXMessage(source_destination, self.destination, reply, desired_method=LXMF.LXMessage.DIRECT)
            self.router.handle_outbound(outbound); self.counters["replies_sent"] += 1; self.counters["last_reply_sent"] = int(time.time()); self.counters["last_reply_error"] = None; self.activity("Reply sent")
        except Exception:
            self.counters["last_reply_error"] = "Reply delivery failed"
            self.activity("Reply delivery failed")
    def run(self):
        import RNS, LXMF
        STATE.mkdir(parents=True, exist_ok=True); CONFIG.mkdir(parents=True, exist_ok=True)
        # Do not instantiate Reticulum until the managed launcher has verified
        # rnsd as the shared instance. This prevents a second local master.
        while not Path("/reticulum/admin-status/rnsd.ready").exists():
            self.write_status(False, "Waiting for managed Reticulum shared instance")
            time.sleep(2)
        # This service is an RNS client only. ``require_shared_instance`` is
        # essential: without it Reticulum falls back to becoming a master when
        # the shared socket cannot be reached, which would reopen every rnsd
        # interface from the bot container.
        RNS.Reticulum(configdir="/reticulum", require_shared_instance=True)
        identity_path = STATE / "identity"
        self.identity = RNS.Identity.from_file(str(identity_path)) if identity_path.exists() else RNS.Identity()
        if not identity_path.exists(): self.identity.to_file(str(identity_path))
        self.router = LXMF.LXMRouter(storagepath=str(STATE / "lxmf"))
        self.destination = self.router.register_delivery_identity(self.identity, display_name=read_settings()["display_name"])
        self.router.register_delivery_callback(self.received)
        last_announce = 0; announced_start = False; previous_display_name = read_settings()["display_name"]
        while True:
            settings = read_settings(); current = time.time()
            try:
                if settings["enabled"] and settings["display_name"] != previous_display_name:
                    # Keep the same identity/destination; update presentation then announce it.
                    if hasattr(self.destination, "display_name"): self.destination.display_name = settings["display_name"]
                    self.announce("Display name updated"); previous_display_name = settings["display_name"]
                if settings["enabled"] and settings["announce_on_startup"] and not announced_start:
                    self.announce("Startup"); announced_start = True; last_announce = current
                if settings["enabled"] and settings["periodic_announce"] and current-last_announce >= settings["interval_minutes"]*60:
                    self.announce("Periodic"); last_announce = current
                request = CONFIG / "announce-request"
                if request.exists() and settings["enabled"]:
                    request.unlink(missing_ok=True); self.announce("Manual"); last_announce = current
                self.write_status(True); time.sleep(2)
            except Exception as error:
                self.write_status(False, "Bot runtime error"); time.sleep(5)

if __name__ == "__main__":
    try:
        EchoBot().run()
    except Exception as error:
        # Never include config or message data here. The exception text is
        # retained only so the authenticated admin can distinguish a shared
        # instance connection problem from a generic waiting state.
        atomic_json(STATUS / "status.json", {
            "running": False,
            "process_running": False,
            "state": "failed",
            "shared_instance_connected": False,
            "lxmf_router_ready": False,
            "error": f"Bot startup failed: {error}",
        })
        raise
