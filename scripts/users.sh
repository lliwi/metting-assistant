#!/usr/bin/env sh
# Gestión de cuentas (no hay registro público). Se ejecuta en el contenedor de la API.
#   scripts/users.sh create ana@example.com --name "Ana" [--org "Equipo" --role member]
#   scripts/users.sh create bob@example.com --name "Bob" --generate-password
#   scripts/users.sh list
#   scripts/users.sh set-password ana@example.com
#   scripts/users.sh add-to-org ana@example.com "Equipo" --role admin
#   scripts/users.sh disable|enable ana@example.com
# Roles: owner | admin | member | viewer. Más ayuda: scripts/users.sh --help
set -eu
cd "$(dirname "$0")/.."
# Con terminal se pide la contraseña sin eco; sin ella (CI, tuberías) no se reserva TTY.
if [ -t 0 ]; then tty_flag=""; else tty_flag="-T"; fi
exec docker compose exec $tty_flag api python -m app.auth.cli "$@"
