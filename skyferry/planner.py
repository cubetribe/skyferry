"""Inventar (gestreamt) und Unit-Planung."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from .state import State, Status, Unit
from .util import fmt_bytes


class DirNode:
    __slots__ = ("path", "name", "id", "files", "bytes", "gdocs", "dups", "children",
                 "tot_files", "tot_bytes", "tot_gdocs", "tot_dups")

    def __init__(self, path: str):
        self.path = path
        self.name = path.rpartition("/")[2]
        self.id: str | None = None
        self.files = self.bytes = self.gdocs = 0
        self.dups: list[str] = []
        self.children: list[DirNode] = []
        self.tot_files = self.tot_bytes = self.tot_gdocs = 0
        self.tot_dups: list[str] = []


@dataclass
class PlannedUnit:
    drive_id: str
    parent_drive_id: str
    drive_path: str
    name: str
    kind: str
    parent_key: tuple[str, str] | None  # (drive_id, kind) des Containers
    is_split: bool
    files: int
    bytes: int
    gdocs: int
    dups: list[str]

    @property
    def key(self) -> tuple[str, str]:
        return (self.drive_id, self.kind)


@dataclass
class Tree:
    root: DirNode
    skipped_done: int = 0
    total_entries: int = 0
    big_files: list[tuple[str, int]] = field(default_factory=list)


def build_tree(entries: Iterable[dict], *, root_id: str, done_prefix: str, max_unit_bytes: int) -> Tree:
    """Streamt lsjson-Eintraege in einen Verzeichnisbaum (nur Aggregate pro Ordner im Speicher).

    Annahmen: Google-Docs haben Size -1 (kein Hash) -> zaehlen als Datei mit 0 Bytes.
    Reihenfolge der Eintraege ist beliebig. Doppelte Pfade werden ueber Hashes erkannt
    (Hash-Kollision = harmloser Fehlalarm Richtung NEEDS_ATTENTION).
    """
    nodes: dict[str, DirNode] = {}
    root = DirNode("")
    root.id = root_id
    nodes[""] = root
    seen_files: set[int] = set()
    tree = Tree(root)

    def node(path: str) -> DirNode:
        n = nodes.get(path)
        if n is None:
            n = nodes[path] = DirNode(path)
        return n

    for obj in entries:
        tree.total_entries += 1
        path = obj["Path"]
        if done_prefix in path and any(c.startswith(done_prefix) for c in path.split("/")):
            tree.skipped_done += 1
            continue
        if obj.get("IsDir"):
            n = node(path)
            if n.id is not None:
                n.dups.append(path)
            n.id = obj.get("ID") or path
        else:
            h = hash(path)
            parent = node(path.rpartition("/")[0])
            if h in seen_files:
                parent.dups.append(path)
            seen_files.add(h)
            size = obj.get("Size", -1)
            parent.files += 1
            if size is None or size < 0:
                parent.gdocs += 1
            else:
                parent.bytes += size
                if size > max_unit_bytes:
                    tree.big_files.append((path, size))
    # Fehlende Zwischenknoten ergaenzen, Eltern verlinken, aufsummieren (tiefste zuerst)
    for path in list(nodes):
        p = path
        while p:
            parent_path = p.rpartition("/")[0]
            node(parent_path)
            p = parent_path
    for n in nodes.values():
        if n.id is None:
            n.id = n.path
    for path, n in nodes.items():
        if path:
            nodes[path.rpartition("/")[0]].children.append(n)
    for n in sorted(nodes.values(), key=lambda x: x.path.count("/") + (1 if x.path else 0), reverse=True):
        n.tot_files += n.files
        n.tot_bytes += n.bytes
        n.tot_gdocs += n.gdocs
        n.tot_dups += n.dups
        if n.path:
            p = nodes[n.path.rpartition("/")[0]]
            p.tot_files += n.tot_files
            p.tot_bytes += n.tot_bytes
            p.tot_gdocs += n.tot_gdocs
            p.tot_dups += n.tot_dups
    for n in nodes.values():
        n.children.sort(key=lambda c: c.path)
    return tree


def plan_units(tree: Tree, *, max_unit_bytes: int,
               existing: Callable[[str, str], Unit | None] | None = None) -> list[PlannedUnit]:
    out: list[PlannedUnit] = []

    def plan(n: DirNode, parent_key: tuple[str, str] | None, parent_id: str) -> None:
        ex = existing(n.id or "", "folder") if existing else None
        split = ex.is_split_container if ex else n.tot_bytes > max_unit_bytes
        pu = PlannedUnit(n.id or "", parent_id, n.path, n.name, "folder", parent_key, split,
                         n.tot_files, n.tot_bytes, n.tot_gdocs, [] if split else list(n.tot_dups))
        out.append(pu)
        if split:
            expand(n, pu.key)

    def expand(n: DirNode, key: tuple[str, str] | None) -> None:
        for c in n.children:
            plan(c, key, n.id or "")
        if n.files > 0:
            out.append(PlannedUnit(n.id or "", n.id or "", n.path, n.name or "(Root)", "loose_files", key,
                                   False, n.files, n.bytes, n.gdocs, list(n.dups)))

    expand(tree.root, None)
    return out


@dataclass
class PlanStats:
    units: int
    bytes: int
    files: int
    gdocs: int
    duplicates: int
    skipped_done: int
    containers: int
    top: list[tuple[str, int]]
    new: int = 0
    updated: int = 0
    unchanged: int = 0
    big_files: int = 0

    def lines(self) -> list[str]:
        out = [f"Units: {self.units} (+ {self.containers} geteilte Container)",
               f"Daten: {fmt_bytes(self.bytes)} in {self.files} Dateien, davon {self.gdocs} Google-Docs",
               f"Doppelte Pfade: {self.duplicates}",
               f"Bereits migrierte Ordner übersprungen (Einträge): {self.skipped_done}",
               f"Neu: {self.new}, aktualisiert: {self.updated}, unverändert: {self.unchanged}",
               "Größte Units:"]
        out += [f"  {fmt_bytes(b):>10}  {p or '(Root)'}" for p, b in self.top]
        return out


def compute_stats(planned: list[PlannedUnit], tree: Tree) -> PlanStats:
    leaves = [u for u in planned if not u.is_split]
    top = sorted(leaves, key=lambda u: u.bytes, reverse=True)[:20]
    return PlanStats(
        units=len(leaves), bytes=sum(u.bytes for u in leaves), files=sum(u.files for u in leaves),
        gdocs=sum(u.gdocs for u in leaves), duplicates=sum(len(u.dups) for u in leaves),
        skipped_done=tree.skipped_done, containers=len(planned) - len(leaves),
        top=[(u.drive_path, u.bytes) for u in top], big_files=len(tree.big_files))


def commit_plan(state: State, planned: list[PlannedUnit], tree: Tree, stats: PlanStats) -> None:
    ids: dict[tuple[str, str], int] = {}
    for pu in planned:
        ex = state.find_unit(pu.drive_id, pu.kind)
        if ex is not None:
            ids[pu.key] = ex.id
            if ex.status == Status.PLANNED and not ex.is_split_container:
                if (ex.expected_files, ex.expected_bytes, ex.gdocs_count) != (pu.files, pu.bytes, pu.gdocs):
                    state.update_expected(ex.id, pu.files, pu.bytes, pu.gdocs)
                    stats.updated += 1
                else:
                    stats.unchanged += 1
                if pu.dups:
                    _flag_dups(state, ex.id, pu, Status.PLANNED)
            else:
                stats.unchanged += 1
            continue
        status = Status.NEEDS_ATTENTION if pu.dups else Status.PLANNED
        uid = state.add_unit(
            drive_id=pu.drive_id, parent_drive_id=pu.parent_drive_id, drive_path=pu.drive_path, name=pu.name,
            kind=pu.kind, parent_unit_id=ids.get(pu.parent_key) if pu.parent_key else None,
            is_split_container=pu.is_split, expected_files=pu.files, expected_bytes=pu.bytes,
            gdocs_count=pu.gdocs, status=status)
        ids[pu.key] = uid
        stats.new += 1
        state.event("planned", f"Unit geplant: {pu.kind}, {pu.files} Dateien, {fmt_bytes(pu.bytes)}"
                    + (" (Container)" if pu.is_split else ""), unit_id=uid)
        if pu.dups:
            _record_dups(state, uid, pu)
    for path, size in tree.big_files:
        state.event("big_file", f"Einzelne Datei größer als max_unit_bytes: {fmt_bytes(size)}",
                    level="warning", data={"path": path, "size": size})
    n = int(state.kv_get("plan_version", "0") or 0) + 1
    state.kv_set("plan_version", str(n))
    state.kv_set("plan_ts", str(time.time()))


def _record_dups(state: State, uid: int, pu: PlannedUnit) -> None:
    state.event("duplicates", f"{len(pu.dups)} doppelte Pfade (rclone dedupe manuell prüfen; wird nicht automatisch "
                "ausgeführt)", unit_id=uid, level="warning", data={"paths": pu.dups[:200]})
    state.conn.execute("UPDATE units SET last_error=? WHERE id=?", (f"{len(pu.dups)} doppelte Pfade", uid))


def _flag_dups(state: State, uid: int, pu: PlannedUnit, frm: str) -> None:
    if state.transition(uid, frm, Status.NEEDS_ATTENTION, message="doppelte Pfade",
                        error=f"{len(pu.dups)} doppelte Pfade", level="warning"):
        _record_dups(state, uid, pu)


def write_plan_report(path, planned: list[PlannedUnit], stats: PlanStats) -> None:
    lines = ["# Plan-Report", "", "```", *stats.lines(), "```", ""]
    dups = [(u, d) for u in planned for d in u.dups]
    if dups:
        lines += ["## Doppelte Pfade (NEEDS_ATTENTION)", "",
                  "Hinweis: Manuell prüfen, z. B. `rclone dedupe --dedupe-mode list <remote>:`. "
                  "Dieses Tool führt dedupe NIE aus.", ""]
        lines += [f"- `{d}`" for _, d in dups[:500]]
        lines.append("")
    lines += ["## Alle Units", ""]
    lines += [f"- {u.kind}{' (Container)' if u.is_split else ''}: `{u.drive_path or '(Root)'}` "
              f"{fmt_bytes(u.bytes)}, {u.files} Dateien" for u in planned]
    path.write_text("\n".join(lines) + "\n", "utf-8")
