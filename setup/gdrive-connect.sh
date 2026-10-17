#!/usr/bin/env bash
# Auf dem Rechner mit Browser ausführen:  ./setup/gdrive-connect.sh <ssh-host>
# Legt auf dem Server das rclone-Remote "gdrive" (Google Drive, Vollzugriff) an.
# Die Google-Anmeldung läuft in deinem lokalen Browser. Ein SSH-Tunnel leitet den
# OAuth-Rücksprung (127.0.0.1:53682) an rclone auf dem Server weiter.
# Das Token landet nur in ~/.config/rclone/rclone.conf auf dem Server.
set -euo pipefail
HOST="${1:-${SKYFERRY_HOST:-}}"
[ -n "$HOST" ] || { echo "Aufruf: $0 <ssh-host>   (oder SKYFERRY_HOST setzen)" >&2; exit 2; }

if ssh -o BatchMode=yes "$HOST" 'PATH=$HOME/.local/bin:$PATH rclone listremotes' | grep -qx 'gdrive:'; then
  echo "Remote 'gdrive' existiert schon auf $HOST. Zum Neuanlegen erst löschen:"
  echo "  ssh $HOST '~/.local/bin/rclone config delete gdrive'"
  exit 1
fi

echo "Eigene Google-OAuth-Client-ID (empfohlen, siehe docs/SETUP.md)."
read -r -p "Client-ID (leer lassen = rclone-Standard-ID): " CID
CSEC=""
if [ -n "$CID" ]; then
  read -r -s -p "Client-Secret: " CSEC; echo
fi

echo
echo "==> Gleich erscheint ein Link http://127.0.0.1:53682/auth?state=…"
echo "    Öffne ihn hier lokal im Browser, melde dich mit dem Google-Konto an und erlaube den Zugriff."
echo "    Bei eigener Client-ID: Warnung 'App nicht verifiziert' → Erweitert → Weiter."
echo

# Client-ID und -Secret gehen über stdin, nicht über die lokale Kommandozeile.
ssh -o ExitOnForwardFailure=yes -L 53682:127.0.0.1:53682 "$HOST" 'bash -s' <<REMOTE
set -euo pipefail
export PATH=\$HOME/.local/bin:\$PATH
args=(scope=drive config_is_local=true)
if [ -n "${CID}" ]; then args+=(client_id="${CID}" client_secret="${CSEC}"); fi
rclone config create gdrive drive "\${args[@]}" >/dev/null
chmod 600 \$HOME/.config/rclone/rclone.conf
echo "==> Verbunden. Speicherbelegung laut Google:"
rclone about gdrive:
REMOTE
echo "Fertig. Remote 'gdrive' ist auf $HOST eingerichtet."
