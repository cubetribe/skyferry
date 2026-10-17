---
sprint: 02
title: Migrations-Engine, Verifier, Benachrichtigungen
status: done
ux_gate: skip
version_relevance: minor
owner: "@builder"
---

# Sprint 02 – Migrations-Engine

## Goal

Ein Python-Paket `skyferry`, das Google Drive („Meine Ablage“) ordnerweise über einen Puffer
in ein austauschbares `SyncTarget` kopiert. Jeder Schritt wird nachweisbar verifiziert. Es ist
wiederaufnehmbar nach Absturz, Neustart oder abgezogener Platte und meldet Probleme per ntfy und
healthchecks.io. Ende-zu-Ende testbar **ohne** Google und **ohne** Sync.com. Dafür ersetzt ein
lokaler Ordner Drive, und `FolderTarget` steht für Sync.com.

## Scope

### Laufzeit und Abhängigkeiten
- Python 3.12, **nur Standardbibliothek** (`tomllib`, `sqlite3`, `subprocess`, `json`, `hashlib`,
  `urllib.request`, `argparse`, `logging`, `signal`, `shutil`, `os`, `pathlib`, `dataclasses`).
- rclone ≥ 1.75 als externes Binary. Der Pfad ist konfigurierbar, Default `rclone` aus PATH.
- Tests mit `unittest` (stdlib). Für Integrationstests ruft der Test echtes rclone mit dem
  **local**-Backend auf. Fehlt rclone, wird übersprungen.

### Paketstruktur (Write Scope, siehe unten)
```
skyferry/
  __init__.py, __main__.py        # python -m skyferry <cmd>
  cli.py                          # argparse-Subcommands
  config.py                       # TOML laden + validieren, Dataclasses
  state.py                        # SQLite-Schema, Migrationen, Repository-Funktionen
  rclone.py                       # Wrapper + Sicherheitsnetz
  planner.py                      # Inventar + Unit-Planung
  verify.py                       # Listing-Vergleich + Reports
  runner.py                       # Zustandsmaschine / Hauptschleife
  notify.py                       # ntfy + healthchecks, Rate-Limit
  buffer.py                       # Puffer-Guard (Mount + Marker + Freiplatz)
  targets/__init__.py, base.py    # SyncTarget-Protokoll
  targets/folder.py               # FolderTarget (Test-/Generic-Ordnerziel)
tests/ ...
config.example.toml
deploy/skyferry.service, deploy/skyferry-notify-failure@.service, deploy/install.sh
README.md (kurz: Installation, Befehle, Zustände, Sicherheitsgarantien)
```

### Konfiguration (`config.example.toml`, echte Datei: `~/.config/skyferry/config.toml`, 0600)
```toml
[source]
remote = "gdrive"                 # rclone-Remote-Name (Typ drive)
root_folder_id = ""               # leer = Meine Ablage (Root). Für Tests: lokaler Pfad via source.local_root
local_root = ""                   # NUR für Tests: wenn gesetzt, wird statt Drive ein lokaler Ordner benutzt
export_formats = "docx,xlsx,pptx,svg"
acknowledge_abuse = true
skip_shortcuts = true
done_prefix = "[MIGRIERT] "

[buffer]
mount = "/mnt/puffer"             # muss os.path.ismount sein (abschaltbar für Tests: require_mount=false)
require_mount = true
marker_file = ".skyferry-puffer"  # muss im mount-Root existieren
staging_dir = "staging"           # relativ zu mount
reserve_bytes = 50_000_000_000    # nie unter diese Freigrenze schreiben
max_unit_bytes = 300_000_000_000  # größere Ordner werden geteilt

[rclone]
binary = "rclone"
config = ""                       # leer = rclone-Default
transfers = 4
checkers = 8
tpslimit = 10
bwlimit = ""                      # z.B. "08:00,20M 23:00,off"
retries = 5
low_level_retries = 20
local_encoding = ""               # leer = rclone-Default; Wine-Profil kommt in Sprint 04
extra_args = []

[target]
type = "folder"                   # folder | (später: wine | webpanel)
path = ""                         # FolderTarget: Zielordner (simuliert Sync.com-Ordner)
remote_subdir = "GDrive-Archiv"   # Unterordner im Ziel, darunter der Original-Drive-Pfad

[notify]
ntfy_url = ""                     # z.B. https://ntfy.sh/<geheimes-topic>; leer = aus
ntfy_token = ""                   # optional
healthchecks_url = ""             # z.B. https://hc-ping.com/<uuid>; leer = aus
heartbeat_seconds = 300
stall_hours = 6                   # kein Fortschritt so lange → Alarm
daily_summary_hour = 20           # lokale Zeit; -1 = aus
min_repeat_minutes = 60           # gleiche Meldung frühestens wieder nach X Min

[state]
dir = "~/.local/state/skyferry"  # state.db, logs/, reports/
```

### Datenmodell (SQLite, WAL, `PRAGMA foreign_keys=ON`, Schema-Version-Tabelle)
- `units`: `id` (int pk), `drive_id` (Ordner-ID; bei Tests Pfad), `parent_drive_id`,
  `drive_path` (Originalpfad relativ zu Meine Ablage, ohne Prefix), `name`,
  `kind` (`folder` | `loose_files`), `parent_unit_id` (für geteilte Ordner: Rollup-Umbenennung),
  `is_split_container` (bool: Ordner wurde geteilt, eigene Unit nur für Umbenennung),
  `expected_files`, `expected_bytes`, `gdocs_count`, `status`, `attempts`, `last_error`,
  `created_at`, `updated_at`, `started_at`, `finished_at`.
- `events`: `id`, `unit_id` (nullable), `ts`, `level`, `kind`, `message`, `data_json`. Das ist
  das Audit-Trail, nur anhängen.
- `kv`: Laufzeitwerte (letzte Meldungen für das Rate-Limit, letzte Fortschrittszeit, Planversion).
- Statusübergänge **nur** über eine Funktion `transition(unit, from_states, to_state, ...)`.
  Sie prüft den Ausgangszustand atomar (UPDATE … WHERE status IN …) und schreibt ein Event.

### Planer (`skyferry plan`)
- Streamt `rclone lsjson -R --fast-list --hash --hash-type md5 --no-mimetype` mit Drive-Flags
  (`--drive-export-formats`, `--drive-skip-shortcuts`, `--drive-acknowledge-abuse`) **zeilenweise**.
  Kein `json.load` der Gesamtausgabe: Das können Millionen Einträge sein bei 8 GB RAM. rclone
  gibt ein Objekt pro Zeile aus, mit `[`/`]` und einem abschließenden Komma.
- Erfasst pro Eintrag Path, Name, Size, IsDir, ID, MD5 und, falls verfügbar, MimeType oder die
  Kennzeichnung als Google-Doc. Google-Docs erkennt man an der Größe -1 bzw. einer Export-Endung
  ohne Hash.
- Baut einen Baum, aggregiert Bytes und Dateien je Ordner und plant die Units:
  - Ordner ≤ `max_unit_bytes` → eine `folder`-Unit.
  - Größer → `is_split_container`-Unit. Jeder Unterordner wird rekursiv geplant, und die
    direkten Dateien bilden eine `loose_files`-Unit. Eine einzelne Datei > `max_unit_bytes` ist
    erlaubt (die Unit ist dann eben so groß) und erzeugt ein Warn-Event.
  - Dateien direkt in der Root → eine `loose_files`-Unit „(Root)“. Die wird nie umbenannt.
- Überspringt Ordner, deren Name mit `done_prefix` beginnt (bereits migriert).
- **Doppelte Pfade**, also gleicher Name im gleichen Ordner, setzen die betroffene Unit auf
  `NEEDS_ATTENTION` mit Liste im Event. Ein Hinweis auf `rclone dedupe` geht nur in den Report,
  **nicht ausführen**.
- Idempotent: Erneutes `plan` ergänzt neue Ordner und ändert keine bestehenden Units, die schon
  über `PLANNED` hinaus sind. `--dry-run` gibt nur die Statistik aus.
- Ausgabe: Statistik (Units, Bytes, Dateien, Google-Docs, Duplikate, Top-20 größte Units) auf
  stdout und als Markdown-Report unter `state/reports/plan-<ts>.md`.

### rclone-Wrapper (`rclone.py`)
- Einheitlicher Aufruf mit globalen Flags aus der Config, `--use-json-log` und `--stats 1m`. Bei
  `copy` werden die Stats geparst und als Fortschritt für die Stall-Erkennung geschrieben.
- **Sicherheitsnetz:**
  - Auf dem Source-Remote sind nur `lsjson`, `lsf`, `size`, `copy` (Source → lokal) und `moveto`
    erlaubt. `moveto` geht nur für die Umbenennung eines Ordners innerhalb desselben Elternordners,
    der neue Name muss `done_prefix + alter Name` sein.
  - `delete`, `purge`, `sync`, `move`, `rmdir`, `rmdirs`, `dedupe`, `cleanup` sowie `copy` oder
    `sync` *auf* den Source-Remote lösen eine `SafetyError` aus.
  - Unit-Tests decken das ab.
- Adressierung über die ID: `"{remote},root_folder_id={id}:"`. Im Testmodus (`local_root`) über
  den Pfad.
- Exit-Codes von rclone werden gemappt: 0 ok, 1/2 Fehler, 3/4 nicht gefunden, 5 temporär,
  6 weniger schwer, 7 fatal, 8 Transferlimit, 9 nichts übertragen. 5 und 8 heißen
  zurückstellen und später erneut versuchen.

### Verifier (`verify.py`, CLI `skyferry verify <unit-id> --stage local|remote`)
- `Listing` = dict `relpath -> Entry(size:int|None, md5:str|None, is_gdoc:bool)`.
- Quellen:
  - Drive bzw. Test-Source per `rclone lsjson -R --hash --files-only`.
  - Lokaler Puffer per `rclone lsjson -R --hash --hash-type md5 --files-only` auf dem lokalen
    Pfad. Das liest alle Daten, so ist es gewollt.
  - `SyncTarget.remote_listing(unit)`.
- Vergleich: fehlend, Größe abweichend (wenn beide bekannt), MD5 abweichend (wenn beide
  vorhanden), zusätzlich vorhanden (nur Warnung), Google-Doc ohne Prüfmöglichkeit (gilt als ok,
  wenn die Datei existiert und Größe > 0 hat), leere Dateien (ok).
- Ergebnis ist `VerifyResult(ok, counts, problems[])`. Gespeichert wird es als JSON und als
  Markdown unter `state/reports/unit-<id>-<stage>-<ts>.{json,md}`. `ok` ist nur True bei
  0 fehlend und 0 Abweichungen.
- `skyferry verify --all-done` prüft fertige Units erneut gegen das Ziel (Audit-Lauf).

### SyncTarget-Protokoll (`targets/base.py`)
```python
class SyncTarget(Protocol):
    name: str
    def stage(self, unit, local_dir: Path) -> None: ...        # Daten an das Ziel übergeben (FolderTarget: kopieren/verschieben)
    def upload_state(self, unit) -> UploadState: ...           # PENDING | IN_PROGRESS(progress) | COMPLETE | ERROR(msg)
    def remote_listing(self, unit) -> Listing: ...             # unabhängige Sicht des Ziels für Verifikationsstufe 2
    def release(self, unit, local_dir: Path) -> ReleaseState:  # lokalen Platz freigeben OHNE Ziel zu löschen; DONE | WAITING(msg)
```
- `FolderTarget`: `stage` kopiert mit `rclone copy` vom Puffer nach `<path>/<remote_subdir>/<drive_path>`.
  `upload_state` ist sofort COMPLETE. `remote_listing` ist lsjson mit MD5 auf dem Zielpfad.
  `release` löscht den Puffer-Unit-Ordner.
- Der Runner muss mit `WAITING` umgehen können. Das wird beim Wine-Client nötig sein, wenn die
  Freigabe einen GUI-Schritt durch Dennis braucht: Status bleibt `VERIFIED_REMOTE`, einmalige
  Meldung „bitte freigeben“, erneuter Versuch beim nächsten Durchlauf.

### Runner (`skyferry run`, `--once` für einen Durchlauf, `--max-units N`)
- Ein-Instanz-Lock (`fcntl.flock` auf `state/run.lock`). SIGTERM und SIGINT führen zu einem
  sauberen Stopp nach dem laufenden Subprozess, der Subprozess bekommt SIGTERM.
- Jede Iteration:
  1. Puffer-Guard prüfen. Fehlt der Mount oder Marker: Alarm und Pause, nichts schreiben.
  2. Units in Zwischenzuständen weiterbringen. Reihenfolge: RELEASE → RENAME → VERIFY_REMOTE →
     UPLOAD-Status → STAGE → VERIFY_LOCAL.
  3. Erst danach eine neue Unit herunterladen, wenn `freier Platz - reserve_bytes ≥
     expected_bytes * 1.05`. Es läuft immer nur ein Download. Upload und Download dürfen sich
     überlappen (Pipeline), solange Platz da ist.
- **Download:** `rclone copy <source-unit> <staging>/<unit-id>/` mit `--checksum`. Bei
  `loose_files` zusätzlich `--max-depth 1`.
- **VERIFY_LOCAL:** Ein Fehler setzt `attempts+1` und zurück auf `PLANNED`. Fehlende Dateien
  werden beim nächsten `copy` ergänzt, abweichende Dateien gelöscht und neu geladen. Nach
  `max_attempts` (Config, Default 3) folgt `FAILED` und ein Alarm.
- **RENAME** (nur `kind=folder`, nicht Root): `moveto` im Elternordner auf `done_prefix+name`.
  Danach per lsjson prüfen, dass die Ordner-ID gleich geblieben ist. Wenn alle Kinder eines
  `is_split_container` auf DONE stehen, wird der Container selbst umbenannt und auf DONE
  gesetzt.
- Fortschritt (Bytes, Dateien) wird in `kv` geschrieben. Kommt seit `stall_hours` kein
  Fortschritt, gibt es einen Alarm, sofern noch Arbeit offen ist.
- Ist alles DONE oder FAILED oder NEEDS_ATTENTION: Abschlussmeldung mit Zusammenfassung, dann
  beenden mit Exit 0. systemd startet den Dienst nicht neu (`Restart=on-failure`).

### Benachrichtigungen (`notify.py`)
- ntfy: POST an `ntfy_url` mit den Headern `Title`, `Priority` (1–5) und `Tags`. Alarm =
  Priorität 5 mit Tag `rotating_light`. Unit fertig = Priorität 2, gesammelt höchstens einmal pro
  Stunde. Tageszusammenfassung = Priorität 3.
- healthchecks:
  - `GET <url>/start` beim Start.
  - `GET <url>` alle `heartbeat_seconds`, auch während langer rclone-Läufe über einen
    Heartbeat-Thread.
  - `GET <url>/fail` mit Fehlertext als Body bei FAILED oder Absturz.
- Rate-Limit über `kv`: dieselbe Meldung (Hash über Titel und Unit) frühestens wieder nach
  `min_repeat_minutes`.
- Netzwerkfehler beim Benachrichtigen dürfen den Runner **nie** abbrechen. Sie werden geloggt und
  nur bei Alarmen dreimal mit Backoff wiederholt.
- `skyferry notify-test` schickt eine Testnachricht an beide Kanäle.

### Weitere CLI-Befehle
- `status`: Units je Status, Bytes fertig und gesamt, Durchsatz der letzten 24 h, ETA, letzte 10
  Events, aktive Unit mit Fortschritt. `--json`.
- `units [--status X]`: Liste.
- `retry <unit-id>|--failed`: FAILED oder NEEDS_ATTENTION zurück auf PLANNED, `attempts=0`.
- `skip <unit-id> --reason`: auf SKIPPED setzen. Eigener Endzustand, wird nicht umbenannt.
- `events [--unit] [--tail N]`.

### Betrieb (`deploy/`)
- `skyferry.service` (systemd **user** unit):
  - `ExecStart=/usr/bin/python3 -m skyferry run`
  - `WorkingDirectory=%h/skyferry`, `Restart=on-failure`, `RestartSec=300`
  - `OnFailure=skyferry-notify-failure@%n.service`
  - `Environment=PATH=%h/.local/bin:/usr/bin:/bin`
  - `Nice=10`, `IOSchedulingClass=idle`
- `skyferry-notify-failure@.service` ruft `python3 -m skyferry notify-alarm "Dienst %i
  abgestürzt"` auf.
- `install.sh`, idempotent, ohne sudo: Code nach `~/skyferry` kopieren (Aufruf vom Mac per
  rsync, das Skript läuft dann remote), Config-Vorlage anlegen, falls keine existiert (chmod 600),
  Unit-Dateien nach `~/.config/systemd/user/`, `systemctl --user daemon-reload`. Den Dienst
  **nicht** automatisch starten. Hinweis auf `loginctl enable-linger` ausgeben.

## Non-Goals
- Keine Wine- oder Web-Panel-Implementierung. Das ist Sprint 04, nur das Protokoll muss passen.
- Kein Löschen in Drive, nie. Keine Delta-Synchronisation.
- Keine Abhängigkeiten außerhalb der Standardbibliothek. Keine Dienstinstallation mit sudo.

## Write Scope (exklusiv @builder)
`skyferry/**`, `tests/**`, `deploy/**`, `config.example.toml`, `README.md`, `.gitignore`,
`reports/v0.1.0/sprint-02/02-builder-report.md`.
**Nicht anfassen:** `plans/**`, `CHANGELOG.md`, `VERSION`.

## Risks
- Speicherbedarf des Planers bei Millionen Dateien. Daher Streaming und kompakte Strukturen
  (Tupel oder `__slots__`).
- Das Format der lsjson-Ausgabe von Google-Docs (Size -1, Endung) zuerst am echten rclone-Verhalten
  prüfen, mit dem local-Backend und den rclone-Docs. Im Code dokumentieren, was angenommen wird.

## Acceptance Criteria
1. `python3 -m unittest discover -s tests` ist grün, lokal auf macOS mit rclone, falls installiert,
   sonst werden die Integrationstests übersprungen, und auf homelab01 mit `~/.local/bin/rclone`.
2. Ende-zu-Ende-Test: Ein Fake-Drive (lokaler Ordner mit ≥ 3 Ebenen, einem Ordner > `max_unit_bytes`
   im Test, z. B. 1 MB, leeren Dateien, Unicode-Namen und Leerzeichen) wird mit `FolderTarget`
   vollständig migriert. Alle Units landen auf DONE, die Puffer-Unit-Ordner sind gelöscht, die
   Zielinhalte sind MD5-identisch, und die Ordner in der Fake-Quelle tragen den Präfix.
3. Fehlertests:
   - Eine korrupte Datei im Puffer wird von VERIFY_LOCAL erkannt und neu geladen.
   - Eine im Ziel fehlende Datei lässt VERIFY_REMOTE scheitern, es wird **nicht** freigegeben.
   - Ein Abbruch mitten im Lauf (Prozess töten) wird beim nächsten `run` sauber fortgesetzt.
   - Ein fehlender Puffer-Marker stoppt das Schreiben.
   - Der Wrapper blockiert `delete` und `sync` auf der Source.
4. `notify` wird mit einem lokalen HTTP-Testserver (http.server im Test) für ntfy und Healthchecks
   geprüft, inklusive Rate-Limit.
5. Auf homelab01: `python3 -m skyferry --help` läuft, `install.sh` ist erfolgreich (ohne
   Dienststart).

## Test Strategy
Unit-Tests für Planer (synthetische lsjson-Zeilen inkl. Google-Doc- und Duplikat-Fällen), Verifier,
Zustandsübergänge und Safety-Wrapper. Integrationstests mit echtem rclone local-Backend in
`tempfile`-Verzeichnissen. Kein Netz in den Tests.

## Changelog Note
„Erste Migrations-Engine skyferry: ordnerweise Drive→Puffer→Ziel-Migration mit zweistufiger
Verifikation, Umbenennung als Fertig-Marker, ntfy-/healthchecks-Benachrichtigung.“

## Result

Integriert am 2026-10-17. Builder-Report: `reports/v0.1.0/sprint-02/02-builder-report.md`.

- Acceptance Criteria 1–5 erfüllt. Der Orchestrator hat die Tests selbst nachgeprüft: 48/48 OK lokal (Python 3.12, rclone 1.74.3) und auf homelab01 (Python 3.12.3, rclone 1.75.2).
- Datenschutz-Zusatz `notify.include_paths=false` umgesetzt und getestet.
- Ergänzungen über die Spezifikation hinaus:
  - Manifest-Abgleich in Stufe 2, gegen Bitrot im Puffer.
  - Freiprüfung des Zielnamens vor `moveto`.
  - `resume_status` für `retry` nach Stufe 2.
- Manuelle Prüfung der sicherheitskritischen Pfade durch den Orchestrator (rclone-Sicherheitsnetz, Umbenennen, Freigabe, Puffer-Guard): keine Befunde.
- Offen und erst am echten Drive zu bestätigen (Sprint 01/05):
  - wie Google-Docs im Listing erscheinen
  - ob `moveto` per `root_folder_id` die Ordner-ID erhält
  - Speicherbedarf von `--fast-list` bei mehreren TB
  - ob nicht exportierbare Google-Formate (Forms, Sites) im Listing überhaupt auftauchen; Inventar mit `--drive-show-all-gdocs` gegenprüfen
- Bekannte Eigenheit: Units im Status FAILED behalten ihre Pufferdaten. Das ist gut für die Diagnose, belegt aber Platz, bis `retry` oder `skip` greift.
