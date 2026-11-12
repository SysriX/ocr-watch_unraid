#!/bin/sh
# Startet ocr-watch als PUID:PGID (Unraid-Standard 99:100 = nobody:users),
# damit neue Dateien auf den Freigaben dem normalen Freigabe-Benutzer gehören.
set -e
PUID="${PUID:-99}"
PGID="${PGID:-100}"
umask "${UMASK:-0002}"

mkdir -p "${CONFIG_DIR:-/config}"
if [ "$(id -u)" = "0" ]; then
  chown "$PUID:$PGID" "${CONFIG_DIR:-/config}" 2>/dev/null || true
  [ -f "${CONFIG_DIR:-/config}/ocr-watch.db" ] && chown "$PUID:$PGID" "${CONFIG_DIR:-/config}"/ocr-watch.db* 2>/dev/null || true
  export HOME=/tmp
  exec setpriv --reuid="$PUID" --regid="$PGID" --clear-groups python -m ocrwatch
fi
exec python -m ocrwatch
