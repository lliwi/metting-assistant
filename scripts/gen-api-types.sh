#!/usr/bin/env sh
# Regenera apps/web/src/lib/api/schema.d.ts a partir del OpenAPI de la API (sin arrancarla).
# CI comprueba que el fichero versionado está al día.
set -eu
cd "$(dirname "$0")/.."
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
(cd apps/api && ENVIRONMENT=development uv run --frozen python -c \
  "import json; from app.main import app; print(json.dumps(app.openapi()))") > "$tmp"
(cd apps/web && npx --no-install openapi-typescript "$tmp" -o src/lib/api/schema.d.ts)
