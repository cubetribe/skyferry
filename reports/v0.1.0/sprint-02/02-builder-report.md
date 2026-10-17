---
agent: builder
date: 2026-10-17
sprint: 02
version: v0.1.0
status: DONE
---

# Sprint 02 - Builder-Report: Migrations-Engine skyferry

## Files Created

(alles unter dem Write Scope)
- `skyferry/`: `__init__`, `__main__`, `cli`, `config`, `state`, `rclone`, `source`, `planner`, `verify`, `runner`, `notify`, `buffer`, `app`, `util`, `targets/{__init__,base,folder}`
- `tests/`: `helpers`, `test_config`, `test_rclone_safety`, `test_planner`, `test_verify`, `test_notify`, `test_e2e`
- `deploy/`: `skyferry.service`, `skyferry-notify-failure@.service`, `install.sh`
- `config.example.toml`, `README.md`, `.gitignore`
- `reports/v0.1.0/sprint-02/02-builder-report.md`

## Files Modified

Keine bestehenden Dateien geaendert (das Projekt war leer ausser `plans/`). Nicht angefasst: `plans/**`, `CHANGELOG.md`, `VERSION`. Kein git commit.

## Abweichungen und Ergaenzungen zur Spezifikation

- **Datenschutz-Zusatz (Koordinator):** `[notify] include_paths = false` (Default). Dann enthalten ntfy-Nachrichten und healthchecks-`/fail`-Bodies nur `Unit #42 (folder, 12,3 GB): <Meldung>`; rclone-Fehlertexte werden nicht gesendet (nur Fehlerklasse und Exit-Code). `strip_paths()` entfernt zusaetzlich alles Pfadartige. Test `PrivacyTests.test_no_paths_by_default` prueft, dass keine Drive-Namen (`Klein`, `Dokumente`, `x.txt` ...) in Pfad, Body oder Headern landen; `test_paths_when_enabled` prueft den Gegenfall.
- Neue Config-Sektion `[run]` (`max_attempts`, `poll_seconds`, `defer_temporary_minutes`, `defer_limit_minutes`) und `rclone.stats_interval`.
- Neue Spalte `units.resume_status`: `retry` setzt Units, die nach der Stufe-2-Verifikation scheiterten (VERIFIED_REMOTE/RELEASED/RENAMED), dort wieder an, statt neu herunterzuladen (Daten sind dann schon freigegeben). Alle anderen gehen auf PLANNED wie spezifiziert.
- Stufe 2 vergleicht zusaetzlich den Puffer mit einem Manifest aus Stufe 1 (`state/manifests/<id>.json`). Sonst wuerde Bitrot im Puffer zwischen den Stufen unbemerkt ins Ziel kopiert und dort als "identisch" gelten.
- Doppelte Pfade in der Quelle lassen auch die Verifikation scheitern (`duplicate_in_source`), damit ein `retry` ohne dedupe nicht stillschweigend eine Datei verliert.
- Vor `moveto` wird geprueft, dass der Zielname im Elternordner frei ist. Sonst wuerde rclone Verzeichnisse zusammenfuehren (Dir-Fallback mit Loeschen in der Quelle). Belegt -> `NEEDS_ATTENTION`.
- Pro Durchlauf bekommt jede Unit hoechstens einen Schritt (Snapshot der Status am Anfang). `run --once` laeuft Durchlaeufe, bis kein Fortschritt mehr moeglich ist.
- `install.sh` ist ueber Umgebungsvariablen umlenkbar (`SKYFERRY_HOME`, `..._CONFIG_DIR`, `..._UNIT_DIR`, `..._STATE_DIR`, `SKIP_SYSTEMCTL=1`), damit der Test auf homelab01 nichts ausserhalb von `~/skyferry-dev` anlegt.

## rclone-Verhalten (lokal gegen v1.74.3 geprueft, homelab01 hat v1.75.2)

Dokumentiert im Kopf von `skyferry/rclone.py`:
- `lsjson -R`: ein Objekt pro Zeile, `[`/`]` auf eigenen Zeilen, Komma am Zeilenende (letzte ohne). Logs auf stderr.
- Fehlendes Verzeichnis: Exit 3. Hashes nur bei Dateien, nicht bei Ordnern.
- `--use-json-log --stats 1m --stats-log-level NOTICE`: Stats als JSON mit `stats.bytes/totalBytes/transfers/totalTransfers` auf stderr.
- `moveto` auf ein Verzeichnis benennt es um; `--drive-export-formats`, `--drive-skip-shortcuts`, `--drive-acknowledge-abuse`, `--drive-root-folder-id` existieren (`rclone help flags drive`).
- Lokal klont rclone auf APFS (server-side copy), `--bwlimit` greift dort nicht. Der Kill-Test nutzt deshalb `--disable Copy`.
- Annahme Google-Docs (nicht gegen echtes Drive geprueft): Export-Endung im Namen, `Size = -1`, kein Hash. Wird als `is_gdoc` erkannt: zaehlt 0 Bytes im Plan, in der Verifikation reicht Existenz und Groesse > 0.

## Acceptance Criteria

1. Unittests gruen: erfuellt (lokal und homelab01, siehe unten).
2. E2E mit Fake-Drive (3+ Ebenen, Ordner > 1 MB geteilt, leere Dateien, Unicode/Leerzeichen/CJK): `test_full_migration` - alle 7 Units DONE, Staging leer, Ziel MD5-identisch, Quellordner mit Praefix (inkl. verschachteltem `[MIGRIERT] Gross/[MIGRIERT] teil1`), Audit `verify`-Logik OK: erfuellt.
3. Fehlertests: korrupte Puffer-Datei (`test_corrupt_buffer_file_detected_and_redownloaded`), fehlende Ziel-Datei ohne Freigabe (`test_remote_missing_file_blocks_release`, `..._retries_restage_then_succeeds`), Prozess-Kill mit SIGKILL waehrend DOWNLOADING und Fortsetzung (`test_kill_mid_run_resumes`, echter Subprozess), fehlender Marker (`test_missing_marker_stops_writing`, kein staging-Verzeichnis entsteht), `delete`/`sync`/weitere auf Source -> `SafetyError` (`test_rclone_safety`): erfuellt.
4. Notify mit lokalem http.server (ntfy-Header Title/Priority/Tags/Auth, Rate-Limit, healthchecks start/ping/fail, Netzfehler, Backoff x3, Done-Batching Prio 2): erfuellt.
5. homelab01: `python3 -m skyferry --help` laeuft, `install.sh` zweimal erfolgreich (Exit 0, idempotent, ohne Dienststart; Config 0600, Units kopiert): erfuellt. `systemctl --user daemon-reload` wurde im Test per `SKIP_SYSTEMCTL=1` uebersprungen, also nicht auf homelab01 geprueft.

## Quality Gates

- [x] `python3.12 -m unittest discover -s tests` lokal: 48/48 OK
- [x] homelab01: 48/48 OK
- [x] kein typecheck/lint-Tooling im Projekt (nur stdlib), Import und `--help` geprueft

## Tests

Tests hinzugefuegt: `tests/test_config.py`, `test_rclone_safety.py`, `test_planner.py`, `test_verify.py`, `test_notify.py`, `test_e2e.py` (48 Faelle).

## Testausgabe

Lokal (macOS, python3.12.12, rclone v1.74.3):
```
Ran 48 tests in 13.160s
OK
```
homelab01 (Python 3.12.3, rclone v1.75.2, `PATH=$HOME/.local/bin:$PATH python3 -m unittest discover -s tests -v`):
```
Ran 48 tests in 30.432s
OK
```
(Alle 48 Testnamen im Verbose-Lauf mit `ok`, u. a. test_full_migration, test_kill_mid_run_resumes, test_no_paths_by_default.)

## Offene Punkte / Annahmen

- Nicht gegen echtes Google Drive getestet: Google-Docs-Listing (Size -1), `moveto` von Ordnern per `root_folder_id`-Connection-String und ID-Erhalt nach der Umbenennung, `--fast-list` bei mehreren TB (Speicher in rclone selbst). Der Code prueft die Ordner-ID nach dem Umbenennen und meldet sonst `NEEDS_ATTENTION`. Beim ersten Drive-Test (Sprint 05, kleiner Ordner) bestaetigen.
- Der lokale Standard-`python3` auf dem Mac ist 3.9 und laeuft nicht (tomllib); Tests dort mit `python3.12`.
- Google-Docs zaehlen im Plan 0 Bytes; der Platzpuffer (Faktor 1,05 plus `reserve_bytes`) deckt das nur grob ab.
- Wurde ein Unterordner einer nicht geteilten Unit vorher separat migriert und umbenannt, wuerde er bei erneutem Download wieder mitkopiert (seltener Randfall nach manuellen Eingriffen).
- Planner-Dedup nutzt 64-Bit-Hashes der Pfade (Kollision = Fehlalarm Richtung `NEEDS_ATTENTION`).
- Der Healthchecks-Check sollte auf "laeuft nicht mehr nach Ende" abgestimmt werden: nach Abschluss pingt der Runner nicht mehr.
- ResourceWarnings in den Tests (nicht geschlossene Sockets des Test-HTTP-Servers / Popen im Test), funktional folgenlos.
