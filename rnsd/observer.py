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


def write_snapshot(payload):
    temporary = SNAPSHOT.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    os.replace(temporary, SNAPSHOT)


def main():
    STATUS.mkdir(parents=True, exist_ok=True)
    while True:
        status, error = get_json_status()
        paths, path_error = get_json_paths()
        write_snapshot({
            "collected_at": utc_now(),
            "rns_version": version(),
            "status": status,
            "status_error": error,
            "paths": paths,
            "path_error": path_error,
            "source": "rnstatus --json --all --totals --queues --announce-stats --pr-stats",
        })
        time.sleep(max(15, int(os.environ.get("OBSERVER_INTERVAL", "30"))))


if __name__ == "__main__":
    main()
