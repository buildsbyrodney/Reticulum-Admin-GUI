"""Allowlisted Reticulum configuration changes and private snapshots.

This is intentionally a small line-preserving editor, not a generic file API.
Only known settings may be changed and unknown lines/comments are retained.
"""
import hashlib, json, logging, os, re, tempfile, time
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path(os.environ.get("RETICULUM_DIR", "/reticulum")) / "config"
ROOT = CONFIG.parent
SNAPSHOTS = ROOT / "admin-config-snapshots"
RESTART_REQUEST = ROOT / "admin-control" / "restart-request"
STATUS_DIR = ROOT / "admin-status"
GENERATION_FILE = STATUS_DIR / "rnsd.generation"
OPERATIONS = ROOT / "admin-config-operations"
# These values deliberately fit inside the explicit Gunicorn and HTTP budgets.
# A Pi 3 can take a while to relaunch rnsd, so observer-based health has a
# bounded deadline and never runs an agent-local Reticulum CLI probe.
RESTART_HEALTH_TIMEOUT = float(os.environ.get("RESTART_HEALTH_TIMEOUT", "35"))
INTERFACE_HEALTH_TIMEOUT = float(os.environ.get("INTERFACE_HEALTH_TIMEOUT", "10"))
POLL_INTERVAL = float(os.environ.get("RESTART_POLL_INTERVAL", "0.75"))
SNAPSHOT_FILE = STATUS_DIR / "snapshot.json"
LOG = logging.getLogger("reticulum_admin.configuration")
MODES = {"full", "point_to_point", "access_point", "roaming", "boundary", "gateway", "internal"}
LOG_LEVELS = {str(n) for n in range(0, 8)}
SECRET_KEYS = {"passphrase", "ifac_passphrase", "network_key"}
COMMON = {"enabled", "mode", "network_name", "passphrase", "ifac_size"}
TYPE_FIELDS = {
    "TCPServerInterface": COMMON | {"listen_ip", "listen_port"},
    "TCPClientInterface": COMMON | {"target_host", "target_port"},
    "AutoInterface": {"enabled", "mode", "network_name", "passphrase", "ifac_size"},
}
INTERFACE_TYPES = set(TYPE_FIELDS)

def utc_now(): return datetime.now(timezone.utc).isoformat()
def config_hash(text): return hashlib.sha256(text.encode()).hexdigest()
def secret(key): return key.lower() in SECRET_KEYS or "passphrase" in key.lower()

def read_config():
    try: return CONFIG.read_text(encoding="utf-8")
    except OSError: return ""

def parse(text):
    sections, current = {"reticulum": {"name": "reticulum", "kind": "global", "settings": {}}}, "reticulum"
    for index, line in enumerate(text.splitlines()):
        match = re.match(r"^\s*(\[\[.*?\]\]|\[.*?\])\s*$", line)
        if match:
            label = match.group(1)
            name = label[2:-2].strip() if label.startswith("[[") else label[1:-1].strip()
            current = name
            sections.setdefault(name, {"name": name, "kind": "interface" if label.startswith("[[") else "section", "settings": {}, "line": index})
            continue
        setting = re.match(r"^(\s*)([A-Za-z][A-Za-z0-9_]*)(\s*=\s*)(.*?)(\s*(?:#.*)?)$", line)
        if setting:
            sections.setdefault(current, {"name": current, "kind": "section", "settings": {}})["settings"][setting.group(2).lower()] = setting.group(4).strip()
    return sections

def public_model(text=None):
    text = read_config() if text is None else text
    sections = parse(text)
    global_settings = sections.get("reticulum", {}).get("settings", {})
    interfaces = []
    for section in sections.values():
        if section.get("kind") != "interface": continue
        values = section["settings"]
        item = {"name": section["name"], "type": values.get("type"), "enabled": values.get("enabled"), "mode": values.get("mode"), "fields": {}}
        for key in COMMON | {"ifac_netname", "ifac_passphrase", "listen_ip", "listen_port", "target_host", "target_port", "port", "frequency", "bandwidth", "txpower", "spreadingfactor", "codingrate"}:
            # IFAC is a shared interface credential that operators distribute
            # to connecting nodes. It is viewable only through the authenticated
            # internal management API; diffs, logs, audit records, and snapshot
            # metadata continue to redact it.
            if key in values: item["fields"][key] = values[key]
        interfaces.append(item)
    return {"hash": config_hash(text), "global": {"enable_transport": global_settings.get("enable_transport", "No"), "loglevel": global_settings.get("loglevel")}, "interfaces": interfaces, "version": os.environ.get("RNS_VERSION", "1.5.7")}

def validate_change(change, text=None):
    if not isinstance(change, dict): raise ValueError("Invalid configuration change")
    kind = change.get("kind")
    if kind == "global":
        setting, value = change.get("setting"), change.get("value")
        if setting == "enable_transport" and isinstance(value, bool): return
        if setting == "loglevel" and str(value) in LOG_LEVELS: return
        raise ValueError("Unsupported global setting")
    if kind == "interface":
        name, changes = change.get("name"), change.get("changes")
        sections = parse(read_config() if text is None else text)
        section = sections.get(name)
        if not isinstance(name, str) or not section or section.get("kind") != "interface" or not isinstance(changes, dict): raise ValueError("Unknown interface")
        allowed = TYPE_FIELDS.get(section["settings"].get("type"), COMMON)
        for key, value in changes.items():
            if key not in allowed: raise ValueError("Unsupported interface setting")
            if key == "enabled" and not isinstance(value, bool): raise ValueError("enabled must be boolean")
            if key == "mode" and str(value).lower() not in MODES: raise ValueError("Unsupported interface mode")
            if key == "listen_port" and (not isinstance(value, int) or not 1 <= value <= 65535): raise ValueError("Listen Port must be between 1 and 65535.")
            if key == "target_port" and (not isinstance(value, int) or not 1 <= value <= 65535): raise ValueError("Target Port must be between 1 and 65535.")
            if key in ("listen_ip", "target_host", "network_name", "passphrase", "port") and (not isinstance(value, str) or not value.strip() or len(value) > 255 or any(ord(c) < 32 for c in value)): raise ValueError("Invalid text value")
            if key == "ifac_size" and (not isinstance(value, int) or value < 1 or value > 512): raise ValueError("Invalid IFAC size")
        return
    if kind == "interface_create":
        validate_interface_definition(change.get("interface"), read_config() if text is None else text)
        return
    if kind == "interface_delete":
        name = change.get("name")
        section = parse(read_config() if text is None else text).get(name)
        if not isinstance(name, str) or not section or section.get("kind") != "interface": raise ValueError("Unknown interface")
        return
    raise ValueError("Unknown configuration operation")

def valid_name(name):
    return isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._:-]{0,79}", name or "") is not None

def validate_interface_definition(interface, text):
    if not isinstance(interface, dict): raise ValueError("Invalid interface definition")
    name, interface_type = interface.get("name"), interface.get("type")
    sections = parse(text)
    if not valid_name(name): raise ValueError("Interface name must be 1–80 characters and use only letters, numbers, spaces, dots, underscores, colons, or hyphens")
    if name in sections: raise ValueError(f'An interface named "{name}" already exists. Choose another name.')
    if interface_type not in INTERFACE_TYPES: raise ValueError("Unsupported interface type")
    fields = dict(interface.get("fields") or {})
    fields["enabled"] = bool(interface.get("enabled", True))
    fields["mode"] = str(interface.get("mode", "full")).lower()
    # Reuse the edit validator for type, ports, IFAC size, and text fields.
    synthetic = "[[candidate]]\ntype = %s\n" % interface_type
    validate_change({"kind":"interface", "name":"candidate", "changes":fields}, synthetic)
    if interface_type == "TCPServerInterface":
        port = fields.get("listen_port")
        if not isinstance(port, int): raise ValueError("Listen Port must be between 1 and 65535.")
        address = fields.get("listen_ip", "0.0.0.0")
        for section in sections.values():
            if section.get("kind") != "interface" or section["settings"].get("type") != "TCPServerInterface": continue
            old = section["settings"]
            if str(old.get("listen_port", "")) == str(port) and old.get("listen_ip", "0.0.0.0") == address:
                raise ValueError(f"TCP server {address}:{port} is already configured by interface {section['name']}")
    if interface_type == "TCPClientInterface" and not fields.get("target_host"): raise ValueError("Target Host must be the hostname or IP address of the remote Reticulum TCP server.")

def section_ranges(text):
    lines, ranges, current, start = text.splitlines(keepends=True), {}, None, None
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(\[\[.*?\]\]|\[.*?\])\s*$", line)
        if not match: continue
        if current is not None: ranges[current] = (start, index)
        label = match.group(1); current = label[2:-2].strip() if label.startswith("[[") else label[1:-1].strip(); start = index
    if current is not None: ranges[current] = (start, len(lines))
    return lines, ranges

def create_interface_text(text, interface):
    validate_interface_definition(interface, text)
    fields = dict(interface.get("fields") or {})
    fields["enabled"] = bool(interface.get("enabled", True)); fields["mode"] = str(interface.get("mode", "full")).lower()
    # Only schema-supported keys are written; arbitrary request keys never
    # become configuration text.
    allowed = TYPE_FIELDS[interface["type"]]
    lines = ["\n", f"[[{interface['name']}]]\n", f"type = {interface['type']}\n"]
    for key in ("enabled", "mode", "listen_ip", "listen_port", "target_host", "target_port", "network_name", "passphrase", "ifac_size"):
        if key in fields and key in allowed: lines.append(f"{key} = {replacement_value(key, fields[key])}\n")
    return text.rstrip("\n") + "\n" + "".join(lines)

def delete_interface_text(text, name):
    validate_change({"kind":"interface_delete", "name":name}, text)
    lines, ranges = section_ranges(text); start, end = ranges[name]
    del lines[start:end]
    return "".join(lines)

def replacement_value(key, value):
    if key in ("enabled", "enable_transport"): return "Yes" if value else "No"
    return str(value)

def edit_text(text, change):
    validate_change(change, text)
    if change["kind"] == "interface_create": return create_interface_text(text, change["interface"])
    if change["kind"] == "interface_delete": return delete_interface_text(text, change["name"])
    if change["kind"] == "global": target, changes = "reticulum", {change["setting"]: change["value"]}
    else: target, changes = change["name"], change["changes"]
    lines, section_start, section_end, current = text.splitlines(keepends=True), {}, {}, None
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(\[\[.*?\]\]|\[.*?\])\s*$", line)
        if match:
            if current is not None: section_end[current] = index
            label = match.group(1); current = label[2:-2].strip() if label.startswith("[[") else label[1:-1].strip(); section_start[current] = index
    if current is not None: section_end[current] = len(lines)
    if target not in section_start: raise ValueError("Configuration section is missing")
    seen = set(); start, end = section_start[target] + 1, section_end[target]
    for index in range(start, end):
        match = re.match(r"^(\s*)([A-Za-z][A-Za-z0-9_]*)(\s*=\s*)(.*?)(\s*(?:#.*)?)(\r?\n?)$", lines[index])
        if match and match.group(2).lower() in changes:
            key = match.group(2).lower(); lines[index] = f"{match.group(1)}{match.group(2)}{match.group(3)}{replacement_value(key, changes[key])}{match.group(5)}{match.group(6)}"; seen.add(key)
    insert = [f"{key} = {replacement_value(key, value)}\n" for key, value in changes.items() if key not in seen]
    lines[end:end] = insert
    return "".join(lines)

def redacted_diff(before, after):
    a, b = parse(before), parse(after); result=[]
    for name in sorted(set(a) | set(b)):
        old, new = a.get(name, {}).get("settings", {}), b.get(name, {}).get("settings", {})
        for key in sorted(set(old) | set(new)):
            if old.get(key) != new.get(key): result.append({"section": name, "key": key, "old": "Changed" if secret(key) and key in old else old.get(key), "new": "Changed" if secret(key) and key in new else new.get(key)})
    return result

def atomic_write(text):
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    if CONFIG.exists():
        original = CONFIG.with_name("config.original")
        if not original.exists(): original.write_bytes(CONFIG.read_bytes()); os.chmod(original, 0o600)
    fd, path = tempfile.mkstemp(prefix="config.", dir=str(CONFIG.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle: handle.write(text); handle.flush(); os.fsync(handle.fileno())
        os.chmod(path, 0o600); os.replace(path, CONFIG)
    finally:
        if os.path.exists(path): os.unlink(path)

def create_snapshot(admin, reason, change, automatic=True, parent=None):
    text = read_config(); SNAPSHOTS.mkdir(parents=True, exist_ok=True); sid = f"{int(time.time()*1000)}"
    directory = SNAPSHOTS / sid; directory.mkdir(mode=0o700)
    (directory / "config").write_text(text, encoding="utf-8"); os.chmod(directory / "config", 0o600)
    safe_change = json.loads(json.dumps(change))
    for key in ("passphrase", "ifac_passphrase", "network_key"):
        if isinstance(safe_change.get("changes"), dict) and key in safe_change["changes"]:
            safe_change["changes"][key] = "Changed"
    meta = {"id": sid, "timestamp": utc_now(), "admin": admin, "reason": reason[:240], "change": safe_change, "automatic": automatic, "hash": config_hash(text), "parent": parent}
    (directory / "metadata.json").write_text(json.dumps(meta), encoding="utf-8"); os.chmod(directory / "metadata.json", 0o600)
    return meta

def list_snapshots():
    if not SNAPSHOTS.exists(): return []
    rows=[]
    for path in SNAPSHOTS.iterdir():
        try: rows.append(json.loads((path / "metadata.json").read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError): pass
    return sorted(rows, key=lambda item: item["timestamp"], reverse=True)

def snapshot_text(snapshot_id):
    if not re.fullmatch(r"\d{8,20}", str(snapshot_id)): raise ValueError("Invalid snapshot")
    try: return (SNAPSHOTS / str(snapshot_id) / "config").read_text(encoding="utf-8")
    except OSError: raise ValueError("Snapshot not found")

def rnsd_generation():
    try: return int(GENERATION_FILE.read_text(encoding="utf-8").strip())
    except (OSError, ValueError): return 0

def observer_snapshot():
    try:
        return json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return None

def probe_health(expected_generation=None):
    """Read a local-rnsd observer result; never invoke rnstatus in this container."""
    snapshot = observer_snapshot()
    if not snapshot: return False, "observer snapshot unavailable"
    observed = snapshot.get("rnsd_generation")
    if expected_generation is not None and not isinstance(observed, int): return False, "observer snapshot has no rnsd generation"
    if expected_generation is not None and observed != expected_generation: return False, f"observer snapshot generation {observed} does not match expected {expected_generation}"
    if expected_generation is not None:
        try:
            collected = datetime.fromisoformat(str(snapshot.get("collected_at", "")).replace("Z", "+00:00")).timestamp()
            if collected <= GENERATION_FILE.stat().st_mtime: return False, "observer snapshot predates rnsd generation transition"
        except (OSError, TypeError, ValueError): return False, "observer snapshot has no valid collection timestamp"
    if snapshot.get("reticulum_ready") is not True: return False, "observer reports Reticulum shared instance unavailable"
    if snapshot.get("status_error"): return False, f"observer status error: {snapshot['status_error']}"
    status = snapshot.get("status")
    if not isinstance(status, (dict, list)) or not status: return False, "observer snapshot has no valid Reticulum status"
    return True, f"observer snapshot generation {observed} healthy"

def health(wait_for_restart=False, generation_before=None, operation_id=None):
    """Bounded health check; restart health never trusts an old heartbeat."""
    if not wait_for_restart:
        return probe_health(rnsd_generation())
    before = rnsd_generation() if generation_before is None else generation_before
    deadline = time.monotonic() + RESTART_HEALTH_TIMEOUT
    last = "Waiting for rnsd generation to advance"
    while time.monotonic() < deadline:
        generation = rnsd_generation()
        if generation > before:
            ok, detail = probe_health(generation)
            if ok:
                LOG.info("%s observer snapshot generation %s healthy", operation_id or "configuration", generation)
                return True, detail, {"restart_verified": True, "generation": generation, "operation_id": operation_id}
            last = detail or "rnstatus has not responded after restart"
        else:
            last = f"Waiting for rnsd generation after {before} (currently {generation})"
        time.sleep(POLL_INTERVAL)
    LOG.warning("%s observer snapshot unavailable or unhealthy: %s", operation_id or "configuration", last)
    return False, last, {"restart_verified": False, "generation": rnsd_generation(), "operation_id": operation_id}

def write_restart_request(operation_id=None):
    RESTART_REQUEST.parent.mkdir(parents=True, exist_ok=True)
    before = rnsd_generation()
    payload = {"operation_id": operation_id or "", "requested_at": utc_now(), "generation_before": before}
    fd, path = tempfile.mkstemp(prefix="restart-request.", dir=str(RESTART_REQUEST.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle); handle.flush(); os.fsync(handle.fileno())
        os.replace(path, RESTART_REQUEST)
    finally:
        if os.path.exists(path): os.unlink(path)
    LOG.info("%s restart requested, generation %s", operation_id or "configuration", before)
    return before

def apply_runtime(change, operation_id=None):
    if change["kind"] == "interface":
        from management import run
        result = run("reload", {"interface": change["name"]})
        return bool(result.get("ok")), "interface_reload", (result.get("stderr") or result.get("stdout") or result.get("error") or "")[-1000:]
    before = write_restart_request(operation_id)
    return True, "rnsd_restart", "restart requested", before

def operation_file(operation_id):
    if not re.fullmatch(r"CFG-\d{8}-\d{6}-[A-F0-9]{6}", str(operation_id or "")):
        raise ValueError("Invalid configuration operation ID")
    return OPERATIONS / f"{operation_id}.json"

def save_operation(operation_id, result):
    path = operation_file(operation_id); OPERATIONS.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix="operation.", dir=str(OPERATIONS))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(result, handle); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)

def apply_change(admin, reason, change, operation_id=None):
    operation_id = operation_id or f"CFG-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-{os.urandom(3).hex().upper()}"
    existing = operation_file(operation_id)
    if existing.exists():
        try: return json.loads(existing.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError): raise ValueError("Configuration operation state is unreadable")
    before = read_config(); candidate = edit_text(before, change); diff = redacted_diff(before, candidate)
    if change.get("kind") != "interface":
        healthy, detail = health(False)
        if not healthy:
            result = {"ok": False, "operation_id": operation_id, "error": f"Refusing configuration restart while Reticulum is unhealthy: {detail}", "preflight_failed": True}
            save_operation(operation_id, result); return result
    snapshot = create_snapshot(admin, reason, change, automatic=True)
    atomic_write(candidate); runtime = apply_runtime(change, operation_id); applied, method, detail = runtime[:3]; generation_before = runtime[3] if len(runtime) > 3 else None
    if applied:
        checked = health(method == "rnsd_restart", generation_before, operation_id)
        ok, health_detail = checked[:2]; verification = checked[2] if len(checked) > 2 else {}
    else: ok, health_detail, verification = False, detail, {}
    if ok:
        result = {"ok": True, "snapshot": snapshot, "diff": diff, "apply_method": method, "health": health_detail, "operation_id": operation_id, **verification}
        LOG.info("%s configuration applied successfully", operation_id)
        save_operation(operation_id, result); return result
    LOG.warning("%s rollback started", operation_id)
    atomic_write(before); rollback = apply_runtime(change, None); rollback_applied, rollback_method, rollback_detail = rollback[:3]; rollback_before = rollback[3] if len(rollback) > 3 else None
    if rollback_applied:
        recovery = health(rollback_method == "rnsd_restart", rollback_before, operation_id)
        recovered, recovered_detail = recovery[:2]
    else: recovered, recovered_detail = False, rollback_detail
    result = {"ok": False, "snapshot": snapshot, "diff": diff, "apply_method": method, "operation_id": operation_id, "error": health_detail, "rolled_back": recovered, "recovery_failed": not recovered, "rollback_error": None if recovered else recovered_detail}
    save_operation(operation_id, result); return result

def restore_snapshot(admin, reason, snapshot_id):
    before, target = read_config(), snapshot_text(snapshot_id); diff = redacted_diff(before, target); safety = create_snapshot(admin, reason, {"restore": snapshot_id}, automatic=True, parent=snapshot_id)
    atomic_write(target); before_generation = write_restart_request()
    checked = health(True, before_generation); ok, detail = checked[:2]
    if ok: return {"ok": True, "snapshot": safety, "diff": diff, "apply_method": "rnsd_restart"}
    atomic_write(before); rollback_generation = write_restart_request(); recovery_result = health(True, rollback_generation); recovered, recovery = recovery_result[:2]
    return {"ok": False, "snapshot": safety, "diff": diff, "rolled_back": recovered, "error": detail, "rollback_error": None if recovered else recovery}
