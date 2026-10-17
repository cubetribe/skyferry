"""Listing-Vergleich (Verifikation Stufe 1/2/Audit) und Reports."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .rclone import Rclone, RcloneError
from .state import Unit


@dataclass(slots=True)
class Entry:
    size: int | None  # None = unbekannt (z.B. Google-Doc im Drive-Listing)
    md5: str | None
    is_gdoc: bool = False


Listing = dict[str, Entry]


@dataclass
class VerifyResult:
    ok: bool
    stage: str
    counts: dict[str, int]
    problems: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        c = self.counts
        return (f"{c.get('expected', 0)} Dateien erwartet, {c.get('missing', 0)} fehlend, "
                f"{c.get('mismatch', 0)} abweichend, {c.get('extra', 0)} zusätzlich")

    def to_dict(self) -> dict:
        return {"ok": self.ok, "stage": self.stage, "counts": self.counts, "problems": self.problems,
                "warnings": self.warnings}


def entry_from_json(obj: dict) -> Entry:
    """Google-Docs: Size -1 (unbekannt) und kein Hash -> is_gdoc (siehe rclone.py)."""
    size = obj.get("Size", -1)
    md5 = (obj.get("Hashes") or {}).get("md5")
    if size is None or size < 0:
        return Entry(None, md5.lower() if md5 else None, md5 is None)
    return Entry(int(size), md5.lower() if md5 else None, False)


def listing_from_objects(objs: Iterable[dict]) -> tuple[Listing, list[str]]:
    """Baut ein Listing; liefert zusaetzlich doppelte Pfade (gleicher Name im selben Ordner)."""
    listing: Listing = {}
    dups: list[str] = []
    for o in objs:
        if o.get("IsDir"):
            continue
        p = o["Path"]
        if p in listing:
            dups.append(p)
        listing[p] = entry_from_json(o)
    return listing, dups


def read_listing(rc: Rclone, spec: str, flags: list[str], *, recursive: bool, missing_ok: bool = False
                 ) -> tuple[Listing, list[str]]:
    args = ["--files-only", "--hash", "--hash-type", "md5", *flags]
    if recursive:
        args.insert(0, "-R")
    try:
        return listing_from_objects(rc.stream_lsjson(spec, args))
    except RcloneError as exc:
        if missing_ok and exc.kind == "not_found":
            return {}, []
        raise


def compare(expected: Listing, actual: Listing, *, stage: str, duplicates: Iterable[str] = (),
            limit: int = 1000) -> VerifyResult:
    counts = {"expected": len(expected), "actual": len(actual), "missing": 0, "mismatch": 0, "extra": 0,
              "gdocs_unchecked_hash": 0, "no_hash": 0, "duplicates": 0}
    problems: list[dict] = []
    warnings: list[dict] = []

    def add(kind: str, path: str, detail: str = "") -> None:
        if len(problems) < limit:
            problems.append({"kind": kind, "path": path, "detail": detail})

    for d in duplicates:
        counts["duplicates"] += 1
        add("duplicate_in_source", d, "Doppelter Name im selben Ordner; rclone kopiert nur eine Datei")
    for path, e in expected.items():
        a = actual.get(path)
        if a is None:
            counts["missing"] += 1
            add("missing", path)
            continue
        if e.is_gdoc:
            # Keine Pruefsumme moeglich: ok, wenn vorhanden und Groesse > 0
            counts["gdocs_unchecked_hash"] += 1
            if a.size is not None and a.size <= 0:
                counts["mismatch"] += 1
                add("gdoc_empty", path, "Google-Doc-Export hat Größe 0")
            continue
        if e.size is not None and a.size is not None and e.size != a.size:
            counts["mismatch"] += 1
            add("size_mismatch", path, f"{e.size} != {a.size}")
            continue
        if e.md5 and a.md5:
            if e.md5 != a.md5:
                counts["mismatch"] += 1
                add("md5_mismatch", path, f"{e.md5} != {a.md5}")
        else:
            counts["no_hash"] += 1
    for path in actual:
        if path not in expected:
            counts["extra"] += 1
            if len(warnings) < limit:
                warnings.append({"kind": "extra", "path": path})
    ok = counts["missing"] == 0 and counts["mismatch"] == 0 and counts["duplicates"] == 0
    return VerifyResult(ok, stage, counts, problems, warnings)


def write_report(reports_dir: Path, unit: Unit, result: VerifyResult) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = reports_dir / f"unit-{unit.id}-{result.stage}-{ts}"
    base.with_suffix(".json").write_text(json.dumps(result.to_dict(), ensure_ascii=False, indent=1), "utf-8")
    lines = [f"# Verifikation Unit #{unit.id} ({result.stage})", "",
             f"- Pfad: `{unit.drive_path or '(Root)'}` ({unit.kind})",
             f"- Ergebnis: **{'OK' if result.ok else 'FEHLGESCHLAGEN'}**",
             f"- Zähler: `{json.dumps(result.counts)}`", ""]
    if result.problems:
        lines += ["## Probleme", ""] + [f"- {p['kind']}: `{p['path']}` {p['detail']}" for p in result.problems]
    if result.warnings:
        lines += ["", "## Warnungen", ""] + [f"- {w['kind']}: `{w['path']}`" for w in result.warnings[:200]]
    base.with_suffix(".md").write_text("\n".join(lines) + "\n", "utf-8")
    return base.with_suffix(".md")


class Verifier:
    def __init__(self, cfg, rc: Rclone, source, state):
        self.cfg, self.rc, self.source, self.state = cfg, rc, source, state

    @staticmethod
    def _recursive(unit: Unit) -> bool:
        return unit.kind == "folder"

    def source_listing(self, unit: Unit) -> tuple[Listing, list[str]]:
        return read_listing(self.rc, self.source.unit_spec(unit), self.source.flags(),
                            recursive=self._recursive(unit))

    def local_listing(self, unit: Unit, local_dir: Path) -> Listing:
        listing, _ = read_listing(self.rc, str(local_dir), [], recursive=self._recursive(unit), missing_ok=True)
        return listing

    def verify_local(self, unit: Unit, local_dir: Path) -> tuple[VerifyResult, Listing]:
        """Stufe 1: Drive (live) gegen Puffer, mit MD5."""
        expected, dups = self.source_listing(unit)
        actual = self.local_listing(unit, local_dir)
        res = compare(expected, actual, stage="local", duplicates=dups)
        write_report(self.cfg.reports_dir, unit, res)
        return res, actual

    def verify_remote(self, unit: Unit, local_dir: Path, target, manifest: Listing | None) -> VerifyResult:
        """Stufe 2: Puffer gegen Ziel (unabhaengige Sicht); zusaetzlich Puffer gegen Stufe-1-Manifest."""
        local = self.local_listing(unit, local_dir)
        remote = target.remote_listing(unit)
        res = compare(local, remote, stage="remote")
        if manifest is not None:
            drift = compare(manifest, local, stage="remote")
            for p in drift.problems:
                p["kind"] = "puffer_changed_" + p["kind"]
            res.problems += drift.problems
            res.counts["puffer_changed"] = drift.counts["missing"] + drift.counts["mismatch"]
            res.ok = res.ok and drift.ok
        write_report(self.cfg.reports_dir, unit, res)
        return res

    def audit(self, unit: Unit, target) -> VerifyResult:
        """Fertige Unit: Quelle (umbenannt, per ID/Aufloesung) gegen Ziel."""
        expected, dups = self.source_listing(unit)
        res = compare(expected, target.remote_listing(unit), stage="audit", duplicates=dups)
        write_report(self.cfg.reports_dir, unit, res)
        return res
