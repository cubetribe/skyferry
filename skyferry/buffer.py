"""Puffer-Guard: ohne Mount und Markerdatei wird nichts geschrieben oder geloescht."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .config import Config


class BufferUnavailable(RuntimeError):
    pass


class Buffer:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.mount = Path(os.path.expanduser(cfg.buffer.mount))
        self.staging = self.mount / cfg.buffer.staging_dir

    def problem(self) -> str | None:
        b = self.cfg.buffer
        if b.require_mount and not os.path.ismount(self.mount):
            return f"Puffer nicht eingehängt: {self.mount}"
        if not self.mount.is_dir():
            return f"Puffer-Verzeichnis fehlt: {self.mount}"
        if not (self.mount / b.marker_file).is_file():
            return f"Marker-Datei fehlt im Puffer: {self.mount / b.marker_file}"
        return None

    def require(self) -> None:
        p = self.problem()
        if p:
            raise BufferUnavailable(p)

    def free_bytes(self) -> int:
        self.require()
        return shutil.disk_usage(self.mount).free

    def has_room_for(self, nbytes: int) -> bool:
        return self.free_bytes() - self.cfg.buffer.reserve_bytes >= int(nbytes * 1.05)

    def total_capacity(self) -> int:
        self.require()
        return shutil.disk_usage(self.mount).total

    def unit_dir(self, unit_id: int, *, create: bool = False) -> Path:
        self.require()
        d = self.staging / str(unit_id)
        if create:
            d.mkdir(parents=True, exist_ok=True)
        return d

    def _inside(self, path: Path) -> bool:
        try:
            Path(os.path.realpath(path)).relative_to(os.path.realpath(self.staging))
            return True
        except ValueError:
            return False

    def remove_dir(self, path: Path) -> None:
        """Loescht ein Verzeichnis nur, wenn Guard ok ist und es unter staging liegt."""
        self.require()
        path = Path(path)
        if not self._inside(path) or os.path.realpath(path) == os.path.realpath(self.staging):
            raise BufferUnavailable(f"Löschen außerhalb des Staging-Bereichs verweigert: {path}")
        shutil.rmtree(path, ignore_errors=False)

    def remove_file(self, path: Path) -> None:
        self.require()
        if not self._inside(Path(path)):
            raise BufferUnavailable("Löschen außerhalb des Staging-Bereichs verweigert")
        Path(path).unlink(missing_ok=True)
