"""SQLite-Zustand: Units, Events (Audit-Trail, nur anhängen), kv."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

from .util import now


class Status:
    PLANNED = "PLANNED"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    VERIFIED_LOCAL = "VERIFIED_LOCAL"
    STAGED = "STAGED"
    UPLOADED = "UPLOADED"
    VERIFIED_REMOTE = "VERIFIED_REMOTE"
    RELEASED = "RELEASED"
    RENAMED = "RENAMED"
    DONE = "DONE"
    FAILED = "FAILED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    SKIPPED = "SKIPPED"

    ALL = (PLANNED, DOWNLOADING, DOWNLOADED, VERIFIED_LOCAL, STAGED, UPLOADED, VERIFIED_REMOTE,
           RELEASED, RENAMED, DONE, FAILED, NEEDS_ATTENTION, SKIPPED)
    TERMINAL = (DONE, FAILED, NEEDS_ATTENTION, SKIPPED)
    # Zustände, in denen Daten im Puffer liegen können
    BUFFERED = (DOWNLOADING, DOWNLOADED, VERIFIED_LOCAL, STAGED, UPLOADED, VERIFIED_REMOTE)
    # Nach FAILED/NEEDS_ATTENTION darf ab hier fortgesetzt statt neu geladen werden
    RESUMABLE = (VERIFIED_REMOTE, RELEASED, RENAMED)


SCHEMA_V1 = """
CREATE TABLE units(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  drive_id TEXT NOT NULL,
  parent_drive_id TEXT NOT NULL DEFAULT '',
  drive_path TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('folder','loose_files')),
  parent_unit_id INTEGER REFERENCES units(id),
  is_split_container INTEGER NOT NULL DEFAULT 0,
  expected_files INTEGER NOT NULL DEFAULT 0,
  expected_bytes INTEGER NOT NULL DEFAULT 0,
  gdocs_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  last_error TEXT,
  resume_status TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  UNIQUE(drive_id, kind)
);
CREATE INDEX ix_units_status ON units(status);
CREATE INDEX ix_units_parent ON units(parent_unit_id);
CREATE TABLE events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  unit_id INTEGER REFERENCES units(id),
  ts REAL NOT NULL,
  level TEXT NOT NULL,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  data_json TEXT
);
CREATE INDEX ix_events_unit ON events(unit_id);
CREATE TABLE kv(
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at REAL NOT NULL
);
"""

MIGRATIONS: list[str] = [SCHEMA_V1]


@dataclass
class Unit:
    id: int
    drive_id: str
    parent_drive_id: str
    drive_path: str
    name: str
    kind: str
    parent_unit_id: int | None
    is_split_container: bool
    expected_files: int
    expected_bytes: int
    gdocs_count: int
    status: str
    attempts: int
    last_error: str | None
    resume_status: str | None
    created_at: float
    updated_at: float
    started_at: float | None
    finished_at: float | None

    @property
    def is_root(self) -> bool:
        return self.kind == "loose_files" and self.drive_path == ""


_UNIT_COLS = [f.name for f in fields(Unit)]


def _unit(row: sqlite3.Row | None) -> Unit | None:
    if row is None:
        return None
    d = dict(row)
    d["is_split_container"] = bool(d["is_split_container"])
    return Unit(**{k: d[k] for k in _UNIT_COLS})


class State:
    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    # ---- Schema ----
    def _migrate(self) -> None:
        with self._lock:
            self.conn.execute("CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL)")
            row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
            current = row["v"] or 0
            for i, sql in enumerate(MIGRATIONS, start=1):
                if i <= current:
                    continue
                self.conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in filter(None, (s.strip() for s in sql.split(";\n"))):
                        self.conn.execute(stmt)
                    self.conn.execute("INSERT INTO schema_version(version) VALUES (?)", (i,))
                    self.conn.execute("COMMIT")
                except Exception:
                    self.conn.execute("ROLLBACK")
                    raise

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    # ---- Events ----
    def event(self, kind: str, message: str, *, unit_id: int | None = None, level: str = "info",
              data: Any = None) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO events(unit_id, ts, level, kind, message, data_json) VALUES (?,?,?,?,?,?)",
                (unit_id, now(), level, kind, message, json.dumps(data, ensure_ascii=False) if data is not None else None),
            )

    def events(self, *, unit_id: int | None = None, limit: int = 50) -> list[sqlite3.Row]:
        with self._lock:
            if unit_id is None:
                rows = self.conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = self.conn.execute("SELECT * FROM events WHERE unit_id=? ORDER BY id DESC LIMIT ?",
                                         (unit_id, limit)).fetchall()
        return list(reversed(rows))

    # ---- kv ----
    def kv_get(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO kv(key,value,updated_at) VALUES (?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now()),
            )

    def kv_del(self, key: str) -> None:
        with self._lock:
            self.conn.execute("DELETE FROM kv WHERE key=?", (key,))

    def kv_prefix(self, prefix: str) -> dict[str, str]:
        with self._lock:
            rows = self.conn.execute("SELECT key,value FROM kv WHERE key LIKE ?", (prefix + "%",)).fetchall()
        return {r["key"]: r["value"] for r in rows}

    # ---- Units ----
    def add_unit(self, *, drive_id: str, parent_drive_id: str, drive_path: str, name: str, kind: str,
                 parent_unit_id: int | None, is_split_container: bool, expected_files: int,
                 expected_bytes: int, gdocs_count: int, status: str) -> int:
        t = now()
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO units(drive_id,parent_drive_id,drive_path,name,kind,parent_unit_id,is_split_container,"
                "expected_files,expected_bytes,gdocs_count,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (drive_id, parent_drive_id, drive_path, name, kind, parent_unit_id, int(is_split_container),
                 expected_files, expected_bytes, gdocs_count, status, t, t),
            )
            return int(cur.lastrowid)

    def update_expected(self, unit_id: int, files: int, nbytes: int, gdocs: int) -> None:
        with self._lock:
            self.conn.execute(
                "UPDATE units SET expected_files=?, expected_bytes=?, gdocs_count=?, updated_at=? WHERE id=?",
                (files, nbytes, gdocs, now(), unit_id))

    def get_unit(self, unit_id: int) -> Unit | None:
        with self._lock:
            return _unit(self.conn.execute("SELECT * FROM units WHERE id=?", (unit_id,)).fetchone())

    def find_unit(self, drive_id: str, kind: str) -> Unit | None:
        with self._lock:
            return _unit(self.conn.execute("SELECT * FROM units WHERE drive_id=? AND kind=?",
                                           (drive_id, kind)).fetchone())

    def units(self, statuses: Sequence[str] | str | None = None) -> list[Unit]:
        with self._lock:
            if statuses is None:
                rows = self.conn.execute("SELECT * FROM units ORDER BY id").fetchall()
            else:
                if isinstance(statuses, str):
                    statuses = [statuses]
                q = ",".join("?" * len(statuses))
                rows = self.conn.execute(f"SELECT * FROM units WHERE status IN ({q}) ORDER BY id",
                                         tuple(statuses)).fetchall()
        return [_unit(r) for r in rows]  # type: ignore[misc]

    def children(self, unit_id: int) -> list[Unit]:
        with self._lock:
            rows = self.conn.execute("SELECT * FROM units WHERE parent_unit_id=? ORDER BY id", (unit_id,)).fetchall()
        return [_unit(r) for r in rows]  # type: ignore[misc]

    def count_by_status(self) -> dict[str, int]:
        with self._lock:
            rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM units GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def sum_bytes(self, statuses: Iterable[str] | None = None, *, leaf_only: bool = True,
                  finished_since: float | None = None) -> int:
        sql = "SELECT COALESCE(SUM(expected_bytes),0) AS s FROM units WHERE 1=1"
        params: list[Any] = []
        if leaf_only:
            sql += " AND is_split_container=0"
        if statuses is not None:
            st = list(statuses)
            sql += f" AND status IN ({','.join('?' * len(st))})"
            params += st
        if finished_since is not None:
            sql += " AND finished_at >= ?"
            params.append(finished_since)
        with self._lock:
            return int(self.conn.execute(sql, params).fetchone()["s"])

    def transition(self, unit_id: int, from_states: Sequence[str] | str, to_state: str, *, message: str = "",
                   error: str | None = None, bump_attempts: bool = False, reset_attempts: bool = False,
                   level: str = "info", data: Any = None, kind: str = "transition") -> bool:
        """Einziger Weg, einen Status zu ändern. Atomar; False wenn Ausgangszustand nicht passt."""
        if isinstance(from_states, str):
            from_states = [from_states]
        if to_state not in Status.ALL:
            raise ValueError(f"Unbekannter Status {to_state}")
        t = now()
        with self.tx() as c:
            prev = c.execute("SELECT status FROM units WHERE id=?", (unit_id,)).fetchone()
            if prev is None:
                return False
            sets = ["status=?", "updated_at=?"]
            params: list[Any] = [to_state, t]
            if error is not None:
                sets.append("last_error=?")
                params.append(error)
            if bump_attempts:
                sets.append("attempts=attempts+1")
            if reset_attempts:
                sets.append("attempts=0")
                sets.append("last_error=NULL")
            if to_state in (Status.FAILED, Status.NEEDS_ATTENTION):
                sets.append("resume_status=?")
                params.append(prev["status"])
            elif to_state == Status.PLANNED and reset_attempts:
                sets.append("resume_status=NULL")
            if to_state == Status.DOWNLOADING:
                sets.append("started_at=COALESCE(started_at, ?)")
                params.append(t)
            if to_state == Status.DONE:
                sets.append("finished_at=?")
                params.append(t)
            q = ",".join("?" * len(from_states))
            cur = c.execute(f"UPDATE units SET {', '.join(sets)} WHERE id=? AND status IN ({q})",
                            (*params, unit_id, *from_states))
            if cur.rowcount != 1:
                return False
            c.execute(
                "INSERT INTO events(unit_id, ts, level, kind, message, data_json) VALUES (?,?,?,?,?,?)",
                (unit_id, t, level, kind, f"{prev['status']} -> {to_state}" + (f": {message}" if message else ""),
                 json.dumps(data, ensure_ascii=False) if data is not None else None))
            return True
