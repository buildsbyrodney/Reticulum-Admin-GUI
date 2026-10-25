"""Tests for the isolated, read-only Nomad page browser service and web view."""
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).parents[1]
SERVICE_PATH = ROOT / "nomad-browser" / "nomad_browser.py"
spec = importlib.util.spec_from_file_location("nomad_browser_service", SERVICE_PATH)
nomad = importlib.util.module_from_spec(spec); spec.loader.exec_module(nomad)


class NomadBrowserServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_state, self.old_requests = nomad.STATE, nomad.REQUESTS
        nomad.STATE, nomad.REQUESTS = Path(self.temp.name) / "state", Path(self.temp.name) / "requests"

    def tearDown(self):
        nomad.STATE, nomad.REQUESTS = self.old_state, self.old_requests
        self.temp.cleanup()

    def test_only_safe_nomad_page_paths_are_accepted(self):
        self.assertTrue(nomad.valid_page_path("/page/index.mu"))
        self.assertTrue(nomad.valid_page_path("/page/docs/about.mu"))
        self.assertFalse(nomad.valid_page_path("/file/secret"))
        self.assertFalse(nomad.valid_page_path("/page/../secret.mu"))
        self.assertFalse(nomad.valid_page_path("page/index.mu"))

    def test_existing_page_cache_is_migrated_for_identification_state(self):
        nomad.STATE.mkdir(parents=True)
        old = __import__("sqlite3").connect(nomad.STATE / "nomad.db")
        old.execute("CREATE TABLE pages(destination_hash TEXT,path TEXT,request_id TEXT,state TEXT,page_text TEXT,error TEXT,fetched_at INTEGER,updated_at INTEGER,PRIMARY KEY(destination_hash,path))")
        old.commit(); old.close()
        with nomad.connect() as con:
            columns = {row[1] for row in con.execute("PRAGMA table_info(pages)")}
        self.assertIn("identify", columns)

    def test_service_requires_the_existing_shared_instance_without_management_access(self):
        source = SERVICE_PATH.read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        service = compose.split("  nomad-browser:", 1)[1].split("\nnetworks:", 1)[0]
        self.assertIn('require_shared_instance=True', source)
        self.assertIn('./data/reticulum:/reticulum:ro', service)
        self.assertNotIn('MANAGEMENT_TOKEN', service)
        self.assertNotIn('docker.sock', service)
        self.assertNotIn('ports:', service)

    def test_request_is_persisted_then_fetched_over_nomad_node_aspect(self):
        target, calls = "a" * 32, []

        class Identity:
            def __init__(self): self.hash = bytes.fromhex("1" * 32)
            @staticmethod
            def from_file(_): return Identity()
            @staticmethod
            def recall(value):
                calls.append(("recall", value)); return object()
            def to_file(self, path): Path(path).write_text("identity", encoding="utf-8")

        class Transport:
            @staticmethod
            def has_path(value): calls.append(("has_path", value)); return True

        class Destination:
            OUT = SINGLE = 1
            def __init__(self, *args): self.args = args

        class Link:
            ACTIVE = 1
            establishment_timeout = 1
            def __init__(self, destination, established_callback=None, **_):
                self.destination, self.status = destination, self.ACTIVE
                established_callback(self)
            def identify(self, identity): calls.append(("identify", identity.hash.hex()))
            def request(self, path, data=None, response_callback=None, failed_callback=None):
                calls.append(("request", path, data))
                response_callback(types.SimpleNamespace(response=b">Hello Nomad"))
                return object()
            def teardown(self): calls.append(("teardown",))

        rns = types.SimpleNamespace(Identity=Identity, Transport=Transport, Destination=Destination, Link=Link)
        service = nomad.NomadBrowser()
        item = {"id": "request-1", "destination": target, "path": "/page/index.mu", "identify": True}
        self.assertTrue(service.fetch(item, rns))
        with nomad.connect() as con:
            row = con.execute("SELECT state,page_text,error,identify FROM pages WHERE destination_hash=?", (target,)).fetchone()
        self.assertEqual(row["state"], "Ready")
        self.assertEqual(row["page_text"], ">Hello Nomad")
        self.assertIsNone(row["error"])
        self.assertEqual(row["identify"], 1)
        self.assertIn(("request", "/page/index.mu", None), calls)
        self.assertIn(("identify", "1" * 32), calls)
        remote = next(call for call in calls if call[0] == "recall")
        self.assertEqual(remote[1], bytes.fromhex(target))

    def test_missing_route_is_safe_failure_and_never_creates_a_master(self):
        target = "b" * 32

        class Identity:
            @staticmethod
            def recall(_): return None
        class Transport:
            @staticmethod
            def has_path(_): return False
            @staticmethod
            def request_path(_): pass

        old = nomad.PATH_LOOKUP_SECONDS; nomad.PATH_LOOKUP_SECONDS = 1
        try:
            service = nomad.NomadBrowser()
            self.assertTrue(service.fetch({"id": "missing", "destination": target, "path": "/page/index.mu"}, types.SimpleNamespace(Identity=Identity, Transport=Transport)))
            with nomad.connect() as con: row = con.execute("SELECT state,error FROM pages WHERE destination_hash=?", (target,)).fetchone()
            self.assertEqual(row["state"], "Failed")
            self.assertIn("route", row["error"].lower())
        finally:
            nomad.PATH_LOOKUP_SECONDS = old

    def test_browser_identity_is_persistent_and_separate_from_page_destination(self):
        class Identity:
            created = 0
            def __init__(self): self.hash = bytes.fromhex("d" * 32); Identity.created += 1
            @staticmethod
            def from_file(_): return Identity()
            def to_file(self, path): Path(path).write_text("private identity", encoding="utf-8")

        first = nomad.NomadBrowser().load_identity(types.SimpleNamespace(Identity=Identity))
        second = nomad.NomadBrowser().load_identity(types.SimpleNamespace(Identity=Identity))
        self.assertEqual(first.hash.hex(), second.hash.hex())
        self.assertTrue((nomad.STATE / "identity").exists())


class NomadBrowserWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(); root = Path(cls.temp.name)
        os.environ.update({"APP_DATA": str(root / "admin"), "MESSAGING_DIR": str(root / "messaging"),
                           "MESSAGING_REQUEST_DIR": str(root / "messaging-requests"), "NOMAD_DIR": str(root / "nomad"),
                           "NOMAD_REQUEST_DIR": str(root / "nomad-requests"), "ADMIN_SECRET_KEY": "nomad-web-test"})
        sys.path.insert(0, str(ROOT / "reticulum-admin")); sys.modules.pop("app", None)
        import app
        cls.web = app; (root / "nomad").mkdir(parents=True)
        with cls.web.db() as con: con.execute("INSERT INTO admins VALUES(?,?,?)", ("admin", "hash", 0))
        db = __import__("sqlite3").connect(root / "nomad" / "nomad.db")
        db.execute("CREATE TABLE pages(destination_hash TEXT,path TEXT,request_id TEXT,state TEXT,page_text TEXT,error TEXT,identify INTEGER NOT NULL DEFAULT 0,fetched_at INTEGER,updated_at INTEGER,PRIMARY KEY(destination_hash,path))")
        cls.destination = "c" * 32
        db.execute("INSERT INTO pages VALUES(?,?,?,?,?,?,?,?,?)", (cls.destination, "/page/index.mu", "id", "Ready", "<b>safe text</b>", None, 0, 1, 1)); db.commit(); db.close()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def client(self):
        client = self.web.app.test_client()
        with client.session_transaction() as session: session.update({"admin": "admin", "csrf": "csrf"})
        return client

    def test_nomad_page_requires_login_and_escapes_remote_content(self):
        self.assertEqual(self.web.app.test_client().get("/nomad").status_code, 302)
        response = self.client().get("/nomad?destination=" + self.destination)
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn("Nomad Browser", page)
        self.assertIn("&lt;b&gt;safe text&lt;/b&gt;", page)
        self.assertIn("Nomad Browser", (ROOT / "reticulum-admin" / "templates" / "base.html").read_text(encoding="utf-8"))

    def test_identify_option_is_compact_on_desktop_and_full_width_on_mobile(self):
        css = (ROOT / "reticulum-admin" / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn(".nomad-request .nomad-identify { display: inline-flex; flex: 0 0 auto;", css)
        self.assertIn(".nomad-request .nomad-identify { width: 100%; white-space: normal;", css)

    def test_browse_post_only_creates_narrow_request_file_and_marks_live_request(self):
        root = Path(os.environ["NOMAD_REQUEST_DIR"])
        response = self.client().post("/nomad", data={"csrf_token": "csrf", "destination": self.destination, "path": "/page/index.mu", "identify": "1"})
        self.assertEqual(response.status_code, 302)
        requests = list(root.glob("browse-*.json")); self.assertEqual(len(requests), 1)
        value = json.loads(requests[0].read_text(encoding="utf-8"))
        self.assertEqual(value["destination"], self.destination); self.assertEqual(value["path"], "/page/index.mu"); self.assertTrue(value["identify"])
        response = self.client().get(response.headers["Location"])
        page = response.get_data(as_text=True)
        self.assertIn('data-request-pending="true"', page)
        self.assertIn("Loading page", page)
        self.assertIn("record.request_id !== requestedId", page)
        # Flask uses a read-only URI and has no Nomad SQLite mutation path.
        source = (ROOT / "reticulum-admin" / "app.py").read_text(encoding="utf-8")
        self.assertIn("file:{NOMAD_DIR / 'nomad.db'}?mode=ro", source)


if __name__ == "__main__":
    unittest.main()
