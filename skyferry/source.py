"""Quelle (Google Drive oder, nur in Tests, ein lokaler Ordner) und Umbenennung."""

from __future__ import annotations

import os
import posixpath
from typing import Iterator

from .config import Config
from .rclone import Rclone
from .state import Unit


class RenameConflict(RuntimeError):
    """Umbenennen nicht sicher moeglich (Zielname belegt, Ordner fehlt, unerwarteter Name)."""


class Source:
    def __init__(self, cfg: Config, rc: Rclone):
        self.cfg = cfg
        self.rc = rc

    @property
    def local_mode(self) -> bool:
        return bool(self.cfg.source.local_root)

    @property
    def root_id(self) -> str:
        """Im Testmodus ist die ID ein relativer Pfad; die Wurzel hat ''."""
        return "" if self.local_mode else self.cfg.source.root_folder_id

    def spec(self, drive_id: str) -> str:
        if self.local_mode:
            return os.path.join(self.cfg.source.local_root, drive_id) if drive_id else self.cfg.source.local_root
        if drive_id:
            return f"{self.cfg.source.remote},root_folder_id={drive_id}:"
        return f"{self.cfg.source.remote}:"

    def flags(self) -> list[str]:
        """Drive-spezifische Flags (im Testmodus keine)."""
        if self.local_mode:
            return []
        s = self.cfg.source
        f = ["--drive-export-formats", s.export_formats]
        if s.skip_shortcuts:
            f.append("--drive-skip-shortcuts")
        if s.acknowledge_abuse:
            f.append("--drive-acknowledge-abuse")
        return f

    def _resolve_local(self, rel: str) -> str:
        """Testmodus: Komponenten koennen inzwischen mit done_prefix umbenannt sein."""
        cur = self.cfg.source.local_root
        for comp in [c for c in rel.split("/") if c]:
            plain = os.path.join(cur, comp)
            if os.path.exists(plain):
                cur = plain
            else:
                cur = os.path.join(cur, self.cfg.source.done_prefix + comp)
        return cur

    def unit_spec(self, unit: Unit) -> str:
        if self.local_mode:
            return self._resolve_local(unit.drive_id)
        return self.spec(unit.drive_id)

    def inventory(self) -> Iterator[dict]:
        flags = ["-R", "--fast-list", "--hash", "--hash-type", "md5", "--no-mimetype", *self.flags()]
        return self.rc.stream_lsjson(self.spec(self.root_id), flags)

    # ---- Umbenennen ----
    def _parent_dirs(self, unit: Unit) -> list[dict]:
        return list(self.rc.stream_lsjson(self.spec(unit.parent_drive_id), ["--dirs-only", *self.flags()]))

    def rename_unit(self, unit: Unit) -> str:
        """Benennt den Unit-Ordner in done_prefix+name um (idempotent). Gibt 'renamed'|'already' zurueck."""
        prefix = self.cfg.source.done_prefix
        new = prefix + unit.name
        if self.local_mode:
            parent = os.path.join(self.cfg.source.local_root, unit.parent_drive_id) if unit.parent_drive_id \
                else self.cfg.source.local_root
            parent = self._resolve_local(unit.parent_drive_id) if unit.parent_drive_id else parent
            old_p, new_p = os.path.join(parent, unit.name), os.path.join(parent, new)
            if os.path.isdir(new_p) and not os.path.exists(old_p):
                return "already"
            if not os.path.isdir(old_p):
                raise RenameConflict("Ordner in der Quelle nicht gefunden")
            if os.path.exists(new_p):
                raise RenameConflict("Zielname in der Quelle bereits belegt")
            self.rc.run("moveto", [old_p, new_p])
            if not os.path.isdir(new_p) or os.path.exists(old_p):
                raise RenameConflict("Umbenennung konnte nicht bestaetigt werden")
            return "renamed"

        entries = self._parent_dirs(unit)
        mine = [e for e in entries if e.get("ID") == unit.drive_id]
        if not mine:
            raise RenameConflict("Ordner-ID im Elternordner nicht gefunden")
        cur = mine[0]["Name"]
        if cur == new:
            return "already"
        if cur != unit.name:
            raise RenameConflict("Ordner hat unerwarteten Namen (manuell umbenannt?)")
        if any(e["Name"] == new for e in entries):
            raise RenameConflict("Zielname im Elternordner bereits belegt (Zusammenfuehren verhindert)")
        parent = self.spec(unit.parent_drive_id)
        self.rc.run("moveto", [parent + unit.name, parent + new], self.flags())
        after = self._parent_dirs(unit)
        ok = [e for e in after if e.get("ID") == unit.drive_id and e["Name"] == new]
        if not ok:
            raise RenameConflict("Nach der Umbenennung stimmt Ordner-ID/Name nicht (ID geaendert?)")
        return "renamed"
