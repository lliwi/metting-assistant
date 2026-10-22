#!/usr/bin/env sh
# Certificado autofirmado para servir la app por HTTPS en la red local (pruebas desde el móvil).
# Incluye localhost y las IP locales del equipo. El navegador mostrará un aviso la primera vez.
set -eu
cd "$(dirname "$0")/.."
mkdir -p certs
ips=$(hostname -I 2>/dev/null || ip -4 -o addr show scope global | awk '{print $4}' | cut -d/ -f1)
san="DNS:localhost,IP:127.0.0.1"
for ip in $ips; do
  case "$ip" in *:*) ;; *) san="$san,IP:$ip" ;; esac
done
openssl req -x509 -newkey rsa:2048 -sha256 -days 825 -nodes \
  -keyout certs/dev.key -out certs/dev.crt \
  -subj "/CN=Metting Agent (desarrollo)" \
  -addext "subjectAltName=$san" \
  -addext "extendedKeyUsage=serverAuth" 2>/dev/null
# nginx-unprivileged (uid 101) debe poder leer la clave. Solo para desarrollo local.
chmod 644 certs/dev.key certs/dev.crt
echo "Certificado generado para: $san"
