"""Persistent, non-administrative LXMF endpoint for the web Messaging UI."""
import json, os, re, sqlite3, time, uuid
from pathlib import Path

STATE = Path(os.environ.get("MESSAGING_STATE_DIR", "/messaging"))
REQUESTS = Path(os.environ.get("MESSAGING_REQUEST_DIR", "/requests"))
RETICULUM_READY = Path("/reticulum/admin-status/rnsd.ready")
DEFAULTS = {"enabled": True, "display_name": "Reticulum Admin User", "announce_on_startup": True, "periodic_announce": False, "interval_minutes": 60}
MIN_INTERVAL, MAX_BODY = 15, 4000
PATH_LOOKUP_SECONDS = max(1, min(30, int(os.environ.get("MESSAGING_PATH_LOOKUP_SECONDS", "10"))))
DESTINATION_RE = re.compile(r"[0-9a-fA-F]{32}")
TERMINAL_OUTBOUND_STATES = {"Delivered", "Failed"}

def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8"); os.replace(tmp, path)

def settings():
    try: value = json.loads((REQUESTS / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): value = {}
    result = {**DEFAULTS, **{k: value[k] for k in DEFAULTS if k in value}}
    try: result["interval_minutes"] = max(MIN_INTERVAL, int(result["interval_minutes"]))
    except (TypeError, ValueError): result["interval_minutes"] = DEFAULTS["interval_minutes"]
    return result

def connect():
    STATE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STATE / "messages.db"); con.row_factory = sqlite3.Row; con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
      CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_key TEXT NOT NULL, direction TEXT NOT NULL, sender_destination TEXT, recipient_destination TEXT, sender_name TEXT, body TEXT NOT NULL, received_at INTEGER NOT NULL, read_at INTEGER, delivery_state TEXT NOT NULL, error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS contacts (id INTEGER PRIMARY KEY, name TEXT NOT NULL, destination TEXT NOT NULL UNIQUE, notes TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS announced_peers (destination_hash TEXT PRIMARY KEY, identity_hash TEXT, display_name TEXT, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, last_hops INTEGER, announce_count INTEGER NOT NULL DEFAULT 1);
      CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages(conversation_key, received_at);
      CREATE INDEX IF NOT EXISTS announced_peers_last_seen_idx ON announced_peers(last_seen DESC);
      CREATE INDEX IF NOT EXISTS announced_peers_display_name_idx ON announced_peers(display_name);
    """)
    return con

class LXMFDeliveryAnnounceHandler:
    """Passive handler for the documented Reticulum lxmf.delivery aspect."""
    aspect_filter = "lxmf.delivery"
    def __init__(self, service): self.service = service
    def received_announce(self, destination_hash, announced_identity, app_data, announce_packet_hash):
        self.service.receive_announce(destination_hash, announced_identity, app_data)

class Messaging:
    def __init__(self):
        self.router = self.destination = self.identity = self.announce_handler = None; self.started = int(time.time())
        # In-memory marker for this process only. A fresh process deliberately
        # retries interrupted Pending/Queued rows once, since LXMF callbacks
        # do not survive a service restart.
        self.session_id = uuid.uuid4().hex; self.inflight_message_ids = set()
        self.state = {"last_message_received": None, "last_message_sent": None, "last_error": None, "last_announce": None}
    def status(self, ready=False):
        with connect() as con: pending = con.execute("SELECT count(*) FROM messages WHERE direction='outbound' AND delivery_state IN ('Pending','Queued')").fetchone()[0]
        atomic_json(STATE / "status.json", {"running": ready, "process_running": True, "shared_instance_connected": ready, "lxmf_router_ready": bool(self.router), "announce_listener_registered": bool(self.announce_handler), "identity_loaded": bool(self.identity), "destination": self.destination.hash.hex() if self.destination else None, "identity": self.identity.hash.hex() if self.identity else None, "display_name": settings()["display_name"], "uptime_seconds": int(time.time())-self.started, "pending_outbound": pending, **self.state})
    def announce(self):
        self.router.announce(self.destination.hash); self.state["last_announce"] = int(time.time()); self.state["last_error"] = None
    def receive(self, message):
        source = getattr(message, "source", None); source_hash = getattr(message, "source_hash", None) or getattr(source, "hash", None)
        if not settings()["enabled"] or not source_hash or (self.destination and source_hash == self.destination.hash): return
        body = (message.content_as_string() if hasattr(message, "content_as_string") else str(getattr(message, "content", "")))[:MAX_BODY]
        if not body: return
        sender = source_hash.hex() if hasattr(source_hash, "hex") else str(source_hash); stamp = int(time.time())
        with connect() as con: con.execute("INSERT INTO messages(id,conversation_key,direction,sender_destination,recipient_destination,sender_name,body,received_at,delivery_state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, sender, "inbound", sender, self.destination.hash.hex(), getattr(source, "display_name", None), body, stamp, "Received", stamp, stamp))
        self.state["last_message_received"] = stamp; self.state["last_error"] = None

    def receive_announce(self, destination_hash, announced_identity=None, app_data=None):
        """Store public LXMF delivery announce metadata; never take action on it."""
        try:
            import RNS, LXMF
            destination = destination_hash.hex() if hasattr(destination_hash, "hex") else str(destination_hash)
            if not DESTINATION_RE.fullmatch(destination): return
            identity = getattr(announced_identity, "hash", None)
            identity_hash = identity.hex().lower() if hasattr(identity, "hex") else None
            try: display_name = LXMF.display_name_from_app_data(app_data)
            except Exception: display_name = None
            if not isinstance(display_name, str): display_name = None
            elif not display_name.strip(): display_name = None
            else: display_name = display_name.strip()[:80]
            try:
                hops = RNS.Transport.hops_to(destination_hash)
                if hops == RNS.Transport.PATHFINDER_M: hops = None
            except Exception: hops = None
            stamp = int(time.time())
            with connect() as con:
                con.execute("INSERT INTO announced_peers(destination_hash,identity_hash,display_name,first_seen,last_seen,last_hops,announce_count) VALUES(?,?,?,?,?,?,1) ON CONFLICT(destination_hash) DO UPDATE SET identity_hash=COALESCE(excluded.identity_hash,announced_peers.identity_hash),display_name=COALESCE(excluded.display_name,announced_peers.display_name),last_seen=excluded.last_seen,last_hops=COALESCE(excluded.last_hops,announced_peers.last_hops),announce_count=announced_peers.announce_count+1", (destination.lower(), identity_hash, display_name, stamp, stamp, hops))
        except Exception:
            # Announces are advisory public metadata. A malformed announce must
            # never interrupt messaging or expose private diagnostic details.
            return

    def register_announce_listener(self, RNS):
        """Register exactly one passive, documented LXMF delivery listener."""
        self.announce_handler = LXMFDeliveryAnnounceHandler(self)
        RNS.Transport.register_announce_handler(self.announce_handler)

    def update_outbound(self, message_id, delivery_state, error=None):
        """Persist an asynchronous LXMF result against its original request ID."""
        stamp = int(time.time())
        with connect() as con:
            con.execute(
                "UPDATE messages SET delivery_state=?,error=?,updated_at=? WHERE id=?",
                (delivery_state, error, stamp, message_id),
            )
        if delivery_state == "Delivered":
            self.state["last_message_sent"] = stamp
            self.state["last_error"] = None
        elif delivery_state == "Failed":
            self.state["last_error"] = "Last outbound message could not be sent"

    def recover_interrupted_outbound(self):
        """Retry non-terminal work once after this service process starts.

        LXMF 0.9.6 delivery callbacks are in-memory, so an abrupt service
        stop cannot tell us whether a previously Queued message completed.
        Retrying on the next process is preferable to silently stranding it.
        It can theoretically cause a duplicate network delivery in the tiny
        crash-at-delivery window; the SQLite row itself remains exactly once.
        """
        with connect() as con:
            rows = con.execute("SELECT id,recipient_destination,body FROM messages WHERE direction='outbound' AND delivery_state IN ('Pending','Queued') ORDER BY created_at").fetchall()
        for row in rows:
            self.send({"id": row["id"], "destination": row["recipient_destination"], "body": row["body"]}, recovering=True)

    def send(self, item, recovering=False):
        message_id, target, body = item.get("id"), item.get("destination", ""), item.get("body", "")[:MAX_BODY]
        stamp = int(time.time())
        with connect() as con:
            existing = con.execute("SELECT delivery_state,recipient_destination,body FROM messages WHERE id=?", (message_id,)).fetchone() if message_id else None
            if existing:
                if existing["delivery_state"] in TERMINAL_OUTBOUND_STATES or message_id in self.inflight_message_ids:
                    return True
                # Only explicit startup recovery is allowed to resubmit an
                # existing non-terminal record. Normal request-file replay in
                # this process must never double-send it.
                if not recovering: return True
                target, body = existing["recipient_destination"], existing["body"]
            elif not message_id or not DESTINATION_RE.fullmatch(target) or not body.strip():
                return False
            else:
                con.execute("INSERT INTO messages(id,conversation_key,direction,sender_destination,recipient_destination,body,received_at,delivery_state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (message_id, target.lower(), "outbound", self.destination.hash.hex() if self.destination else None, target.lower(), body, stamp, "Pending", stamp, stamp))
        if not DESTINATION_RE.fullmatch(target) or not body.strip():
            self.update_outbound(message_id, "Failed", "Stored outbound message is invalid.")
            return True
        try:
            import RNS, LXMF
            destination_hash = bytes.fromhex(target)
            identity = RNS.Identity.recall(destination_hash)
            if identity is None:
                RNS.Transport.request_path(destination_hash)
                deadline = time.monotonic() + PATH_LOOKUP_SECONDS
                while identity is None and time.monotonic() < deadline:
                    time.sleep(0.25); identity = RNS.Identity.recall(destination_hash)
            if identity is None:
                self.update_outbound(message_id, "Failed", "No usable Reticulum route was available before the lookup timeout.")
                return True
        except Exception:
            self.update_outbound(message_id, "Failed", "No usable Reticulum route was available before the lookup timeout.")
            return True
        try:
            destination = RNS.Destination(identity, RNS.Destination.OUT, RNS.Destination.SINGLE, "lxmf", "delivery")
            outbound = LXMF.LXMessage(destination, self.destination, body, desired_method=LXMF.LXMessage.DIRECT)
            outbound.register_delivery_callback(lambda *_: self.update_outbound(message_id, "Delivered"))
            outbound.register_failed_callback(lambda *_: self.update_outbound(message_id, "Failed", "LXMF delivery failed."))
            # Router acceptance only means LXMF has queued the message. The
            # delivery callback is the sole source of a Delivered state.
            with connect() as con: con.execute("UPDATE messages SET delivery_state='Queued',updated_at=? WHERE id=?", (stamp, message_id))
            self.inflight_message_ids.add(message_id)
            self.router.handle_outbound(outbound)
        except Exception:
            self.update_outbound(message_id, "Failed", "LXMF submission failed.")
        return True
    def process_requests(self):
        REQUESTS.mkdir(parents=True, exist_ok=True)
        for request in sorted(REQUESTS.glob("outbound-*.json")):
            try: item = json.loads(request.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError): continue
            # Delete only after send() has committed the durable, idempotent
            # message record. A crash before then leaves a replayable request.
            if self.send(item): request.unlink(missing_ok=True)
        for request in sorted(REQUESTS.glob("read-*.json")):
            try: item = json.loads(request.read_text(encoding="utf-8")); key = item["conversation_key"]
            except (OSError, json.JSONDecodeError, KeyError): continue
            with connect() as con: con.execute("UPDATE messages SET read_at=? WHERE conversation_key=? AND direction='inbound' AND read_at IS NULL", (int(time.time()), key))
            request.unlink(missing_ok=True)
        for request in sorted(REQUESTS.glob("contact-*.json")):
            try: item = json.loads(request.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError): continue
            destination = item.get("destination", "")
            with connect() as con:
                if request.name.startswith("contact-delete-"): con.execute("DELETE FROM contacts WHERE destination=?", (destination,))
                elif DESTINATION_RE.fullmatch(destination) and item.get("name"):
                    stamp = int(time.time()); con.execute("INSERT INTO contacts(name,destination,notes,created_at,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(destination) DO UPDATE SET name=excluded.name,notes=excluded.notes,updated_at=excluded.updated_at", (item["name"][:100], destination.lower(), item.get("notes", "")[:500], stamp, stamp))
                else:
                    continue
            request.unlink(missing_ok=True)
        announce = REQUESTS / "announce-request"
        if announce.exists():
            self.announce()
            announce.unlink(missing_ok=True)
    def run(self):
        import RNS, LXMF
        STATE.mkdir(parents=True, exist_ok=True); REQUESTS.mkdir(parents=True, exist_ok=True)
        while not RETICULUM_READY.exists(): self.status(False); time.sleep(2)
        RNS.Reticulum(configdir="/reticulum", require_shared_instance=True)
        identity_file = STATE / "identity"
        self.identity = RNS.Identity.from_file(str(identity_file)) if identity_file.exists() else RNS.Identity()
        if not identity_file.exists(): self.identity.to_file(str(identity_file)); os.chmod(identity_file, 0o600)
        self.router = LXMF.LXMRouter(storagepath=str(STATE / "lxmf")); self.destination = self.router.register_delivery_identity(self.identity, display_name=settings()["display_name"]); self.router.register_delivery_callback(self.receive)
        self.register_announce_listener(RNS)
        announced, last_periodic, previous_name, recovery_attempted = False, 0, settings()["display_name"], False
        while True:
            try:
                current, current_settings = time.time(), settings()
                if current_settings["display_name"] != previous_name:
                    if hasattr(self.destination, "display_name"): self.destination.display_name = current_settings["display_name"]
                    self.announce(); previous_name = current_settings["display_name"]
                if current_settings["enabled"] and not recovery_attempted:
                    self.recover_interrupted_outbound(); recovery_attempted = True
                if current_settings["enabled"] and current_settings["announce_on_startup"] and not announced: self.announce(); announced = True; last_periodic = current
                if current_settings["enabled"] and current_settings["periodic_announce"] and current-last_periodic >= current_settings["interval_minutes"]*60: self.announce(); last_periodic = current
                if current_settings["enabled"]: self.process_requests()
                self.status(True); time.sleep(2)
            except Exception: self.state["last_error"] = "Messaging runtime error"; self.status(False); time.sleep(5)

if __name__ == "__main__": Messaging().run()
