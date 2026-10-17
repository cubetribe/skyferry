"""TOML-Konfiguration laden und validieren."""

from __future__ import annotations

import logging
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class ConfigError(ValueError):
    pass


@dataclass
class SourceCfg:
    remote: str = "gdrive"
    root_folder_id: str = ""
    local_root: str = ""  # NUR Tests: lokaler Ordner statt Drive
    export_formats: str = "docx,xlsx,pptx,svg"
    acknowledge_abuse: bool = True
    skip_shortcuts: bool = True
    done_prefix: str = "[MIGRIERT] "


@dataclass
class BufferCfg:
    mount: str = "/mnt/skyferry"
    require_mount: bool = True
    marker_file: str = ".skyferry-buffer"
    staging_dir: str = "staging"
    reserve_bytes: int = 50_000_000_000
    max_unit_bytes: int = 300_000_000_000


@dataclass
class RcloneCfg:
    binary: str = "rclone"
    config: str = ""
    transfers: int = 4
    checkers: int = 8
    tpslimit: int = 10
    bwlimit: str = ""
    retries: int = 5
    low_level_retries: int = 20
    local_encoding: str = ""
    stats_interval: str = "1m"
    extra_args: list = field(default_factory=list)


@dataclass
class TargetCfg:
    type: str = "folder"
    path: str = ""
    remote_subdir: str = "GDrive-Archiv"


@dataclass
class NotifyCfg:
    ntfy_url: str = ""
    ntfy_token: str = ""
    healthchecks_url: str = ""
    heartbeat_seconds: int = 300
    stall_hours: int = 6
    daily_summary_hour: int = 20
    min_repeat_minutes: int = 60
    include_paths: bool = False  # False: keine Pfade/Dateinamen an Fremdserver


@dataclass
class StateCfg:
    dir: str = "~/.local/state/skyferry"


@dataclass
class RunCfg:
    max_attempts: int = 3
    poll_seconds: int = 60
    defer_temporary_minutes: int = 15
    defer_limit_minutes: int = 360


@dataclass
class Config:
    source: SourceCfg = field(default_factory=SourceCfg)
    buffer: BufferCfg = field(default_factory=BufferCfg)
    rclone: RcloneCfg = field(default_factory=RcloneCfg)
    target: TargetCfg = field(default_factory=TargetCfg)
    notify: NotifyCfg = field(default_factory=NotifyCfg)
    state: StateCfg = field(default_factory=StateCfg)
    run: RunCfg = field(default_factory=RunCfg)

    # abgeleitete Pfade
    @property
    def state_dir(self) -> Path:
        return Path(os.path.expanduser(self.state.dir))

    @property
    def reports_dir(self) -> Path:
        return self.state_dir / "reports"

    @property
    def logs_dir(self) -> Path:
        return self.state_dir / "logs"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "state.db"

    @property
    def staging_root(self) -> Path:
        return Path(os.path.expanduser(self.buffer.mount)) / self.buffer.staging_dir


def default_config_path() -> Path:
    env = os.environ.get("SKYFERRY_CONFIG")
    if env:
        return Path(env).expanduser()
    return Path("~/.config/skyferry/config.toml").expanduser()


def _section(cls: type, data: Any, name: str):
    if not isinstance(data, dict):
        raise ConfigError(f"[{name}] muss eine Tabelle sein")
    ref = cls()
    known = {f.name for f in fields(cls)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"[{name}]: unbekannte Schlüssel: {', '.join(sorted(unknown))}")
    for key, value in data.items():
        default = getattr(ref, key)
        want = type(default)
        if want is int:
            ok = isinstance(value, int) and not isinstance(value, bool)
        else:
            ok = isinstance(value, want)
        if not ok:
            raise ConfigError(f"[{name}] {key}: erwartet {want.__name__}, erhalten {type(value).__name__}")
    return cls(**data)


def validate(cfg: Config) -> Config:
    if not cfg.source.done_prefix:
        raise ConfigError("source.done_prefix darf nicht leer sein")
    if "/" in cfg.source.done_prefix:
        raise ConfigError("source.done_prefix darf kein '/' enthalten")
    if cfg.buffer.max_unit_bytes <= 0 or cfg.buffer.reserve_bytes < 0:
        raise ConfigError("buffer.max_unit_bytes muss > 0 und reserve_bytes >= 0 sein")
    if cfg.target.type != "folder":
        raise ConfigError(f"target.type '{cfg.target.type}' wird noch nicht unterstützt (nur 'folder')")
    if cfg.target.type == "folder" and not cfg.target.path:
        raise ConfigError("target.path muss für target.type=folder gesetzt sein")
    if cfg.run.max_attempts < 1:
        raise ConfigError("run.max_attempts muss >= 1 sein")
    if not cfg.source.local_root and not cfg.source.remote:
        raise ConfigError("source.remote darf nicht leer sein")
    if not (-1 <= cfg.notify.daily_summary_hour <= 23):
        raise ConfigError("notify.daily_summary_hour muss -1 oder 0..23 sein")
    return cfg


def parse_config(data: dict) -> Config:
    known = {f.name for f in fields(Config)}
    unknown = set(data) - known
    if unknown:
        raise ConfigError(f"Unbekannte Abschnitte: {', '.join(sorted(unknown))}")
    classes = {f.name: f.default_factory for f in fields(Config)}  # type: ignore[misc]
    kwargs = {name: _section(classes[name], data[name], name) for name in data}
    return validate(Config(**kwargs))


def load_config(path: Path | str | None = None) -> Config:
    p = Path(path).expanduser() if path else default_config_path()
    try:
        raw = p.read_bytes()
    except FileNotFoundError:
        raise ConfigError(f"Konfiguration nicht gefunden: {p}") from None
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Konfiguration ungültig ({p}): {exc}") from None
    try:
        if p.stat().st_mode & 0o077:
            log.warning("Konfiguration %s ist für andere lesbar (enthält ggf. Token); chmod 600 empfohlen", p)
    except OSError:
        pass
    return parse_config(data)
