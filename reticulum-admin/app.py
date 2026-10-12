import json
import os
import re
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
import socket
import platform
import time
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from reticulum_adapter import find_interface_records, inbound_tcp_child_records

APP_DATA = Path(os.environ.get("APP_DATA", "/var/lib/reticulum-admin"))
DB_PATH = APP_DATA / "admin.db"
RETICULUM_DIR = Path(os.environ.get("RETICULUM_DIR", "/reticulum"))
BOT_STATUS_DIR = Path(os.environ.get("BOT_STATUS_DIR", "/bot-status"))
BOT_CONFIG_DIR = Path(os.environ.get("BOT_CONFIG_DIR", "/bot-config"))
TIMEZONE_NAME = os.environ.get("SERVER_TIMEZONE", "America/Chicago")
try:
    SERVER_TIMEZONE = ZoneInfo(TIMEZONE_NAME)
except ZoneInfoNotFoundError:
    SERVER_TIMEZONE = timezone.utc
    TIMEZONE_NAME = "UTC"

app = Flask(__name__)
app.config.update(SECRET_KEY=os.environ.get("ADMIN_SECRET_KEY"), SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
CONFIG_VIEW_CACHE = {"config": None}
if not app.config["SECRET_KEY"]:
    raise RuntimeError("ADMIN_SECRET_KEY must be set")

def now(): return int(time.time())

def db():
    APP_DATA.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=15); con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
      CREATE TABLE IF NOT EXISTS admins (username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, created_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, occurred_at INTEGER NOT NULL, kind TEXT NOT NULL, severity TEXT NOT NULL DEFAULT 'info', interface_name TEXT, remote_ip TEXT, message TEXT NOT NULL, detail_json TEXT);
      CREATE TABLE IF NOT EXISTS connections (id INTEGER PRIMARY KEY, connection_key TEXT NOT NULL, started_at INTEGER NOT NULL, disconnected_at INTEGER, remote_ip TEXT, remote_port INTEGER, interface_name TEXT, parent_interface_hash TEXT, runtime_interface_hash TEXT, ifac_netname TEXT, rx_bytes INTEGER, tx_bytes INTEGER, announces INTEGER, path_requests INTEGER, correlation TEXT, status TEXT NOT NULL DEFAULT 'active');
      CREATE TABLE IF NOT EXISTS labels (entity_type TEXT NOT NULL, entity_value TEXT NOT NULL, label TEXT NOT NULL, notes TEXT, updated_at INTEGER NOT NULL, PRIMARY KEY(entity_type, entity_value));
      CREATE TABLE IF NOT EXISTS label_history (id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_value TEXT NOT NULL, old_label TEXT, old_notes TEXT, new_label TEXT, new_notes TEXT, changed_at INTEGER NOT NULL, admin TEXT NOT NULL, action TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS observer_state (state_key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS incidents (id INTEGER PRIMARY KEY, started_at INTEGER NOT NULL, admin TEXT NOT NULL, target_json TEXT NOT NULL, actions_json TEXT NOT NULL, preview_json TEXT NOT NULL, results_json TEXT, notes TEXT, status TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS config_changes (id INTEGER PRIMARY KEY, occurred_at INTEGER NOT NULL, admin TEXT NOT NULL, setting TEXT NOT NULL, old_value TEXT, new_value TEXT, snapshot_id TEXT, apply_method TEXT, result TEXT NOT NULL, rolled_back INTEGER NOT NULL DEFAULT 0, error TEXT);
      CREATE TABLE IF NOT EXISTS ui_preferences (pref_key TEXT PRIMARY KEY, pref_value TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS events_filter_idx ON events(occurred_at, kind, interface_name, remote_ip);
      CREATE INDEX IF NOT EXISTS connections_active_idx ON connections(connection_key, status);
      CREATE INDEX IF NOT EXISTS label_history_entity_idx ON label_history(entity_type, entity_value, id DESC);
    """)
    # Keep existing persistent installations compatible with the operation-ID audit trail.
    try: con.execute("ALTER TABLE config_changes ADD COLUMN operation_id TEXT")
    except sqlite3.OperationalError: pass
    for column, definition in (("remote_port", "INTEGER"), ("parent_interface_hash", "TEXT"),
                               ("runtime_interface_hash", "TEXT"), ("ifac_netname", "TEXT")):
        try: con.execute(f"ALTER TABLE connections ADD COLUMN {column} {definition}")
        except sqlite3.OperationalError: pass
    return con

def format_time(value):
    if not value: return "—"
    return datetime.fromtimestamp(value, timezone.utc).astimezone(SERVER_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S %Z")

def format_bytes(value):
    if value is None: return "—"
    value = int(value); units = ["B", "KiB", "MiB", "GiB", "TiB"]
    for unit in units:
        if value < 1024 or unit == units[-1]: return f"{value:.1f} {unit}" if unit != "B" else f"{value} B"
        value /= 1024

BOT_DEFAULTS = {"enabled": True, "display_name": "Reticulum Admin Test Bot", "announce_on_startup": True, "periodic_announce": True, "interval_minutes": 60}
def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8"); os.replace(temporary, path)
def bot_settings():
    try: loaded = json.loads((BOT_CONFIG_DIR / "settings.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): loaded = {}
    return {**BOT_DEFAULTS, **{key: loaded[key] for key in BOT_DEFAULTS if key in loaded}}
def bot_status():
    try: return json.loads((BOT_STATUS_DIR / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return {"running": False, "shared_instance_connected": False, "lxmf_router_ready": False, "error": "Bot has not reported status yet."}

def csrf_token():
    session.setdefault("csrf", secrets.token_urlsafe(32)); return session["csrf"]

@app.context_processor
def common_values():
    return {"csrf_token": csrf_token, "access_mode": os.environ.get("ADMIN_ACCESS_MODE", "local"), "server_timezone": TIMEZONE_NAME, "format_time": format_time, "format_bytes": format_bytes, "app_version": os.environ.get("RETICULUM_ADMIN_VERSION", "0.4.2")}

def require_csrf():
    if not secrets.compare_digest(session.get("csrf", ""), request.form.get("csrf_token", "")): abort(400, "Invalid form token")

def is_configured():
    with db() as con: return con.execute("SELECT 1 FROM admins LIMIT 1").fetchone() is not None

def audit(kind, message, severity="info", interface=None, ip=None, detail=None):
    with db() as con: con.execute("INSERT INTO events(occurred_at,kind,severity,interface_name,remote_ip,message,detail_json) VALUES(?,?,?,?,?,?,?)", (now(), kind, severity, interface, ip, message, json.dumps(detail) if detail else None))

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin"): return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

def snapshot():
    heartbeat = RETICULUM_DIR / "admin-status" / "rnsd.heartbeat"; path = RETICULUM_DIR / "admin-status" / "snapshot.json"
    result = {"status": None, "status_error": "No observer snapshot yet.", "paths": None, "path_error": None, "collected_at": None, "rns_version": None}
    try: result.update(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError): pass
    try: result["heartbeat_age"] = round(time.time() - int(heartbeat.read_text().strip()))
    except (OSError, ValueError): result["heartbeat_age"] = None
    result["running"] = result["heartbeat_age"] is not None and result["heartbeat_age"] < 45
    return result

def config_interfaces():
    config = RETICULUM_DIR / "config"
    if not config.exists(): return []
    records, current = [], None
    try: lines = config.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError: return []
    for line in lines:
        match = re.match(r"^\s*\[\[(.+?)\]\]", line)
        if match:
            if current: records.append(current)
            current = {"name": match.group(1), "type": None, "enabled": None, "mode": None, "ifac_enabled": False, "source": "config"}; continue
        if not current or "=" not in line: continue
        key, value = [part.strip() for part in line.split("=", 1)]
        if key.lower() == "type": current["type"] = value
        elif key.lower() in ("enabled", "interface_enabled"): current["enabled"] = value.lower() in ("yes", "true", "1")
        elif key.lower() == "mode": current["mode"] = value
        elif "ifac" in key.lower(): current["ifac_enabled"] = True
    if current: records.append(current)
    return records


# A Reticulum process exposes several runtime interfaces which are not config
# sections an operator created. Keep this classification in one place so a
# template can never accidentally offer destructive actions for plumbing.
SYSTEM_RUNTIME_TYPES = {"LocalServerInterface", "LocalClientInterface"}

def _bracketed_interface_name(name):
    match = re.match(r"^[^\[]+\[(.+)\]$", str(name or ""))
    return match.group(1) if match else None

def _runtime_matches_configured(configured, runtime):
    """Match only stable Reticulum naming forms; never fuzzy-match interfaces."""
    configured_name = str(configured.get("name") or "")
    runtime_type = runtime.get("type")
    configured_type = configured.get("type")
    if configured_type and runtime_type and configured_type != runtime_type:
        return False
    candidates = (
        runtime.get("short_name"),
        runtime.get("name"),
        _bracketed_interface_name(runtime.get("name")),
    )
    return configured_name in {str(candidate) for candidate in candidates if candidate}

def merge_interface_models(configured, observed):
    """Create one explicit presentation model per configured or runtime interface."""
    remaining = list(observed or [])
    configured_rows = []
    for configured_item in configured or []:
        runtime = next((item for item in remaining if _runtime_matches_configured(configured_item, item)), None)
        if runtime:
            remaining.remove(runtime)
        row = dict(runtime or {})
        row.update({"name": configured_item.get("name"), "short_name": configured_item.get("name"),
                    "type": configured_item.get("type") or row.get("type"),
                    "mode": configured_item.get("mode") or row.get("mode"),
                    "fields": configured_item.get("fields") or {},
                    "configured": True, "runtime_only": False, "system_internal": False,
                    "editable": True, "cloneable": True, "deletable": True,
                    "configured_enabled": configured_item.get("enabled"),
                    "configured_mode": configured_item.get("mode"),
                    "configured_fields": configured_item.get("fields") or {},
                    # A restored AutoInterface can legitimately omit ifac_size:
                    # network_name + passphrase still mean IFAC is configured.
                    "configured_ifac": bool(any((configured_item.get("fields") or {}).get(key) for key in ("network_name", "ifac_netname", "passphrase", "ifac_passphrase")) or configured_item.get("ifac_enabled")),
                    "configured_ifac_size": (configured_item.get("fields") or {}).get("ifac_size")})
        configured_rows.append(row)
    runtime_rows = []
    for runtime in remaining:
        row = dict(runtime)
        internal = row.get("type") in SYSTEM_RUNTIME_TYPES
        row.update({"configured": False, "runtime_only": True, "system_internal": internal,
                    "editable": False, "cloneable": False, "deletable": False,
                    "configured_enabled": None, "configured_mode": None,
                    "configured_fields": {}, "configured_ifac": None, "configured_ifac_size": None})
        runtime_rows.append(row)
    return configured_rows, runtime_rows

def configured_interface(name):
    return next((item for item in configuration_model().get("interfaces", []) if item.get("name") == name), None)

def is_user_configurable_interface(name):
    """Return a persistent config entry, or None for Reticulum runtime objects."""
    return configured_interface(name)

def transport_enabled():
    try: contents = (RETICULUM_DIR / "config").read_text(encoding="utf-8", errors="replace")
    except OSError: return None
    in_reticulum = False
    for line in contents.splitlines():
        if re.match(r"^\s*\[reticulum\]\s*$", line, re.I): in_reticulum = True; continue
        if re.match(r"^\s*\[", line): in_reticulum = False
        if in_reticulum and re.match(r"^\s*enable_transport\s*=", line, re.I):
            return line.split("=", 1)[1].strip().lower() in ("yes", "true", "1")
    return False

def configured_name_for_runtime(runtime, configured):
    """Use the same exact configured/runtime matcher used by Interfaces."""
    match = next((item for item in configured or [] if _runtime_matches_configured(item, runtime)), None)
    return match.get("name") if match else runtime.get("short_name") or runtime.get("name")

def current_clients(interfaces, configured=None):
    """Build proven inbound TCP peers from rns 1.5.x runtime child records.

    Reticulum's aggregate `clients` field is not an individual-client list.
    Only a TCPClientInterface with a TCPServerInterface parent and the strict
    inbound runtime name contributes a detailed peer here.
    """
    clients = []
    for relation in inbound_tcp_child_records(interfaces, configured):
        child, parent, endpoint = relation["runtime"], relation["parent"], relation["endpoint"]
        interface_name = configured_name_for_runtime(parent, configured)
        parent_hash = parent.get("interface_hash") or child.get("parent_interface_hash") or "unknown-parent"
        runtime_hash = child.get("interface_hash") or child.get("name")
        key = f"{parent_hash}|{runtime_hash}|{endpoint['remote_ip']}|{endpoint['remote_port']}"
        clients.append({
            "key": key, "connection_key": key,
            "remote_ip": endpoint["remote_ip"], "remote_port": endpoint["remote_port"],
            "interface": interface_name, "parent_interface_hash": parent_hash,
            "runtime_interface_hash": child.get("interface_hash"),
            "status": "active", "rx": child.get("rx_bytes"), "tx": child.get("tx_bytes"),
            "announces": child.get("announces"), "path_requests": child.get("path_requests"),
            "ifac_netname": child.get("ifac_netname"), "ifac_active": child.get("ifac_enabled") is True,
            "identity": None, "destination": None, "correlation": "unknown", "connected_at": None,
        })
    return clients

def ingest_snapshot():
    snap = snapshot(); interfaces = find_interface_records(snap.get("status")) if snap.get("status") else []; clients = current_clients(interfaces, config_interfaces())
    state = {"interfaces": {x["name"]: x.get("status") for x in interfaces}, "clients": {x["key"]: x for x in clients}, "client_counts": {x["name"]: x["client_count"] for x in interfaces if x.get("client_count") is not None}, "held_announce_total": sum(x.get("held_announces") or 0 for x in interfaces), "announce_total": sum(x.get("announces") or 0 for x in interfaces), "path_count": len(snap.get("paths") or [])}
    with db() as con:
        row = con.execute("SELECT value_json FROM observer_state WHERE state_key='latest'").fetchone(); prior = json.loads(row["value_json"]) if row else {"interfaces": {}, "clients": {}, "client_counts": {}, "held_announce_total": 0, "announce_total": 0, "path_count": 0}
        for name, status in state["interfaces"].items():
            if name not in prior["interfaces"]: con.execute("INSERT INTO events(occurred_at,kind,severity,interface_name,message) VALUES(?,?,?,?,?)", (now(), "interface_attached", "info", name, f"Interface {name} observed"))
            elif status != prior["interfaces"].get(name): con.execute("INSERT INTO events(occurred_at,kind,severity,interface_name,message) VALUES(?,?,?,?,?)", (now(), "interface_status", "warning", name, f"Interface {name} status changed to {status}"))
        for name in set(prior["interfaces"]) - set(state["interfaces"]): con.execute("INSERT INTO events(occurred_at,kind,severity,interface_name,message) VALUES(?,?,?,?,?)", (now(), "interface_detached", "warning", name, f"Interface {name} no longer observed"))
        for name, count in state["client_counts"].items():
            old = prior.get("client_counts", {}).get(name)
            if old is not None and old != count:
                direction = "increased" if count > old else "decreased"
                con.execute("INSERT INTO events(occurred_at,kind,severity,interface_name,message) VALUES(?,?,?,?,?)", (now(), "client_count_changed", "info", name, f"TCP/client count {direction} from {old} to {count} on {name}"))
        for key, client in state["clients"].items():
            if key not in prior["clients"]:
                con.execute("INSERT INTO connections(connection_key,started_at,remote_ip,remote_port,interface_name,parent_interface_hash,runtime_interface_hash,ifac_netname,rx_bytes,tx_bytes,announces,path_requests,correlation,status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?, 'active')", (key, now(), client["remote_ip"], client["remote_port"], client["interface"], client["parent_interface_hash"], client["runtime_interface_hash"], client["ifac_netname"], client["rx"], client["tx"], client["announces"], client["path_requests"], client["correlation"]))
                con.execute("INSERT INTO events(occurred_at,kind,severity,interface_name,remote_ip,message) VALUES(?,?,?,?,?,?)", (now(), "client_connected", "info", client["interface"], client["remote_ip"], f"TCP client {client['remote_ip']}:{client['remote_port']} connected to {client['interface']}"))
            else: con.execute("UPDATE connections SET rx_bytes=?,tx_bytes=?,announces=?,path_requests=?,ifac_netname=? WHERE connection_key=? AND status='active'", (client["rx"], client["tx"], client["announces"], client["path_requests"], client["ifac_netname"], key))
        for key, client in prior.get("clients", {}).items():
            if key not in state["clients"]:
                con.execute("UPDATE connections SET disconnected_at=?,status='disconnected' WHERE connection_key=? AND status='active'", (now(), key)); con.execute("INSERT INTO events(occurred_at,kind,severity,interface_name,remote_ip,message) VALUES(?,?,?,?,?,?)", (now(), "client_disconnected", "info", client.get("interface"), client.get("remote_ip"), f"TCP client {client.get('remote_ip')}:{client.get('remote_port') or '—'} disconnected from {client.get('interface')}"))
        if state["held_announce_total"] >= 100 and state["held_announce_total"] > prior.get("held_announce_total", 0): con.execute("INSERT INTO events(occurred_at,kind,severity,message) VALUES(?,?,?,?)", (now(), "queue_warning", "warning", f"Held announces increased to {state['held_announce_total']}"))
        if state["announce_total"] - prior.get("announce_total", 0) >= 100: con.execute("INSERT INTO events(occurred_at,kind,severity,message) VALUES(?,?,?,?)", (now(), "announce_rate", "warning", f"High announce activity: {state['announce_total'] - prior['announce_total']} new announces"))
        if state["path_count"] - prior.get("path_count", 0) >= 25: con.execute("INSERT INTO events(occurred_at,kind,severity,message) VALUES(?,?,?,?)", (now(), "path_growth", "warning", f"{state['path_count'] - prior['path_count']} new paths learned"))
        con.execute("INSERT INTO observer_state(state_key,value_json,updated_at) VALUES('latest',?,?) ON CONFLICT(state_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at", (json.dumps(state), now()))
    return snap, interfaces, clients

def labels():
    with db() as con: return {(x["entity_type"], x["entity_value"]): dict(x) for x in con.execute("SELECT * FROM labels")}

def label_history(entity_type, entity_value):
    with db() as con:
        return [dict(row) for row in con.execute("SELECT * FROM label_history WHERE entity_type=? AND entity_value=? ORDER BY id DESC", (entity_type, entity_value))]

def save_label_state(entity_type, entity_value, label, notes, action):
    """Change one label only; this deliberately never observes Reticulum or calls management."""
    started = time.monotonic()
    with db() as con:
        previous = con.execute("SELECT * FROM labels WHERE entity_type=? AND entity_value=?", (entity_type, entity_value)).fetchone()
        old_label, old_notes = (previous["label"], previous["notes"]) if previous else (None, None)
        if label is None:
            con.execute("DELETE FROM labels WHERE entity_type=? AND entity_value=?", (entity_type, entity_value))
        else:
            con.execute("INSERT INTO labels(entity_type,entity_value,label,notes,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(entity_type,entity_value) DO UPDATE SET label=excluded.label,notes=excluded.notes,updated_at=excluded.updated_at", (entity_type, entity_value, label, notes, now()))
        con.execute("INSERT INTO label_history(entity_type,entity_value,old_label,old_notes,new_label,new_notes,changed_at,admin,action) VALUES(?,?,?,?,?,?,?,?,?)", (entity_type, entity_value, old_label, old_notes, label, notes, now(), session.get("admin", "admin"), action))
    elapsed_ms = round((time.monotonic() - started) * 1000, 1)
    app.logger.info("label action=%s entity_type=%s elapsed_ms=%s", action, entity_type, elapsed_ms)
    audit("label_" + action, f"Label {action} for {entity_type} {entity_value}", detail={"elapsed_ms": elapsed_ms})
    return elapsed_ms

def verified_path_identity(path):
    """An identity is actionable only when the data source explicitly marks it verified."""
    identity = path.get("identity") or path.get("identity_hash") or path.get("associated_identity")
    verified = path.get("identity_verified") is True or str(path.get("identity_relationship", "")).lower() == "verified"
    return str(identity) if identity and verified else None

def path_models(raw_paths, known, query=""):
    rows = []
    needle = query.lower().strip()
    for raw in raw_paths or []:
        if not isinstance(raw, dict):
            continue
        destination, transport = str(raw.get("hash") or ""), str(raw.get("via") or "")
        identity = verified_path_identity(raw)
        destination_label = known.get(("destination", destination), {}) if destination else {}
        transport_label = known.get(("transport", transport), {}) if transport else {}
        identity_label = known.get(("identity", identity), {}) if identity else {}
        searchable = " ".join((destination, transport, str(raw.get("interface") or ""), destination_label.get("label", ""), transport_label.get("label", ""), identity_label.get("label", ""))).lower()
        if needle and needle not in searchable:
            continue
        rows.append({**raw, "destination": destination, "transport": transport, "identity": identity,
                     "destination_label": destination_label, "transport_label": transport_label,
                     "identity_label": identity_label, "hash_type": "Destination", "identity_relationship": "Verified" if identity else "Unknown"})
    return rows

def hash_context(value, path_rows, known):
    """Report every context actually observed. Hash length is deliberately never a classifier."""
    contexts = []
    for path in path_rows:
        if path["destination"] == value: contexts.append(("Destination path", path))
        if path["transport"] == value: contexts.append(("Transport identity", path))
        if path.get("identity") == value: contexts.append(("Verified identity", path))
    label_contexts = [(kind.title() + " label", {"label": row["label"]}) for (kind, entity), row in known.items() if entity == value]
    return contexts + label_contexts

def parse_blackhole_rows(result, known):
    """rnpath output differs by Reticulum version; render structured JSON when available."""
    if not result.get("ok"):
        return []
    raw = result.get("stdout") or ""
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []
    if isinstance(parsed, dict):
        parsed = parsed.get("blackholes") or parsed.get("entries") or []
    rows = []
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict): continue
        identity = str(item.get("identity") or item.get("hash") or "")
        rows.append({"identity": identity, "label": known.get(("identity", identity), {}).get("label"),
                     "reason": item.get("reason"), "expiry": item.get("expiry") or item.get("expires"),
                     "created": item.get("created") or item.get("added")})
    return rows

def management(path, body=None, timeout=None):
    token = os.environ.get("MANAGEMENT_TOKEN", ""); base = os.environ.get("MANAGEMENT_URL", "")
    if not token or not base: return {"ok": False, "error": "Management agent is not configured"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base+path, data=data, headers={"X-Management-Token":token, "Content-Type":"application/json"})
    try:
        request_timeout = float(os.environ.get("MANAGEMENT_HTTP_TIMEOUT", "100")) if timeout is None else timeout
        with urllib.request.urlopen(req, timeout=request_timeout) as response:
            payload = json.loads(response.read())
            if not isinstance(payload, dict): raise ValueError("Management response is not an object")
            return payload
    except (TimeoutError, socket.timeout) as error:
        return {"ok":False, "unknown":True, "error":"The management operation did not return a result in time. Reticulum may still have applied the change. Check current status before retrying.", "technical_error":str(error)}
    except urllib.error.HTTPError as error:
        # A 4xx response from the allowlisted management API is commonly a
        # validation rejection. It completed before any live change was made,
        # so preserve its safe, structured explanation instead of presenting
        # it as an uncertain timeout that might invite an unsafe retry.
        try:
            payload = json.loads(error.read().decode("utf-8"))
            if isinstance(payload, dict):
                return payload
        except (json.JSONDecodeError, UnicodeDecodeError, OSError, ValueError):
            pass
        return {"ok":False, "unknown":True, "error":"The management operation did not return a usable result. Reticulum may still have applied the change. Check current status before retrying.", "technical_error":str(error)}
    except (urllib.error.URLError, json.JSONDecodeError, ValueError, OSError) as error:
        return {"ok":False, "unknown":True, "error":"The management operation did not return a usable result. Reticulum may still have applied the change. Check current status before retrying.", "technical_error":str(error)}

def record_config_change(result, change):
    diff = result.get("diff") or []; setting = change.get("setting") or change.get("name") or (change.get("interface") or {}).get("name") or "snapshot restore"
    old = ", ".join(f"{x.get('section')}.{x.get('key')}={x.get('old')}" for x in diff)[:1000]
    new = ", ".join(f"{x.get('section')}.{x.get('key')}={x.get('new')}" for x in diff)[:1000]
    snapshot_id = (result.get("snapshot") or {}).get("id")
    status = "Applied" if result.get("ok") else "Result Unknown / Verification Timed Out" if result.get("unknown") else "Rolled Back" if result.get("rolled_back") else "Recovery Failed" if result.get("recovery_failed") else "Failed"
    with db() as con: con.execute("INSERT INTO config_changes(occurred_at,admin,setting,old_value,new_value,snapshot_id,apply_method,result,rolled_back,error,operation_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (now(), session.get("admin", "admin"), setting, old, new, snapshot_id, result.get("apply_method"), status, int(bool(result.get("rolled_back"))), result.get("error") or result.get("rollback_error"), result.get("operation_id")))
    kind = "config_changed" if result.get("ok") else "config_unknown" if result.get("unknown") else "config_rollback"
    message = f"Configuration {setting}: {'applied' if result.get('ok') else 'result unknown' if result.get('unknown') else 'rolled back' if result.get('rolled_back') else 'failed'}"
    audit(kind, message, "info" if result.get("ok") else "warning" if result.get("unknown") else "error", detail={"snapshot_id": snapshot_id, "apply_method": result.get("apply_method"), "operation_id": result.get("operation_id")})

def update_config_view_cache(change):
    config = CONFIG_VIEW_CACHE.get("config")
    if not config: return
    if change.get("kind") == "global": config.setdefault("global", {})[change.get("setting")] = "Yes" if change.get("value") is True else "No" if change.get("value") is False else str(change.get("value"))
    elif change.get("kind") == "interface":
        for item in config.get("interfaces", []):
            if item.get("name") == change.get("name"):
                for key, value in (change.get("changes") or {}).items():
                    if key in ("enabled", "mode"): item[key] = "Yes" if value is True else "No" if value is False else str(value)
                    else: item.setdefault("fields", {})[key] = str(value)
                break
    elif change.get("kind") == "interface_create":
        item = dict(change.get("interface") or {})
        item["fields"] = dict(item.pop("fields", {}))
        item["enabled"] = "Yes" if item.get("enabled", True) else "No"
        CONFIG_VIEW_CACHE["config"].setdefault("interfaces", []).append(item)
    elif change.get("kind") == "interface_delete":
        config["interfaces"] = [item for item in config.get("interfaces", []) if item.get("name") != change.get("name")]

MODE_HELP = {
    "full": "Normal full Reticulum participation and announce propagation.",
    "boundary": "Useful between an outside network and a protected internal segment; outside announces are not automatically propagated into Internal interfaces.",
    "internal": "Marks a protected local segment such as RF or a private network. Remote paths can still be discovered on demand where Reticulum supports it.",
    "gateway": "Provides gateway-style path discovery while still participating in routing.",
    "point_to_point": "For a deliberate point-to-point link between known peers.",
    "access_point": "For an access-point style interface where supported.",
    "roaming": "For roaming-style operation where supported.",
}

PRESETS = {
    "user_tcp": {"label":"User Access TCP", "type":"TCPServerInterface", "name":"Public TCP", "mode":"boundary", "listen_ip":"0.0.0.0", "listen_port":"4242", "help":"Use this when ordinary Reticulum users should connect to this node over TCP."},
    "trusted_tcp": {"label":"Trusted Backbone TCP", "type":"TCPServerInterface", "name":"Trusted TCP", "mode":"full", "listen_ip":"0.0.0.0", "listen_port":"4243", "help":"For known operators or transport nodes deliberately linking network segments. Use separate IFAC credentials from user access."},
    "tcp_client": {"label":"Trusted TCP Client", "type":"TCPClientInterface", "name":"Trusted Upstream", "mode":"full", "help":"Makes this node connect outward to another Reticulum TCP server."},
    "auto": {"label":"AutoInterface", "type":"AutoInterface", "name":"Local Discovery", "mode":"full", "help":"Lets nearby Reticulum nodes discover each other over the local network without a TCP server."},
    "protected": {"label":"Protected Local Segment", "type":"TCPServerInterface", "name":"Protected TCP", "mode":"internal", "listen_ip":"127.0.0.1", "listen_port":"4244", "help":"A starting point for a protected local segment. Internal is common for RF or private networks, but is not mandatory for every RF setup."},
}

def configuration_model():
    cached = CONFIG_VIEW_CACHE.get("config")
    if cached: return cached
    result = management("/config", timeout=float(os.environ.get("MANAGEMENT_PREVIEW_TIMEOUT", "5")))
    if result.get("ok") and result.get("config"):
        CONFIG_VIEW_CACHE["config"] = result["config"]
    return result.get("config", {})

def make_interface_change(form):
    purpose = form.get("purpose", "manual")
    preset = PRESETS.get(purpose, {})
    interface_type = form.get("interface_type") or preset.get("type")
    fields = {}
    # The shared form contains both server and client controls for clarity.
    # Send only settings supported by the selected interface type: otherwise a
    # TCP Client would inherit the server form's default listen_ip and the
    # allowlisted configuration adapter would (correctly) reject it.
    type_text_fields = {
        "TCPServerInterface": ("listen_ip",),
        "TCPClientInterface": ("target_host",),
        "AutoInterface": (),
    }
    type_port_fields = {
        "TCPServerInterface": ("listen_port",),
        "TCPClientInterface": ("target_port",),
        "AutoInterface": (),
    }
    for key in type_text_fields.get(interface_type, ("network_name",)):
        value = form.get(key, "").strip()
        if value: fields[key] = value
    for key in type_port_fields.get(interface_type, ()):
        value = form.get(key, "").strip()
        if value: fields[key] = int(value) if value.isdigit() else value
    ifac_enabled = bool(form.get("ifac_enabled"))
    network_name = form.get("network_name", "").strip()
    passphrase = form.get("passphrase", "")
    custom_ifac_size = form.get("ifac_size_mode") == "custom"
    if not ifac_enabled and (network_name or passphrase or custom_ifac_size):
        raise ValueError("You entered IFAC settings, but IFAC is not enabled. Enable IFAC or clear the IFAC fields.")
    if ifac_enabled:
        if not network_name or not passphrase:
            raise ValueError("IFAC requires both a network name and a passphrase")
        # IFAC is deliberately per-interface. Nothing is copied from another
        # configured interface, even if it happens to use similar credentials.
        fields["network_name"] = network_name
        fields["passphrase"] = passphrase
        if custom_ifac_size:
            bits = form.get("ifac_size_bits", "")
            if not bits.isdigit() or int(bits) < 8 or int(bits) > 512 or int(bits) % 8: raise ValueError("Custom IFAC size must be a whole number of bytes from 8 to 512 bits")
            fields["ifac_size"] = int(bits) // 8
    return {"kind":"interface_create", "interface": {"name":form.get("name", "").strip(), "type":interface_type, "enabled":bool(form.get("enabled")), "mode":form.get("mode") or preset.get("mode", "full"), "fields":fields}}

def provisioning_summary(change, form):
    """Explain the intent and prerequisites; never alter topology implicitly."""
    interface = change.get("interface") or {}
    interface_type = interface.get("type")
    if interface_type == "TCPClientInterface":
        route_others = form.get("tcp_client_goal") == "route_others"
        configured = configuration_model().get("interfaces", [])
        usable_other = any(str(item.get("enabled", "Yes")).lower() not in ("no", "false", "0") for item in configured)
        return {"kind": "tcp_client", "route_others": route_others,
                "transport": transport_enabled(), "has_other_interface": usable_other}
    if interface_type == "TCPServerInterface":
        return {"kind": "tcp_server"}
    if interface_type == "AutoInterface":
        return {"kind": "auto"}
    return None

def preview_provision_change(change, reason, warning=None):
    result = management("/config/preview", {"change":change}, timeout=float(os.environ.get("MANAGEMENT_PREVIEW_TIMEOUT", "5")))
    if not result.get("ok"): raise ValueError(result.get("error", "Configuration validation failed"))
    operation_id = f"CFG-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3).upper()}"
    session["config_change"] = {"change":change, "reason":reason[:240], "operation_id":operation_id}
    return result, warning

def display_config_value(value, secret_value=False):
    """Use the same safe values in a local review that the UI uses elsewhere."""
    if secret_value:
        return "Changed"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return value

def local_config_preview(change):
    """Build an immediate, read-only review from the model already shown to admin.

    The management agent remains authoritative at Apply time.  A review must not
    wait behind a long-running restart or recovery request in its single worker.
    """
    config = CONFIG_VIEW_CACHE.get("config") or {}
    diff = []
    if change.get("kind") == "global":
        setting = change.get("setting")
        old = (config.get("global") or {}).get(setting)
        diff.append({"section": "reticulum", "key": setting, "old": display_config_value(old), "new": display_config_value(change.get("value"))})
        return {"ok": True, "diff": diff, "local_review": True}

    name = change.get("name")
    item = next((row for row in config.get("interfaces", []) if row.get("name") == name), {})
    fields = item.get("fields") or {}
    for key, value in (change.get("changes") or {}).items():
        old = item.get(key) if key in ("enabled", "mode") else fields.get(key)
        is_secret = "passphrase" in key.lower()
        diff.append({"section": name or "interface", "key": key, "old": display_config_value(old, is_secret and old is not None), "new": display_config_value(value, is_secret)})
    return {"ok": True, "diff": diff, "local_review": True}

def recovery_preview(form):
    target = {"identity":form.get("identity", "").strip(), "transport":form.get("transport", "").strip(), "destination":form.get("destination", "").strip(), "interface":form.get("interface", "").strip()}
    actions = [key for key in ("blackhole","unblackhole","drop_path","drop_via","drop_announces","attach","detach","reload") if form.get(key)]
    return {"target":target, "actions":actions, "duration":form.get("duration", ""), "reason":form.get("reason", ""), "queue_warning": "Drop queued announces clears local queues globally." if "drop_announces" in actions else None}

def system_info():
    memory = "Unavailable"
    try:
        values = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines() if ":" in line)
        memory = values.get("MemTotal", "Unavailable").strip()
    except OSError: pass
    return {"app_version":os.environ.get("RETICULUM_ADMIN_VERSION", "0.4.2"), "container_arch":platform.machine(), "host_arch":os.environ.get("HOST_ARCH", "unknown"), "host_os":os.environ.get("HOST_OS", "unknown"), "python":platform.python_version(), "cpu_count":os.cpu_count(), "memory":memory, "docker":os.environ.get("INSTALL_DOCKER_VERSION", "unknown"), "compose":os.environ.get("INSTALL_COMPOSE_VERSION", "unknown")}

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if is_configured(): return redirect(url_for("dashboard"))
    if request.method == "POST":
        require_csrf(); username = request.form.get("username", "").strip(); password = request.form.get("password", "")
        if len(username) < 3 or len(password) < 12 or password != request.form.get("confirmation", ""): flash("Use a username of at least 3 characters and matching password of at least 12 characters.", "error")
        else:
            with db() as con: con.execute("INSERT INTO admins VALUES (?, ?, ?)", (username, generate_password_hash(password), now()))
            session["admin"] = username; audit("admin_setup", "First administrator account created"); return redirect(url_for("dashboard"))
    return render_template("setup.html", title="First-run setup")

@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_configured(): return redirect(url_for("setup"))
    if request.method == "POST":
        require_csrf(); username = request.form.get("username", "").strip()
        with db() as con: account = con.execute("SELECT * FROM admins WHERE username=?", (username,)).fetchone()
        if account and check_password_hash(account["password_hash"], request.form.get("password", "")):
            session.clear(); session["admin"] = username; csrf_token(); audit("admin_login", f"Administrator {username} signed in"); return redirect(url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html", title="Sign in")

@app.post("/logout")
def logout(): require_csrf(); session.clear(); return redirect(url_for("login"))

@app.get("/")
@login_required
def dashboard():
    snap, interfaces, clients = ingest_snapshot(); configured = config_interfaces()
    with db() as con:
        recent = con.execute("SELECT * FROM events ORDER BY occurred_at DESC LIMIT 8").fetchall(); warnings = con.execute("SELECT COUNT(*) AS n FROM events WHERE severity='warning' AND occurred_at>?", (now()-86400,)).fetchone()["n"]
        onboarding = con.execute("SELECT pref_value FROM ui_preferences WHERE pref_key='onboarding_dismissed'").fetchone() is None
    return render_template("dashboard.html", title="Dashboard", snap=snap, interfaces=interfaces, configured=configured, clients=clients, client_count=sum(x["client_count"] or 0 for x in interfaces), transport=transport_enabled(), recent=recent, warnings=warnings, onboarding=onboarding)

@app.get("/interfaces")
@login_required
def interfaces():
    snap, observed, _ = ingest_snapshot()
    configured_rows, runtime_rows = merge_interface_models(configuration_model().get("interfaces", []), observed)
    return render_template("interfaces.html", title="Interfaces", configured_rows=configured_rows,
                           runtime_rows=runtime_rows, error=snap.get("status_error"))

@app.get("/interfaces/<name>")
@login_required
def interface_detail(name):
    _, observed, _ = ingest_snapshot()
    configured_rows, runtime_rows = merge_interface_models(configuration_model().get("interfaces", []), observed)
    row = next((item for item in configured_rows + runtime_rows if item.get("name") == name), None)
    if not row:
        abort(404)
    return render_template("interface_detail.html", title=f"Interface: {name}", interface=row)

@app.route("/interfaces/<name>/edit", methods=["GET", "POST"])
@login_required
def interface_edit(name):
    source = is_user_configurable_interface(name)
    if not source:
        flash("This interface is managed internally by Reticulum and does not have a persistent configuration entry to edit.", "error")
        return redirect(url_for("interfaces"))
    _, observed, _ = ingest_snapshot()
    configured_rows, _ = merge_interface_models(configuration_model().get("interfaces", []), observed)
    row = next((item for item in configured_rows if item.get("name") == name), {})
    if request.method == "POST":
        require_csrf()
        changes = {}
        for key in ("enabled", "mode", "network_name", "passphrase", "ifac_size", "listen_ip", "listen_port", "target_host", "target_port"):
            value = request.form.get(key)
            if value not in (None, ""):
                changes[key] = (value.lower() in ("on", "yes", "true", "1")) if key == "enabled" else (int(value) if key in ("ifac_size", "listen_port", "target_port") and value.isdigit() else value)
        change = {"kind": "interface", "name": name, "changes": changes}
        result = local_config_preview(change)
        operation_id = f"CFG-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3).upper()}"
        session["config_change"] = {"change": change, "reason": request.form.get("reason", f"Update {name}")[:240], "operation_id": operation_id}
        return render_template("configuration_preview.html", title="Review Interface Change", preview=result, change=change)
    return render_template("interface_edit.html", title=f"Edit Interface: {name}", interface=row, modes=MODE_HELP)

@app.route("/interfaces/add", methods=["GET", "POST"])
@login_required
def interface_add():
    if request.method == "POST":
        require_csrf()
        try:
            change = make_interface_change(request.form)
            preview, warning = preview_provision_change(change, request.form.get("reason", "Add interface"), "Creating a TCP Server does not automatically make it Internet reachable; firewall, NAT, or router configuration may still be required." if change["interface"]["type"] == "TCPServerInterface" else None)
        except ValueError as error:
            flash(str(error), "error"); return redirect(url_for("interface_add", purpose=request.form.get("purpose", "manual")))
        return render_template("configuration_preview.html", title="Review New Interface", preview=preview, change=change, warning=warning, setup_summary=provisioning_summary(change, request.form))
    purpose = request.args.get("purpose", "user_tcp")
    return render_template("interface_form.html", title="Add Interface", presets=PRESETS, selected=purpose, values=PRESETS.get(purpose, {}), modes=MODE_HELP, clone=False)

@app.route("/interfaces/<name>/clone", methods=["GET", "POST"])
@login_required
def interface_clone(name):
    source = is_user_configurable_interface(name)
    if not source:
        flash("This interface is managed internally by Reticulum and does not have a persistent configuration entry to clone.", "error")
        return redirect(url_for("interfaces"))
    if request.method == "POST":
        require_csrf()
        try:
            change = make_interface_change(request.form)
            preview, warning = preview_provision_change(change, request.form.get("reason", f"Clone {name}"), "A cloned TCP server must use a different port from its source.")
        except ValueError as error:
            flash(str(error), "error"); return redirect(url_for("interface_clone", name=name))
        return render_template("configuration_preview.html", title="Review Interface Clone", preview=preview, change=change, warning=warning)
    values = {"type":source.get("type"), "name":f"{name} Copy", "mode":source.get("mode") or "full", **(source.get("fields") or {})}
    if source.get("type") == "TCPServerInterface": values["listen_port"] = ""
    return render_template("interface_form.html", title=f"Clone {name}", presets=PRESETS, selected="manual", values=values, modes=MODE_HELP, clone=True)

@app.post("/interfaces/<name>/delete")
@login_required
def interface_delete(name):
    require_csrf()
    if not is_user_configurable_interface(name):
        flash("This interface is managed internally by Reticulum and does not have a persistent configuration entry to delete.", "error")
        return redirect(url_for("interfaces"))
    _, observed, _ = ingest_snapshot(); configured_rows, _ = merge_interface_models(configuration_model().get("interfaces", []), observed); active = next((item for item in configured_rows if item.get("name") == name), {})
    warning = "This interface currently has active clients or peers." if (active.get("client_count") or active.get("peer_count")) else "Deleting an interface removes its persistent configuration. A snapshot will be created first."
    try: preview, _ = preview_provision_change({"kind":"interface_delete", "name":name}, f"Delete {name}", warning)
    except ValueError as error: flash(str(error), "error"); return redirect(url_for("interfaces"))
    return render_template("configuration_preview.html", title="Review Interface Deletion", preview=preview, change={"kind":"interface_delete", "name":name}, warning=warning, delete_confirmation=True)

@app.get("/topology")
@login_required
def topology():
    _, observed, _ = ingest_snapshot(); rows, _ = merge_interface_models(configuration_model().get("interfaces", []), observed)
    warnings=[]; modes={str(item.get("mode", "")).lower() for item in rows}
    if "full" in modes and "internal" in modes: warnings.append("A Full interface and an Internal interface are both configured. Review announce propagation for this topology.")
    if transport_enabled() and any(item.get("type") == "TCPServerInterface" and str(item.get("mode", "")).lower() == "full" for item in rows): warnings.append("Transport Mode is enabled while a Full TCP Server interface is configured. Confirm this node is intended to route for other peers.")
    for item in rows:
        if item.get("type") == "TCPServerInterface" and not item.get("fields", {}).get("passphrase"): warnings.append(f"IFAC is disabled on TCP Server interface {item.get('name')}.")
    return render_template("topology.html", title="Topology", rows=rows, warnings=warnings)

@app.get("/help")
@login_required
def help_concepts(): return render_template("help.html", title="Help / Concepts", modes=MODE_HELP)

@app.post("/onboarding/dismiss")
@login_required
def onboarding_dismiss():
    require_csrf()
    with db() as con: con.execute("INSERT INTO ui_preferences(pref_key,pref_value) VALUES('onboarding_dismissed','yes') ON CONFLICT(pref_key) DO UPDATE SET pref_value='yes'")
    return redirect(request.referrer or url_for("dashboard"))

@app.get("/clients")
@login_required
def clients():
    _, observed, current = ingest_snapshot(); known = labels(); selected_interface = request.args.get("interface", "").strip()
    configured = config_interfaces()
    server_summaries = []
    for runtime in observed:
        if runtime.get("type") != "TCPServerInterface":
            continue
        interface_name = configured_name_for_runtime(runtime, configured)
        identified = sum(1 for client in current if client["interface"] == interface_name)
        server_summaries.append({"name": interface_name, "reported": runtime.get("client_count"), "identified": identified})
    if selected_interface:
        current = [client for client in current if client["interface"] == selected_interface]
        server_summaries = [row for row in server_summaries if row["name"] == selected_interface]
    with db() as con:
        active = {row["connection_key"]: dict(row) for row in con.execute("SELECT * FROM connections WHERE status='active'")}
        if selected_interface:
            history = con.execute("SELECT * FROM connections WHERE interface_name=? ORDER BY started_at DESC LIMIT 100", (selected_interface,)).fetchall()
        else:
            history = con.execute("SELECT * FROM connections ORDER BY started_at DESC LIMIT 100").fetchall()
    for client in current:
        client["label"] = known.get(("ip", client["remote_ip"]), {}).get("label")
        client["first_observed"] = active.get(client["key"], {}).get("started_at")
    return render_template("clients.html", title="Connected Clients", clients=current, history=history,
                           labels=known, server_summaries=server_summaries, selected_interface=selected_interface)

@app.post("/labels")
@login_required
def save_label():
    require_csrf(); entity_type = request.form.get("entity_type", "ip"); entity_value = request.form.get("entity_value", "").strip(); label = request.form.get("label", "").strip(); notes = request.form.get("notes", "").strip()
    if entity_type not in ("ip", "identity", "destination", "transport") or not entity_value or not label or len(label) > 80 or len(notes) > 1000: abort(400, "Invalid label")
    existing = labels().get((entity_type, entity_value))
    save_label_state(entity_type, entity_value, label, notes, "update" if existing else "create")
    flash("Label saved.", "success"); return redirect(request.form.get("return_to") or request.referrer or url_for("clients"))

@app.post("/labels/clear")
@login_required
def clear_label():
    require_csrf(); entity_type = request.form.get("entity_type", ""); entity_value = request.form.get("entity_value", "").strip()
    if entity_type not in ("ip", "identity", "destination", "transport") or not entity_value: abort(400, "Invalid label")
    if not labels().get((entity_type, entity_value)):
        flash("No label exists to clear.", "error")
    elif request.form.get("confirm") != "CLEAR LABEL":
        flash("Type CLEAR LABEL to remove this label.", "error")
    else:
        save_label_state(entity_type, entity_value, None, None, "clear")
        flash(f"Label cleared for {entity_type} {entity_value}.", "success")
    return redirect(request.form.get("return_to") or request.referrer or url_for("paths"))

@app.post("/labels/undo")
@login_required
def undo_label():
    require_csrf(); entity_type = request.form.get("entity_type", ""); entity_value = request.form.get("entity_value", "").strip()
    if entity_type not in ("ip", "identity", "destination", "transport") or not entity_value: abort(400, "Invalid label")
    history = label_history(entity_type, entity_value)
    if not history:
        flash("No label change is available to undo.", "error")
    else:
        previous = history[0]
        save_label_state(entity_type, entity_value, previous["old_label"], previous["old_notes"], "restore")
        flash("Last label change undone.", "success")
    return redirect(request.form.get("return_to") or request.referrer or url_for("paths"))

@app.get("/timeline")
@login_required
def timeline():
    clauses, args = [], []
    for key, column in (("kind", "kind"), ("severity", "severity"), ("interface", "interface_name"), ("ip", "remote_ip")):
        value = request.args.get(key, "").strip()
        if value: clauses.append(f"{column} = ?" if key in ("kind", "severity") else f"{column} LIKE ?"); args.append(value if key in ("kind", "severity") else f"%{value}%")
    since = request.args.get("since", "").strip()
    if since:
        try: clauses.append("occurred_at >= ?"); args.append(int(datetime.fromisoformat(since).replace(tzinfo=SERVER_TIMEZONE).timestamp()))
        except ValueError: flash("Invalid date/time filter.", "error")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with db() as con: events = con.execute("SELECT * FROM events" + where + " ORDER BY occurred_at DESC LIMIT 500", args).fetchall()
    return render_template("timeline.html", title="Timeline", events=events)

@app.get("/logs")
@login_required
def logs():
    query = request.args.get("q", "").lower(); severity = request.args.get("severity", ""); lines = []
    for candidate in (RETICULUM_DIR / "logfile", RETICULUM_DIR / "logs" / "logfile"):
        try: lines = candidate.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]; break
        except OSError: continue
    secret = re.compile(r"(?i)(ifac[_ -]?(?:key|passphrase)?\s*=\s*)\S+|((?:ADMIN_SECRET_KEY|password_hash)\s*=\s*)\S+")
    lines = [secret.sub(lambda m: (m.group(1) or m.group(2)) + "[REDACTED]", line) for line in lines]
    if query: lines = [line for line in lines if query in line.lower()]
    if severity: lines = [line for line in lines if severity.lower() in line.lower()]
    return render_template("logs.html", title="Logs", lines=reversed(lines))

@app.get("/paths")
@login_required
def paths():
    snap, _, _ = ingest_snapshot()
    known = labels(); query = request.args.get("q", "")
    paths = path_models(snap.get("paths") or [], known, query)
    return render_template("paths.html", title="Paths", paths=paths, error=snap.get("path_error"), known=known, query=query)

@app.get("/hashes/<value>")
@login_required
def hash_lookup(value):
    snap, _, _ = ingest_snapshot(); known = labels(); rows = path_models(snap.get("paths") or [], known)
    return render_template("hash_lookup.html", title="Hash Lookup", value=value, contexts=hash_context(value, rows, known), label=next((row for (kind, entity), row in known.items() if entity == value), None))

@app.get("/hash-lookup")
@login_required
def hash_lookup_search():
    value = request.args.get("q", "").strip()
    if not value:
        flash("Enter a hash to inspect.", "error")
        return redirect(url_for("paths"))
    return redirect(url_for("hash_lookup", value=value))

@app.route("/recovery", methods=["GET", "POST"])
@login_required
def recovery():
    capabilities = management("/capabilities").get("capabilities", {})
    if request.method == "POST":
        require_csrf(); preview = recovery_preview(request.form)
        snap, _, _ = ingest_snapshot(); known = labels(); rows = path_models(snap.get("paths") or [], known)
        contexts = hash_context(preview["target"]["identity"], rows, known) if preview["target"]["identity"] else []
        if "blackhole" in preview["actions"] and preview["target"]["identity"] and any(kind == "Destination path" for kind, _ in contexts) and not any(kind == "Verified identity" for kind, _ in contexts):
            flash("This hash is currently known as a destination, not a verified identity. Blackholing requires an identity hash.", "error")
            return redirect(url_for("recovery", **preview["target"]))
        preview["labels"] = {key: known.get((kind, value), {}).get("label") for key, kind, value in (("identity", "identity", preview["target"]["identity"]), ("transport", "transport", preview["target"]["transport"]), ("destination", "destination", preview["target"]["destination"])) if value}
        session["recovery_preview"] = preview
        if not preview["actions"]: flash("Select at least one recovery action.", "error"); return redirect(url_for("recovery"))
        return render_template("recovery_preview.html", title="Recovery Preview", preview=preview, capabilities=capabilities)
    target = {key: request.args.get(key, "").strip() for key in ("identity", "transport", "destination", "interface")}
    snap, _, _ = ingest_snapshot(); known = labels(); rows = path_models(snap.get("paths") or [], known)
    warnings = []
    if target["identity"] and any(kind == "Destination path" for kind, _ in hash_context(target["identity"], rows, known)):
        warnings.append("This hash is known as a destination, not a verified identity. Blackholing requires an identity hash.")
    return render_template("recovery.html", title="Incident Recovery", capabilities=capabilities, target=target, labels=known, warnings=warnings)

@app.post("/recovery/apply")
@login_required
def recovery_apply():
    require_csrf(); preview = session.pop("recovery_preview", None)
    if not preview: abort(400, "No recovery preview")
    if "drop_announces" in preview["actions"] and request.form.get("typed_drop") != "DROP ANNOUNCES": abort(400, "Typed confirmation required")
    if "detach" in preview["actions"] and request.form.get("typed_detach") != "DETACH": abort(400, "Typed confirmation required")
    results=[]; status="Completed"
    op_map={"blackhole":"blackhole", "unblackhole":"unblackhole", "drop_path":"drop_path", "drop_via":"drop_via", "drop_announces":"drop_announces", "attach":"attach", "detach":"detach", "reload":"reload"}
    payloads={"blackhole":{"identity":preview["target"]["identity"],"duration":preview["duration"],"reason":preview["reason"]},"unblackhole":{"identity":preview["target"]["identity"]},"drop_path":{"destination":preview["target"]["destination"]},"drop_via":{"transport":preview["target"]["transport"]},"drop_announces":{},"attach":{"interface":preview["target"]["interface"]},"detach":{"interface":preview["target"]["interface"]},"reload":{"interface":preview["target"]["interface"]}}
    for action in preview["actions"]:
        result=management("/execute", {"operation":op_map[action],"payload":payloads[action]}); results.append(result)
        audit("recovery_"+action, f"Recovery {action}: {'succeeded' if result.get('ok') else 'failed'}", "info" if result.get("ok") else "error", preview["target"].get("interface") or None, detail={"target":preview["target"],"result":result})
        if not result.get("ok"): status="Partially completed" if any(x.get("ok") for x in results) else "Failed"; break
    with db() as con: con.execute("INSERT INTO incidents(started_at,admin,target_json,actions_json,preview_json,results_json,status) VALUES(?,?,?,?,?,?,?)", (now(),session["admin"],json.dumps(preview["target"]),json.dumps(preview["actions"]),json.dumps(preview),json.dumps(results),status))
    flash(f"Incident recovery {status.lower()}.", "success" if status == "Completed" else "error"); return redirect(url_for("incident_history"))

@app.get("/incidents")
@login_required
def incident_history():
    with db() as con: rows=con.execute("SELECT * FROM incidents ORDER BY started_at DESC LIMIT 100").fetchall()
    return render_template("incidents.html", title="Incident History", rows=rows)

@app.get("/blackholes")
@login_required
def blackholes():
    result=management("/execute", {"operation":"list_blackholes","payload":{}})
    known = labels()
    return render_template("blackholes.html", title="Blackholes", result=result, known=known, rows=parse_blackhole_rows(result, known))

@app.get("/system")
@login_required
def system():
    snap, _, _ = ingest_snapshot(); caps=management("/capabilities")
    checks={"Container running":snap["running"], "Reticulum ready":snap.get("reticulum_ready") is True and not bool(snap.get("status_error")), "observer snapshot":bool(snap.get("collected_at")), "rnpath":not bool(snap.get("path_error")), "management agent":bool(caps.get("capabilities")), "admin database":True}
    return render_template("system.html", title="System", info=system_info(), snap=snap, checks=checks, capabilities=caps.get("capabilities", {}))

def interface_diagnostic(row):
    fields = row.get("configured_fields") or {}; runtime_up = row.get("status") is True
    checks = []
    interface_type = row.get("type")
    if interface_type == "TCPClientInterface":
        host, port = fields.get("target_host"), fields.get("target_port")
        checks += [("Network Layer", None), ("Target host configured", bool(host)), ("Target port configured", bool(port))]
        if host:
            try: socket.getaddrinfo(host, None); checks.append(("Target resolved", True))
            except socket.gaierror: checks.append(("Target resolved", False))
        checks += [("Reticulum Layer", None), ("Runtime interface present", not row.get("runtime_only")), ("Runtime status Up", runtime_up), ("IFAC active", row.get("ifac_enabled") is True), ("Traffic observed", bool((row.get("rx_bytes") or 0) + (row.get("tx_bytes") or 0)))]
    elif interface_type == "TCPServerInterface":
        checks += [("Network Layer", None), ("Listen IP configured", bool(fields.get("listen_ip"))), ("Listen port valid", str(fields.get("listen_port", "")).isdigit() and 0 < int(fields.get("listen_port")) < 65536), ("Reticulum Layer", None), ("Runtime interface present", not row.get("runtime_only")), ("Runtime status Up", runtime_up), ("IFAC active", row.get("ifac_enabled") is True), ("Traffic observed", bool((row.get("rx_bytes") or 0) + (row.get("tx_bytes") or 0)))]
    else:
        checks += [("Reticulum Layer", None), ("Runtime interface present", not row.get("runtime_only")), ("Runtime status Up", runtime_up), ("IFAC active", row.get("ifac_enabled") is True), ("Peers reachable", row.get("peer_count") if row.get("peer_count") is not None else None), ("Traffic observed", bool((row.get("rx_bytes") or 0) + (row.get("tx_bytes") or 0)))]
    return checks

@app.get("/diagnostics")
@login_required
def diagnostics():
    snap, observed, clients = ingest_snapshot(); rows, _ = merge_interface_models(configuration_model().get("interfaces", []), observed)
    health = {"Shared Instance": snap.get("reticulum_ready") is True and not snap.get("status_error"), "Transport Mode": transport_enabled(), "Known Paths": len(snap.get("paths") or []), "Configured Interfaces": len(rows), "Runtime Interfaces": len(observed), "Connected Clients": len(clients), "Observer": bool(snap.get("collected_at")), "Management Agent": bool(management("/capabilities").get("capabilities"))}
    return render_template("diagnostics.html", title="Diagnostics", rows=rows, health=health, bot=bot_status())

@app.get("/diagnostics/interfaces/<name>")
@login_required
def diagnose_interface(name):
    _, observed, _ = ingest_snapshot(); rows, _ = merge_interface_models(configuration_model().get("interfaces", []), observed)
    row = next((item for item in rows if item.get("name") == name), None)
    if not row: abort(404)
    return render_template("interface_diagnostics.html", title=f"Diagnose {name}", interface=row, checks=interface_diagnostic(row))

@app.route("/diagnostics/bot", methods=["GET", "POST"])
@login_required
def test_echo_bot():
    if request.method == "POST":
        require_csrf(); action = request.form.get("action")
        settings = bot_settings()
        if action == "save":
            try: interval = int(request.form.get("interval_minutes", ""))
            except ValueError: interval = 0
            if interval < 15:
                flash("Periodic announce interval must be at least 15 minutes.", "error")
                return redirect(url_for("test_echo_bot"))
            settings.update({"enabled": bool(request.form.get("enabled")), "display_name": request.form.get("display_name", "").strip()[:80] or BOT_DEFAULTS["display_name"], "announce_on_startup": bool(request.form.get("announce_on_startup")), "periodic_announce": bool(request.form.get("periodic_announce")), "interval_minutes": interval})
            atomic_json(BOT_CONFIG_DIR / "settings.json", settings); audit("bot_settings", "Test Echo Bot settings changed"); flash("Bot settings saved.", "success")
        elif action == "announce":
            (BOT_CONFIG_DIR / "announce-request").parent.mkdir(parents=True, exist_ok=True); (BOT_CONFIG_DIR / "announce-request").write_text(str(now())); audit("bot_announce_requested", "Manual Test Echo Bot announce requested"); flash("Manual announce requested.", "success")
        elif action == "stop":
            settings["enabled"] = False; atomic_json(BOT_CONFIG_DIR / "settings.json", settings); audit("bot_disabled", "Test Echo Bot disabled"); flash("Bot disabled; its identity is preserved.", "success")
        return redirect(url_for("test_echo_bot"))
    return render_template("bot.html", title="Test Echo Bot", settings=bot_settings(), status=bot_status())

@app.get("/announces")
@login_required
def announces():
    _, interfaces, _ = ingest_snapshot()
    return render_template("announces.html", title="Announces", rows=interfaces)

@app.get("/queues")
@login_required
def queues():
    _, interfaces, _ = ingest_snapshot()
    return render_template("queues.html", title="Queues", rows=interfaces)

@app.get("/configuration")
@login_required
def configuration():
    config = configuration_model()
    with db() as con: changes=con.execute("SELECT * FROM config_changes ORDER BY occurred_at DESC LIMIT 50").fetchall()
    return render_template("configuration.html", title="Configuration", config=config, error=None if config else "Configuration data is temporarily unavailable.", changes=changes)

@app.post("/configuration/preview")
@login_required
def configuration_preview():
    require_csrf(); kind=request.form.get("kind")
    if kind == "global":
        setting=request.form.get("setting"); raw=request.form.get("value")
        change={"kind":"global","setting":setting,"value":raw == "on" if setting == "enable_transport" else raw}
    else:
        changes={}
        for key in ("enabled", "mode", "network_name", "passphrase", "ifac_size", "listen_ip", "listen_port", "target_host", "target_port"):
            value=request.form.get(key)
            if value not in (None, ""): changes[key] = (value.lower() in ("on","yes","true","1")) if key == "enabled" else (int(value) if key in ("ifac_size","listen_port","target_port") and value.isdigit() else value)
        change={"kind":"interface","name":request.form.get("name"),"changes":changes}
    # This must be instant: it is only a review of the submitted values.  The
    # allowlisted management agent validates the same change against the live
    # config immediately before it writes anything in configuration_apply().
    result = local_config_preview(change)
    operation_id = f"CFG-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{secrets.token_hex(3).upper()}"
    session["config_change"]={"change":change,"reason":request.form.get("reason", "Configuration change")[:240], "operation_id":operation_id}
    return render_template("configuration_preview.html", title="Review Configuration Change", preview=result, change=change)

@app.post("/configuration/apply")
@login_required
def configuration_apply():
    require_csrf(); item=session.pop("config_change", None)
    if not item: abort(400, "No configuration change to apply")
    if item["change"].get("kind") == "interface_delete" and request.form.get("delete_confirmation") != "DELETE INTERFACE":
        abort(400, "Typed confirmation required")
    result=management("/config/apply", {"admin":session["admin"],"reason":item["reason"],"change":item["change"],"operation_id":item["operation_id"]})
    result.setdefault("operation_id", item["operation_id"])
    if result.get("ok"): update_config_view_cache(item["change"])
    record_config_change(result, item["change"])
    flash("Configuration applied and restart verified." if result.get("ok") and result.get("restart_verified") else "Configuration applied." if result.get("ok") else "The management operation did not return a result in time. Reticulum may still have applied the change; check current status before retrying." if result.get("unknown") else "Configuration failed and was rolled back." if result.get("rolled_back") else "Configuration failed; recovery needs attention.", "success" if result.get("ok") else "error")
    return redirect(url_for("configuration"))

@app.route("/snapshots", methods=["GET", "POST"])
@login_required
def snapshots():
    if request.method == "POST":
        require_csrf(); result=management("/snapshots", {"admin":session["admin"],"reason":request.form.get("reason", "Manual snapshot")[:240]})
        audit("snapshot_created", f"Manual configuration snapshot {(result.get('snapshot') or {}).get('id','')}", detail={"snapshot":result.get("snapshot")}); flash("Snapshot created." if result.get("ok") else result.get("error","Snapshot failed"), "success" if result.get("ok") else "error")
        return redirect(url_for("snapshots"))
    result=management("/snapshots")
    return render_template("snapshots.html", title="Configuration Snapshots", snapshots=result.get("snapshots", []), current_hash=result.get("current_hash"), error=result.get("error"))

@app.post("/snapshots/<snapshot_id>/compare")
@login_required
def snapshot_compare(snapshot_id):
    require_csrf(); result=management(f"/snapshots/{snapshot_id}/compare", {})
    return render_template("snapshot_compare.html", title="Snapshot Comparison", result=result, snapshot_id=snapshot_id)

@app.post("/snapshots/<snapshot_id>/restore")
@login_required
def snapshot_restore(snapshot_id):
    require_csrf()
    if request.form.get("confirm") != "RESTORE":
        compare=management(f"/snapshots/{snapshot_id}/compare", {})
        return render_template("snapshot_restore_confirm.html", title="Confirm Snapshot Restore", snapshot_id=snapshot_id, result=compare)
    result=management(f"/snapshots/{snapshot_id}/restore", {"admin":session["admin"],"reason":"Manual snapshot restore"})
    # A restore replaces the config behind the web process. Never show a
    # pre-restore interface list from the fast local configuration cache.
    if result.get("ok"):
        CONFIG_VIEW_CACHE["config"] = None
    record_config_change(result, {"setting":f"restore snapshot {snapshot_id}"}); flash("Snapshot restored." if result.get("ok") else "Restore failed; safety rollback attempted.", "success" if result.get("ok") else "error")
    return redirect(url_for("snapshots"))

@app.get("/<page>")
@login_required
def read_only_page(page):
    if page not in (): abort(404)
    return render_template("read_only.html", title=page.title(), text="This is intentionally read-only. The dashboard and Interfaces views show metrics when rnstatus exposes them; no control operations are enabled.", raw=None)

def collector():
    """Persist observer changes even while nobody has the dashboard open."""
    last_cleanup = 0
    while True:
        try:
            ingest_snapshot()
            if now() - last_cleanup > 21600:
                days = max(1, int(os.environ.get("EVENT_RETENTION_DAYS", "90")))
                cutoff = now() - days*86400
                with db() as con:
                    con.execute("DELETE FROM events WHERE occurred_at < ?", (cutoff,))
                    # Connection history is observability data, so retain it
                    # for the same operator-controlled window as Timeline.
                    con.execute("DELETE FROM connections WHERE status='disconnected' AND disconnected_at < ?", (cutoff,))
                last_cleanup = now()
        except Exception: pass
        time.sleep(15)

if os.environ.get("OBSERVATION_COLLECTOR", "true").lower() == "true":
    threading.Thread(target=collector, name="reticulum-observer-collector", daemon=True).start()
