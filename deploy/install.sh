#!/usr/bin/env bash
# Idempotente Installation OHNE sudo. Startet den Dienst NICHT.
#
# Vom Mac aus:
#   rsync -a --delete --exclude .git --exclude reports --exclude plans ./ <ssh-host>:~/skyferry/
#   ssh <ssh-host> 'bash ~/skyferry/deploy/install.sh'
#
# Ueberschreibbar (z.B. fuer Tests): SKYFERRY_HOME, SKYFERRY_CONFIG_DIR, SKYFERRY_UNIT_DIR,
# SKYFERRY_STATE_DIR, SKIP_SYSTEMCTL=1
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${SKYFERRY_HOME:-$HOME/skyferry}"
CONF_DIR="${SKYFERRY_CONFIG_DIR:-$HOME/.config/skyferry}"
UNIT_DIR="${SKYFERRY_UNIT_DIR:-$HOME/.config/systemd/user}"
STATE_DIR="${SKYFERRY_STATE_DIR:-$HOME/.local/state/skyferry}"

command -v python3 >/dev/null || { echo "python3 fehlt" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Python >= 3.11 erforderlich" >&2; exit 1; }
command -v rclone >/dev/null || [ -x "$HOME/.local/bin/rclone" ] || echo "WARNUNG: rclone nicht gefunden (erwartet in ~/.local/bin)"

if [ "$SRC" != "$DEST" ]; then
  mkdir -p "$DEST"
  rm -rf "$DEST/skyferry" "$DEST/deploy"
  cp -R "$SRC/skyferry" "$SRC/deploy" "$DEST/"
  cp "$SRC/config.example.toml" "$DEST/"
  [ -f "$SRC/README.md" ] && cp "$SRC/README.md" "$DEST/"
  find "$DEST" -name __pycache__ -type d -prune -exec rm -rf {} +
fi

mkdir -p "$CONF_DIR" "$STATE_DIR" "$UNIT_DIR"
chmod 700 "$CONF_DIR"
if [ ! -f "$CONF_DIR/config.toml" ]; then
  cp "$SRC/config.example.toml" "$CONF_DIR/config.toml"
  chmod 600 "$CONF_DIR/config.toml"
  echo "Konfigurationsvorlage angelegt: $CONF_DIR/config.toml (bitte anpassen)"
else
  echo "Vorhandene Konfiguration bleibt unveraendert: $CONF_DIR/config.toml"
fi

cp "$SRC/deploy/skyferry.service" "$SRC/deploy/skyferry-notify-failure@.service" "$UNIT_DIR/"

if [ "${SKIP_SYSTEMCTL:-0}" != "1" ]; then
  systemctl --user daemon-reload || echo "WARNUNG: systemctl --user daemon-reload fehlgeschlagen (kein User-Bus?)"
fi

(cd "$DEST" && python3 -m skyferry --help >/dev/null) && echo "Paket startet: OK"
echo
echo "Installiert nach $DEST. Der Dienst wurde NICHT gestartet."
echo "Naechste Schritte:"
echo "  1. $CONF_DIR/config.toml anpassen"
echo "  2. loginctl enable-linger \$USER   # damit der User-Dienst ohne Login laeuft (ggf. Admin noetig)"
echo "  3. python3 -m skyferry plan --dry-run   (in $DEST)"
echo "  4. systemctl --user enable --now skyferry.service"
