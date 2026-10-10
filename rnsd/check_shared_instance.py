#!/usr/bin/env python3
"""One bounded local-client probe; never creates a Reticulum instance."""
import json, subprocess, sys

config = sys.argv[1]
try:
    result = subprocess.run(["rnstatus", "--config", config, "--json", "--all"], capture_output=True, text=True, timeout=3)
    data = json.loads(result.stdout) if result.returncode == 0 else None
    text = json.dumps(data) if data is not None else ""
    if result.returncode != 0 or "Shared Instance[" not in text:
        raise ValueError(result.stderr.strip() or "shared instance interface was not reported")
except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError) as error:
    print(str(error), file=sys.stderr); raise SystemExit(1)
raise SystemExit(0)
