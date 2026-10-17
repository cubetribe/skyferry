#!/usr/bin/env bash
# Stabilisiert USB-Gehäuse, die unter Last die Verbindung verlieren (typisch für
# JMicron-USB-NVMe-Brücken wie JMS583, 152d:0583): Linux nutzt dafür usb-storage
# (Bulk-Only) statt UAS. Etwas langsamer, aber stabil. Danach wird das Dateisystem
# geprüft und /mnt/skyferry wieder eingehängt.
#
#   ssh -t <ssh-host> 'sudo bash ~/skyferry-setup/buffer-usb-fix.sh [VENDOR:PRODUCT]'
#
# Idempotent. Rückgängig machen: /etc/modprobe.d/skyferry-usb-quirks.conf löschen,
# `update-initramfs -u` und die Platte neu anstecken.
set -euo pipefail

ID="${1:-152d:0583}"
MNT=/mnt/skyferry
CONF=/etc/modprobe.d/skyferry-usb-quirks.conf

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
[[ "$ID" =~ ^[0-9a-f]{4}:[0-9a-f]{4}$ ]] || { echo "Ungültige USB-ID: $ID" >&2; exit 1; }

echo "==> UAS für $ID dauerhaft abschalten ($CONF)"
echo "options usb-storage quirks=${ID}:u" > "$CONF"
update-initramfs -u >/dev/null 2>&1 || echo "   (update-initramfs übersprungen)"

echo "==> Sofort anwenden und Gerät neu anmelden"
modprobe usb-storage
echo "${ID}:u" > /sys/module/usb_storage/parameters/quirks
DEVPATH=""
for d in /sys/bus/usb/devices/*; do
  [ -f "$d/idVendor" ] || continue
  if [ "$(cat "$d/idVendor"):$(cat "$d/idProduct")" = "$ID" ]; then DEVPATH="$d"; fi
done
[ -n "$DEVPATH" ] || { echo "USB-Gerät $ID nicht gefunden. Ist die Platte angesteckt?" >&2; exit 1; }
DRIVER=$(basename "$(readlink -f "$DEVPATH"/*:1.0/driver 2>/dev/null)" 2>/dev/null || echo unbekannt)
if [ "$DRIVER" != "usb-storage" ]; then
  # Ein Software-Neuanmelden lässt JMicron-Brücken oft hängen; ein Stromreset ist zuverlässig.
  if mountpoint -q "$MNT"; then umount "$MNT"; fi
  echo "   Treiber ist noch '$DRIVER'. Bitte die Platte jetzt kurz ABSTECKEN und wieder ANSTECKEN,"
  echo "   danach dieses Skript erneut starten."
  exit 3
fi
echo "   Treiber: usb-storage (stabil)"

PART=""
for i in $(seq 1 30); do
  PART=$(blkid -L skyferry-buffer 2>/dev/null || true)
  [ -n "$PART" ] && break
  sleep 2; udevadm settle
done
[ -n "$PART" ] || { echo "Partition mit Label skyferry-buffer nicht gefunden. Platte ab- und anstecken, dann erneut starten." >&2; exit 1; }

echo "==> Dateisystem prüfen ($PART)"
e2fsck -f -y "$PART" || [ $? -le 1 ]

echo "==> Einhängen"
systemctl daemon-reload
systemctl start mnt-skyferry.mount
mountpoint "$MNT"
df -h "$MNT" | tail -1
ls -la "$MNT"
echo "Fertig."
