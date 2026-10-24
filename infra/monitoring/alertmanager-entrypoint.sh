#!/bin/sh
# Genera la configuración de Alertmanager a partir de las variables de entorno (el formato no
# admite variables). La contraseña SMTP va en un fichero aparte para no tener que escaparla.
set -eu
umask 077
printf '%s' "${SMTP_PASSWORD:-}" > /tmp/smtp_password
escape() { printf '%s' "$1" | sed -e 's/[\\|&]/\\&/g' -e 's/"/\\"/g'; }
sed -e "s|__ALERT_EMAIL_TO__|$(escape "${ALERT_EMAIL_TO:?Falta ALERT_EMAIL_TO}")|" \
    -e "s|__EMAIL_FROM__|$(escape "${EMAIL_FROM:-alertas@localhost}")|" \
    -e "s|__SMTP_HOST__|$(escape "${SMTP_HOST:?Falta SMTP_HOST}")|" \
    -e "s|__SMTP_PORT__|$(escape "${SMTP_PORT:-587}")|" \
    -e "s|__SMTP_USERNAME__|$(escape "${SMTP_USERNAME:-}")|" \
    -e "s|__SMTP_STARTTLS__|${SMTP_STARTTLS:-true}|" \
    /etc/alertmanager/alertmanager.yml.tmpl > /tmp/alertmanager.yml
exec /bin/alertmanager --config.file=/tmp/alertmanager.yml --storage.path=/alertmanager "$@"
