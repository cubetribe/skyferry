"""Kleine Hilfsfunktionen."""

from __future__ import annotations

import re
import time


def now() -> float:
    return time.time()


def safe_relpath(path: str) -> str:
    """Normalisiert einen Drive-Pfad; verbietet '.' und '..' Komponenten."""
    parts = [p for p in path.split("/") if p != ""]
    if any(p in (".", "..") for p in parts):
        raise ValueError("Unzulässige Pfadkomponente ('.' oder '..') im Drive-Pfad")
    return "/".join(parts)


def fmt_bytes(n: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    v = float(n)
    for u in units:
        if abs(v) < 1000 or u == units[-1]:
            if u == "B":
                return f"{int(v)} B"
            return f"{v:.1f} {u}".replace(".", ",")
        v /= 1000.0
    return f"{n} B"


def fmt_duration(seconds: float) -> str:
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, _ = divmod(s, 60)
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


_PATHLIKE = re.compile(r"""(["'`]).*?\1|\S*[/\\]\S*""")


def strip_paths(text: str, max_len: int = 160) -> str:
    """Entfernt alles, was nach Pfad/Dateiname aussieht (für Fremdserver-Meldungen)."""
    cleaned = _PATHLIKE.sub("<pfad>", text or "")
    cleaned = " ".join(cleaned.split())
    return cleaned[:max_len]
