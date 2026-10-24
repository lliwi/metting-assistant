#!/usr/bin/env bash
# Copia de seguridad: base de datos (pg_dump) + almacenamiento de objetos (audio, exportaciones).
#
#   scripts/backup.sh                 # en BACKUP_DIR (por defecto ./backups)
#   BACKUP_KEEP_DAYS=30 scripts/backup.sh
#
# Cada copia es un directorio con fecha: db.dump.gpg, objects.tar.gpg y SHA256SUMS. Se cifra con
# GPG (AES-256) usando BACKUP_PASSPHRASE de .env: guárdala también FUERA del servidor; sin ella
# las copias no se pueden restaurar. Para programarla: cron o un timer de systemd, p. ej.
#   15 3 * * *  cd /ruta/metting-assistant && scripts/backup.sh >> backups/backup.log 2>&1
# Restaurar y comprobar: scripts/restore.sh.
set -euo pipefail
umask 077  # copias legibles solo por su propietario
cd "$(dirname "$0")/.."

# Valor de una variable en .env (vacío si no está).
env_value() {
  { grep -E "^$1=" .env 2>/dev/null || true; } | tail -1 | cut -d= -f2- \
    | sed -e "s/^['\"]//" -e "s/['\"]$//"
}

PASSPHRASE="${BACKUP_PASSPHRASE:-$(env_value BACKUP_PASSPHRASE)}"
if [ -z "$PASSPHRASE" ]; then
  echo "Falta BACKUP_PASSPHRASE en .env (las copias contienen audio y transcripciones)." >&2
  exit 1
fi
DB_USER="${POSTGRES_USER:-$(env_value POSTGRES_USER)}"; DB_USER="${DB_USER:-metting}"
DB_NAME="${POSTGRES_DB:-$(env_value POSTGRES_DB)}"; DB_NAME="${DB_NAME:-metting}"
ROOT="${BACKUP_DIR:-backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
DEST="$ROOT/$(date -u +%Y%m%dT%H%M%SZ)"
COMPOSE=(docker compose)

encrypt() {
  gpg --batch --yes --quiet --symmetric --cipher-algo AES256 \
      --passphrase-fd 3 --output "$1" 3<<<"$PASSPHRASE"
}

mkdir -p "$DEST"
chmod 700 "$ROOT" "$DEST"
trap 'echo "La copia ha fallado; se borra $DEST" >&2; rm -rf "$DEST"' ERR

echo "Base de datos → $DEST/db.dump.gpg"
"${COMPOSE[@]}" exec -T postgres pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc | encrypt "$DEST/db.dump.gpg"

echo "Objetos → $DEST/objects.tar.gpg"
"${COMPOSE[@]}" exec -T api python -m app.ops.objects export | encrypt "$DEST/objects.tar.gpg"
"${COMPOSE[@]}" exec -T api python -m app.ops.objects count > "$DEST/objects.count"

(cd "$DEST" && sha256sum db.dump.gpg objects.tar.gpg objects.count > SHA256SUMS)
trap - ERR
du -sh "$DEST" | awk '{print "Copia completada: " $2 " (" $1 ")"}'

# Retención: borra copias más antiguas que KEEP_DAYS (nunca la recién hecha).
find "$ROOT" -mindepth 1 -maxdepth 1 -type d -name '20*Z' -mtime +"$KEEP_DAYS" \
  ! -path "$DEST" -print -exec rm -rf {} +
