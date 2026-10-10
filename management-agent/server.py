import json, os, secrets, subprocess
from flask import Flask, abort, jsonify, request
from management import ALLOWED, capabilities, run

app = Flask(__name__); TOKEN = os.environ.get("MANAGEMENT_TOKEN", ""); CONFIG = os.environ.get("RETICULUM_DIR", "/reticulum")
def auth():
    if not TOKEN or not secrets.compare_digest(request.headers.get("X-Management-Token", ""), TOKEN): abort(401)
def help_text():
    parts=[]
    for binary in ("rnpath", "rnstatus"):
        try: parts.append(subprocess.run([binary,"--help"],capture_output=True,text=True,timeout=5).stdout)
        except OSError: pass
    return "\n".join(parts)
@app.get("/capabilities")
def get_capabilities(): auth(); return jsonify({"capabilities":capabilities(help_text())})
@app.post("/execute")
def execute():
    auth(); body=request.get_json(silent=True) or {}; operation=body.get("operation")
    if operation not in ALLOWED: return jsonify({"ok":False,"error":"Unknown operation"}),400
    try: return jsonify(run(operation, body.get("payload") or {}))
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
    except subprocess.TimeoutExpired: return jsonify({"ok":False,"error":"Reticulum utility timed out"}),504
