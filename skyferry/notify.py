"""Benachrichtigungen: ntfy (Push) und healthchecks.io (Totmannschalter).

Datenschutz: Mit notify.include_paths=false (Default) verlassen KEINE Pfade oder Dateinamen
den Rechner. Meldungen enthalten nur Unit-ID, Art, Groesse, Status und Fehlerklasse.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from .config import NotifyCfg
from .state import State, Unit
from .util import fmt_bytes, now, strip_paths

log = logging.getLogger(__name__)

TIMEOUT = 15


def _hdr(value: str) -> str:
    """HTTP-Header sind latin-1; Umlaute/Sonderzeichen als RFC-2047 (von ntfy unterstuetzt)."""
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode("utf-8")).decode("ascii") + "?="


def unit_label(unit: Unit, include_paths: bool) -> str:
    base = f"Unit #{unit.id} ({unit.kind}, {fmt_bytes(unit.expected_bytes)})"
    if include_paths:
        base += f" [{unit.drive_path or '(Root)'}]"
    return base


def safe_error(text: str, include_paths: bool) -> str:
    """Fehlertext fuer Fremdserver: gekuerzt, ohne Pfade (ausser include_paths)."""
    if include_paths:
        return (text or "")[:300]
    return strip_paths(text or "")


class Notifier:
    def __init__(self, cfg: NotifyCfg, state: State | None = None, *, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.state = state
        self.sleep = sleep
        self._mem: dict[str, str] = {}
        self._hb_stop = threading.Event()
        self._hb_thread: threading.Thread | None = None

    @property
    def include_paths(self) -> bool:
        return self.cfg.include_paths

    # ---- kv (Rate-Limit) ----
    def _get(self, k: str) -> str | None:
        return self.state.kv_get(k) if self.state else self._mem.get(k)

    def _set(self, k: str, v: str) -> None:
        if self.state:
            self.state.kv_set(k, v)
        else:
            self._mem[k] = v

    def _rate_limited(self, title: str, unit_id: int | None) -> bool:
        key = "notify:last:" + hashlib.sha1(f"{title}|{unit_id}".encode()).hexdigest()[:16]
        last = self._get(key)
        if last and now() - float(last) < self.cfg.min_repeat_minutes * 60:
            return True
        self._set(key, str(now()))
        return False

    # ---- HTTP ----
    def _http(self, req: urllib.request.Request) -> bool:
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                resp.read()
            return True
        except (urllib.error.URLError, OSError, ValueError) as exc:
            log.warning("Benachrichtigung fehlgeschlagen (%s): %s", req.full_url.split("?")[0][:40], exc)
            return False

    def _ntfy(self, title: str, message: str, priority: int, tags: str, retries: int = 0) -> bool:
        if not self.cfg.ntfy_url:
            return False
        headers = {"Title": _hdr(title), "Priority": str(priority), "Tags": tags}
        if self.cfg.ntfy_token:
            headers["Authorization"] = "Bearer " + self.cfg.ntfy_token
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(self.cfg.ntfy_url, data=message.encode("utf-8"), headers=headers,
                                             method="POST")
            except ValueError as exc:
                log.warning("ntfy_url ungültig: %s", exc)
                return False
            if self._http(req):
                return True
            if attempt < retries:
                self.sleep(2 ** attempt * 2)
        return False

    # ---- ntfy ----
    def alarm(self, title: str, message: str, *, unit_id: int | None = None) -> bool:
        """Prioritaet 5, rotating_light; hoechstens alle min_repeat_minutes pro (Titel, Unit)."""
        log.error("ALARM %s: %s", title, message)
        if self._rate_limited(title, unit_id):
            return False
        return self._ntfy(title, message, 5, "rotating_light", retries=3)

    def info(self, title: str, message: str, *, priority: int = 3, tags: str = "information_source",
             unit_id: int | None = None, rate_limit: bool = True) -> bool:
        if rate_limit and self._rate_limited(title, unit_id):
            return False
        return self._ntfy(title, message, priority, tags)

    def unit_done(self, unit: Unit) -> None:
        """Sammelt fertige Units; Versand gebuendelt (Prio 2)."""
        n = int(self._get("notify:done_count") or 0) + 1
        b = int(self._get("notify:done_bytes") or 0) + unit.expected_bytes
        self._set("notify:done_count", str(n))
        self._set("notify:done_bytes", str(b))

    def flush_done(self, *, force: bool = False) -> bool:
        n = int(self._get("notify:done_count") or 0)
        if n == 0:
            return False
        last = float(self._get("notify:done_last") or 0)
        if not force and now() - last < 3600:
            return False
        b = int(self._get("notify:done_bytes") or 0)
        ok = self._ntfy("Migration: Units fertig", f"{n} Unit(s) fertig, {fmt_bytes(b)}", 2, "white_check_mark")
        self._set("notify:done_last", str(now()))
        self._set("notify:done_count", "0")
        self._set("notify:done_bytes", "0")
        return ok

    def summary(self, message: str) -> bool:
        return self._ntfy("Migration: Zusammenfassung", message, 3, "bar_chart")

    # ---- healthchecks ----
    def _hc_url(self, suffix: str = "") -> str:
        return self.cfg.healthchecks_url.rstrip("/") + suffix

    def hc_start(self) -> bool:
        return bool(self.cfg.healthchecks_url) and self._http(urllib.request.Request(self._hc_url("/start")))

    def hc_ping(self) -> bool:
        return bool(self.cfg.healthchecks_url) and self._http(urllib.request.Request(self._hc_url()))

    def hc_fail(self, body: str) -> bool:
        if not self.cfg.healthchecks_url:
            return False
        req = urllib.request.Request(self._hc_url("/fail"), data=body[:900].encode("utf-8"), method="POST")
        return self._http(req)

    def start_heartbeat(self) -> None:
        if not self.cfg.healthchecks_url or self._hb_thread:
            return
        self._hb_stop.clear()

        def loop() -> None:
            while not self._hb_stop.wait(max(1, self.cfg.heartbeat_seconds)):
                self.hc_ping()

        self._hb_thread = threading.Thread(target=loop, name="hc-heartbeat", daemon=True)
        self._hb_thread.start()

    def stop_heartbeat(self) -> None:
        self._hb_stop.set()
        if self._hb_thread:
            self._hb_thread.join(timeout=5)
            self._hb_thread = None

    def test(self) -> dict[str, bool]:
        res = {}
        res["ntfy"] = self._ntfy("skyferry Test", "Testnachricht von skyferry", 3, "white_check_mark")
        res["healthchecks"] = self.hc_ping()
        return res
