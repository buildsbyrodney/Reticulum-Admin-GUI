"""Bounded, read-only NomadNet page client for Reticulum Admin.

This is intentionally a small consumer of the documented Reticulum Link
request/response mechanism used by NomadNet nodes. It is not a NomadNet node,
does not host pages, and does not execute or submit remote page content.
"""
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from pathlib import Path

STATE = Path(os.environ.get("NOMAD_STATE_DIR", "/nomad"))
REQUESTS = Path(os.environ.get("NOMAD_REQUEST_DIR", "/requests"))
RETICULUM_READY = Path("/reticulum/admin-status/rnsd.ready")
DESTINATION_RE = re.compile(r"[0-9a-fA-F]{32}")
DEFAULT_PATH = "/page/index.mu"
MAX_PATH = 200
MAX_PAGE_BYTES = 128 * 1024
PATH_LOOKUP_SECONDS = max(1, min(30, int(os.environ.get("NOMAD_PATH_LOOKUP_SECONDS", "10"))))
LINK_TIMEOUT_SECONDS = max(3, min(45, int(os.environ.get("NOMAD_LINK_TIMEOUT_SECONDS", "15"))))
REQUEST_TIMEOUT_SECONDS = max(3, min(45, int(os.environ.get("NOMAD_REQUEST_TIMEOUT_SECONDS", "20"))))
IDENTITY_FILE = "identity"


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def valid_destination(value):
    return bool(DESTINATION_RE.fullmatch(value or ""))


def valid_page_path(value):
    """Accept only a normal Nomad page path, never files or traversal."""
    if not isinstance(value, str):
        return False
    return (value.startswith("/page/") and len(value) <= MAX_PATH and ".." not in value
            and "\\" not in value and "\x00" not in value)


def connect():
    STATE.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STATE / "nomad.db")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript("""
      CREATE TABLE IF NOT EXISTS pages (
        destination_hash TEXT NOT NULL,
        path TEXT NOT NULL,
        request_id TEXT,
        state TEXT NOT NULL,
        page_text TEXT,
        error TEXT,
        identify INTEGER NOT NULL DEFAULT 0,
        fetched_at INTEGER,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY(destination_hash, path)
      );
      CREATE INDEX IF NOT EXISTS pages_updated_idx ON pages(updated_at DESC);
    """)
    try:
        con.execute("ALTER TABLE pages ADD COLUMN identify INTEGER NOT NULL DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    return con


class NomadBrowser:
    def __init__(self):
        self.started = int(time.time())
        self.identity = None
        self.state = {"last_error": None, "last_fetch_at": None, "last_destination": None,
                      "last_path": None, "requests_processed": 0}

    def load_identity(self, RNS):
        """Load one private, persistent identity used only when requested.

        Nodes that protect a Nomad page use the caller identity sent over an
        established Link. This must never be the Bot, Messaging, rnsd, or web
        identity. The private file remains in this service's own state volume.
        """
        if self.identity:
            return self.identity
        STATE.mkdir(parents=True, exist_ok=True)
        identity_path = STATE / IDENTITY_FILE
        self.identity = RNS.Identity.from_file(str(identity_path)) if identity_path.exists() else RNS.Identity()
        if not identity_path.exists():
            self.identity.to_file(str(identity_path))
            try:
                os.chmod(identity_path, 0o600)
            except OSError:
                pass
        return self.identity

    def status(self, ready=False):
        with connect() as con:
            page_count = con.execute("SELECT count(*) FROM pages WHERE state='Ready'").fetchone()[0]
        atomic_json(STATE / "status.json", {
            "running": ready,
            "process_running": True,
            "shared_instance_connected": ready,
            "mode": "Read-only Nomad page browser",
            "identity_loaded": bool(self.identity),
            "identity": self.identity.hash.hex() if self.identity else None,
            "page_cache_count": page_count,
            "uptime_seconds": int(time.time()) - self.started,
            **self.state,
        })

    def persist(self, destination, path, state, request_id=None, page_text=None, error=None, identify=False):
        stamp = int(time.time())
        with connect() as con:
            con.execute("""INSERT INTO pages(destination_hash,path,request_id,state,page_text,error,identify,fetched_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(destination_hash,path) DO UPDATE SET
                             request_id=excluded.request_id,state=excluded.state,page_text=excluded.page_text,
                             error=excluded.error,identify=excluded.identify,fetched_at=excluded.fetched_at,updated_at=excluded.updated_at""",
                        (destination, path, request_id, state, page_text, error, int(bool(identify)),
                         stamp if state == "Ready" else None, stamp))

    def fetch(self, item, RNS):
        destination = str(item.get("destination", "")).lower()
        path = item.get("path", DEFAULT_PATH)
        request_id = str(item.get("id") or uuid.uuid4().hex)
        identify = item.get("identify") is True
        if not valid_destination(destination) or not valid_page_path(path):
            return False
        self.persist(destination, path, "Pending", request_id=request_id, identify=identify)
        destination_hash = bytes.fromhex(destination)
        self.state.update({"last_destination": destination, "last_path": path, "last_error": None})
        link = None
        try:
            # A path may be learned shortly after an application announce. Ask
            # once, then wait only within this deliberately bounded budget.
            if not RNS.Transport.has_path(destination_hash):
                RNS.Transport.request_path(destination_hash)
                deadline = time.monotonic() + PATH_LOOKUP_SECONDS
                while not RNS.Transport.has_path(destination_hash) and time.monotonic() < deadline:
                    time.sleep(0.1)
            identity = RNS.Identity.recall(destination_hash)
            if identity is None:
                self.persist(destination, path, "Failed", request_id, error="No usable Reticulum route was available before the lookup timeout.", identify=identify)
                return True

            established, response_done = threading.Event(), threading.Event()
            response = {"body": None, "failed": False}
            remote = RNS.Destination(identity, RNS.Destination.OUT, RNS.Destination.SINGLE,
                                     "nomadnetwork", "node")
            link = RNS.Link(remote, established_callback=lambda _: established.set())
            budget = max(LINK_TIMEOUT_SECONDS, getattr(link, "establishment_timeout", 0))
            if not established.wait(budget) or getattr(link, "status", None) != RNS.Link.ACTIVE:
                self.persist(destination, path, "Failed", request_id, error="Could not establish a Reticulum link to this Nomad node.", identify=identify)
                return True

            if identify:
                # Link identification is a supported Reticulum operation. It
                # reveals only this separate browser identity to the selected
                # node; it is never performed for a normal public-page request.
                link.identify(self.load_identity(RNS))

            def received(receipt):
                response["body"] = getattr(receipt, "response", None)
                response_done.set()

            def failed(_=None):
                response["failed"] = True
                response_done.set()

            receipt = link.request(path, data=None, response_callback=received, failed_callback=failed)
            if not receipt or not response_done.wait(REQUEST_TIMEOUT_SECONDS):
                self.persist(destination, path, "Failed", request_id, error="Nomad page request timed out.", identify=identify)
                return True
            if response["failed"] or not isinstance(response["body"], (bytes, bytearray)):
                self.persist(destination, path, "Failed", request_id, error="Nomad node did not return a page.", identify=identify)
                return True
            body = bytes(response["body"])
            if len(body) > MAX_PAGE_BYTES:
                self.persist(destination, path, "Failed", request_id, error="Nomad page exceeds the local safety limit.", identify=identify)
                return True
            text = body.decode("utf-8", errors="strict")
            self.persist(destination, path, "Ready", request_id, page_text=text, identify=identify)
            self.state["last_fetch_at"] = int(time.time())
        except UnicodeDecodeError:
            self.persist(destination, path, "Failed", request_id, error="Nomad page was not valid UTF-8 text.", identify=identify)
        except Exception:
            # Do not disclose Reticulum/link implementation details in the UI.
            self.persist(destination, path, "Failed", request_id, error="Nomad page request could not be completed.", identify=identify)
        finally:
            try:
                if link: link.teardown()
            except Exception:
                pass
        return True

    def process_requests(self, RNS):
        REQUESTS.mkdir(parents=True, exist_ok=True)
        for request in sorted(REQUESTS.glob("browse-*.json")):
            try:
                item = json.loads(request.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            # Retain malformed or not-yet-durable requests. A successfully
            # handled request has first written its state to the service DB.
            if self.fetch(item, RNS):
                request.unlink(missing_ok=True)
                self.state["requests_processed"] += 1

    def run(self):
        import RNS
        STATE.mkdir(parents=True, exist_ok=True)
        REQUESTS.mkdir(parents=True, exist_ok=True)
        while not RETICULUM_READY.exists():
            self.status(False)
            time.sleep(2)
        RNS.Reticulum(configdir="/reticulum", require_shared_instance=True)
        self.load_identity(RNS)
        while True:
            try:
                self.process_requests(RNS)
                self.status(True)
                time.sleep(1)
            except Exception:
                self.state["last_error"] = "Nomad browser runtime error"
                self.status(False)
                time.sleep(5)


if __name__ == "__main__":
    NomadBrowser().run()
