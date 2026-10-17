#!/usr/bin/env bash
# Einmalig auf dem Server ausführen (Ubuntu/Debian, fragt nach dem sudo-Passwort).
# Das Skript muss auf dem Server liegen: sudo braucht ein Terminal für die Passwortabfrage,
# und ein per stdin hineingeleitetes Skript (`bash -s < …`) hat keins.
#   rsync -a setup/ <ssh-host>:~/skyferry-setup/
#   ssh -t <ssh-host> 'bash ~/skyferry-setup/homelab-packages.sh'
# Installiert Wine und die headless-X-Werkzeuge für den Sync.com-Client unter Wine
# sowie Hilfsprogramme für die Pufferplatte. Idempotent.
set -euo pipefail

if [ "$(id -u)" -ne 0 ] && [ ! -t 0 ]; then
  echo "Kein Terminal: sudo kann so nicht nach dem Passwort fragen." >&2
  echo "Aufruf: ssh -t <ssh-host> 'bash ~/skyferry-setup/homelab-packages.sh'" >&2
  exit 1
fi

echo "==> i386-Architektur für wine32 aktivieren"
if ! dpkg --print-foreign-architectures | grep -qx i386; then
  sudo dpkg --add-architecture i386
fi

echo "==> Paketlisten aktualisieren"
sudo apt-get update -q

echo "==> Pakete installieren"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q --install-recommends \
  wine wine64 wine32:i386 winetricks winbind cabextract \
  xvfb x11vnc xdotool imagemagick \
  sqlite3 smartmontools

echo "==> Versionen"
wine --version
Xvfb -help 2>&1 | head -1 || true
x11vnc -version 2>/dev/null | head -1 || true
echo "Fertig."
