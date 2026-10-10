#!/usr/bin/env python3
"""Execute a tiny allowlist of Reticulum commands inside the rnsd runtime.

The management agent writes structured requests to the shared control directory;
it never gets Docker access or a shell in this container.
"""
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

CONFIG = Path(sys.argv[1])
CONTROL = CONFIG / "admin-control"
REQUESTS = CONTROL / "requests"
RESULTS = CONTROL / "results"
STATUS = CONFIG / "admin-status"
READY = STATUS / "rnsd.ready"
HASH = re.compile(r"^[0-9a-fA-F]{32}$")
NAME = re.compile(r"^[^\x00-\x1f\n\r]{1,100}$")
OPERATION = re.compile(r"^OP-[A-F0-9]{16}$")
ALLOWED = {"blackhole", "unblackhole", "list_blackholes", "drop_path", "drop_via", "drop_announces", "attach", "detach", "reload"}

def atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)

def hash_value(value):
    if not isinstance(value, str) or not HASH.fullmatch(value): raise ValueError("Expected a 32-character hexadecimal Reticulum hash")
    return value.lower()
def name_value(value):
    if not isinstance(value, str) or not NAME.fullmatch(value): raise ValueError("Invalid interface name")
    return value
def duration_value(value):
    if value in (None, "", "permanent"): return None
    try: hours = float(value)
    except (TypeError, ValueError): raise ValueError("Duration must be a number of hours")
    if not 0 < hours <= 8760: raise ValueError("Duration must be between 0 and 8760 hours")
    return hours
def reason_value(value):
    if value in (None, ""): return None
    if not isinstance(value, str) or len(value) > 240 or any(ord(c) < 32 for c in value): raise ValueError("Reason must be printable and at most 240 characters")
    return value

def command_for(action, payload):
    if action not in ALLOWED: raise ValueError("Unknown management operation")
    prefix = ["--config", str(CONFIG)]
    if action == "list_blackholes": return ["rnpath", *prefix, "--blackholed", "--json"]
    if action == "blackhole":
        command = ["rnpath", *prefix, "--blackhole", hash_value(payload.get("identity"))]
        duration, reason = duration_value(payload.get("duration")), reason_value(payload.get("reason"))
        if duration is not None: command += ["--duration", str(duration)]
        if reason: command += ["--reason", reason]
        return command
    if action == "unblackhole": return ["rnpath", *prefix, "--unblackhole", hash_value(payload.get("identity"))]
    if action == "drop_path": return ["rnpath", *prefix, "--drop", hash_value(payload.get("destination"))]
    if action == "drop_via": return ["rnpath", *prefix, "--drop-via", hash_value(payload.get("transport"))]
    if action == "drop_announces": return ["rnpath", *prefix, "--drop-announces"]
    return ["rnstatus", *prefix, {"attach":"--attach", "detach":"--detach", "reload":"--reload"}[action], name_value(payload.get("interface"))]

def write_capabilities():
    help_text = ""
    for binary in ("rnpath", "rnstatus"):
        try: help_text += subprocess.run([binary, "--help"], capture_output=True, text=True, timeout=5).stdout
        except OSError: pass
    atomic_json(STATUS / "capabilities.json", {"blackhole":"--blackhole" in help_text, "unblackhole":"--unblackhole" in help_text, "drop_path":"--drop" in help_text, "drop_via_transport":"--drop-via" in help_text, "drop_announces":"--drop-announces" in help_text, "attach_interface":"--attach" in help_text, "detach_interface":"--detach" in help_text, "reload_interface":"--reload" in help_text})

def execute(request):
    operation_id, action, payload = request.get("operation_id"), request.get("action"), request.get("payload") or {}
    if not OPERATION.fullmatch(str(operation_id or "")): raise ValueError("Invalid control operation ID")
    if not READY.exists(): return {"operation_id": operation_id, "operation": action, "ok": False, "error": "Reticulum is not ready; operation was not executed."}
    command = command_for(action, payload)
    result = subprocess.run(command, capture_output=True, text=True, timeout=20)
    return {"operation_id": operation_id, "operation": action, "ok": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:]}

def main():
    REQUESTS.mkdir(parents=True, exist_ok=True); RESULTS.mkdir(parents=True, exist_ok=True); STATUS.mkdir(parents=True, exist_ok=True)
    write_capabilities()
    while True:
        for path in sorted(REQUESTS.glob("*.json")):
            request = None
            try:
                request = json.loads(path.read_text(encoding="utf-8")); result = execute(request)
            except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
                operation_id = request.get("operation_id") if isinstance(request, dict) else ""
                result = {"operation_id": operation_id, "ok": False, "error": str(error)}
            if result.get("operation_id"):
                atomic_json(RESULTS / f"{result['operation_id']}.json", result)
            path.unlink(missing_ok=True)
        time.sleep(0.2)

if __name__ == "__main__": main()
