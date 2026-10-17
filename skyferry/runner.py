"""Zustandsmaschine / Hauptschleife.

PLANNED -> DOWNLOADING -> DOWNLOADED -> VERIFIED_LOCAL -> STAGED -> UPLOADED -> VERIFIED_REMOTE
        -> RELEASED -> RENAMED -> DONE   (+ FAILED / NEEDS_ATTENTION / SKIPPED)

Pro Durchlauf (`step`) bekommt jede Unit hoechstens EINEN Schritt; zuerst die Units, die
Platz freigeben (Reihenfolge RELEASE, RENAME, VERIFY_REMOTE, UPLOAD, STAGE, VERIFY_LOCAL),
danach hoechstens ein Download.
"""

from __future__ import annotations

import fcntl
import json
import logging
import signal
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .app import App
from .buffer import BufferUnavailable
from .notify import safe_error, unit_label
from .rclone import RcloneError, SafetyError
from .source import RenameConflict
from .state import Status, Unit
from .targets.base import UploadState
from .util import fmt_bytes, fmt_duration, now
from .verify import Entry, Listing

log = logging.getLogger(__name__)


class AlreadyRunning(RuntimeError):
    pass


@contextmanager
def instance_lock(state_dir: Path) -> Iterator[None]:
    state_dir.mkdir(parents=True, exist_ok=True)
    fh = open(state_dir / "run.lock", "w")
    try:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise AlreadyRunning("Es läuft bereits eine skyferry-Instanz") from None
        yield
    finally:
        fh.close()


class Runner:
    def __init__(self, app: App, *, stop_event: threading.Event | None = None):
        self.app = app
        self.cfg = app.cfg
        self.st = app.state
        self.stop = stop_event or threading.Event()
        self.started_downloads = 0
        self.max_units: int | None = None
        self.paths = app.notifier.include_paths

    # ---- Hilfen ----
    def _label(self, u: Unit) -> str:
        return unit_label(u, self.paths)

    def _touch_progress(self) -> None:
        self.st.kv_set("last_progress_ts", str(now()))

    def _manifest_path(self, unit_id: int) -> Path:
        return self.cfg.state_dir / "manifests" / f"{unit_id}.json"

    def _save_manifest(self, unit_id: int, listing: Listing) -> None:
        p = self._manifest_path(unit_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({k: [e.size, e.md5, e.is_gdoc] for k, e in listing.items()}), "utf-8")

    def _load_manifest(self, unit_id: int) -> Listing | None:
        p = self._manifest_path(unit_id)
        if not p.exists():
            return None
        return {k: Entry(v[0], v[1], v[2]) for k, v in json.loads(p.read_text("utf-8")).items()}

    def _deferred(self, unit: Unit) -> bool:
        v = self.st.kv_get(f"defer:{unit.id}")
        return bool(v) and float(v) > now()

    def _defer(self, unit: Unit, minutes: int, why: str) -> None:
        self.st.kv_set(f"defer:{unit.id}", str(now() + minutes * 60))
        self.st.event("deferred", f"Zurückgestellt für {minutes} min: {why}", unit_id=unit.id, level="warning")

    def _fail_attempt(self, unit: Unit, back_to: str, error: str, *, from_state: str | None = None,
                      public_error: str | None = None) -> None:
        """attempts+1 und zurueck auf `back_to`; nach max_attempts FAILED + Alarm."""
        cur = self.st.get_unit(unit.id)
        frm = from_state or (cur.status if cur else unit.status)
        attempts = (cur.attempts if cur else unit.attempts) + 1
        if attempts >= self.cfg.run.max_attempts:
            if self.st.transition(unit.id, frm, Status.FAILED, error=error, bump_attempts=True, level="error",
                                  message=error[:200]):
                msg = f"{self._label(unit)}: {public_error or 'Fehler'} (nach {attempts} Versuchen aufgegeben)"
                self.app.notifier.alarm("Migration: Unit fehlgeschlagen", msg, unit_id=unit.id)
                self.app.notifier.hc_fail(msg)
        else:
            self.st.transition(unit.id, frm, back_to, error=error, bump_attempts=True, level="warning",
                               message=error[:200])

    def _needs_attention(self, unit: Unit, error: str, public: str) -> None:
        cur = self.st.get_unit(unit.id)
        if cur and self.st.transition(unit.id, cur.status, Status.NEEDS_ATTENTION, error=error, level="error",
                                      message=error[:200]):
            self.app.notifier.alarm("Migration: Unit braucht Aufmerksamkeit", f"{self._label(unit)}: {public}",
                                    unit_id=unit.id)

    def _guarded(self, unit: Unit, back_to: str, fn) -> bool:
        """Fuehrt einen Schritt aus und uebersetzt Fehler in Zustandsuebergaenge."""
        try:
            return bool(fn(unit))
        except (BufferUnavailable, SafetyError):
            raise
        except RcloneError as exc:
            if self.stop.is_set():
                return False
            if exc.kind == "transfer_limit":
                self._defer(unit, self.cfg.run.defer_limit_minutes, "Transferlimit")
                self.app.notifier.info("Migration: Transferlimit", f"{self._label(unit)}: Limit erreicht, später erneut",
                                       unit_id=unit.id)
                return False
            if exc.kind == "temporary":
                self._defer(unit, self.cfg.run.defer_temporary_minutes, "temporärer Fehler")
                return False
            if exc.kind == "not_found":
                self._needs_attention(unit, str(exc), "Quelle nicht gefunden (rclone Exit 3/4)")
                return True
            self._fail_attempt(unit, back_to, str(exc), public_error=f"rclone-Fehler ({exc.kind}, Exit {exc.code})")
            return True
        except RenameConflict as exc:
            self._needs_attention(unit, str(exc), "Umbenennen nicht sicher möglich")
            return True
        except Exception as exc:  # unerwartet: protokollieren, Versuch zaehlen
            log.error("Unerwarteter Fehler bei Unit %s: %s", unit.id, traceback.format_exc())
            self._fail_attempt(unit, back_to, f"{type(exc).__name__}: {exc}",
                               public_error=f"interner Fehler ({type(exc).__name__})")
            return True

    # ---- Schritte ----
    def _download(self, unit: Unit) -> bool:
        a = self.app
        local = a.buffer.unit_dir(unit.id, create=True)
        if not self.st.transition(unit.id, [Status.PLANNED, Status.DOWNLOADING], Status.DOWNLOADING,
                                  message="Download startet"):
            return False
        flags = [*a.source.flags(), "--checksum", "--create-empty-src-dirs"]
        if unit.kind == "loose_files":
            flags += ["--max-depth", "1"]

        def progress(b: int, tb: int, f: int, tf: int) -> None:
            self.st.kv_set(f"progress:{unit.id}", json.dumps({"bytes": b, "total": tb, "files": f,
                                                               "total_files": tf, "ts": now()}))
            self._touch_progress()

        a.rc.copy(a.source.unit_spec(unit), str(local), flags, progress)
        if self.stop.is_set():
            return False
        self.st.transition(unit.id, Status.DOWNLOADING, Status.DOWNLOADED, message="Download fertig")
        return True

    def _verify_local(self, unit: Unit) -> bool:
        a = self.app
        local = a.buffer.unit_dir(unit.id)
        res, actual = a.verifier.verify_local(unit, local)
        if res.ok:
            self._save_manifest(unit.id, actual)
            self.st.transition(unit.id, Status.DOWNLOADED, Status.VERIFIED_LOCAL, message=res.summary(),
                               data=res.counts)
            return True
        # defekte Dateien loeschen, damit der naechste copy sie neu laedt
        for p in res.problems:
            if p["kind"] in ("md5_mismatch", "size_mismatch", "gdoc_empty"):
                a.buffer.remove_file(local / p["path"])
        self.st.event("verify_failed", f"Verifikation lokal fehlgeschlagen: {res.summary()}", unit_id=unit.id,
                      level="warning", data={"counts": res.counts, "problems": res.problems[:20]})
        self._fail_attempt(unit, Status.PLANNED, "Verifikation lokal fehlgeschlagen: " + res.summary(),
                           from_state=Status.DOWNLOADED,
                           public_error=f"Verifikation lokal fehlgeschlagen, "
                                        f"{res.counts['missing'] + res.counts['mismatch'] + res.counts['duplicates']} Abweichungen")
        return True

    def _stage(self, unit: Unit) -> bool:
        a = self.app
        a.target.stage(unit, a.buffer.unit_dir(unit.id))
        self.st.transition(unit.id, Status.VERIFIED_LOCAL, Status.STAGED, message=f"an Ziel '{a.target.name}' übergeben")
        return True

    def _check_upload(self, unit: Unit) -> bool:
        s: UploadState = self.app.target.upload_state(unit)
        if s.kind == "COMPLETE":
            self.st.transition(unit.id, Status.STAGED, Status.UPLOADED, message="Upload abgeschlossen")
            return True
        if s.kind == "ERROR":
            self._fail_attempt(unit, Status.VERIFIED_LOCAL, f"Upload-Fehler: {s.message}", from_state=Status.STAGED,
                               public_error="Upload-Fehler")
            return True
        if s.progress is not None:
            self.st.kv_set(f"progress:{unit.id}", json.dumps({"upload": s.progress, "ts": now()}))
        return False

    def _verify_remote(self, unit: Unit) -> bool:
        a = self.app
        res = a.verifier.verify_remote(unit, a.buffer.unit_dir(unit.id), a.target, self._load_manifest(unit.id))
        if res.ok:
            self.st.transition(unit.id, Status.UPLOADED, Status.VERIFIED_REMOTE, message=res.summary(),
                               data=res.counts)
            return True
        self.st.event("verify_failed", f"Verifikation Ziel fehlgeschlagen: {res.summary()}", unit_id=unit.id,
                      level="warning", data={"counts": res.counts, "problems": res.problems[:20]})
        bad = res.counts["missing"] + res.counts["mismatch"] + res.counts.get("puffer_changed", 0)
        # NICHT freigeben: zurueck zum Staging (idempotent), nach max_attempts FAILED
        self._fail_attempt(unit, Status.VERIFIED_LOCAL, "Verifikation Ziel fehlgeschlagen: " + res.summary(),
                           from_state=Status.UPLOADED,
                           public_error=f"Verifikation Ziel fehlgeschlagen, {bad} Abweichungen")
        return True

    def _release(self, unit: Unit) -> bool:
        a = self.app
        rs = a.target.release(unit, a.buffer.unit_dir(unit.id))
        if rs.done:
            self.st.kv_del(f"waiting:{unit.id}")
            self.st.transition(unit.id, Status.VERIFIED_REMOTE, Status.RELEASED, message="Puffer freigegeben")
            return True
        if not self.st.kv_get(f"waiting:{unit.id}"):
            self.st.kv_set(f"waiting:{unit.id}", "1")
            a.notifier.info("Migration: bitte freigeben", f"{self._label(unit)}: wartet auf Freigabe",
                            unit_id=unit.id, rate_limit=False)
        self.st.event("waiting", f"Freigabe wartet: {rs.message}", unit_id=unit.id)
        return False

    def _finish(self, unit: Unit) -> None:
        self.st.transition(unit.id, Status.RENAMED, Status.DONE, message="fertig")
        self._manifest_path(unit.id).unlink(missing_ok=True)
        self.st.kv_del(f"progress:{unit.id}")
        self.app.notifier.unit_done(unit)
        self._touch_progress()

    def _rename(self, unit: Unit) -> bool:
        if unit.kind == "loose_files":
            self.st.transition(unit.id, Status.RELEASED, Status.RENAMED, message="keine Umbenennung nötig")
        else:
            outcome = self.app.source.rename_unit(unit)
            self.st.transition(unit.id, Status.RELEASED, Status.RENAMED, message=f"Quelle umbenannt ({outcome})")
        self._finish(unit)
        return True

    def _finish_renamed(self, unit: Unit) -> bool:
        self._finish(unit)
        return True

    def _rename_container(self, unit: Unit) -> bool:
        outcome = self.app.source.rename_unit(unit)
        self.st.transition(unit.id, Status.PLANNED, Status.RENAMED, message=f"Container umbenannt ({outcome})")
        self._finish(unit)
        return True

    # ---- Durchlauf ----
    def _containers_ready(self) -> list[Unit]:
        ready = []
        for u in self.st.units(Status.PLANNED):
            if u.is_split_container:
                kids = self.st.children(u.id)
                if kids and all(k.status == Status.DONE for k in kids):
                    ready.append(u)
        return ready

    def _pick_download(self, skip: set[int]) -> Unit | None:
        for u in self.st.units(Status.PLANNED):
            if not u.is_split_container and not self._deferred(u) and u.id not in skip:
                return u
        return None

    def step(self) -> bool:
        """Ein Durchlauf; True wenn irgendein Fortschritt gemacht wurde. Wirft BufferUnavailable."""
        a = self.app
        a.buffer.require()
        progressed = False
        order = [
            (Status.VERIFIED_REMOTE, Status.VERIFIED_REMOTE, self._release),
            (Status.RELEASED, Status.RELEASED, self._rename),
            (Status.RENAMED, Status.RENAMED, self._finish_renamed),
            (Status.UPLOADED, Status.VERIFIED_LOCAL, self._verify_remote),
            (Status.STAGED, Status.VERIFIED_LOCAL, self._check_upload),
            (Status.VERIFIED_LOCAL, Status.VERIFIED_LOCAL, self._stage),
            (Status.DOWNLOADED, Status.PLANNED, self._verify_local),
        ]
        snapshot = [(self.st.units(status), back_to, fn) for status, back_to, fn in order]
        touched: set[int] = set()
        for units, back_to, fn in snapshot:
            for u in units:
                if self.stop.is_set():
                    return progressed
                if self._guarded(u, back_to, fn):
                    progressed = True
                    touched.add(u.id)
                    self._touch_progress()
        for u in self._containers_ready():
            if self.stop.is_set():
                return progressed
            if u.id in touched:
                continue
            if self._guarded(u, Status.PLANNED, self._rename_container):
                progressed = True
        # unterbrochener Download hat Vorrang vor neuem
        resume = [u for u in self.st.units(Status.DOWNLOADING) if not self._deferred(u)]
        nxt = resume[0] if resume else None
        if nxt is None and not self.stop.is_set():
            if self.max_units is None or self.started_downloads < self.max_units:
                nxt = self._pick_download(touched)
                if nxt is not None:
                    busy = bool(self.st.units(list(Status.BUFFERED)))
                    if not a.buffer.has_room_for(nxt.expected_bytes):
                        if not busy and nxt.expected_bytes * 1.05 > a.buffer.total_capacity() - self.cfg.buffer.reserve_bytes:
                            self._needs_attention(nxt, "Unit passt nie in den Puffer",
                                                  "Unit ist größer als der Puffer (max_unit_bytes senken)")
                            progressed = True
                        nxt = None
                    elif nxt.status == Status.PLANNED:
                        self.started_downloads += 1
        if nxt is not None and not self.stop.is_set():
            if self._guarded(nxt, Status.PLANNED, self._download):
                progressed = True
                self._touch_progress()
        return progressed

    def pending_work(self) -> bool:
        for u in self.st.units():
            if u.status in Status.TERMINAL:
                continue
            if u.is_split_container:
                continue
            return True
        return bool(self._containers_ready())

    # ---- Meldungen ----
    def _summary_text(self) -> str:
        counts = self.st.count_by_status()
        done_b = self.st.sum_bytes([Status.DONE])
        total_b = self.st.sum_bytes()
        parts = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items()))
        return f"{fmt_bytes(done_b)} von {fmt_bytes(total_b)} fertig. Units: {parts or 'keine'}"

    def _periodic(self) -> None:
        n = self.cfg.notify
        a = self.app
        a.notifier.flush_done()
        t = now()
        last = float(self.st.kv_get("last_progress_ts", str(t)) or t)
        if self.pending_work() and t - last > n.stall_hours * 3600:
            a.notifier.alarm("Migration: kein Fortschritt",
                             f"Seit {fmt_duration(t - last)} kein Fortschritt. {self._summary_text()}")
        if n.daily_summary_hour >= 0:
            lt = time.localtime()
            today = time.strftime("%Y-%m-%d", lt)
            if lt.tm_hour >= n.daily_summary_hour and self.st.kv_get("summary:last_date") != today:
                self.st.kv_set("summary:last_date", today)
                a.notifier.summary(self._summary_text())

    def run(self, *, once: bool = False, max_units: int | None = None) -> int:
        self.max_units = max_units
        a = self.app
        a.notifier.hc_start()
        a.notifier.start_heartbeat()
        self.st.kv_set("last_progress_ts", str(now()))
        old = self._install_signals()
        try:
            while not self.stop.is_set():
                try:
                    progressed = self.step()
                except BufferUnavailable as exc:
                    a.notifier.alarm("Migration: Puffer nicht verfügbar", str(exc) if self.paths else
                                     "Puffer nicht eingehängt oder Marker fehlt")
                    self.st.event("buffer_guard", str(exc), level="error")
                    if once:
                        return 2
                    self.stop.wait(self.cfg.run.poll_seconds)
                    continue
                self._periodic()
                if not self.pending_work():
                    break
                if not progressed:
                    if once:
                        break
                    self.stop.wait(self.cfg.run.poll_seconds)
            if self.stop.is_set():
                log.info("Gestoppt auf Anforderung")
                return 0
            if not self.pending_work():
                a.notifier.flush_done(force=True)
                a.notifier.hc_ping()
                if self.st.units():
                    a.notifier.info("Migration: Lauf beendet", self._summary_text(), priority=3,
                                    tags="tada", rate_limit=False)
            return 0
        except SafetyError as exc:
            self._crash(exc, "Sicherheitsnetz ausgelöst")
            return 1
        except Exception as exc:
            log.error("Absturz: %s", traceback.format_exc())
            self._crash(exc, "Absturz")
            return 1
        finally:
            a.notifier.stop_heartbeat()
            self._restore_signals(old)

    def _crash(self, exc: Exception, what: str) -> None:
        detail = f"{what}: {type(exc).__name__}" + (f": {safe_error(str(exc), True)}" if self.paths else "")
        self.st.event("crash", f"{what}: {exc}", level="error")
        self.app.notifier.alarm("Migration: Runner abgestürzt", detail)
        self.app.notifier.hc_fail(detail)

    # ---- Signale ----
    def _install_signals(self):
        if threading.current_thread() is not threading.main_thread():
            return None

        def handler(signum, _frame):
            self.stop.set()
            self.app.rc.terminate_current()

        return {s: signal.signal(s, handler) for s in (signal.SIGTERM, signal.SIGINT)}

    def _restore_signals(self, old) -> None:
        if old:
            for s, h in old.items():
                signal.signal(s, h)
