#!/usr/bin/env bash
# Installiert den klassischen Sync.com-Windows-Client (ohne CloudFiles, die
# Windows-Cloud-Files-API gibt es unter Wine nicht) in ein eigenes Wine-Prefix.
#   install-sync.sh <pfad/zu/sync-installer-2.2.64.exe>
# Voraussetzung: display.sh start
set -euo pipefail
INSTALLER="${1:?Pfad zum Installer fehlt}"
export WINEPREFIX="$HOME/.wine-sync" WINEARCH=win64 DISPLAY=:99 WINEDEBUG=-all
LOG="$HOME/.local/state/skyferry/spike/install-sync.log"
mkdir -p "$(dirname "$LOG")"
[ -d "$WINEPREFIX" ] || wineboot --init >>"$LOG" 2>&1
winecfg -v win10 >>"$LOG" 2>&1 || true
echo "Starte Installer, das Fenster ist per VNC sichtbar."
wine "$INSTALLER" >>"$LOG" 2>&1 &
echo "Installer-PID $!  Log: $LOG"
