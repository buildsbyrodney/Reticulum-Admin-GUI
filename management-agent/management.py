"""Allowlisted Reticulum operation builder; deliberately contains no shell API."""
import re
import subprocess

HASH = re.compile(r"^[0-9a-fA-F]{32}$")
NAME = re.compile(r"^[^\x00-\x1f\n\r]{1,100}$")
ALLOWED = {"blackhole", "unblackhole", "list_blackholes", "drop_path", "drop_via", "drop_announces", "attach", "detach", "reload"}

def validate_hash(value):
    if not isinstance(value, str) or not HASH.fullmatch(value): raise ValueError("Expected a 32-character hexadecimal Reticulum hash")
    return value.lower()
def validate_name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value): raise ValueError("Invalid interface name")
    return value
def validate_duration(value):
    if value in (None, "", "permanent"): return None
    try: hours = float(value)
    except (TypeError, ValueError): raise ValueError("Duration must be a number of hours")
    if not 0 < hours <= 8760: raise ValueError("Duration must be between 0 and 8760 hours")
    return hours
def validate_reason(value):
    if value in (None, ""): return None
    if not isinstance(value, str) or len(value) > 240 or any(ord(c) < 32 for c in value): raise ValueError("Reason must be printable and at most 240 characters")
    return value

def command_for(operation, payload, config="/reticulum"):
    if operation not in ALLOWED: raise ValueError("Unknown management operation")
    prefix = ["--config", config]
    if operation == "list_blackholes": return ["rnpath", *prefix, "--blackholed", "--json"]
    if operation == "blackhole":
        cmd = ["rnpath", *prefix, "--blackhole", validate_hash(payload.get("identity"))]
        duration = validate_duration(payload.get("duration")); reason = validate_reason(payload.get("reason"))
        if duration is not None: cmd += ["--duration", str(duration)]
        if reason: cmd += ["--reason", reason]
        return cmd
    if operation == "unblackhole": return ["rnpath", *prefix, "--unblackhole", validate_hash(payload.get("identity"))]
    if operation == "drop_path": return ["rnpath", *prefix, "--drop", validate_hash(payload.get("destination"))]
    if operation == "drop_via": return ["rnpath", *prefix, "--drop-via", validate_hash(payload.get("transport"))]
    if operation == "drop_announces": return ["rnpath", *prefix, "--drop-announces"]
    flag = {"attach":"--attach", "detach":"--detach", "reload":"--reload"}[operation]
    return ["rnstatus", *prefix, flag, validate_name(payload.get("interface"))]

def capabilities(help_text):
    return {"blackhole":"--blackhole" in help_text, "unblackhole":"--unblackhole" in help_text, "drop_path":"--drop" in help_text, "drop_via_transport":"--drop-via" in help_text, "drop_announces":"--drop-announces" in help_text, "attach_interface":"--attach" in help_text, "detach_interface":"--detach" in help_text, "reload_interface":"--reload" in help_text}

def run(operation, payload, runner=subprocess.run):
    command = command_for(operation, payload)
    result = runner(command, capture_output=True, text=True, timeout=20)
    return {"operation": operation, "ok": result.returncode == 0, "returncode": result.returncode, "stdout": result.stdout[-4000:], "stderr": result.stderr[-4000:], "command": [command[0], *command[2:]]}

def run_sequence(steps, execute):
    """Deterministic recovery policy: stop on first failed destructive action."""
    results=[]
    for operation, payload in steps:
        result=execute(operation, payload); results.append(result)
        if not result.get("ok"): break
    status = "Completed" if results and all(x.get("ok") for x in results) else ("Partially completed" if any(x.get("ok") for x in results) else "Failed")
    return status, results
