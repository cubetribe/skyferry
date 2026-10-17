# Einrichtung

Diese Anleitung führt von einem nackten Linux-Server bis zu einem unbeaufsichtigten skyferry-Lauf. Sie ist für einen Heimserver oder Homelab-Rechner geschrieben, funktioniert aber auf jeder Linux-Maschine mit systemd und einem USB-Anschluss.

Die englische Fassung steht in [docs/SETUP.md](../SETUP.md).

Einige Schritte brauchen ein Passwort, eine Anmeldung oder deine Zustimmung und lassen sich deshalb nicht skripten. Sie sind mit **(manuell)** markiert.

## Übersicht

| # | Schritt | Braucht |
|---|---|---|
| 1 | Server-Pakete (jetzt optional, für den Sync.com-Versuch nötig) | sudo, einmalig |
| 2 | Eigene Google-OAuth-Client-ID | Browser, Google-Konto |
| 3 | Google Drive mit dem Server verbinden | Browser auf dem Laptop |
| 4 | skyferry auf dem Server installieren | SSH |
| 5 | ntfy-App und ein nicht erratbares Topic | Handy |
| 6 | healthchecks.io-Check | Browser |
| 7 | Puffer-Festplatte vorbereiten | sudo, Bestätigung per Tippen |
| 8 | skyferry konfigurieren | SSH |
| 9 | Planen, Trockenlauf, überwachter Lauf, dann Dienst aktivieren | SSH |
| 10 | Desktop-Sync-Clients: Migrationsordner abwählen | Desktop-App |

Überall steht `<server>` für den SSH-Host-Alias deines Servers und `<SERIAL>` für die Seriennummer der Puffer-Festplatte.

---

## 1. Server-Pakete (jetzt optional)

Die Migrations-Engine braucht nur Python 3.12 und rclone ab Version 1.75. Wine und die headless X-Werkzeuge werden nur für die Sync.com-Versuche in `spike/wine/` gebraucht. Das Skript installiert außerdem `sqlite3` und `smartmontools`, nützlich zur Kontrolle und für den Zustand der Platte.

```bash
rsync -a setup/ <server>:~/skyferry-setup/
ssh -t <server> 'bash ~/skyferry-setup/homelab-packages.sh'
```

Das Skript fragt einmal nach dem sudo-Passwort. Es ist idempotent, kann also gefahrlos erneut laufen.

Prüfen, ob Python und rclone vorhanden sind:

```bash
ssh <server> 'python3 --version; rclone version | head -1'
```

Fehlt rclone, installiere eine aktuelle Version nach `~/.local/bin` (siehe <https://rclone.org/install/>). Die systemd-Unit ergänzt `~/.local/bin` im `PATH`.

---

## 2. Eigene Google-OAuth-Client-ID anlegen (empfohlen)

rclone bringt eine gemeinsame Standard-Client-ID mit, die alle rclone-Nutzer weltweit teilen. Bei großen Transfers greifen Googles Limits pro Client schnell (`rateLimitExceeded`). Eine eigene Client-ID vermeidet das.

1. Öffne die [Google Cloud Console](https://console.cloud.google.com/) und lege ein neues Projekt an, zum Beispiel `skyferry`.
2. Gehe zu **APIs & Dienste → Bibliothek**, suche **Google Drive API** und aktiviere sie.
3. Richte den Zustimmungsbildschirm ein (in neueren Konsolen: **Google Auth Platform**):
   - Nutzertyp **Extern**
   - Ein beliebiger App-Name
   - Deine eigene E-Mail-Adresse als Support- und Entwickler-Kontakt
4. **Wichtig: App veröffentlichen.** Klicke unter **Zielgruppe** (oder **Veröffentlichungsstatus**) auf **App veröffentlichen**, sodass sie **In Produktion** ist.

   > **Achtung:** Im Status „Testing“ läuft das Refresh-Token von Google nach **7 Tagen** ab. Die Migration würde dann stillschweigend stehen bleiben. Für die eigene Nutzung ist keine Verifizierung durch Google nötig. Beim Anmelden kann eine Warnung „App nicht verifiziert“ erscheinen. Klicke dann auf **Erweitert → Weiter**.

5. Gehe zu **Clients** (oder **Anmeldedaten**) → **OAuth-Client-ID erstellen** → Anwendungstyp **Desktop-App**. Halte Client-ID und Client-Secret für Schritt 3 bereit.

Du kannst diesen Schritt überspringen und die Standard-ID von rclone verwenden. Drücke dann bei Schritt 3 einfach Enter, wenn nach der Client-ID gefragt wird.

---

## 3. Google Drive mit dem Server verbinden (manuell)

Führe dies auf deinem Laptop im Projektordner aus:

```bash
./setup/gdrive-connect.sh <server>
```

Das Skript:

1. prüft, ob auf dem Server noch kein Remote `gdrive` existiert,
2. fragt nach Client-ID und Client-Secret aus Schritt 2 (leer lassen für die rclone-Standard-ID),
3. öffnet einen SSH-Tunnel und zeigt einen Link wie `http://127.0.0.1:53682/auth?state=…`,
4. lässt dich diesen Link **im Browser auf deinem Laptop** öffnen, dich mit deinem Google-Konto anmelden und den Zugriff erlauben. Der OAuth-Rücksprung läuft durch den Tunnel zu rclone auf dem Server,
5. speichert das Token nur in `~/.config/rclone/rclone.conf` auf dem Server (Rechte `0600`) und zeigt dir den Speicherstand des Drive-Kontos.

Zum Neuanfang das Remote auf dem Server löschen und das Skript erneut starten:

```bash
ssh <server> '~/.local/bin/rclone config delete gdrive'
```

---

## 4. skyferry auf dem Server installieren

Vom Projektordner auf deinem Laptop aus:

```bash
rsync -a --delete --exclude .git --exclude reports --exclude plans ./ <server>:~/skyferry/
ssh <server> 'bash ~/skyferry/deploy/install.sh'
```

`install.sh` braucht kein sudo. Es ist idempotent und

- kopiert das Paket nach `~/skyferry`,
- legt `~/.config/skyferry/config.toml` aus der Vorlage an (Rechte `0600`), falls sie noch nicht existiert; eine vorhandene Konfiguration wird nie überschrieben,
- legt das Zustandsverzeichnis `~/.local/state/skyferry` an,
- installiert die systemd-User-Units `skyferry.service` und `skyferry-notify-failure@.service`,
- prüft, ob `python3 -m skyferry --help` läuft.

Der Dienst wird dabei **nicht** gestartet.

Damit der User-Dienst auch ohne aktive Anmeldung läuft:

```bash
ssh <server> 'loginctl enable-linger $USER'
```

Je nach System kann dafür ein Administrator nötig sein. Ohne Lingering stoppt der Dienst, sobald du dich abmeldest.

---

## 5. ntfy-App und ein nicht erratbares Topic (manuell)

[ntfy](https://ntfy.sh) liefert Push-Benachrichtigungen aufs Handy. Du brauchst dafür kein Konto.

1. Installiere die App **ntfy** (iOS App Store oder Google Play).
2. Wähle einen Topic-Namen, den niemand erraten kann, zum Beispiel eine lange Zufallszeichenkette. Erzeugen lässt sich einer so:

   ```bash
   python3 -c "import secrets; print('skyferry-' + secrets.token_urlsafe(18))"
   ```

3. Tippe in der App auf **+**, gib den Server `ntfy.sh` und dein Topic ein und abonniere es.
4. Trage die vollständige URL später in Schritt 8 in die Konfiguration ein: `ntfy_url = "https://ntfy.sh/<dein-topic>"`.

**Behandle das Topic wie ein Passwort.** Wer es kennt, kann deine Benachrichtigungen mitlesen. skyferry sendet deshalb standardmäßig keine Ordner- oder Dateinamen (`notify.include_paths = false`). Die Nachrichten enthalten nur Unit-Nummern, Größen, Status und Fehlerklassen.

Wenn du ntfy selbst betreibst, verwende deine eigene Server-URL und setze, falls du eine Zugriffskontrolle aktiviert hast, `ntfy_token`.

---

## 6. healthchecks.io als Totmannschalter (manuell)

[healthchecks.io](https://healthchecks.io) meldet sich, wenn der Server nichts mehr von sich hören lässt, etwa nach einem Stromausfall, einem Absturz oder einem toten Dienst.

1. Lege ein kostenloses Konto auf <https://healthchecks.io> an.
2. Lege einen neuen Check mit dem Namen `skyferry` an:
   - Zeitplan: **Simple**
   - **Period: 5 Minuten**
   - **Grace Time: 30 Minuten**
3. Füge unter **Integrations** ntfy hinzu, mit demselben Topic wie in Schritt 5. E-Mail ist meist schon aktiv.
4. Kopiere die **Ping-URL** (`https://hc-ping.com/<uuid>`). Du trägst sie in Schritt 8 ein.

skyferry sendet alle `notify.heartbeat_seconds` (Standard 300 s, passend zur Period von 5 Minuten) ein Heartbeat, beim Start eines Laufs `/start` und bei fatalen Fehlern `/fail`.

---

## 7. Puffer-Festplatte vorbereiten (manuell)

Der Puffer ist eine externe USB-Festplatte. Daten werden dorthin geladen, geprüft, an das Ziel übergeben und danach wieder von der Platte gelöscht. Sie sollte groß genug für deine größte Unit (`buffer.max_unit_bytes`, Standard 300 GB) plus etwas Reserve sein.

> **Achtung:** Das Skript **löscht die Festplatte vollständig**. Verwende nur eine Platte, auf der nichts liegt, was du behalten willst. Prüfe die Platte vorher. Das Skript zeigt dir den aktuellen Inhalt und bricht ab, wenn du nicht das Bestätigungswort `LOESCHEN` tippst.

1. Schließe die Platte an den Server an und ermittle ihre Seriennummer:

   ```bash
   ssh <server> 'lsblk -o NAME,SERIAL,TRAN,SIZE'
   ```

   Die Zeile mit `TRAN` gleich `usb` ist deine Platte. Notiere den Wert in `SERIAL`.

2. Kopiere die Setup-Skripte und starte das Puffer-Skript mit sudo:

   ```bash
   rsync -a setup/ <server>:~/skyferry-setup/
   ssh -t <server> 'sudo bash ~/skyferry-setup/buffer-disk.sh <SERIAL> $USER'
   ```

Was das Skript tut:

- Es sucht die Platte **über die Seriennummer**, daher spielt ein wechselnder Gerätename wie `/dev/sdb` keine Rolle.
- Es verweigert alles, was keine USB-Platte ist, eingehängt ist oder LVM-, RAID- oder Swap-Daten enthält.
- Es legt eine GPT-Partition mit ext4 an (Label `skyferry-buffer`).
- Es schreibt einen Eintrag in `/etc/fstab` per UUID mit `nofail`, sodass der Server auch ohne Platte startet.
- Es hängt die Platte unter `/mnt/skyferry` ein und macht den **leeren** Einhängepunkt unveränderlich (`chattr +i`). Ist die Platte nicht eingehängt, kann nichts aus Versehen dorthin geschrieben werden, und die System-SSD läuft nicht voll.
- Es legt die Markerdatei `.skyferry-buffer` und den Ordner `staging/` an, beides gehört deinem Benutzer.

Ergebnis prüfen:

```bash
ssh <server> 'df -h /mnt/skyferry; ls -la /mnt/skyferry'
```

skyferry startet nicht, wenn Einhängung oder Markerdatei fehlen.

---

## 8. skyferry konfigurieren

Auf dem Server die Vorlage ansehen und anpassen:

```bash
ssh -t <server> 'nano ~/.config/skyferry/config.toml'
```

Diese Einstellungen solltest du zuerst prüfen:

| Abschnitt | Schlüssel | Was eintragen |
|---|---|---|
| `[source]` | `remote` | `gdrive`, sofern du das rclone-Remote anders benannt hast |
| `[source]` | `root_folder_id` | Leer für ganz „Meine Ablage“. Oder die ID eines einzelnen Ordners, dann wird nur dieser Teilbaum migriert |
| `[buffer]` | `reserve_bytes` | Freier Platz, der nie beschrieben wird |
| `[buffer]` | `max_unit_bytes` | Ordner über dieser Größe werden geteilt |
| `[rclone]` | `bwlimit` | Optionaler Zeitplan, zum Beispiel `08:00,20M 23:00,off` |
| `[target]` | `type`, `path`, `remote_subdir` | Derzeit nur `folder`. `path` ist der lokale Ordner, in den das Ziel schreibt |
| `[notify]` | `ntfy_url` | `https://ntfy.sh/<dein-topic>` aus Schritt 5, oder leer |
| `[notify]` | `healthchecks_url` | Die Ping-URL aus Schritt 6, oder leer |
| `[notify]` | `include_paths` | Bleibt `false` |

Halte die Datei privat. Sie enthält dein Topic und die Ping-URL:

```bash
ssh <server> 'chmod 600 ~/.config/skyferry/config.toml'
```

Lege die Datei nicht ins Repository. `config.toml` steht in der `.gitignore`.

---

## 9. Planen, testen, laufen lassen

Im Projektordner auf dem Server (`~/skyferry`):

```bash
cd ~/skyferry
python3 -m skyferry notify-test        # ntfy und healthchecks erreichbar?
python3 -m skyferry plan --dry-run     # Inventar und Statistik, nichts gespeichert
python3 -m skyferry plan               # Units speichern, Report nach ~/.local/state/skyferry/reports/
python3 -m skyferry run --once         # überwachter Lauf, bis nichts mehr vorangeht
python3 -m skyferry status             # Übersicht
```

Der Trockenlauf zeigt, wie viele Units und Bytes zu erwarten sind, und warnt vor einzelnen Dateien, die größer sind als `max_unit_bytes`. Lies den Plan-Report, bevor du weitermachst.

Wenn der überwachte Lauf gesund aussieht, aktiviere den Dienst:

```bash
systemctl --user enable --now skyferry.service
journalctl --user -u skyferry.service -f    # Log mitlesen
```

Der Dienst startet nach Fehlern neu (`Restart=on-failure`, im Abstand von 5 Minuten). Stürzt er ab, schickt die Alarm-Unit eine Nachricht über ntfy. Nützliche Befehle während des Laufs:

```bash
python3 -m skyferry status               # Zähler, Durchsatz, Restzeit
python3 -m skyferry units --status FAILED
python3 -m skyferry events --tail 50
python3 -m skyferry retry --failed       # nachdem du die Ursache behoben hast
```

---

## 10. Desktop-Sync-Clients: Migrationsordner abwählen (manuell, später)

Sobald der Zielordner in einem Cloud-Desktop-Client (zum Beispiel Sync.com) existiert und dieser Client auf deinem Laptop läuft, lädt der Laptop alles herunter, was dort ankommt. Mehrere Terabyte migrierter Daten sollten nicht die Festplatte deines Laptops füllen. Erledige das vor dem ersten Upload:

1. Öffne die Einstellungen des Desktop-Clients.
2. Gehe zu **Selective Sync** (oder der entsprechenden Einstellung deines Clients).
3. Wähle den Migrationsordner ab, zum Beispiel `GDrive-Archiv` (`target.remote_subdir`).

Prüfe das auch nach jeder neuen Client-Installation.

---

## Fehlersuche

| Symptom | Mögliche Ursache und Abhilfe |
|---|---|
| `Konfiguration nicht gefunden` | Keine Konfiguration unter `~/.config/skyferry/config.toml`. `install.sh` erneut ausführen oder `SKYFERRY_CONFIG` setzen. |
| `Puffer nicht eingehängt` oder `Marker-Datei fehlt` | Die Platte ist nicht eingehängt oder die Markerdatei fehlt. Prüfe `df -h /mnt/skyferry` und `ls -la /mnt/skyferry`. skyferry schreibt erst wieder, wenn das behoben ist. |
| `Es läuft bereits eine skyferry-Instanz` | Ein anderer Lauf hält die Sperre. Warte, bis er fertig ist, oder stoppe den Dienst. |
| Token nach etwa einer Woche abgelaufen | Die OAuth-App ist noch im Status „Testing“. Veröffentliche sie (Schritt 2) und führe `gdrive-connect.sh` erneut aus. |
| `rateLimitExceeded` von Google | Verwende eine eigene Client-ID (Schritt 2), senke `rclone.tpslimit` oder setze ein `bwlimit`. |
| Eine Unit steht auf `NEEDS_ATTENTION` | Den Grund zeigt `python3 -m skyferry units --status NEEDS_ATTENTION`. Häufige Ursachen: doppelte Namen in einem Drive-Ordner, oder ein Ordner wurde von Hand umbenannt. Ursache beheben, dann `retry <id>`. |
| Keine Benachrichtigungen | `notify-test` ausführen. Topic und URL in der Konfiguration prüfen. |

Mehr zum Aufbau steht in der [README](../../README.md) (englisch).
