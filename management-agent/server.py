import json, os, secrets, subprocess
from flask import Flask, abort, jsonify, request
from management import ALLOWED, capabilities, run
import configuration

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

@app.get("/config")
def config_get(): auth(); return jsonify({"ok": True, "config": configuration.public_model()})

@app.post("/config/preview")
def config_preview():
    auth(); body=request.get_json(silent=True) or {}
    try:
        before=configuration.read_config(); candidate=configuration.edit_text(before, body.get("change") or {})
        return jsonify({"ok":True, "diff":configuration.redacted_diff(before, candidate), "candidate_hash":configuration.config_hash(candidate)})
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400

@app.post("/config/apply")
def config_apply():
    auth(); body=request.get_json(silent=True) or {}
    try: return jsonify(configuration.apply_change(str(body.get("admin") or "admin")[:80], str(body.get("reason") or "Configuration change")[:240], body.get("change") or {}, body.get("operation_id")))
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400

@app.get("/snapshots")
def snapshots(): auth(); return jsonify({"ok":True,"snapshots":configuration.list_snapshots(),"current_hash":configuration.config_hash(configuration.read_config())})

@app.post("/snapshots")
def snapshot_create():
    auth(); body=request.get_json(silent=True) or {}
    return jsonify({"ok":True,"snapshot":configuration.create_snapshot(str(body.get("admin") or "admin")[:80], str(body.get("reason") or "Manual snapshot")[:240], {"manual":True}, automatic=False)})

@app.post("/snapshots/<snapshot_id>/compare")
def snapshot_compare(snapshot_id):
    auth()
    try: return jsonify({"ok":True,"diff":configuration.redacted_diff(configuration.read_config(), configuration.snapshot_text(snapshot_id))})
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400

@app.post("/snapshots/<snapshot_id>/restore")
def snapshot_restore(snapshot_id):
    auth(); body=request.get_json(silent=True) or {}
    try: return jsonify(configuration.restore_snapshot(str(body.get("admin") or "admin")[:80], str(body.get("reason") or "Snapshot restore")[:240], snapshot_id))
    except ValueError as error: return jsonify({"ok":False,"error":str(error)}),400
