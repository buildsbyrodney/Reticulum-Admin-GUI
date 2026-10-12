#!/usr/bin/env python3
"""Report whether this rnsd image can run Reticulum interface discovery."""
import importlib.metadata
import json
import sys


def capability():
    try:
        import LXMF  # noqa: F401 - importing is the runtime compatibility check
        return {"lxmf_available": True, "lxmf_version": importlib.metadata.version("lxmf")}
    except (ImportError, importlib.metadata.PackageNotFoundError) as error:
        return {"lxmf_available": False, "error": f"LXMF is unavailable: {error}"}


if __name__ == "__main__":
    result = capability()
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["lxmf_available"] else 1)
