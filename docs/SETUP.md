# Setup guide

This guide takes you from a bare Linux server to an unattended skyferry run. It is written for a home server or homelab box, but any Linux machine with systemd and a USB port works.

The German version is at [docs/de/EINRICHTUNG.md](de/EINRICHTUNG.md).

Some steps need a password, a login or your approval, so they cannot be scripted. They are marked **(manual)**.

## Overview

| # | Step | Needs |
|---|---|---|
| 1 | Server packages (optional now, needed for the Sync.com spike) | sudo, once |
| 2 | Your own Google OAuth client ID | Browser, Google account |
| 3 | Connect Google Drive to the server | Browser on your laptop |
| 4 | Install skyferry on the server | SSH |
| 5 | ntfy app and an unguessable topic | Phone |
| 6 | healthchecks.io check | Browser |
| 7 | Prepare the buffer disk | sudo, typed confirmation |
| 8 | Configure skyferry | SSH |
| 9 | Plan, dry run, supervised run, then enable the service | SSH |
| 10 | Desktop sync clients: deselect the migration folder | Desktop app |

Throughout, `<server>` stands for the SSH host alias of your server, and `<SERIAL>` for the serial number of the buffer disk.

---

## 1. Server packages (optional for now)

The migration engine needs only Python 3.12 and rclone 1.75 or newer. Wine and the headless X tools are needed only for the Sync.com experiments in `spike/wine/`. The script also installs `sqlite3` and `smartmontools`, which are handy for inspection and disk health checks.

```bash
ssh -t <server> 'bash -s' < setup/homelab-packages.sh
```

The script asks for the sudo password once. It is idempotent.

Check that Python and rclone are available:

```bash
ssh <server> 'python3 --version; rclone version | head -1'
```

If rclone is missing, install a current build into `~/.local/bin` (see <https://rclone.org/install/>). The systemd unit adds `~/.local/bin` to its `PATH`.

---

## 2. Create your own Google OAuth client ID (recommended)

rclone ships with a shared default client ID that every rclone user in the world uses. Large transfers hit Google's per-client rate limits quickly (`rateLimitExceeded`). Your own client ID avoids that.

1. Open the [Google Cloud Console](https://console.cloud.google.com/) and create a new project, for example `skyferry`.
2. Go to **APIs & Services → Library**, search for **Google Drive API** and enable it.
3. Configure the consent screen (in newer consoles: **Google Auth Platform**):
   - User type **External**
   - Any app name
   - Your own e-mail address as support and developer contact
4. **Important: publish the app.** Under **Audience** (or **Publishing status**), click **Publish app** so that it is **In production**.

   > **Warning:** In the "Testing" publishing status, Google expires refresh tokens after **7 days**. Your migration would silently stop working after a week. Publishing the app for your own use does not require Google's verification. You may see an "unverified app" warning during login. Click **Advanced → Continue**.

5. Go to **Clients** (or **Credentials**) → **Create OAuth client ID** → application type **Desktop app**. Keep the client ID and the client secret ready for step 3.

You can skip this step and use rclone's default client. In that case press Enter when step 3 asks for the client ID.

---

## 3. Connect Google Drive to the server (manual)

Run this on your laptop, from the project folder:

```bash
./setup/gdrive-connect.sh <server>
```

The script:

1. Checks that no `gdrive` remote exists yet on the server.
2. Asks for the client ID and client secret from step 2 (leave empty for rclone's default).
3. Opens an SSH tunnel and prints a link like `http://127.0.0.1:53682/auth?state=…`.
4. Lets you open that link **in the browser on your laptop**, sign in with your Google account and grant access. The OAuth redirect travels through the tunnel to rclone on the server.
5. Stores the token only in `~/.config/rclone/rclone.conf` on the server, with mode `0600`, and shows you the Drive storage quota.

To start over, delete the remote on the server and run the script again:

```bash
ssh <server> '~/.local/bin/rclone config delete gdrive'
```

---

## 4. Install skyferry on the server

From the project folder on your laptop:

```bash
rsync -a --delete --exclude .git --exclude reports --exclude plans ./ <server>:~/skyferry/
ssh <server> 'bash ~/skyferry/deploy/install.sh'
```

`install.sh` does not need sudo. It is idempotent and:

- copies the package to `~/skyferry`,
- creates `~/.config/skyferry/config.toml` from the example (mode `0600`) if it does not exist yet; an existing config is never overwritten,
- creates the state directory `~/.local/state/skyferry`,
- installs the systemd user units `skyferry.service` and `skyferry-notify-failure@.service`,
- checks that `python3 -m skyferry --help` works.

It does **not** start the service.

Let the user service run without an active login session:

```bash
ssh <server> 'loginctl enable-linger $USER'
```

Depending on your system this may need an administrator. Without lingering, the service stops when you log out.

---

## 5. ntfy app and an unguessable topic (manual)

[ntfy](https://ntfy.sh) delivers push notifications to your phone. You do not need an account.

1. Install the **ntfy** app (iOS App Store or Google Play).
2. Pick a topic name that nobody else can guess, for example a long random string. Generate one with:

   ```bash
   python3 -c "import secrets; print('skyferry-' + secrets.token_urlsafe(18))"
   ```

3. In the app, tap **+**, enter the server `ntfy.sh` and your topic name, and subscribe.
4. Put the full URL into the config on the server later (step 8): `ntfy_url = "https://ntfy.sh/<your-topic>"`.

**Treat the topic like a password.** Anyone who knows it can read your notifications. skyferry therefore sends no folder or file names by default (`notify.include_paths = false`). Messages contain only unit numbers, sizes, statuses and error classes.

If you self-host ntfy, use your own server URL and, if you enabled access control, set `ntfy_token`.

---

## 6. healthchecks.io dead man's switch (manual)

[healthchecks.io](https://healthchecks.io) alerts you when the server stops reporting, for example after a power outage, a crash, or a dead service.

1. Create a free account at <https://healthchecks.io>.
2. Create a new check, named `skyferry`:
   - Schedule: **Simple**
   - **Period: 5 minutes**
   - **Grace time: 30 minutes**
3. Open **Integrations** and add **ntfy** with the same topic as in step 5. E-mail is usually enabled already.
4. Copy the **ping URL** (`https://hc-ping.com/<uuid>`). You will put it into the config in step 8.

skyferry sends a heartbeat every `notify.heartbeat_seconds` (default 300 s, which matches the 5-minute period), sends `/start` when a run begins, and `/fail` on fatal errors.

---

## 7. Prepare the buffer disk (manual)

The buffer is an external USB disk. Data is downloaded there, verified, handed to the target, and then deleted from the disk again. It should be large enough for your largest unit (`buffer.max_unit_bytes`, default 300 GB) plus some headroom.

> **Warning:** the script **erases the disk**. Only use a disk that holds nothing you need. Check the disk first. The script shows you the current contents and refuses to continue unless you type the confirmation word `LOESCHEN`.

1. Plug the disk into the server and find its serial number:

   ```bash
   ssh <server> 'lsblk -o NAME,SERIAL,TRAN,SIZE'
   ```

   The line with `TRAN` set to `usb` is your disk. Note its `SERIAL`.

2. Copy the setup scripts and run the buffer script with sudo:

   ```bash
   rsync -a setup/ <server>:~/skyferry-setup/
   ssh -t <server> 'sudo bash ~/skyferry-setup/buffer-disk.sh <SERIAL> $USER'
   ```

What the script does:

- Looks the disk up **by serial number**, so a changing device name such as `/dev/sdb` does not matter.
- Refuses to touch anything that is not a USB disk, is mounted, or contains LVM, RAID or swap data.
- Creates a GPT partition with ext4 (label `skyferry-buffer`).
- Writes an `/etc/fstab` entry by UUID with `nofail`, so the server still boots without the disk.
- Mounts the disk at `/mnt/skyferry` and makes the **empty** mountpoint immutable (`chattr +i`). If the disk is not mounted, nothing can be written there by accident, and the system SSD never fills up.
- Creates the marker file `.skyferry-buffer` and the `staging/` folder, both owned by your user.

Check the result:

```bash
ssh <server> 'df -h /mnt/skyferry; ls -la /mnt/skyferry'
```

skyferry refuses to run if the mount or the marker file is missing.

---

## 8. Configure skyferry

On the server, copy the example and edit it:

```bash
ssh -t <server> 'nano ~/.config/skyferry/config.toml'
```

Settings to review first:

| Section | Key | What to set |
|---|---|---|
| `[source]` | `remote` | `gdrive`, unless you named the rclone remote differently |
| `[source]` | `root_folder_id` | Empty for all of My Drive. Or the ID of one folder to migrate only that subtree. |
| `[buffer]` | `reserve_bytes` | Free space that is never written to |
| `[buffer]` | `max_unit_bytes` | Folders above this size are split |
| `[rclone]` | `bwlimit` | Optional schedule, for example `08:00,20M 23:00,off` |
| `[target]` | `type`, `path`, `remote_subdir` | Currently only `folder`. `path` is the local folder that the target writes into |
| `[notify]` | `ntfy_url` | `https://ntfy.sh/<your-topic>` from step 5, or empty |
| `[notify]` | `healthchecks_url` | The ping URL from step 6, or empty |
| `[notify]` | `include_paths` | Keep `false` |

Keep the file private. It holds your topic and ping URL:

```bash
ssh <server> 'chmod 600 ~/.config/skyferry/config.toml'
```

Do not put the file into the repository. `config.toml` is in `.gitignore`.

---

## 9. Plan, test and run

From the project directory on the server (`~/skyferry`):

```bash
cd ~/skyferry
python3 -m skyferry notify-test        # ntfy + healthchecks reachable?
python3 -m skyferry plan --dry-run     # inventory and statistics, nothing saved
python3 -m skyferry plan               # save the units, writes a report to ~/.local/state/skyferry/reports/
python3 -m skyferry run --once         # supervised run until no further progress is possible
python3 -m skyferry status             # overview
```

The dry run shows how many units and bytes to expect, and warns about single files larger than `max_unit_bytes`. Read the plan report before you continue.

When the supervised run looks healthy, enable the service:

```bash
systemctl --user enable --now skyferry.service
journalctl --user -u skyferry.service -f    # follow the log
```

The service restarts after failures (`Restart=on-failure`, 5 minutes apart). If it crashes, the failure unit sends an alarm through ntfy. Useful commands while it runs:

```bash
python3 -m skyferry status               # counts, throughput, ETA
python3 -m skyferry units --status FAILED
python3 -m skyferry events --tail 50
python3 -m skyferry retry --failed       # after you have fixed the cause
```

---

## 10. Desktop sync clients: deselect the migration folder (manual, later)

Once the target folder exists in a cloud desktop client (for example Sync.com) and that client runs on your laptop, the laptop will start downloading everything that lands there. Terabytes of migrated data should not fill your laptop disk. Before the first upload:

1. Open the desktop client's settings.
2. Go to **Selective Sync** (or the equivalent setting in your client).
3. Untick the migration folder, for example `GDrive-Archiv` (`target.remote_subdir`).

Check this after each new client installation as well.

---

## Troubleshooting

| Symptom | Likely cause and fix |
|---|---|
| `Konfiguration nicht gefunden` | No config at `~/.config/skyferry/config.toml`. Run `install.sh` again or set `SKYFERRY_CONFIG`. |
| `Puffer nicht eingehängt` or `Marker-Datei fehlt` | The disk is not mounted, or the marker is missing. Check `df -h /mnt/skyferry` and `ls -la /mnt/skyferry`. skyferry will not write until this is fixed. |
| `Es läuft bereits eine skyferry-Instanz` | Another run holds the lock. Wait for it or stop the service. |
| Token expired after about a week | The OAuth app is still in "Testing". Publish it (step 2) and run `gdrive-connect.sh` again. |
| `rateLimitExceeded` from Google | Use your own client ID (step 2), and lower `rclone.tpslimit` or add a `bwlimit`. |
| A unit is `NEEDS_ATTENTION` | Read the reason with `python3 -m skyferry units --status NEEDS_ATTENTION`. Common causes: duplicate names in one Drive folder, or a folder renamed by hand. Fix the cause, then `retry <id>`. |
| No notifications | Run `notify-test`. Check the topic and the URL in the config. |

More detail on the design is in the [README](../README.md).
