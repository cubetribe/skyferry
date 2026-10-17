import hashlib
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from skyferry.app import build_app
from skyferry.config import Config, parse_config

RCLONE = shutil.which("rclone")
needs_rclone = unittest.skipUnless(RCLONE, "rclone nicht installiert")

MAX_UNIT = 1_000_000  # 1 MB im Test


def make_config(tmp: Path, **over) -> Config:
    (tmp / "buf").mkdir(exist_ok=True)
    (tmp / "buf" / ".skyferry-buffer").write_text("x")
    (tmp / "target").mkdir(exist_ok=True)
    (tmp / "drive").mkdir(exist_ok=True)
    data = {
        "source": {"local_root": str(tmp / "drive")},
        "buffer": {"mount": str(tmp / "buf"), "require_mount": False, "reserve_bytes": 0,
                   "max_unit_bytes": MAX_UNIT},
        "rclone": {"binary": RCLONE or "rclone", "transfers": 2, "checkers": 2, "tpslimit": 0,
                   "retries": 1, "low_level_retries": 1},
        "target": {"type": "folder", "path": str(tmp / "target")},
        "notify": {"daily_summary_hour": -1},
        "state": {"dir": str(tmp / "state")},
        "run": {"poll_seconds": 1},
    }
    for sec, vals in over.items():
        data.setdefault(sec, {}).update(vals)
    return parse_config(data)


def write_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def make_fake_drive(root: Path) -> None:
    blob = lambda n, seed: (hashlib.sha256(seed.encode()).digest() * (n // 32 + 1))[:n]  # noqa: E731
    write_file(root / "Dokumente/Projekte/2024/Bericht.txt", blob(5000, "a"))
    write_file(root / "Dokumente/Projekte/2024/leer.txt", b"")
    write_file(root / "Dokumente/Projekte/Ünïcode Ördner/datei mit Leerzeichen.txt", blob(300, "b"))
    write_file(root / "Dokumente/Projekte/Ünïcode Ördner/日本語.txt", blob(100, "c"))
    write_file(root / "Gross/teil1/a.bin", blob(600_000, "d"))
    write_file(root / "Gross/teil2/b.bin", blob(600_000, "e"))
    write_file(root / "Gross/lose.bin", blob(100_000, "f"))
    write_file(root / "Klein/x.txt", blob(10, "g"))
    write_file(root / "wurzel.txt", blob(77, "h"))


def md5_tree(root: Path) -> dict:
    out = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            p = Path(dp) / fn
            out[p.relative_to(root).as_posix()] = hashlib.md5(p.read_bytes()).hexdigest()
    return out


class TmpCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.addCleanup(self._td.cleanup)

    def app(self, **over):
        cfg = make_config(self.tmp, **over)
        app = build_app(cfg)
        self.addCleanup(app.state.close)
        return app
