#!/usr/bin/env bash
# Formatiert eine externe USB-Platte als Puffer für skyferry.
# ACHTUNG: LÖSCHT ALLE DATEN AUF DER PLATTE. Vorher wird der Inhalt angezeigt und
# eine getippte Bestätigung verlangt.
#
# Aufruf vom Mac aus (Seriennummer aus `lsblk -o NAME,SERIAL,TRAN,SIZE`):
#   rsync -a setup/ <ssh-host>:~/skyferry-setup/
#   ssh -t <ssh-host> sudo bash ~/skyferry-setup/buffer-disk.sh <SERIENNUMMER> [benutzer]
# Ohne Rückfrage (z. B. in einem Skript): SKYFERRY_CONFIRM_WIPE=<SERIENNUMMER> setzen.
#
# Ergebnis:
# - Partitionstabelle GPT mit einer ext4-Partition (Label skyferry-buffer)
# - Eintrag in /etc/fstab per UUID, mit nofail (der Server bootet auch ohne Platte)
# - Einhängepunkt /mnt/skyferry, der leere Ordner ist unveränderlich (chattr +i):
#   Ist die Platte nicht eingehängt, kann nichts versehentlich auf die Systemplatte geschrieben werden.
# - Markerdatei .skyferry-buffer und Ordner staging/, beides dem Benutzer gehörend
set -euo pipefail

SERIAL="${1:?Seriennummer der Platte fehlt (lsblk -o NAME,SERIAL,TRAN,SIZE)}"
OWNER="${2:-${SUDO_USER:-}}"
MNT=/mnt/skyferry
LABEL=skyferry-buffer
MARKER=.skyferry-buffer

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
[ -n "$OWNER" ] && id "$OWNER" >/dev/null 2>&1 || { echo "Benutzer unbekannt: '$OWNER'" >&2; exit 1; }

# Platte über die Seriennummer finden. Der Name /dev/sdX kann sich ändern.
DEV=""
while read -r name serial tran; do
  if [ "$serial" = "$SERIAL" ]; then DEV="/dev/$name"; TRAN="$tran"; fi
done < <(lsblk -dn -o NAME,SERIAL,TRAN)
[ -n "$DEV" ] || { echo "Keine Platte mit Seriennummer $SERIAL gefunden." >&2; exit 1; }
[ "${TRAN:-}" = "usb" ] || { echo "$DEV ist keine USB-Platte (TRAN=${TRAN:-?}), Abbruch." >&2; exit 1; }

# Nichts anfassen, was in Benutzung ist: eingehängt, als Swap oder als LVM/RAID-Mitglied.
if lsblk -nr -o MOUNTPOINTS "$DEV" | grep -q .; then
  echo "Eine Partition von $DEV ist eingehängt. Bitte erst aushängen:" >&2
  lsblk -o NAME,MOUNTPOINTS "$DEV" >&2; exit 1
fi
if lsblk -nr -o FSTYPE "$DEV" | grep -qE 'LVM2_member|linux_raid_member|swap'; then
  echo "$DEV enthält LVM-, RAID- oder Swap-Daten, Abbruch." >&2; exit 1
fi

echo "==> Diese Platte wird GELÖSCHT:"
lsblk -o NAME,SIZE,FSTYPE,LABEL,MODEL,SERIAL "$DEV"
echo
echo "==> Inhalt (nur lesend eingehängt, falls möglich):"
TMP=$(mktemp -d)
while read -r part fstype; do
  [ -n "$fstype" ] || continue
  if mount -o ro "$part" "$TMP" 2>/dev/null; then
    echo "--- $part ($fstype)"
    ls -la "$TMP" | head -25
    df -h "$TMP" | tail -1
    umount "$TMP"
  else
    echo "--- $part ($fstype): nicht lesbar"
  fi
done < <(lsblk -lnp -o NAME,FSTYPE "$DEV" | tail -n +2)
rmdir "$TMP"
echo
# Nicht-interaktive Bestätigung: SKYFERRY_CONFIRM_WIPE muss exakt die Seriennummer enthalten.
if [ "${SKYFERRY_CONFIRM_WIPE:-}" = "$SERIAL" ]; then
  echo "Löschen bestätigt über SKYFERRY_CONFIRM_WIPE=$SERIAL"
else
  read -r -p "Alle Daten auf $DEV ($SERIAL) löschen? Tippe LOESCHEN: " ANSWER
  [ "$ANSWER" = "LOESCHEN" ] || { echo "Abgebrochen, nichts verändert."; exit 1; }
fi

echo "==> Signaturen löschen und neu partitionieren"
for p in $(lsblk -lnp -o NAME "$DEV" | tail -n +2); do wipefs -a "$p"; done
wipefs -a "$DEV"
printf 'label: gpt\n,,L\n' | sfdisk --quiet "$DEV"
udevadm settle
PART=$(lsblk -lnp -o NAME "$DEV" | tail -n +2 | head -1)
[ -b "$PART" ] || { echo "Neue Partition nicht gefunden." >&2; exit 1; }

echo "==> ext4 auf $PART anlegen"
mkfs.ext4 -F -q -L "$LABEL" -m 0 "$PART"
UUID=$(blkid -s UUID -o value "$PART")

echo "==> Einhängepunkt $MNT und /etc/fstab"
mkdir -p "$MNT"
if ! mountpoint -q "$MNT"; then chattr +i "$MNT"; fi
cp /etc/fstab "/etc/fstab.bak-skyferry-$(date +%Y%m%d-%H%M%S)"
awk -v m="$MNT" '$2 != m' /etc/fstab > /etc/fstab.new
echo "UUID=$UUID $MNT ext4 defaults,noatime,nofail,x-systemd.device-timeout=15s 0 2" >> /etc/fstab.new
cat /etc/fstab.new > /etc/fstab && rm /etc/fstab.new
systemctl daemon-reload
mount "$MNT"

echo "==> Rechte, Marker und Staging-Ordner"
chown "$OWNER:$OWNER" "$MNT"
sudo -u "$OWNER" touch "$MNT/$MARKER"
sudo -u "$OWNER" mkdir -p "$MNT/staging"

echo "==> Ergebnis"
df -h "$MNT"
ls -la "$MNT"
echo
echo "==> SMART-Zustand (bei USB-Gehäusen nicht immer verfügbar)"
smartctl -H -d sntjmicron "$DEV" 2>/dev/null || smartctl -H "$DEV" 2>/dev/null || echo "SMART nicht abrufbar"
echo "Fertig."
