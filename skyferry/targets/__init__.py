from __future__ import annotations

from .base import ReleaseState, SyncTarget, UploadState
from .folder import FolderTarget

__all__ = ["SyncTarget", "UploadState", "ReleaseState", "FolderTarget", "build_target"]


def build_target(cfg, rc, buffer) -> SyncTarget:
    if cfg.target.type == "folder":
        return FolderTarget(cfg, rc, buffer)
    raise ValueError(f"Unbekannter target.type: {cfg.target.type}")
