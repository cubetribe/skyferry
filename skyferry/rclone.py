"""rclone-Wrapper mit hartem Sicherheitsnetz fuer den Source-Remote.

Verifiziertes rclone-Verhalten (v1.74/1.75, local-Backend und rclone-Doku):
  * `lsjson -R` schreibt EIN JSON-Objekt pro Zeile; Zeilen 1 und n sind `[` bzw. `]`,
    alle Objektzeilen ausser der letzten enden auf `,`. Logmeldungen gehen nach stderr.
  * Fehlendes Verzeichnis: Exit-Code 3 (stdout enthaelt trotzdem `[`).
  * `--hash --hash-type md5` liefert `Hashes: {"md5": ...}` nur fuer Dateien, nicht fuer Ordner.
  * `--use-json-log` + `--stats 1m --stats-log-level NOTICE`: Stats als JSON-Zeile auf stderr
    (Schluessel `stats` mit bytes/totalBytes/transfers/totalTransfers).
  * `moveto` auf ein Verzeichnis benennt es um (DirMove). Existiert das Ziel schon, wuerde
    rclone zusammenfuehren -> wir pruefen das VORHER (siehe source.py).
Google-Docs (Annahmen laut rclone-Doku, am echten Drive in Sprint 01/05 zu bestaetigen):
  * Mit --drive-export-formats erscheinen Docs mit Export-Endung im Listing (Bericht.docx),
    Size = -1 (unbekannt), kein Hash. Nach dem Download hat die Datei eine echte Groesse.
"""

from __future__ import annotations

import json
import logging
import os
import posixpath
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from typing import Callable, Iterator, Sequence

from .config import Config

log = logging.getLogger(__name__)


class SafetyError(RuntimeError):
    """Versuch, auf dem Source-Remote etwas anderes als lesen/umbenennen zu tun."""


EXIT_KINDS = {
    0: "ok", 1: "error", 2: "error", 3: "not_found", 4: "not_found", 5: "temporary",
    6: "less_serious", 7: "fatal", 8: "transfer_limit", 9: "nothing_transferred",
}

FORBIDDEN = {"delete", "deletefile", "purge", "sync", "move", "rmdir", "rmdirs", "dedupe", "cleanup",
             "bisync", "mount", "serve", "copyto", "touch", "mkdir", "setmodtime", "rcat", "settier"}
SOURCE_READONLY = {"lsjson", "lsf", "size"}
NON_SOURCE_ALLOWED = {"lsjson", "lsf", "size", "copy"}


def classify_exit(code: int) -> str:
    if code < 0:
        return "interrupted"
    return EXIT_KINDS.get(code, "error")


class RcloneError(RuntimeError):
    def __init__(self, cmd: str, code: int, stderr: str):
        self.cmd = cmd
        self.code = code
        self.kind = classify_exit(code)
        self.stderr = stderr
        super().__init__(f"rclone {cmd} fehlgeschlagen (Exit {code}, {self.kind}): {stderr[-300:]}")

    @property
    def retry_later(self) -> bool:
        return self.kind in ("temporary", "transfer_limit")


ProgressCb = Callable[[int, int, int, int], None]  # bytes, total_bytes, files, total_files


class Rclone:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._proc: subprocess.Popen | None = None
        self._plock = threading.Lock()

    # ---- Sicherheitsnetz ----
    @property
    def local_root(self) -> str:
        return os.path.normpath(os.path.abspath(self.cfg.source.local_root)) if self.cfg.source.local_root else ""

    def is_source_spec(self, spec: str) -> bool:
        if self.local_root:
            p = os.path.normpath(os.path.abspath(spec))
            return p == self.local_root or p.startswith(self.local_root + os.sep)
        r = self.cfg.source.remote
        return spec.startswith(r + ":") or spec.startswith(r + ",")

    def _split(self, spec: str) -> tuple[str, str]:
        if self.local_root:
            return "", os.path.normpath(os.path.abspath(spec))
        head, _, path = spec.partition(":")
        return head, path

    def check(self, cmd: str, positional: Sequence[str]) -> None:
        """Wirft SafetyError, wenn der Aufruf den Source-Remote veraendern koennte."""
        if cmd in FORBIDDEN:
            raise SafetyError(f"rclone {cmd} ist gesperrt")
        involved = [self.is_source_spec(p) for p in positional]
        if cmd == "moveto":
            self._check_rename(positional)
            return
        if cmd not in NON_SOURCE_ALLOWED:
            raise SafetyError(f"rclone {cmd} ist nicht freigegeben")
        if any(involved):
            if cmd in SOURCE_READONLY:
                return
            if cmd == "copy":
                if len(positional) != 2 or not involved[0] or involved[1]:
                    raise SafetyError("copy auf/innerhalb des Source-Remotes ist gesperrt")
                return
            raise SafetyError(f"rclone {cmd} auf dem Source-Remote ist gesperrt")

    def _check_rename(self, positional: Sequence[str]) -> None:
        if len(positional) != 2:
            raise SafetyError("moveto braucht genau Quelle und Ziel")
        src, dst = positional
        if not (self.is_source_spec(src) and self.is_source_spec(dst)):
            raise SafetyError("moveto ist nur als Umbenennung auf dem Source-Remote erlaubt")
        h1, p1 = self._split(src)
        h2, p2 = self._split(dst)
        base1, base2 = posixpath.basename(p1), posixpath.basename(p2)
        if h1 != h2 or posixpath.dirname(p1) != posixpath.dirname(p2):
            raise SafetyError("moveto: Umbenennung nur im selben Elternordner erlaubt")
        if not base1 or base2 != self.cfg.source.done_prefix + base1:
            raise SafetyError("moveto: neuer Name muss done_prefix + alter Name sein")

    # ---- Aufbau ----
    def _global_flags(self) -> list[str]:
        r = self.cfg.rclone
        f: list[str] = []
        if r.config:
            f += ["--config", os.path.expanduser(r.config)]
        if r.local_encoding:
            f += ["--local-encoding", r.local_encoding]
        f += list(r.extra_args)
        return f

    def _transfer_flags(self) -> list[str]:
        r = self.cfg.rclone
        f = ["--transfers", str(r.transfers), "--checkers", str(r.checkers), "--tpslimit", str(r.tpslimit),
             "--retries", str(r.retries), "--low-level-retries", str(r.low_level_retries)]
        if r.bwlimit:
            f += ["--bwlimit", r.bwlimit]
        return f

    def build(self, cmd: str, positional: Sequence[str], flags: Sequence[str] = (), *, transfer: bool = False,
              json_log: bool = False) -> list[str]:
        self.check(cmd, positional)
        argv = [self.cfg.rclone.binary, cmd, *self._global_flags()]
        if transfer:
            argv += self._transfer_flags()
        if json_log:
            argv += ["--use-json-log", "--log-level", "NOTICE", "--stats", self.cfg.rclone.stats_interval,
                     "--stats-log-level", "NOTICE"]
        else:
            argv += ["--log-level", "ERROR"]
        argv += list(flags)
        if any(self.is_source_spec(p) for p in positional):
            bad = [a for a in flags if a.startswith("--delete") or a.startswith("--backup-dir")]
            if bad:
                raise SafetyError(f"Flag {bad[0]} auf Source-Remote gesperrt")
        argv += list(positional)
        return argv

    # ---- Prozesse ----
    def terminate_current(self) -> None:
        with self._plock:
            if self._proc and self._proc.poll() is None:
                self._proc.terminate()

    def _spawn(self, argv: list[str], **kw) -> subprocess.Popen:
        proc = subprocess.Popen(argv, **kw)
        with self._plock:
            self._proc = proc
        return proc

    def _clear(self) -> None:
        with self._plock:
            self._proc = None

    def run(self, cmd: str, positional: Sequence[str], flags: Sequence[str] = ()) -> str:
        """Kleine Kommandos (rename, size); gibt stdout zurueck."""
        argv = self.build(cmd, positional, flags)
        proc = self._spawn(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace")
        try:
            out, err = proc.communicate()
        finally:
            self._clear()
        if proc.returncode != 0:
            raise RcloneError(cmd, proc.returncode, err)
        return out

    def stream_lsjson(self, spec: str, flags: Sequence[str] = ()) -> Iterator[dict]:
        """Streamt lsjson-Objekte zeilenweise (nie die ganze Ausgabe im Speicher)."""
        argv = self.build("lsjson", [spec], flags)
        with tempfile.TemporaryFile("w+", errors="replace") as errf:
            proc = self._spawn(argv, stdout=subprocess.PIPE, stderr=errf, text=True, encoding="utf-8",
                               errors="replace")
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    line = line.strip()
                    if line in ("", "[", "]"):
                        continue
                    if line.endswith(","):
                        line = line[:-1]
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        raise RcloneError("lsjson", 7, f"Unlesbare lsjson-Zeile: {line[:120]}") from None
                code = proc.wait()
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                if proc.stdout:
                    proc.stdout.close()
                self._clear()
            if code != 0:
                errf.seek(0)
                raise RcloneError("lsjson", code, errf.read()[-2000:])

    def copy(self, src: str, dst: str, flags: Sequence[str] = (), on_progress: ProgressCb | None = None) -> None:
        argv = self.build("copy", [src, dst], flags, transfer=True, json_log=True)
        proc = self._spawn(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                           encoding="utf-8", errors="replace")
        errors: list[str] = []
        try:
            assert proc.stderr is not None
            for line in proc.stderr:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    errors.append(line.strip()[:300])
                    continue
                stats = obj.get("stats")
                if isinstance(stats, dict) and on_progress:
                    on_progress(int(stats.get("bytes", 0)), int(stats.get("totalBytes", 0)),
                                int(stats.get("transfers", 0)), int(stats.get("totalTransfers", 0)))
                elif obj.get("level") in ("error", "critical"):
                    errors.append(str(obj.get("msg", ""))[:300])
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            if proc.stderr:
                proc.stderr.close()
            self._clear()
        if code != 0:
            raise RcloneError("copy", code, "\n".join(errors[-10:]))
