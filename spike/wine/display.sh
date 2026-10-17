#!/usr/bin/env bash
# Virtueller Bildschirm für den Sync.com-Client unter Wine auf dem Server.
#   display.sh start|stop|status
# Xvfb auf :99, x11vnc nur an localhost:5900 (Zugriff ausschließlich per SSH-Tunnel):
#   Lokal:  ssh -N -L 5900:127.0.0.1:5900 <ssh-host>   dann z. B.  open vnc://127.0.0.1:5900
# Das VNC-Passwort liegt in ~/.config/skyferry/vnc.pass (wird beim ersten Start erzeugt).
set -euo pipefail
DISPLAY_NUM=":99"
CONF="$HOME/.config/skyferry"
RUN="$HOME/.local/state/skyferry/spike"
mkdir -p "$CONF" "$RUN"

start() {
  if ! pgrep -f "Xvfb $DISPLAY_NUM" >/dev/null; then
    nohup Xvfb "$DISPLAY_NUM" -screen 0 1280x800x24 -nolisten tcp >"$RUN/xvfb.log" 2>&1 &
    sleep 1
  fi
  if [ ! -f "$CONF/vnc.pass" ]; then
    umask 077
    LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 8 >"$CONF/vnc.pass.txt"
    x11vnc -storepasswd "$(cat "$CONF/vnc.pass.txt")" "$CONF/vnc.pass" >/dev/null 2>&1
  fi
  if ! pgrep -f "x11vnc .*-rfbport 5900" >/dev/null; then
    nohup x11vnc -display "$DISPLAY_NUM" -localhost -rfbport 5900 -rfbauth "$CONF/vnc.pass" \
      -forever -shared -noxdamage >"$RUN/x11vnc.log" 2>&1 &
    sleep 1
  fi
  status
}

stop() {
  pkill -f "x11vnc .*-rfbport 5900" || true
  pkill -f "Xvfb $DISPLAY_NUM" || true
}

status() {
  pgrep -af "Xvfb $DISPLAY_NUM" || echo "Xvfb läuft nicht"
  pgrep -af "x11vnc .*-rfbport 5900" || echo "x11vnc läuft nicht"
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  status) status ;;
  *) echo "Aufruf: $0 start|stop|status" >&2; exit 2 ;;
esac
