"""Zusammenbau aller Komponenten."""

from __future__ import annotations

from dataclasses import dataclass

from .buffer import Buffer
from .config import Config
from .notify import Notifier
from .rclone import Rclone
from .source import Source
from .state import State
from .targets import SyncTarget, build_target
from .verify import Verifier


@dataclass
class App:
    cfg: Config
    state: State
    rc: Rclone
    source: Source
    buffer: Buffer
    target: SyncTarget
    verifier: Verifier
    notifier: Notifier


def build_app(cfg: Config, *, state: State | None = None, notifier: Notifier | None = None) -> App:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    st = state or State(cfg.db_path)
    rc = Rclone(cfg)
    source = Source(cfg, rc)
    buf = Buffer(cfg)
    return App(cfg, st, rc, source, buf, build_target(cfg, rc, buf), Verifier(cfg, rc, source, st),
               notifier or Notifier(cfg.notify, st))
