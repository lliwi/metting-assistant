#!/usr/bin/env bash
# Prueba de carga: USERS usuarios grabando y consultando a la vez durante DURATION segundos.
#
#   scripts/loadtest.sh                     # 20 usuarios, 60 s
#   USERS=50 DURATION=120 scripts/loadtest.sh
#   API_URL=http://otra-api:8000 scripts/loadtest.sh   # otra instancia en la red interna
#
# Crea (o reactiva) las cuentas carga-N@example.com en la organización temporal «Prueba de
# carga», lanza la carga desde el contenedor del worker contra la API por la red interna (sin
# el límite por IP de nginx) y, al terminar, elimina la organización con todo lo subido y
# desactiva las cuentas. Después comprueba que nginx limita las ráfagas desde una IP.
set -euo pipefail
cd "$(dirname "$0")/.."

USERS="${USERS:-20}"
DURATION="${DURATION:-60}"
ORG="Prueba de carga $(date +%H%M%S)"
PASSWORD="carga $(openssl rand -hex 12)"
EMAILS=()

echo "Preparando $USERS cuentas en «$ORG»"
for i in $(seq 1 "$USERS"); do
  email="carga-$i@example.com"
  EMAILS+=("$email")
  role=$([ "$i" = 1 ] && echo owner || echo member)
  if ! printf '%s\n' "$PASSWORD" | scripts/users.sh create "$email" --name "Carga $i" \
        --password-stdin >/dev/null 2>&1; then
    scripts/users.sh enable "$email" >/dev/null 2>&1
    printf '%s\n' "$PASSWORD" | scripts/users.sh set-password "$email" --password-stdin >/dev/null 2>&1
  fi
  scripts/users.sh add-to-org "$email" "$ORG" --role "$role" >/dev/null 2>&1
done

emails=$(IFS=,; echo "${EMAILS[*]}")
segment=$(base64 -w0 apps/web/e2e/fixtures/reunion-corta.ogg)
status=0
docker compose exec -T \
  -e BASE_URL="${API_URL:-http://api:8000}" -e EMAILS="$emails" -e PASSWORD="$PASSWORD" -e ORG_NAME="$ORG" -e DURATION="$DURATION" \
  -e SEGMENT_B64="$segment" worker python - < apps/api/tests/load/loadtest.py || status=$?

for email in "${EMAILS[@]}"; do scripts/users.sh disable "$email" >/dev/null 2>&1; done

echo
echo "Límite por IP de nginx (ráfaga de 100 peticiones):"
codes=$(for _ in $(seq 1 100); do
  curl -s -o /dev/null -w "%{http_code}\n" "${BASE_URL:-http://localhost:8080}/api/healthz" &
done; wait)
echo "$codes" | sort | uniq -c | sed 's/^/  /'
echo "$codes" | grep -q 429 || { echo "nginx no ha limitado la ráfaga" >&2; status=1; }
exit "$status"
