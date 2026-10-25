"""Persistent, non-administrative LXMF endpoint for the web Messaging UI."""
import base64, json, os, re, sqlite3, time, uuid
from pathlib import Path
from urllib.parse import urlparse

STATE = Path(os.environ.get("MESSAGING_STATE_DIR", "/messaging"))
REQUESTS = Path(os.environ.get("MESSAGING_REQUEST_DIR", "/requests"))
RETICULUM_READY = Path("/reticulum/admin-status/rnsd.ready")
DEFAULTS = {"enabled": True, "display_name": "Reticulum Admin User", "announce_on_startup": True, "periodic_announce": False, "interval_minutes": 60}
MIN_INTERVAL, MAX_BODY = 15, 4000
PATH_LOOKUP_SECONDS = max(1, min(30, int(os.environ.get("MESSAGING_PATH_LOOKUP_SECONDS", "10"))))
DESTINATION_RE = re.compile(r"[0-9a-fA-F]{32}")
TERMINAL_OUTBOUND_STATES = {"Delivered", "Failed"}
APPLICATION_ASPECTS = ("lxmf.delivery", "lxmf.propagation", "nomadnetwork.node")
PUSH_DIAGNOSTICS_DEFAULTS = {"last_push_attempt": None, "last_provider_result": None, "last_provider_http_status": None, "last_provider_accepted": None, "push_attempts": 0, "provider_accepts": 0, "provider_failures": 0, "stale_subscriptions_removed": 0}

def vapid_subject():
    """Return a safe VAPID contact URI or None; never invent a localhost one."""
    value = os.environ.get("WEB_PUSH_VAPID_SUBJECT", "").strip()
    if not value or len(value) > 512 or any(character.isspace() for character in value): return None
    parsed = urlparse(value)
    if parsed.scheme == "https" and parsed.hostname:
        hostname = parsed.hostname.lower()
        if hostname not in {"localhost", "127.0.0.1", "::1"} and not hostname.endswith(".localhost"):
            return value
    if parsed.scheme == "mailto" and parsed.path and "@" in parsed.path and not parsed.netloc:
        return value
    return None

def push_navigate_url(destination):
    """Prefer the configured HTTPS origin for declarative notification taps."""
    subject = vapid_subject()
    parsed = urlparse(subject or "")
    path = "/messaging/conversations/" + destination.lower()
    return f"{parsed.scheme}://{parsed.netloc}{path}" if parsed.scheme == "https" else path

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
      CREATE TABLE IF NOT EXISTS announced_peers (destination_hash TEXT PRIMARY KEY, identity_hash TEXT, display_name TEXT, message_display_name TEXT, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, last_hops INTEGER, announce_count INTEGER NOT NULL DEFAULT 1);
      CREATE TABLE IF NOT EXISTS application_announces (destination_hash TEXT NOT NULL, aspect TEXT NOT NULL, identity_hash TEXT, display_name TEXT, first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL, last_hops INTEGER, announce_count INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(destination_hash, aspect));
      CREATE TABLE IF NOT EXISTS push_subscriptions (endpoint TEXT PRIMARY KEY, subscription_json TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS inbound_message_ids (source_message_id TEXT PRIMARY KEY, received_at INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS messages_conversation_idx ON messages(conversation_key, received_at);
      CREATE INDEX IF NOT EXISTS announced_peers_last_seen_idx ON announced_peers(last_seen DESC);
      CREATE INDEX IF NOT EXISTS announced_peers_display_name_idx ON announced_peers(display_name);
      CREATE INDEX IF NOT EXISTS application_announces_last_seen_idx ON application_announces(last_seen DESC);
      CREATE INDEX IF NOT EXISTS application_announces_aspect_idx ON application_announces(aspect, last_seen DESC);
    """)
    try: con.execute("ALTER TABLE announced_peers ADD COLUMN message_display_name TEXT")
    except sqlite3.OperationalError: pass
    return con

def web_push_preview(value):
    """A short, plain-text preview suitable for a provider-visible payload."""
    text = re.sub(r"<[^>]*>", "", str(value or ""))
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return " ".join(re.sub(r"[`*_~>#]", "", text).split())[:140]

def subscription_is_valid(subscription):
    if not isinstance(subscription, dict) or set(subscription) - {"endpoint", "keys", "expirationTime"}: return False
    endpoint, keys = subscription.get("endpoint"), subscription.get("keys")
    if not isinstance(endpoint, str) or not endpoint.startswith("https://") or len(endpoint) > 2048: return False
    if not isinstance(keys, dict) or set(keys) != {"p256dh", "auth"}: return False
    return all(isinstance(keys.get(name), str) and 1 <= len(keys[name]) <= 1024 and re.fullmatch(r"[A-Za-z0-9_-]+", keys[name]) for name in ("p256dh", "auth"))

def stable_message_id(message):
    """Use LXMF's message hash when exposed; never notify twice for that hash."""
    value = getattr(message, "hash", None) or getattr(message, "message_hash", None)
    if isinstance(value, bytes): return value.hex()
    if hasattr(value, "hex"): return value.hex()
    return str(value) if value else None

def push_diagnostics():
    try: value = json.loads((STATE / "webpush-diagnostics.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): value = {}
    return {**PUSH_DIAGNOSTICS_DEFAULTS, **{key: value[key] for key in PUSH_DIAGNOSTICS_DEFAULTS if key in value}}

class ApplicationAnnounceHandler:
    """Passive observer for one documented Reticulum application aspect."""
    def __init__(self, service, aspect): self.service, self.aspect_filter = service, aspect
    def received_announce(self, destination_hash, announced_identity, app_data, announce_packet_hash):
        self.service.receive_application_announce(self.aspect_filter, destination_hash, announced_identity, app_data)

class Messaging:
    def __init__(self):
        self.router = self.destination = self.identity = self.announce_handler = None; self.announce_handlers = []; self.started = int(time.time())
        # In-memory marker for this process only. A fresh process deliberately
        # retries interrupted Pending/Queued rows once, since LXMF callbacks
        # do not survive a service restart.
        self.session_id = uuid.uuid4().hex; self.inflight_message_ids = set()
        self.state = {"last_message_received": None, "last_message_sent": None, "last_error": None, "last_announce": None, "last_lxmf_delivery_announce": None, "lxmf_delivery_announces_observed": 0, "last_application_announce": None, "application_announces_observed": 0, "application_announce_counts": {aspect: 0 for aspect in APPLICATION_ASPECTS}, **push_diagnostics()}
        self.vapid_public_key = None
    def ensure_vapid_key(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat, load_pem_private_key
        key = STATE / "webpush-vapid.pem"
        private = load_pem_private_key(key.read_bytes(), None) if key.exists() else ec.generate_private_key(ec.SECP256R1())
        if not key.exists(): key.write_bytes(private.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())); os.chmod(key, 0o600)
        else: os.chmod(key, 0o600)
        self.vapid_public_key = base64.urlsafe_b64encode(private.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)).rstrip(b"=").decode()
    def status(self, ready=False):
        with connect() as con:
            pending = con.execute("SELECT count(*) FROM messages WHERE direction='outbound' AND delivery_state IN ('Pending','Queued')").fetchone()[0]
            stored_peers = con.execute("SELECT count(*) FROM announced_peers WHERE announce_count > 0").fetchone()[0]
            unique_destinations = con.execute("SELECT count(DISTINCT destination_hash) FROM application_announces").fetchone()[0]
        subject = vapid_subject()
        atomic_json(STATE / "status.json", {"running": ready, "process_running": True, "shared_instance_connected": ready, "lxmf_router_ready": bool(self.router), "announce_listener_registered": bool(self.announce_handlers), "identity_loaded": bool(self.identity), "destination": self.destination.hash.hex() if self.destination else None, "identity": self.identity.hash.hex() if self.identity else None, "display_name": settings()["display_name"], "web_push_public_key": self.vapid_public_key, "web_push_configured": bool(subject), "web_push_subject": subject, "uptime_seconds": int(time.time())-self.started, "pending_outbound": pending, "unique_lxmf_delivery_peers": stored_peers, "unique_application_announce_destinations": unique_destinations, **self.state})
    def announce(self):
        self.router.announce(self.destination.hash); self.state["last_announce"] = int(time.time()); self.state["last_error"] = None
    def record_push_result(self, result, http_status=None, accepted=False, stale_removed=False, attempted=True):
        """Persist bounded provider observations, never payloads or endpoints."""
        stamp = int(time.time())
        if attempted: self.state["last_push_attempt"] = stamp
        self.state["last_provider_result"] = result
        self.state["last_provider_http_status"] = http_status
        if attempted: self.state["push_attempts"] += 1
        if accepted:
            self.state["provider_accepts"] += 1; self.state["last_provider_accepted"] = stamp
        elif result not in {"Configuration error"}:
            self.state["provider_failures"] += 1
        if stale_removed: self.state["stale_subscriptions_removed"] += 1
        atomic_json(STATE / "webpush-diagnostics.json", {key: self.state[key] for key in PUSH_DIAGNOSTICS_DEFAULTS})
    def receive(self, message):
        source = getattr(message, "source", None); source_hash = getattr(message, "source_hash", None) or getattr(source, "hash", None)
        if not settings()["enabled"] or not source_hash or (self.destination and source_hash == self.destination.hash): return
        body = (message.content_as_string() if hasattr(message, "content_as_string") else str(getattr(message, "content", "")))[:MAX_BODY]
        if not body: return
        sender = source_hash.hex() if hasattr(source_hash, "hex") else str(source_hash); stamp = int(time.time())
        sender_name = getattr(source, "display_name", None)
        if not isinstance(sender_name, str) or not sender_name.strip(): sender_name = None
        elif len(sender_name.strip()) > 80: sender_name = sender_name.strip()[:80]
        else: sender_name = sender_name.strip()
        source_id = stable_message_id(message)
        with connect() as con:
            # A replayed LXMF callback must not create another row or another
            # provider notification. Older LXMF objects without a hash remain
            # safely accepted once, but cannot be proven duplicate here.
            if source_id and con.execute("INSERT OR IGNORE INTO inbound_message_ids(source_message_id,received_at) VALUES(?,?)", (source_id, stamp)).rowcount != 1:
                return
            contact = con.execute("SELECT name FROM contacts WHERE destination=?", (sender.lower(),)).fetchone() if DESTINATION_RE.fullmatch(sender) else None
            announced = con.execute("SELECT display_name,message_display_name FROM announced_peers WHERE destination_hash=?", (sender.lower(),)).fetchone() if DESTINATION_RE.fullmatch(sender) else None
            push_sender = ((contact["name"] if contact else None) or sender_name or (announced["display_name"] if announced else None) or (announced["message_display_name"] if announced else None) or sender[:12])
            con.execute("INSERT INTO messages(id,conversation_key,direction,sender_destination,recipient_destination,sender_name,body,received_at,delivery_state,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, sender, "inbound", sender, self.destination.hash.hex(), sender_name, body, stamp, "Received", stamp, stamp))
            # Message-provided names are informational only. They are retained
            # separately so a later LXMF announce remains the preferred name.
            if DESTINATION_RE.fullmatch(sender) and sender_name:
                con.execute("INSERT INTO announced_peers(destination_hash,message_display_name,first_seen,last_seen,last_hops,announce_count) VALUES(?,?,?,?,?,0) ON CONFLICT(destination_hash) DO UPDATE SET message_display_name=excluded.message_display_name", (sender.lower(), sender_name, stamp, stamp, None))
        self.state["last_message_received"] = stamp; self.state["last_error"] = None
        self.send_push(push_sender, body, sender)

    def send_push(self, sender, body, destination):
        subject = vapid_subject()
        if not subject:
            self.state["last_error"] = "Web Push is not configured: set a valid WEB_PUSH_VAPID_SUBJECT."
            self.record_push_result("Configuration error", attempted=False)
            return
        try:
            from pywebpush import WebPushException, webpush
            with connect() as con:
                rows = con.execute("SELECT endpoint,subscription_json FROM push_subscriptions").fetchall()
                unread = con.execute("SELECT count(*) FROM messages WHERE direction='inbound' AND read_at IS NULL").fetchone()[0]
            navigate = push_navigate_url(destination)
            payload = json.dumps({"web_push": 8030, "notification": {"title": (sender or "Reticulum Admin")[:80], "body": web_push_preview(body), "tag": "conversation-" + destination.lower(), "navigate": navigate, "silent": False}, "app_badge": str(unread)})
            for row in rows:
                try:
                    response = webpush(json.loads(row["subscription_json"]), data=payload, vapid_private_key=str(STATE / "webpush-vapid.pem"), vapid_claims={"sub": subject}, ttl=120)
                    self.record_push_result("Accepted", getattr(response, "status_code", 201), accepted=True)
                except WebPushException as error:
                    status = getattr(getattr(error, "response", None), "status_code", None)
                    stale = status in (404, 410)
                    if stale:
                        with connect() as con: con.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (row["endpoint"],))
                    self.record_push_result("Rejected", status, stale_removed=stale)
        except Exception:
            # Never include subscription material or message text in status.
            self.state["last_error"] = "Web Push notification delivery failed"
            self.record_push_result("Failed")

    def receive_announce(self, destination_hash, announced_identity=None, app_data=None):
        """Compatibility entry point for the existing LXMF delivery observer."""
        self.receive_application_announce("lxmf.delivery", destination_hash, announced_identity, app_data)

    @staticmethod
    def nomad_name_from_app_data(app_data):
        """NomadNet Node.announce() publishes the UTF-8 node name directly."""
        if isinstance(app_data, bytes): value = app_data.decode("utf-8", errors="strict")
        elif isinstance(app_data, str): value = app_data
        else: return None
        value = value.strip()
        return value[:80] if value else None

    def receive_application_announce(self, aspect, destination_hash, announced_identity=None, app_data=None):
        """Persist public announce observations without acting on them."""
        try:
            import RNS, LXMF
            destination = destination_hash.hex() if hasattr(destination_hash, "hex") else str(destination_hash)
            if aspect not in APPLICATION_ASPECTS or not DESTINATION_RE.fullmatch(destination): return
            destination = destination.lower()
            self.state["application_announces_observed"] += 1
            self.state["application_announce_counts"][aspect] += 1
            identity = getattr(announced_identity, "hash", None)
            identity_hash = identity.hex().lower() if hasattr(identity, "hex") else None
            if aspect == "lxmf.delivery":
                try: display_name = LXMF.display_name_from_app_data(app_data)
                except Exception: display_name = None
            elif aspect == "nomadnetwork.node":
                try: display_name = self.nomad_name_from_app_data(app_data)
                except (UnicodeDecodeError, AttributeError): display_name = None
            else: display_name = None
            if not isinstance(display_name, str): display_name = None
            elif not display_name.strip(): display_name = None
            else: display_name = display_name.strip()[:80]
            try:
                hops = RNS.Transport.hops_to(destination_hash)
                if hops == RNS.Transport.PATHFINDER_M: hops = None
            except Exception: hops = None
            stamp = int(time.time())
            with connect() as con:
                con.execute("INSERT INTO application_announces(destination_hash,aspect,identity_hash,display_name,first_seen,last_seen,last_hops,announce_count) VALUES(?,?,?,?,?,?,?,1) ON CONFLICT(destination_hash,aspect) DO UPDATE SET identity_hash=COALESCE(excluded.identity_hash,application_announces.identity_hash),display_name=COALESCE(excluded.display_name,application_announces.display_name),last_seen=excluded.last_seen,last_hops=excluded.last_hops,announce_count=application_announces.announce_count+1", (destination, aspect, identity_hash, display_name, stamp, stamp, hops))
                if aspect == "lxmf.delivery":
                    con.execute("INSERT INTO announced_peers(destination_hash,identity_hash,display_name,first_seen,last_seen,last_hops,announce_count) VALUES(?,?,?,?,?,?,1) ON CONFLICT(destination_hash) DO UPDATE SET identity_hash=COALESCE(excluded.identity_hash,announced_peers.identity_hash),display_name=COALESCE(excluded.display_name,announced_peers.display_name),last_seen=excluded.last_seen,last_hops=excluded.last_hops,announce_count=announced_peers.announce_count+1", (destination, identity_hash, display_name, stamp, stamp, hops))
            self.state["last_application_announce"] = stamp
            if aspect == "lxmf.delivery":
                self.state["lxmf_delivery_announces_observed"] += 1; self.state["last_lxmf_delivery_announce"] = stamp
        except Exception:
            # Announces are advisory public metadata. A malformed announce must
            # never interrupt messaging or expose private diagnostic details.
            return

    def register_announce_listener(self, RNS):
        """Register only passive handlers for documented public app announces."""
        self.announce_handlers = [ApplicationAnnounceHandler(self, aspect) for aspect in APPLICATION_ASPECTS]
        self.announce_handler = self.announce_handlers[0]
        for handler in self.announce_handlers: RNS.Transport.register_announce_handler(handler)

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
    def process_announce_clear_requests(self):
        """Process the safe local-history clear even when messaging is disabled."""
        REQUESTS.mkdir(parents=True, exist_ok=True)
        # This deliberately clears only public, locally observed announce
        # metadata. It is processed here rather than by the web application so
        # Messaging remains the sole SQLite writer. Replaying the request is
        # harmless: both deletes are idempotent.
        for request in sorted(REQUESTS.glob("clear-announces-*.json")):
            try:
                item = json.loads(request.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if item.get("action") != "clear_observed_announces" or not isinstance(item.get("id"), str):
                continue
            with connect() as con:
                con.execute("DELETE FROM announced_peers")
                con.execute("DELETE FROM application_announces")
            request.unlink(missing_ok=True)

    def process_requests(self):
        self.process_announce_clear_requests()
        for request in sorted(REQUESTS.glob("push-subscribe-*.json")):
            try: item=json.loads(request.read_text()); sub=item["subscription"]; endpoint=sub["endpoint"]
            except (OSError,json.JSONDecodeError,KeyError,TypeError): continue
            if subscription_is_valid(sub):
                with connect() as con: con.execute("INSERT INTO push_subscriptions(endpoint,subscription_json,created_at,updated_at) VALUES(?,?,?,?) ON CONFLICT(endpoint) DO UPDATE SET subscription_json=excluded.subscription_json,updated_at=excluded.updated_at",(endpoint,json.dumps(sub),int(time.time()),int(time.time())))
                request.unlink(missing_ok=True)
        for request in sorted(REQUESTS.glob("push-unsubscribe-*.json")):
            try: endpoint=json.loads(request.read_text())["endpoint"]
            except (OSError,json.JSONDecodeError,KeyError,TypeError): continue
            if isinstance(endpoint, str) and endpoint.startswith("https://") and len(endpoint) <= 2048:
                with connect() as con: con.execute("DELETE FROM push_subscriptions WHERE endpoint=?", (endpoint,))
                request.unlink(missing_ok=True)
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
        self.ensure_vapid_key()
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
                # Clearing locally retained public observations does not send
                # or receive LXMF traffic, so it remains available even when
                # the operator has disabled Messaging.
                self.process_announce_clear_requests()
                if current_settings["enabled"]: self.process_requests()
                self.status(True); time.sleep(2)
            except Exception: self.state["last_error"] = "Messaging runtime error"; self.status(False); time.sleep(5)

if __name__ == "__main__": Messaging().run()
