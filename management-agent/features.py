"""Durable, dependency-aware feature activation policy.

This is intentionally not a Docker controller.  Feature data is preserved on
disable and services decide how to honour their enabled setting.  A later host
orchestrator can consume the same manifest for start/stop operations.
"""
import json, os, secrets, time
from pathlib import Path

ROOT = Path(os.environ.get("FEATURE_STATE_DIR", "/feature-services"))
BOT_CONFIG = Path(os.environ.get("BOT_CONFIG_DIR", "/bot-config"))
MESSAGING_REQUESTS = Path(os.environ.get("MESSAGING_REQUEST_DIR", "/messaging-requests"))
NOMAD_REQUESTS = Path(os.environ.get("NOMAD_REQUEST_DIR", "/nomad-requests"))

CATALOG = {
    "reticulum-core": {"title": "Core Reticulum", "mandatory": True, "depends_on": [], "available": True},
    "messaging": {"title": "LXMF Messaging", "mandatory": False, "depends_on": ["reticulum-core"], "available": True},
    "test-bot": {"title": "LXMF Test Echo Bot", "mandatory": False, "depends_on": ["reticulum-core"], "available": True},
    "nomad-browser": {"title": "Nomad Browser", "mandatory": False, "depends_on": ["reticulum-core"], "available": True},
    "lxmf-propagation": {"title": "LXMF Propagation Host", "mandatory": False, "depends_on": ["reticulum-core"], "available": False},
    "rnode": {"title": "RNode radio integration", "mandatory": False, "depends_on": ["reticulum-core"], "available": False},
}

def _read(path, fallback):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback
    except (OSError, json.JSONDecodeError): return fallback

def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)

def state():
    saved = _read(ROOT / "services.json", {})
    enabled = saved.get("enabled", {}) if isinstance(saved.get("enabled", {}), dict) else {}
    return {"enabled": {name: bool(enabled.get(name, meta["mandatory"])) for name, meta in CATALOG.items()}, "updated_at": saved.get("updated_at")}

def public_state():
    current = state()
    return {"features": [{"id": name, **meta, "enabled": current["enabled"][name]} for name, meta in CATALOG.items()], "updated_at": current["updated_at"]}

def _service_setting(path, enabled):
    value = _read(path, {})
    value["enabled"] = enabled
    _write(path, value)

def _apply_service_policies(enabled):
    _service_setting(MESSAGING_REQUESTS / "settings.json", enabled["messaging"])
    _service_setting(BOT_CONFIG / "settings.json", enabled["test-bot"])
    _service_setting(NOMAD_REQUESTS / "settings.json", enabled["nomad-browser"])

def apply(requested, admin="admin"):
    if not isinstance(requested, dict) or set(requested) != set(CATALOG):
        raise ValueError("Feature selection must include each known feature exactly once")
    proposed = {name: bool(requested[name]) for name in CATALOG}
    for name, meta in CATALOG.items():
        if meta["mandatory"] and not proposed[name]: raise ValueError("Core Reticulum cannot be disabled")
        if proposed[name] and not meta["available"]: raise ValueError(f"{meta['title']} is planned and cannot be enabled yet")
        if proposed[name] and any(not proposed[dependency] for dependency in meta["depends_on"]): raise ValueError(f"{meta['title']} requires its dependencies to remain enabled")
    before = state()
    backup = ROOT / "backups" / (str(int(time.time())) + "-" + secrets.token_hex(4) + ".json")
    _write(backup, before)
    try:
        _apply_service_policies(proposed)
        _write(ROOT / "services.json", {"enabled": proposed, "updated_at": int(time.time()), "updated_by": str(admin)[:80]})
    except Exception:
        # Best effort restoration is deterministic and data-preserving; the
        # previous durable selection remains authoritative on failed applies.
        _apply_service_policies(before["enabled"])
        raise
    return {"ok": True, "before": before["enabled"], "after": proposed, "rollback_snapshot": backup.name}
