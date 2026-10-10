"""Allowlisted Reticulum configuration changes and private snapshots.

This is intentionally a small line-preserving editor, not a generic file API.
Only known settings may be changed and unknown lines/comments are retained.
"""
import hashlib, json, os, re, subprocess, tempfile, time
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
# A Pi 3 can take a while to relaunch rnsd, but a single rnstatus probe should
# never consume most of that wait.
RESTART_HEALTH_TIMEOUT = float(os.environ.get("RESTART_HEALTH_TIMEOUT", "35"))
INTERFACE_HEALTH_TIMEOUT = float(os.environ.get("INTERFACE_HEALTH_TIMEOUT", "10"))
RNSTATUS_PROBE_TIMEOUT = float(os.environ.get("RNSTATUS_PROBE_TIMEOUT", "3"))
POLL_INTERVAL = float(os.environ.get("RESTART_POLL_INTERVAL", "0.75"))
MODES = {"full", "point_to_point", "access_point", "roaming", "boundary", "gateway", "internal"}
LOG_LEVELS = {str(n) for n in range(0, 8)}
SECRET_KEYS = {"passphrase", "ifac_passphrase", "network_key"}
COMMON = {"enabled", "mode", "network_name", "passphrase", "ifac_size"}
TYPE_FIELDS = {
    "TCPServerInterface": COMMON | {"listen_ip", "listen_port"},
    "TCPClientInterface": COMMON | {"target_host", "target_port"},
}

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
        for key in COMMON | {"listen_ip", "listen_port", "target_host", "target_port", "port", "frequency", "bandwidth", "txpower", "spreadingfactor", "codingrate"}:
            if key in values: item["fields"][key] = "Configured" if secret(key) else values[key]
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
            if key in ("listen_port", "target_port") and (not isinstance(value, int) or not 1 <= value <= 65535): raise ValueError("Port must be between 1 and 65535")
            if key in ("listen_ip", "target_host", "network_name", "passphrase", "port") and (not isinstance(value, str) or not value.strip() or len(value) > 255 or any(ord(c) < 32 for c in value)): raise ValueError("Invalid text value")
            if key == "ifac_size" and (not isinstance(value, int) or value < 1 or value > 512): raise ValueError("Invalid IFAC size")
        return
    raise ValueError("Unknown configuration operation")

def replacement_value(key, value):
    if key in ("enabled", "enable_transport"): return "Yes" if value else "No"
    return str(value)

def edit_text(text, change):
    validate_change(change, text)
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

def probe_health():
    """Run one bounded, inexpensive health probe."""
    try:
        result = subprocess.run(["rnstatus", "--config", str(ROOT), "--json"], capture_output=True, text=True, timeout=RNSTATUS_PROBE_TIMEOUT)
        return result.returncode == 0, (result.stderr or result.stdout)[-1000:]
    except (OSError, subprocess.TimeoutExpired) as error:
        return False, str(error)

def health(wait_for_restart=False, generation_before=None, operation_id=None):
    """Bounded health check; restart health never trusts an old heartbeat."""
    if not wait_for_restart:
        return probe_health()
    before = rnsd_generation() if generation_before is None else generation_before
    deadline = time.monotonic() + RESTART_HEALTH_TIMEOUT
    last = "Waiting for rnsd generation to advance"
    while time.monotonic() < deadline:
        generation = rnsd_generation()
        if generation > before:
            ok, detail = probe_health()
            if ok:
                return True, detail, {"restart_verified": True, "generation": generation, "operation_id": operation_id}
            last = detail or "rnstatus has not responded after restart"
        else:
            last = f"Waiting for rnsd generation after {before} (currently {generation})"
        time.sleep(POLL_INTERVAL)
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
    return before

def apply_runtime(change, operation_id=None):
    if change["kind"] == "interface":
        result = subprocess.run(["rnstatus", "--config", str(ROOT), "--reload", change["name"]], capture_output=True, text=True, timeout=INTERFACE_HEALTH_TIMEOUT)
        return result.returncode == 0, "interface_reload", (result.stderr or result.stdout)[-1000:]
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
    snapshot = create_snapshot(admin, reason, change, automatic=True)
    atomic_write(candidate); runtime = apply_runtime(change, operation_id); applied, method, detail = runtime[:3]; generation_before = runtime[3] if len(runtime) > 3 else None
    if applied:
        checked = health(method == "rnsd_restart", generation_before, operation_id)
        ok, health_detail = checked[:2]; verification = checked[2] if len(checked) > 2 else {}
    else: ok, health_detail, verification = False, detail, {}
    if ok:
        result = {"ok": True, "snapshot": snapshot, "diff": diff, "apply_method": method, "health": health_detail, "operation_id": operation_id, **verification}
        save_operation(operation_id, result); return result
    atomic_write(before); rollback = apply_runtime(change, None); rollback_applied, rollback_method, rollback_detail = rollback[:3]; rollback_before = rollback[3] if len(rollback) > 3 else None
    if rollback_applied:
        recovery = health(rollback_method == "rnsd_restart", rollback_before, operation_id)
        recovered, recovered_detail = recovery[:2]
    else: recovered, recovered_detail = False, rollback_detail
    result = {"ok": False, "snapshot": snapshot, "diff": diff, "apply_method": method, "operation_id": operation_id, "error": health_detail, "rolled_back": recovered, "rollback_error": None if recovered else recovered_detail}
    save_operation(operation_id, result); return result

def restore_snapshot(admin, reason, snapshot_id):
    before, target = read_config(), snapshot_text(snapshot_id); diff = redacted_diff(before, target); safety = create_snapshot(admin, reason, {"restore": snapshot_id}, automatic=True, parent=snapshot_id)
    atomic_write(target); before_generation = write_restart_request()
    checked = health(True, before_generation); ok, detail = checked[:2]
    if ok: return {"ok": True, "snapshot": safety, "diff": diff, "apply_method": "rnsd_restart"}
    atomic_write(before); rollback_generation = write_restart_request(); recovery_result = health(True, rollback_generation); recovered, recovery = recovery_result[:2]
    return {"ok": False, "snapshot": safety, "diff": diff, "rolled_back": recovered, "error": detail, "rollback_error": None if recovered else recovery}
