"""Small, deliberately non-administrative LXMF echo destination."""
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

MIN_INTERVAL = 15
STATE = Path(os.environ.get("BOT_STATE_DIR", "/bot"))
STATUS = Path(os.environ.get("BOT_STATUS_DIR", "/status"))
CONFIG = Path(os.environ.get("BOT_CONFIG_DIR", "/config"))

DEFAULTS = {"enabled": True, "display_name": "Reticulum Admin Test Bot", "announce_on_startup": True, "periodic_announce": True, "interval_minutes": 60}

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
    return settings

class CommandProcessor:
    """Pure replies make command safety and test coverage independent of LXMF."""
    def __init__(self, status): self.status = status
    def reply(self, text):
        command = (text or "").strip()
        upper = command.upper()
        if upper == "PING": return "PONG"
        if upper == "HELP": return "PING\nECHO <text>\nHELP\nSTATUS"
        if upper == "STATUS":
            value = self.status()
            return "Reticulum Admin Test Bot\nTransport: {transport}\nKnown paths: {paths}\nInterfaces up: {interfaces}\nConnected TCP clients: {clients}".format(**value)
        if upper.startswith("ECHO "): return "Echo: " + command[5:]
        return "Echo: " + command

class EchoBot:
    def __init__(self):
        self.router = self.destination = None
        self.counters = {"messages_received": 0, "replies_sent": 0, "last_announce": None, "last_message_received": None, "last_reply_sent": None, "activity": []}
    def activity(self, text):
        self.counters["activity"] = [{"at": int(time.time()), "message": text}] + self.counters["activity"][:19]
    def safe_status(self):
        try: snapshot = json.loads((Path("/reticulum/admin-status/snapshot.json")).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): snapshot = {}
        status = snapshot.get("status") or {}; interfaces = status.get("interfaces") or []
        return {"transport": "On" if bool(status.get("transport_instance") or status.get("transport_enabled")) else "Off", "paths": len(snapshot.get("paths") or []), "interfaces": sum(1 for row in interfaces if row.get("status") is True), "clients": sum((row.get("clients") or 0) for row in interfaces)}
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
            "error": error,
            **self.counters,
        })
    def announce(self, reason):
        self.router.announce(self.destination.hash); self.counters["last_announce"] = int(time.time()); self.activity(f"{reason} announce sent")
    def received(self, message):
        self.counters["messages_received"] += 1; self.counters["last_message_received"] = int(time.time()); self.activity("Message received")
        text = message.content_as_string() if hasattr(message, "content_as_string") else str(getattr(message, "content", ""))
        reply = CommandProcessor(self.safe_status).reply(text)
        try:
            import RNS, LXMF
            source = RNS.Identity.recall(message.source_hash)
            if source is None: return
            destination = RNS.Destination(source, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
            outbound = LXMF.LXMessage(destination, self.destination, reply, desired_method=LXMF.LXMessage.DIRECT)
            self.router.handle_outbound(outbound); self.counters["replies_sent"] += 1; self.counters["last_reply_sent"] = int(time.time()); self.activity("Reply sent")
        except Exception as error: self.activity("Reply failed")
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
        last_announce = 0; announced_start = False
        while True:
            settings = read_settings(); current = time.time()
            try:
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
