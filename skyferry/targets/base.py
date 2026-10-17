"""SyncTarget-Protokoll: austauschbare Anbindung des Ziels (Sync.com)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..state import Unit
from ..verify import Listing


@dataclass
class UploadState:
    kind: str  # PENDING | IN_PROGRESS | COMPLETE | ERROR
    progress: float | None = None  # 0..1
    message: str = ""

    @classmethod
    def pending(cls) -> "UploadState":
        return cls("PENDING")

    @classmethod
    def in_progress(cls, progress: float | None = None) -> "UploadState":
        return cls("IN_PROGRESS", progress)

    @classmethod
    def complete(cls) -> "UploadState":
        return cls("COMPLETE", 1.0)

    @classmethod
    def error(cls, message: str) -> "UploadState":
        return cls("ERROR", None, message)


@dataclass
class ReleaseState:
    done: bool  # True = DONE, False = WAITING
    message: str = ""

    @classmethod
    def finished(cls) -> "ReleaseState":
        return cls(True)

    @classmethod
    def waiting(cls, message: str) -> "ReleaseState":
        return cls(False, message)


class SyncTarget(Protocol):
    name: str

    def stage(self, unit: Unit, local_dir: Path) -> None:
        """Daten an das Ziel uebergeben (FolderTarget: kopieren)."""

    def upload_state(self, unit: Unit) -> UploadState: ...

    def remote_listing(self, unit: Unit) -> Listing:
        """Unabhaengige Sicht des Ziels fuer Verifikationsstufe 2."""

    def release(self, unit: Unit, local_dir: Path) -> ReleaseState:
        """Lokalen Platz freigeben, OHNE das Ziel zu loeschen."""
