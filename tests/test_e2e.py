import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from http.server import HTTPServer
from pathlib import Path

from skyferry.app import build_app
from skyferry.notify import Notifier
from skyferry.planner import build_tree, commit_plan, compute_stats, plan_units
from skyferry.runner import AlreadyRunning, Runner, instance_lock
from skyferry.state import Status
from tests.helpers import RCLONE, TmpCase, make_fake_drive, md5_tree, needs_rclone
from tests.test_notify import Capture

ROOT = Path(__file__).resolve().parent.parent


def plan_app(app):
    cfg = app.cfg
    tree = build_tree(app.source.inventory(), root_id=app.source.root_id, done_prefix=cfg.source.done_prefix,
                      max_unit_bytes=cfg.buffer.max_unit_bytes)
    planned = plan_units(tree, max_unit_bytes=cfg.buffer.max_unit_bytes, existing=app.state.find_unit)
    commit_plan(app.state, planned, tree, compute_stats(planned, tree))
    return planned


def by_path(app, path, kind="folder"):
    return app.state.find_unit(path, kind)


def step_until(runner, pred, limit=200):
    for _ in range(limit):
        if pred():
            return True
        runner.step()
    return pred()


@needs_rclone
class EndToEnd(TmpCase):
    def setUp(self):
        super().setUp()
        make_fake_drive(self.tmp / "drive")
        self.before = md5_tree(self.tmp / "drive")

    def test_full_migration(self):
        app = self.app()
        plan_app(app)
        names = sorted((u.drive_path, u.kind, u.is_split_container) for u in app.state.units())
        self.assertEqual(names, [("", "loose_files", False), ("Dokumente", "folder", False),
                                 ("Gross", "folder", True), ("Gross", "loose_files", False),
                                 ("Gross/teil1", "folder", False), ("Gross/teil2", "folder", False),
                                 ("Klein", "folder", False)])
        self.assertEqual(Runner(app).run(once=True), 0)
        self.assertEqual({u.status for u in app.state.units()}, {Status.DONE})
        # Puffer: Unit-Ordner geloescht
        self.assertEqual(list((self.tmp / "buf" / "staging").iterdir()), [])
        # Ziel MD5-identisch
        self.assertEqual(md5_tree(self.tmp / "target" / "GDrive-Archiv"), self.before)
        # Fake-Quelle umbenannt, Inhalte unveraendert
        d = self.tmp / "drive"
        self.assertEqual(sorted(p.name for p in d.iterdir()),
                         sorted(["[MIGRIERT] Dokumente", "[MIGRIERT] Gross", "[MIGRIERT] Klein", "wurzel.txt"]))
        self.assertTrue((d / "[MIGRIERT] Gross" / "[MIGRIERT] teil1" / "a.bin").exists())
        self.assertTrue((d / "[MIGRIERT] Gross" / "lose.bin").exists())
        self.assertEqual(md5_tree(d).values().__len__(), len(self.before))
        # Erneutes plan: nichts mehr zu tun; Audit ok
        for u in app.state.units(Status.DONE):
            if not u.is_split_container:
                self.assertTrue(app.verifier.audit(u, app.target).ok, u.drive_path)

    def test_corrupt_buffer_file_detected_and_redownloaded(self):
        app = self.app()
        plan_app(app)
        r = Runner(app)
        r.step()  # erster Download
        u = app.state.units(Status.DOWNLOADED)[0]
        victim = next(p for p in (self.tmp / "buf" / "staging" / str(u.id)).rglob("*") if p.is_file() and p.stat().st_size > 20)
        data = bytearray(victim.read_bytes())
        data[10] ^= 0xFF
        victim.write_bytes(bytes(data))
        r.step()
        u2 = app.state.get_unit(u.id)
        self.assertEqual((u2.status, u2.attempts), (Status.PLANNED, 1))
        self.assertTrue(any(e["kind"] == "verify_failed" for e in app.state.events(unit_id=u.id)))
        Runner(app).run(once=True)
        self.assertEqual({x.status for x in app.state.units()}, {Status.DONE})
        self.assertEqual(md5_tree(self.tmp / "target" / "GDrive-Archiv"), self.before)

    def test_remote_missing_file_blocks_release(self):
        app = self.app(run={"max_attempts": 1, "poll_seconds": 1})
        plan_app(app)
        r = Runner(app)
        klein = by_path(app, "Klein")
        self.assertTrue(step_until(r, lambda: app.state.get_unit(klein.id).status == Status.UPLOADED))
        (self.tmp / "target" / "GDrive-Archiv" / "Klein" / "x.txt").unlink()
        local = self.tmp / "buf" / "staging" / str(klein.id)
        r.step()
        k = app.state.get_unit(klein.id)
        self.assertEqual(k.status, Status.FAILED)
        self.assertTrue(local.exists(), "Puffer darf nicht freigegeben werden")
        self.assertTrue((self.tmp / "drive" / "Klein").exists(), "Quelle darf nicht umbenannt werden")
        # retry springt NICHT ueber die Verifikation: zurueck auf PLANNED (Stand VERIFIED... war UPLOADED)
        self.assertEqual(k.resume_status, Status.UPLOADED)

    def test_remote_missing_retries_restage_then_succeeds(self):
        app = self.app()
        plan_app(app)
        r = Runner(app)
        klein = by_path(app, "Klein")
        step_until(r, lambda: app.state.get_unit(klein.id).status == Status.UPLOADED)
        (self.tmp / "target" / "GDrive-Archiv" / "Klein" / "x.txt").unlink()
        r.step()
        self.assertEqual(app.state.get_unit(klein.id).status, Status.VERIFIED_LOCAL)
        r.run(once=True)
        self.assertEqual(app.state.get_unit(klein.id).status, Status.DONE)

    def test_missing_marker_stops_writing(self):
        app = self.app()
        plan_app(app)
        (self.tmp / "buf" / ".skyferry-buffer").unlink()
        rc = Runner(app).run(once=True)
        self.assertEqual(rc, 2)
        self.assertFalse((self.tmp / "buf" / "staging").exists())
        self.assertEqual(sum(1 for u in app.state.units() if u.status != Status.PLANNED), 0)

    def test_mount_required(self):
        app = self.app(buffer={"require_mount": True})
        self.assertIn("nicht eingehängt", app.buffer.problem())

    def test_rename_conflict_does_not_merge(self):
        app = self.app()
        plan_app(app)
        (self.tmp / "drive" / "[MIGRIERT] Klein").mkdir()
        Runner(app).run(once=True)
        k = by_path(app, "Klein")
        self.assertEqual(app.state.get_unit(k.id).status, Status.NEEDS_ATTENTION)
        self.assertTrue((self.tmp / "drive" / "Klein" / "x.txt").exists())
        self.assertEqual(os.listdir(self.tmp / "drive" / "[MIGRIERT] Klein"), [])

    def test_duplicate_blocks_unit(self):
        app = self.app()
        plan_app(app)
        u = by_path(app, "Klein")
        app.state.transition(u.id, Status.PLANNED, Status.NEEDS_ATTENTION, error="doppelt")
        Runner(app).run(once=True)
        self.assertEqual(app.state.get_unit(u.id).status, Status.NEEDS_ATTENTION)
        self.assertTrue((self.tmp / "drive" / "Klein").exists())

    def test_single_instance_lock(self):
        with instance_lock(self.tmp / "st"):
            with self.assertRaises(AlreadyRunning):
                with instance_lock(self.tmp / "st"):
                    pass

    def test_max_units(self):
        app = self.app()
        plan_app(app)
        Runner(app).run(once=True, max_units=1)
        self.assertEqual(len(app.state.units(Status.DONE)), 1)

    def _write_toml(self, bwlimit=""):
        extra = '["--disable", "Copy"]' if bwlimit else "[]"  # Clone/Server-Side-Copy wuerde Limit umgehen
        c = self.tmp
        path = c / "config.toml"
        path.write_text(f"""
[source]
local_root = "{c / 'drive'}"
[buffer]
mount = "{c / 'buf'}"
require_mount = false
reserve_bytes = 0
max_unit_bytes = 1000000
[rclone]
binary = "{RCLONE}"
bwlimit = "{bwlimit}"
extra_args = {extra}
tpslimit = 0
[target]
path = "{c / 'target'}"
[notify]
daily_summary_hour = -1
[state]
dir = "{c / 'state'}"
[run]
poll_seconds = 1
""")
        os.chmod(path, 0o600)
        (c / "buf").mkdir(exist_ok=True)
        (c / "buf" / ".skyferry-buffer").write_text("x")
        (c / "target").mkdir(exist_ok=True)
        return path

    def _cli(self, cfg, *args, **kw):
        return subprocess.Popen([sys.executable, "-m", "skyferry", "--config", str(cfg), *args], cwd=ROOT,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                start_new_session=True, **kw)

    def test_kill_mid_run_resumes(self):
        slow = self._write_toml(bwlimit="150k")
        p = self._cli(slow, "plan")
        self.assertEqual(p.wait(30), 0, p.stdout.read())
        run = self._cli(slow, "run", "--once")
        import sqlite3
        db = self.tmp / "state" / "state.db"
        seen = False
        deadline = time.time() + 60
        while time.time() < deadline and run.poll() is None:
            try:
                con = sqlite3.connect(db, timeout=5)
                row = con.execute("SELECT status FROM units WHERE drive_id='Gross/teil1' AND kind='folder'").fetchone()
                con.close()
            except sqlite3.Error:
                row = None
            if row and row[0] == Status.DOWNLOADING:
                seen = True
                break
            time.sleep(0.1)
        self.assertTrue(seen, "Download-Zustand nicht beobachtet")
        os.killpg(run.pid, signal.SIGKILL)
        run.wait(10)
        con = sqlite3.connect(db)
        mid = dict(con.execute("SELECT drive_id||'|'||kind, status FROM units").fetchall())
        con.close()
        self.assertEqual(mid["Gross/teil1|folder"], Status.DOWNLOADING)
        # Neustart (ohne Bandbreitenlimit) setzt sauber fort
        fast = self._write_toml()
        out = self._cli(fast, "run", "--once")
        text = out.communicate(timeout=120)[0]
        self.assertEqual(out.returncode, 0, text)
        con = sqlite3.connect(db)
        statuses = {r[0] for r in con.execute("SELECT status FROM units")}
        con.close()
        self.assertEqual(statuses, {Status.DONE})
        self.assertEqual(md5_tree(self.tmp / "target" / "GDrive-Archiv"), self.before)

    def test_cli_status_and_units(self):
        cfg = self._write_toml()
        for args in (["plan"], ["run", "--once"], ["status"], ["status", "--json"], ["units"], ["events", "--tail", "3"]):
            p = self._cli(cfg, *args)
            out = p.communicate(timeout=120)[0]
            self.assertEqual(p.returncode, 0, (args, out))
        p = self._cli(cfg, "status", "--json")
        data = json.loads(p.communicate()[0])
        self.assertEqual(data["counts"], {"DONE": 7})


@needs_rclone
class PrivacyTests(TmpCase):
    """include_paths=false: kein Drive-Pfad in ntfy/healthchecks-Bodies oder Headern."""

    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Capture)
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def _scenario(self, include_paths):
        Capture.log = []
        make_fake_drive(self.tmp / "drive")
        base = f"http://127.0.0.1:{self.port}"
        app = self.app(notify={"ntfy_url": base + "/t", "healthchecks_url": base + "/hc",
                               "include_paths": include_paths}, run={"max_attempts": 1, "poll_seconds": 1})
        app.notifier.sleep = lambda s: None
        plan_app(app)
        r = Runner(app)
        klein = by_path(app, "Klein")
        step_until(r, lambda: app.state.get_unit(klein.id).status == Status.UPLOADED)
        (self.tmp / "target" / "GDrive-Archiv" / "Klein" / "x.txt").unlink()
        r.step()  # FAILED -> Alarm + hc_fail
        # Zusaetzlich NEEDS_ATTENTION (Umbenennen blockiert)
        (self.tmp / "drive" / "[MIGRIERT] Dokumente").mkdir(exist_ok=True)
        r.run(once=True)
        return " ".join(f"{x['path']} {x['body']} {json.dumps(x['headers'])}" for x in Capture.log)

    def test_no_paths_by_default(self):
        blob = self._scenario(False)
        self.assertTrue(Capture.log, "es muessen Meldungen gesendet worden sein")
        self.assertIn("/hc/fail", blob)
        for forbidden in ("Klein", "Dokumente", "Gross", "Projekte", "x.txt", "Bericht"):
            self.assertNotIn(forbidden, blob)
        self.assertRegex(blob, r"Unit #\d+ \(folder, ")

    def test_paths_when_enabled(self):
        blob = self._scenario(True)
        self.assertIn("Klein", blob)


if __name__ == "__main__":
    unittest.main()
