#!/usr/bin/env sh
# Genera un .env con secretos aleatorios a partir de .env.example. No sobrescribe uno existente.
set -eu
cd "$(dirname "$0")/.."
if [ -e .env ]; then
  echo ".env ya existe; no se modifica." >&2
  exit 1
fi
hex() { openssl rand -hex "$1"; }
sed \
  -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(hex 24)|" \
  -e "s|^REDIS_PASSWORD=.*|REDIS_PASSWORD=$(hex 24)|" \
  -e "s|^S3_ACCESS_KEY=.*|S3_ACCESS_KEY=GK$(hex 12)|" \
  -e "s|^S3_SECRET_KEY=.*|S3_SECRET_KEY=$(hex 32)|" \
  -e "s|^GARAGE_RPC_SECRET=.*|GARAGE_RPC_SECRET=$(hex 32)|" \
  -e "s|^GARAGE_ADMIN_TOKEN=.*|GARAGE_ADMIN_TOKEN=$(hex 32)|" \
  -e "s|^JWT_SIGNING_KEY=.*|JWT_SIGNING_KEY=$(hex 32)|" \
  .env.example > .env
chmod 600 .env
echo ".env generado."
