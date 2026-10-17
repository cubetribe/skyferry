"""Kommandozeile: python -m skyferry <befehl>."""

from __future__ import annotations

import argparse
import json
import logging
import logging.handlers
import sys
import time
from pathlib import Path

from .app import App, build_app
from .config import Config, ConfigError, load_config
from .planner import build_tree, commit_plan, compute_stats, plan_units, write_plan_report
from .rclone import RcloneError, SafetyError
from .runner import AlreadyRunning, Runner, instance_lock
from .state import Status
from .util import fmt_bytes, fmt_duration, now


def _setup_logging(cfg: Config | None, verbose: bool) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if cfg is not None:
        try:
            cfg.logs_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(cfg.logs_dir / "skyferry.log", maxBytes=5_000_000,
                                                      backupCount=5, encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError:
            pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="skyferry", description="Google Drive -> Puffer -> Sync.com Migration")
    p.add_argument("--config", help="Pfad zur config.toml (Default: ~/.config/skyferry/config.toml)")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("plan", help="Inventar lesen und Units planen")
    sp.add_argument("--dry-run", action="store_true", help="nur Statistik, nichts speichern")

    sp = sub.add_parser("run", help="Migration ausführen")
    sp.add_argument("--once", action="store_true", help="nur laufen, bis kein Fortschritt mehr möglich ist")
    sp.add_argument("--max-units", type=int, help="höchstens N neue Downloads starten")

    sp = sub.add_parser("status", help="Übersicht")
    sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("units", help="Units auflisten")
    sp.add_argument("--status")

    sp = sub.add_parser("retry", help="FAILED/NEEDS_ATTENTION zurücksetzen")
    sp.add_argument("unit_id", nargs="?", type=int)
    sp.add_argument("--failed", action="store_true", help="alle FAILED/NEEDS_ATTENTION")

    sp = sub.add_parser("skip", help="Unit überspringen (Endzustand SKIPPED)")
    sp.add_argument("unit_id", type=int)
    sp.add_argument("--reason", required=True)

    sp = sub.add_parser("events", help="Audit-Trail")
    sp.add_argument("--unit", type=int)
    sp.add_argument("--tail", type=int, default=30)

    sp = sub.add_parser("verify", help="Unit erneut verifizieren")
    sp.add_argument("unit_id", nargs="?", type=int)
    sp.add_argument("--stage", choices=["local", "remote"], default="local")
    sp.add_argument("--all-done", action="store_true", help="Audit: fertige Units gegen das Ziel prüfen")

    sub.add_parser("notify-test", help="Testnachricht an ntfy und healthchecks")
    sp = sub.add_parser("notify-alarm", help="Alarm senden (für systemd OnFailure)")
    sp.add_argument("text")
    return p


def cmd_plan(app: App, args) -> int:
    cfg = app.cfg
    print("Lese Inventar (kann bei großen Laufwerken lange dauern) ...")
    tree = build_tree(app.source.inventory(), root_id=app.source.root_id, done_prefix=cfg.source.done_prefix,
                      max_unit_bytes=cfg.buffer.max_unit_bytes)
    existing = None if args.dry_run else app.state.find_unit
    planned = plan_units(tree, max_unit_bytes=cfg.buffer.max_unit_bytes, existing=existing)
    stats = compute_stats(planned, tree)
    if not args.dry_run:
        commit_plan(app.state, planned, tree, stats)
        cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        rep = cfg.reports_dir / f"plan-{time.strftime('%Y%m%d-%H%M%S')}.md"
        write_plan_report(rep, planned, stats)
    print("\n".join(stats.lines()))
    if tree.big_files:
        print(f"Warnung: {len(tree.big_files)} Einzeldatei(en) größer als max_unit_bytes")
    if not args.dry_run:
        print(f"Report: {rep}")
    else:
        print("(Trockenlauf: nichts gespeichert)")
    return 0


def cmd_run(app: App, args) -> int:
    with instance_lock(app.cfg.state_dir):
        return Runner(app).run(once=args.once, max_units=args.max_units)


def cmd_status(app: App, args) -> int:
    st = app.state
    counts = st.count_by_status()
    total_b = st.sum_bytes()
    done_b = st.sum_bytes([Status.DONE])
    since = now() - 86400
    day_b = st.sum_bytes([Status.DONE], finished_since=since)
    remaining = total_b - done_b - st.sum_bytes([Status.SKIPPED])
    eta = remaining / (day_b / 86400) if day_b > 0 else None
    active = [u for u in st.units([Status.DOWNLOADING]) ]
    prog = {}
    for u in active:
        v = st.kv_get(f"progress:{u.id}")
        if v:
            prog[u.id] = json.loads(v)
    events = st.events(limit=10)
    if args.json:
        print(json.dumps({"counts": counts, "bytes_total": total_b, "bytes_done": done_b, "bytes_24h": day_b,
                          "eta_seconds": eta, "active": [{"id": u.id, "progress": prog.get(u.id)} for u in active],
                          "events": [dict(e) for e in events]}, ensure_ascii=False, indent=1))
        return 0
    print("Units je Status:")
    for k in Status.ALL:
        if counts.get(k):
            print(f"  {k:<16}{counts[k]}")
    print(f"Fertig: {fmt_bytes(done_b)} von {fmt_bytes(total_b)}")
    print(f"Durchsatz letzte 24 h: {fmt_bytes(day_b)}")
    print("ETA: " + (fmt_duration(eta) if eta is not None else "unbekannt"))
    for u in active:
        p = prog.get(u.id)
        extra = f" {fmt_bytes(p['bytes'])} / {fmt_bytes(p['total'])}" if p and "bytes" in p else ""
        print(f"Aktiv: Unit #{u.id} {u.drive_path or '(Root)'}{extra}")
    print("Letzte Events:")
    for e in events:
        print(f"  {time.strftime('%d.%m. %H:%M:%S', time.localtime(e['ts']))} [{e['level']}] "
              f"#{e['unit_id'] or '-'} {e['message']}")
    return 0


def cmd_units(app: App, args) -> int:
    for u in app.state.units(args.status) if args.status else app.state.units():
        kind = ("container" if u.is_split_container else u.kind)
        print(f"#{u.id:<5}{u.status:<16}{kind:<12}{fmt_bytes(u.expected_bytes):>10}  {u.drive_path or '(Root)'}"
              + (f"  [{u.last_error}]" if u.last_error else ""))
    return 0


def cmd_retry(app: App, args) -> int:
    st = app.state
    if args.failed:
        targets = st.units([Status.FAILED, Status.NEEDS_ATTENTION])
    elif args.unit_id is not None:
        u = st.get_unit(args.unit_id)
        if not u:
            print(f"Unit #{args.unit_id} nicht gefunden")
            return 1
        targets = [u]
    else:
        print("Unit-ID oder --failed angeben")
        return 1
    n = 0
    for u in targets:
        resume = u.resume_status if u.resume_status in Status.RESUMABLE else Status.PLANNED
        if st.transition(u.id, [Status.FAILED, Status.NEEDS_ATTENTION], resume, reset_attempts=True,
                         message="manueller Retry", kind="retry"):
            st.kv_del(f"defer:{u.id}")
            n += 1
            print(f"Unit #{u.id} -> {resume}")
        else:
            print(f"Unit #{u.id} ist nicht FAILED/NEEDS_ATTENTION ({u.status})")
    return 0 if n or not targets else 1


def cmd_skip(app: App, args) -> int:
    ok = app.state.transition(args.unit_id, [Status.PLANNED, Status.FAILED, Status.NEEDS_ATTENTION],
                              Status.SKIPPED, message=f"übersprungen: {args.reason}", kind="skip")
    print("Unit übersprungen" if ok else "Überspringen nicht möglich (Unit nicht PLANNED/FAILED/NEEDS_ATTENTION)")
    return 0 if ok else 1


def cmd_events(app: App, args) -> int:
    for e in app.state.events(unit_id=args.unit, limit=args.tail):
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(e['ts']))} [{e['level']}] {e['kind']} "
              f"#{e['unit_id'] or '-'}: {e['message']}")
    return 0


def cmd_verify(app: App, args) -> int:
    rc = 0
    if args.all_done:
        for u in app.state.units(Status.DONE):
            if u.is_split_container:
                continue
            res = app.verifier.audit(u, app.target)
            print(f"Unit #{u.id}: {'OK' if res.ok else 'FEHLER'} ({res.summary()})")
            rc |= 0 if res.ok else 1
        return rc
    if args.unit_id is None:
        print("Unit-ID oder --all-done angeben")
        return 1
    u = app.state.get_unit(args.unit_id)
    if not u:
        print("Unit nicht gefunden")
        return 1
    local = app.buffer.unit_dir(u.id)
    if args.stage == "local":
        res, _ = app.verifier.verify_local(u, local)
    else:
        res = app.verifier.verify_remote(u, local, app.target, None)
    print(f"Unit #{u.id} Stufe {args.stage}: {'OK' if res.ok else 'FEHLER'} ({res.summary()})")
    for p in res.problems[:20]:
        print(f"  {p['kind']}: {p['path']} {p['detail']}")
    return 0 if res.ok else 1


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2
    _setup_logging(cfg, args.verbose)
    app = build_app(cfg)
    try:
        if args.cmd == "notify-test":
            res = app.notifier.test()
            for k, v in res.items():
                print(f"{k}: {'OK' if v else 'nicht konfiguriert oder fehlgeschlagen'}")
            return 0 if all(res.values()) else 1
        if args.cmd == "notify-alarm":
            app.notifier.alarm("skyferry Alarm", args.text)
            app.notifier.hc_fail(args.text)
            return 0
        handler = {"plan": cmd_plan, "run": cmd_run, "status": cmd_status, "units": cmd_units,
                   "retry": cmd_retry, "skip": cmd_skip, "events": cmd_events, "verify": cmd_verify}[args.cmd]
        return handler(app, args)
    except AlreadyRunning as exc:
        print(str(exc), file=sys.stderr)
        return 3
    except (RcloneError, SafetyError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    finally:
        app.state.close()
