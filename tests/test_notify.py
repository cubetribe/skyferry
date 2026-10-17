import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from skyferry.config import NotifyCfg
from skyferry.notify import Notifier, safe_error, unit_label
from skyferry.state import State, Unit
from skyferry.util import strip_paths


class Capture(BaseHTTPRequestHandler):
    log: list = []

    def _rec(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8") if n else ""
        Capture.log.append({"method": self.command, "path": self.path, "headers": dict(self.headers), "body": body})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    do_GET = do_POST = _rec

    def log_message(self, *a):
        pass


class NotifyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Capture)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def setUp(self):
        Capture.log = []
        base = f"http://127.0.0.1:{self.port}"
        self.cfg = NotifyCfg(ntfy_url=base + "/topic", ntfy_token="tok", healthchecks_url=base + "/hc/uuid",
                             min_repeat_minutes=60)
        self.n = Notifier(self.cfg, State(":memory:"), sleep=lambda s: None)

    def test_alarm_headers_and_rate_limit(self):
        self.assertTrue(self.n.alarm("Migration: Fehler äöü", "Text", unit_id=1))
        r = Capture.log[0]
        self.assertEqual(r["method"], "POST")
        self.assertEqual(r["path"], "/topic")
        self.assertEqual(r["headers"]["Priority"], "5")
        self.assertEqual(r["headers"]["Tags"], "rotating_light")
        self.assertTrue(r["headers"]["Title"].startswith("=?UTF-8?B?"))
        self.assertEqual(r["headers"]["Authorization"], "Bearer tok")
        self.assertFalse(self.n.alarm("Migration: Fehler äöü", "Text", unit_id=1))  # Rate-Limit
        self.assertTrue(self.n.alarm("Migration: Fehler äöü", "Text", unit_id=2))  # andere Unit
        self.assertEqual(len(Capture.log), 2)

    def test_healthchecks(self):
        self.n.hc_start()
        self.n.hc_ping()
        self.n.hc_fail("kaputt")
        self.assertEqual([(r["method"], r["path"]) for r in Capture.log],
                         [("GET", "/hc/uuid/start"), ("GET", "/hc/uuid"), ("POST", "/hc/uuid/fail")])
        self.assertEqual(Capture.log[2]["body"], "kaputt")

    def test_done_batching_prio2(self):
        u = Unit(1, "a", "", "p", "n", "folder", None, False, 1, 1000, 0, "DONE", 0, None, None, 0, 0, None, None)
        self.n.unit_done(u)
        self.n.unit_done(u)
        self.assertTrue(self.n.flush_done())
        self.assertEqual(Capture.log[0]["headers"]["Priority"], "2")
        self.n.unit_done(u)
        self.assertFalse(self.n.flush_done())  # hoechstens einmal pro Stunde

    def test_network_failure_never_raises_and_alarm_retries(self):
        sleeps = []
        n = Notifier(NotifyCfg(ntfy_url="http://127.0.0.1:1/x", healthchecks_url="http://127.0.0.1:1/h"),
                     sleep=sleeps.append)
        self.assertFalse(n.alarm("t", "m"))
        self.assertEqual(len(sleeps), 3)  # 3 Wiederholungen mit Backoff
        self.assertFalse(n.hc_ping())
        self.assertFalse(Notifier(NotifyCfg(ntfy_url="nicht-eine-url")).info("t", "m"))

    def test_disabled_channels(self):
        n = Notifier(NotifyCfg())
        self.assertFalse(n.alarm("t", "m"))
        self.assertFalse(n.hc_start())

    def test_label_without_paths(self):
        u = Unit(42, "id", "", "Kunde NDA/Projekt X", "Projekt X", "folder", None, False, 1, 12_300_000_000, 0,
                 "PLANNED", 0, None, None, 0, 0, None, None)
        self.assertEqual(unit_label(u, False), "Unit #42 (folder, 12,3 GB)")
        self.assertIn("Kunde NDA", unit_label(u, True))

    def test_strip_paths(self):
        s = strip_paths("Failed to copy: 'Kunde NDA/Vertrag.pdf': error \"Mein Ordner/x\" in /home/u/data/a.txt")
        self.assertNotIn("Kunde", s)
        self.assertNotIn("Vertrag", s)
        self.assertNotIn("/home", s)
        self.assertNotIn("Mein Ordner", s)
        self.assertLessEqual(len(safe_error("x" * 1000, False)), 160)


if __name__ == "__main__":
    unittest.main()
