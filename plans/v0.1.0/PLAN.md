# Plan v0.1.0: Google Drive → Sync.com Migration über homelab01

Stand: 2026-10-17 · Orchestrator: Claude Code · Auftraggeber: Dennis Westermann

## Ziel

„Meine Ablage“ aus Google Drive (mehrere TB) wird über Wochen unbeaufsichtigt nach Sync.com kopiert.
Das läuft auf homelab01, mit einer externen 2-TB-Platte als Puffer. Jeder Ordner wird erst
heruntergeladen und gegen Google-MD5 geprüft, dann hochgeladen und gegen Sync.com geprüft.
Danach wird er lokal gelöscht und in Google Drive mit dem Präfix `[MIGRIERT] ` umbenannt.
Bricht etwas ab, kommt eine Push-Nachricht aufs Handy.

## Entscheidungen (mit Dennis am 2026-10-17 abgestimmt)

| Thema | Entscheidung |
|---|---|
| Sync.com-Upload | **Zuerst den Wine-Test** mit dem offiziellen Windows-Client auf homelab01. Plan B ist die Web-Panel-Automation. Sync.com hat keine API, keinen Linux-Client und kein rclone-Backend. |
| Benachrichtigung | **ntfy** (Push) für Ereignisse und **healthchecks.io** als Totmannschalter |
| Umfang | Nur „Meine Ablage“. Kein „Für mich freigegeben“, keine Shared Drives. |
| Google Docs | Export als docx/xlsx/pptx/svg. Prüfung nur auf Existenz und Größe > 0, weil Google dafür kein MD5 liefert. |
| Markierung | Fertige Ordner werden in Drive umbenannt zu `[MIGRIERT] <Name>`. **In Drive wird nie gelöscht.** |
| Werkzeug | rclone v1.75.2 in `~/.local/bin` auf homelab01, SHA256 geprüft |

## Fakten zur Umgebung

- Server (homelab01): Ubuntu 24.04, 2 Kerne, 7,6 GB RAM, rund 170 GB frei auf der NVMe. Kein sudo ohne Passwort.
- Der gesamte ausgehende Traffic läuft über ein VPN mit Kill-Switch. Fällt das VPN aus, gibt es kein Netz, und der Totmannschalter muss Alarm schlagen.
- cp.sync.com antwortet über die VPN-IP mit 403, vermutlich Bot-Schutz. Das ist für den Web-Weg relevant.
- Auf einem Desktop-Rechner läuft bereits ein Sync.com-Client (2.2.x) mit lokalem Sync-Ordner. **Der Migrationsordner muss dort per Selective Sync abgewählt werden**, sonst lädt dieser Rechner alle migrierten TB herunter.

## Architektur (Inline-Brief)

- **Python 3.12, nur Standardbibliothek**, ruft rclone als Subprozess auf. Paket `skyferry`, wird per rsync nach `~/skyferry` auf homelab01 verteilt.
- **Arbeitseinheiten (Units)** werden über die Drive-Ordner-ID adressiert, nicht über den Pfad (`gdrive,root_folder_id=<ID>:`). So bleiben Umbenennungen von Elternordnern folgenlos. Ordner, die größer als `max_unit_bytes` sind, werden rekursiv geteilt. Lose Dateien eines geteilten Ordners bilden eine eigene Unit.
- **Zustand** liegt in SQLite auf der internen NVMe (`~/.local/state/skyferry/`), nicht auf dem Puffer. Jeder Schritt ist idempotent und läuft nach einem Absturz einfach weiter.
- **Zustandsmaschine pro Unit:** `PLANNED → DOWNLOADING → DOWNLOADED → VERIFIED_LOCAL → STAGED → UPLOADED → VERIFIED_REMOTE → RELEASED → RENAMED → DONE`. Dazu kommen `FAILED` (mit Zähler der Versuche) und `NEEDS_ATTENTION` (etwa bei doppelten Dateinamen in Drive).
- **Sync.com-Anbindung ist austauschbar** über die Schnittstelle `SyncTarget` (stage / is_uploaded / remote_listing / release). Wine-Client, Web-Panel und Test-Ordner sind jeweils eigene Implementierungen.
- **Prüfung** ist eine generische Gegenüberstellung zweier Listen (rclone lsjson mit Hash):
  - Stufe 1: Drive gegen Puffer, mit MD5.
  - Stufe 2: Puffer gegen Sync.com. Wie das genau geht (Webpanel-Liste und Stichproben-Download), klärt Sprint 03.
- **Sicherheitsnetz:**
  - Der rclone-Wrapper erlaubt auf dem Drive-Remote nur lesende Befehle und `moveto` zum Umbenennen.
  - Der Puffer muss eingehängt sein und eine Markerdatei tragen. Sonst würde ein abgezogenes USB-Laufwerk die NVMe vollschreiben.
  - Lokal wird erst gelöscht, wenn Stufe 2 bestanden ist.

## Sprints

| Sprint | Inhalt | ux_gate | Status |
|---|---|---|---|
| 01 | Infrastruktur: rclone ✓, Google-Drive-Remote (OAuth durch Dennis), Inventar-Probe von „Meine Ablage“ | skip | in-progress |
| 02 | Engine: Planer, Zustand, Runner, Verifier, Benachrichtigungen, CLI, Tests mit Test-Ziel ([Sprint-Datei](sprints/sprint-02.md)) | skip | **done** |
| 03 | Wine-Spike: Läuft der Sync.com-Windows-Client headless unter Wine/Xvfb? Wie erkennt man den Upload-Abschluss? Wie gibt man lokal Platz frei, ohne in der Cloud zu löschen? Wie geht er mit Dateinamen um? | human | pending (wartet auf sudo-Installation durch Dennis) |
| 04 | `SyncTarget` für Wine-Client oder Web-Panel umsetzen, je nach Ergebnis von 03. Dazu der Webpanel-Verifier für Stufe 2. | skip | pending |
| 05 | Betrieb: Pufferplatte (ext4, Marker), systemd-User-Dienst, Healthchecks, Testlauf mit kleinem Ordner, Produktivstart | human | pending |
| 06 | Open-Source-Veröffentlichung als **skyferry** (MIT): Umbenennung, persönliche Daten entfernt, englische Doku, CI (tests, gitleaks), Push-Schutz, Ruleset, Pre-Commit-Hook, öffentliches Repo `cubetribe/skyferry` | skip | **done** |

## Nicht-Ziele

- Nichts in Google Drive löschen. Das Aufräumen macht Dennis selbst, wenn alles fertig ist.
- Keine laufende Synchronisation von Änderungen, die nach der Migration eines Ordners in Drive passieren. Ein späterer Delta-Lauf ist möglich, aber ein eigener Auftrag.
- Kein „Für mich freigegeben“, keine Shared Drives.

## Risiken

1. **Wine-Client läuft nicht oder ist instabil.** Dann greift Plan B, die Web-Panel-Automation. Dort droht der 403-Bot-Schutz über die VPN-IP, eventuell ist eine Split-Tunnel-Ausnahme nötig.
2. **Lokalen Platz freigeben** ist beim Desktop-Client nur über Selective Sync oder den Vault möglich, also über die GUI. Lokal löschen würde auch in der Cloud löschen. Das klärt Sprint 03.
3. **Doppelte Dateinamen in Drive.** Drive erlaubt zwei gleichnamige Dateien in einem Ordner, rclone kopiert davon nur eine. Der Planer erkennt das und setzt die Unit auf `NEEDS_ATTENTION`.
4. **Dateinamen, die unter Windows ungültig sind** (`: * ? " < > |`, Punkt oder Leerzeichen am Ende), könnten im Wine-Client scheitern. Gegenmaßnahme: rclone `--local-encoding` im Windows-Profil.
5. **OAuth-Token läuft ab:** Eine eigene Google-Client-ID im Modus „Testing“ verliert ihr Token nach 7 Tagen. Die App muss deshalb auf „In Produktion“ gestellt werden.

## Routing-Log

- Sprint 06 (2026-10-17, Wunsch des Auftraggebers): Projekt wird öffentlich als `skyferry`. Name und Lizenz (MIT) hat Dennis gewählt.
  - Paket, Pfade, Marker und Einhängepunkt (`/mnt/skyferry`) wurden mechanisch umbenannt, die Tests bestätigen es (48/48).
  - Die Doku schreibt @scribe.
  - `.github/workflows/` ist eine Sicherheitsfläche, deshalb prüft @ci-security-guardian vor dem Push.
  - Der unveröffentlichte erste Commit wird vor dem Push neu geschrieben, um eine persönliche E-Mail-Adresse aus der Historie zu entfernen.
  - Ergebnis: veröffentlicht als <https://github.com/cubetribe/skyferry>, MIT.
    - Die Historie besteht aus einem sauberen Commit. gitleaks und eine Suche nach persönlichen Daten über die ganze Historie fanden nichts.
    - Die CI-Läufe secret-scan (5/5 Canaries erkannt, Negativkontrolle sauber, keine Leaks) und tests (48/48) waren beim ersten Push grün.
    - Ruleset `protect-main` ist aktiv: deletion, non_fast_forward, Pflicht-Checks `gitleaks` und `unittest`, Admin-Bypass.
    - Secret Scanning und Push Protection sind an, ebenso Private Vulnerability Reporting und die Freigabepflicht für Fork-PRs externer Beitragender.
    - Einstiegs-Issues: #1 (Sync.com-Ziel, help wanted) und #2 (englische Oberfläche, good first issue).
    - Review-Bericht: `reports/v0.1.0/sprint-06/03-ci-security-guardian-report.md`.

- @architect übersprungen: Inline-Brief oben genügt. Das Projekt ist neu und klein, ein einzelner Bereich.
- @api-guardian übersprungen: keine öffentliche API und keine Typverträge zu Konsumenten.
- @security: Prüfung des Umgangs mit Zugangsdaten (rclone.conf, ntfy-Topic, Healthchecks-URL) ist für Sprint 05 vor dem Produktivstart vorgesehen.
- Sprint-Dateien: Nur Sprint 02 hat eine eigene Datei. 01, 03, 04 und 05 sind Ops- bzw. Spike-Sprints und stehen hier kompakt. Bewusste Vereinfachung gegenüber ADR-004.
- VERSION gibt es noch nicht, Projektversion v0.1.0. CHANGELOG mit `[Unreleased]` hat der Orchestrator bei der Integration von Sprint 02 direkt angelegt, ohne @scribe: kleiner Einzeleintrag, ein Schreiber.
- Sprint 02: kein zweiter Modell-Review. Die deterministischen Tests liefen nach (48/48 lokal und auf homelab01), dazu eine manuelle Durchsicht der sicherheitskritischen Pfade. `/code-review` ist vor dem Produktivstart in Sprint 05 vorgesehen, wenn das echte Sync.com-Ziel steht.
