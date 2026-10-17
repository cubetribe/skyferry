"""FolderTarget: lokaler Ordner als Ziel (Tests / generisches Ziel)."""

from __future__ import annotations

import os
from pathlib import Path

from ..buffer import Buffer
from ..config import Config
from ..rclone import Rclone
from ..state import Unit
from ..util import safe_relpath
from ..verify import Listing, read_listing
from .base import ReleaseState, UploadState


class FolderTarget:
    name = "folder"

    def __init__(self, cfg: Config, rc: Rclone, buffer: Buffer):
        self.cfg, self.rc, self.buffer = cfg, rc, buffer

    def dest_dir(self, unit: Unit) -> Path:
        base = Path(os.path.expanduser(self.cfg.target.path))
        if self.cfg.target.remote_subdir:
            base = base / safe_relpath(self.cfg.target.remote_subdir)
        rel = safe_relpath(unit.drive_path)
        return base / rel if rel else base

    def stage(self, unit: Unit, local_dir: Path) -> None:
        self.buffer.require()
        dest = self.dest_dir(unit)
        flags = ["--checksum", "--create-empty-src-dirs"]
        if unit.kind == "loose_files":
            flags += ["--max-depth", "1"]
        dest.mkdir(parents=True, exist_ok=True)
        self.rc.copy(str(local_dir), str(dest), flags)

    def upload_state(self, unit: Unit) -> UploadState:
        return UploadState.complete()

    def remote_listing(self, unit: Unit) -> Listing:
        listing, _ = read_listing(self.rc, str(self.dest_dir(unit)), [], recursive=unit.kind == "folder",
                                  missing_ok=True)
        return listing

    def release(self, unit: Unit, local_dir: Path) -> ReleaseState:
        if Path(local_dir).exists():
            self.buffer.remove_dir(Path(local_dir))
        return ReleaseState.finished()
