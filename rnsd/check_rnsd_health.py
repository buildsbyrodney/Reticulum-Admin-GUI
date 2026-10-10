#!/usr/bin/env python3
"""Cheap Docker health probe based on rnsd's local observer snapshot."""
import json, os, sys, time
from datetime import datetime
from pathlib import Path

def healthy(root, maximum_age=45, clock=time.time):
    status = root / "admin-status"
    try:
        generation = int((status / "rnsd.generation").read_text().strip())
        snapshot = json.loads((status / "snapshot.json").read_text())
        collected = datetime.fromisoformat(str(snapshot["collected_at"]).replace("Z", "+00:00")).timestamp()
        if clock() - collected > maximum_age: raise ValueError("observer snapshot is stale")
        if snapshot.get("rnsd_generation") != generation: raise ValueError("observer snapshot generation does not match rnsd")
        if snapshot.get("reticulum_ready") is not True: raise ValueError("Reticulum shared instance is unavailable")
        if snapshot.get("status_error"): raise ValueError("observer reported Reticulum status error")
        if not isinstance(snapshot.get("status"), (dict, list)) or not snapshot.get("status"): raise ValueError("observer has no valid Reticulum status")
        return True, "healthy"
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error: return False, str(error)

if __name__ == "__main__":
    root = Path(os.environ.get("RETICULUM_CONFIG_DIR", "/root/.reticulum"))
    ok, message = healthy(root, int(os.environ.get("RNSD_SNAPSHOT_MAX_AGE", "45")))
    if not ok: print(f"rnsd unhealthy: {message}", file=sys.stderr)
    raise SystemExit(0 if ok else 1)
