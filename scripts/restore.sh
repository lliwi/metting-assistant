#!/usr/bin/env bash
# Restaura o comprueba una copia de scripts/backup.sh.
#
#   scripts/restore.sh --verify backups/20261024T031500Z   # no toca los datos
#   scripts/restore.sh --yes    backups/20261024T031500Z   # SOBRESCRIBE base de datos y objetos
#
# --verify comprueba las sumas, descifra, restaura la base de datos en una base temporal
# (metting_restore_check, que se borra al final) y cuenta los objetos del tar.
set -euo pipefail
cd "$(dirname "$0")/.."

MODE="${1:-}"; DIR="${2:-}"
if [[ "$MODE" != "--verify" && "$MODE" != "--yes" ]] || [ ! -d "$DIR" ]; then
  sed -n '2,8p' "$0" >&2; exit 2
fi

# Valor de una variable en .env (vacío si no está).
env_value() {
  { grep -E "^$1=" .env 2>/dev/null || true; } | tail -1 | cut -d= -f2- \
    | sed -e "s/^['\"]//" -e "s/['\"]$//"
}
PASSPHRASE="${BACKUP_PASSPHRASE:-$(env_value BACKUP_PASSPHRASE)}"
DB_USER="${POSTGRES_USER:-$(env_value POSTGRES_USER)}"; DB_USER="${DB_USER:-metting}"
DB_NAME="${POSTGRES_DB:-$(env_value POSTGRES_DB)}"; DB_NAME="${DB_NAME:-metting}"
COMPOSE=(docker compose)

decrypt() { gpg --batch --quiet --decrypt --passphrase-fd 3 "$1" 3<<<"$PASSPHRASE"; }
psql_admin() { "${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d postgres -v ON_ERROR_STOP=1 -qAt "$@"; }

echo "Sumas de comprobación"
(cd "$DIR" && sha256sum --quiet -c SHA256SUMS)

if [ "$MODE" = "--verify" ]; then
  CHECK_DB=metting_restore_check
  psql_admin -c "DROP DATABASE IF EXISTS $CHECK_DB" -c "CREATE DATABASE $CHECK_DB"
  trap 'psql_admin -c "DROP DATABASE IF EXISTS $CHECK_DB" >/dev/null' EXIT
  decrypt "$DIR/db.dump.gpg" | "${COMPOSE[@]}" exec -T postgres \
    pg_restore -U "$DB_USER" -d "$CHECK_DB" --no-owner --exit-on-error
  echo "Base de datos restaurada en $CHECK_DB:"
  "${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$CHECK_DB" -qAt -F ' ' -c \
    "SELECT 'usuarios', count(*)::text FROM users
     UNION ALL SELECT 'organizaciones', count(*)::text FROM organizations
     UNION ALL SELECT 'grabaciones', count(*)::text FROM recordings
     UNION ALL SELECT 'transcripciones', count(*)::text FROM transcripts
     UNION ALL SELECT 'migración', version_num FROM alembic_version" | sed 's/^/  /'
  expected=$(cut -d' ' -f1 "$DIR/objects.count")
  actual=$(decrypt "$DIR/objects.tar.gpg" | tar -t | wc -l)
  echo "Objetos en la copia: $actual (esperados $expected)"
  [ "$actual" -eq "$expected" ] || { echo "El número de objetos no coincide" >&2; exit 1; }
  echo "Copia verificada."
  exit 0
fi

echo "Se detienen la API y los workers durante la restauración"
"${COMPOSE[@]}" stop api worker worker-asr scheduler
decrypt "$DIR/db.dump.gpg" | "${COMPOSE[@]}" exec -T postgres \
  pg_restore -U "$DB_USER" -d "$DB_NAME" --clean --if-exists --no-owner --exit-on-error
"${COMPOSE[@]}" run --rm --no-deps -T api python -m app.ops.objects import < <(decrypt "$DIR/objects.tar.gpg")
"${COMPOSE[@]}" up -d --wait api worker worker-asr scheduler
echo "Restauración completada desde $DIR"
