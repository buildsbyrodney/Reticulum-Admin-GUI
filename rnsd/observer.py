#!/usr/bin/env python3
"""A read-only Reticulum observer that writes a sanitized status snapshot.

It runs beside rnsd, never receives web requests and never accepts commands from
the admin UI. Unknown rnstatus JSON fields are preserved in the snapshot so a
future adapter can add support without weakening the container boundary.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path(sys.argv[1])
STATUS = CONFIG / "admin-status"
SNAPSHOT = STATUS / "snapshot.json"
GENERATION = STATUS / "rnsd.generation"
CONTROL = CONFIG / "admin-control"
OBSERVE_REQUEST = CONTROL / "observe-request"


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def run(*command):
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=12)
        if result.returncode == 0:
            return result.stdout.strip(), None
        return None, result.stderr.strip() or "command exited with %s" % result.returncode
    except (OSError, subprocess.TimeoutExpired) as error:
        return None, str(error)


def get_json_status():
    # rnstatus is the supported local diagnostics client for the rnsd shared
    # instance. Flags request all available, read-only statistics.
    output, error = run("rnstatus", "--config", str(CONFIG), "--json", "--all", "--totals", "--queues", "--announce-stats", "--pr-stats")
    if output is None:
        return None, error
    try:
        return json.loads(output), None
    except json.JSONDecodeError as parse_error:
        return None, "rnstatus returned non-JSON data: %s" % parse_error

def get_json_paths():
    output, error = run("rnpath", "--config", str(CONFIG), "--table", "--json")
    if output is None:
        return None, error
    try:
        return json.loads(output), None
    except json.JSONDecodeError as parse_error:
        return None, "rnpath returned non-JSON data: %s" % parse_error


def version():
    output, _ = run("rnsd", "--version")
    return output

def host_network():
    """Capture only local Linux interface names/IPv4 addresses for safe UI validation.

    With host networking, this is the actual server network namespace. The
    management agent reads the resulting snapshot; it never needs host shell
    access or a Docker socket to validate a TCP Server bind address.
    """
    output, error = run("ip", "-j", "-4", "addr", "show")
    if output is None:
        return {"interfaces": [], "ipv4_addresses": [], "error": error}
    try:
        rows = json.loads(output)
        interfaces = []
        addresses = []
        for row in rows:
            local = [item.get("local") for item in row.get("addr_info", [])
                     if item.get("family") == "inet" and item.get("local")]
            interfaces.append({"name": row.get("ifname"), "ipv4_addresses": local})
            addresses.extend(local)
        return {"interfaces": interfaces, "ipv4_addresses": sorted(set(addresses)), "error": None}
    except (TypeError, ValueError, json.JSONDecodeError) as parse_error:
        return {"interfaces": [], "ipv4_addresses": [], "error": str(parse_error)}


def write_snapshot(payload):
    temporary = SNAPSHOT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, SNAPSHOT)

def generation():
    try: return int(GENERATION.read_text(encoding="utf-8").strip())
    except (OSError, ValueError): return 0

def previous_sequence():
    try: return int(json.loads(SNAPSHOT.read_text(encoding="utf-8")).get("observer_sequence") or 0)
    except (OSError, ValueError, json.JSONDecodeError): return 0


def main():
    STATUS.mkdir(parents=True, exist_ok=True); CONTROL.mkdir(parents=True, exist_ok=True)
    sequence = previous_sequence()
    while True:
        observed_generation = generation()
        status, error = get_json_status()
        paths, path_error = get_json_paths()
        # Do not attribute a status collected while rnsd was changing to the
        # new instance. The next requested observation will collect it again.
        if generation() != observed_generation:
            status, error = None, "rnsd generation changed during observation"
        sequence += 1
        write_snapshot({
            "collected_at": utc_now(),
            "rnsd_generation": observed_generation,
            "observer_sequence": sequence,
            "rns_version": version(),
            "status": status,
            "status_error": error,
            "reticulum_ready": error is None and isinstance(status, (dict, list)) and bool(status),
            "paths": paths,
            "path_error": path_error,
            "source": "rnstatus --json --all --totals --queues --announce-stats --pr-stats",
            "host_network": host_network(),
        })
        interval = max(5, int(os.environ.get("OBSERVER_INTERVAL", "30")))
        for _ in range(interval * 2):
            if OBSERVE_REQUEST.exists():
                OBSERVE_REQUEST.unlink(missing_ok=True)
                break
            time.sleep(0.5)


if __name__ == "__main__":
    main()
