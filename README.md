# Metting Agent

Aplicación web (PWA) para grabar o importar audio, transcribirlo y generar apuntes, actas y tareas con IA.

- Especificación: [`especificacion-app-transcripcion-web.md`](especificacion-app-transcripcion-web.md)
- Plan de desarrollo: [`PLAN-DESARROLLO.md`](PLAN-DESARROLLO.md)

## Estructura

| Ruta | Contenido |
| --- | --- |
| `apps/web` | Next.js 16 (App Router) + TypeScript, PWA, i18n `es`/`ca` |
| `apps/api` | FastAPI, SQLAlchemy 2, Alembic, Celery (misma base de código para API y worker) |
| `infra/nginx` | Reverse proxy: único punto de entrada |
| `infra/garage` | Almacenamiento S3 compatible (Garage) para desarrollo y on-premise |
| `infra/compose` | Overrides de Compose (`dev.yaml`) |
| `infra/otel` | OpenTelemetry Collector (perfil `observability`) |

## Puesta en marcha

Requisitos: Docker con Compose v2, `openssl`.

```sh
./scripts/generate-env.sh                 # crea .env con secretos aleatorios
docker compose -f compose.yaml -f infra/compose/dev.yaml up -d --build --wait
```

- Aplicación: <http://localhost:8080>
- API: <http://localhost:8080/api/docs>
- Emails de desarrollo (Mailpit): <http://localhost:58025>

Si tu `.env` es anterior a la Fase 1, añade `APP_SECRET_KEY`, `DATA_ENCRYPTION_KEY`, `PUBLIC_APP_URL` y `COOKIE_SECURE` (ver `.env.example`).

### Usuarios

No hay registro público: las cuentas las crea un administrador con `scripts/users.sh`
(ejecuta `python -m app.auth.cli` en el contenedor de la API). Se crean activas y con
su espacio personal; cada usuario puede cambiar su contraseña en «Mi cuenta».

```sh
scripts/users.sh create ana@example.com --name "Ana"              # pide la contraseña
scripts/users.sh create bob@example.com --name "Bob" --org "Equipo" --role member \
    --generate-password                                             # la muestra una vez
scripts/users.sh add-to-org ana@example.com "Equipo" --role admin   # roles: owner|admin|member|viewer
scripts/users.sh set-password ana@example.com                      # cierra sus sesiones
scripts/users.sh disable ana@example.com                           # enable la reactiva
scripts/users.sh list
```

`--org` acepta el id o el nombre de la organización; si no existe, se crea con ese usuario
como propietario.

### Copias de seguridad

`scripts/backup.sh` guarda la base de datos (`pg_dump`) y todo el almacenamiento de objetos
(audio, exportaciones) en `backups/<fecha>/`, cifrados con GPG (AES-256) con
`BACKUP_PASSPHRASE` de `.env`. **Guarda esa frase también fuera del servidor**: sin ella las
copias no se pueden restaurar. Borra las copias de más de `BACKUP_KEEP_DAYS` días (14).

```sh
scripts/backup.sh                                  # programable con cron, p. ej. a las 03:15
scripts/restore.sh --verify backups/20261024T031500Z   # comprueba sin tocar los datos
scripts/restore.sh --yes    backups/20261024T031500Z   # restaura (sobrescribe todo)
```

`--verify` comprueba las sumas, restaura la base de datos en una base temporal y cuenta los
objetos. Copia la carpeta `backups/` a otro equipo o almacenamiento (p. ej. con `rclone`).

### Monitorización y alertas

Perfil `monitoring`: Prometheus (consulta `/metrics` de la API por la red interna; nginx no la
publica), Alertmanager (avisos por email con el SMTP de `.env` a `ALERT_EMAIL_TO`) y Grafana
con el panel «Metting Agent» ya cargado (usuario `admin`, contraseña `GRAFANA_ADMIN_PASSWORD`).

```sh
docker compose -f compose.yaml -f infra/compose/dev.yaml --profile monitoring up -d
```

En desarrollo: Grafana <http://localhost:53001>, Prometheus <http://localhost:59090>,
Alertmanager <http://localhost:59093> (los emails llegan a Mailpit). En producción no publican
puertos: usa un túnel SSH (`ssh -L 3000:grafana:3000`…) o publícalos tras nginx con TLS.

Alertas (`infra/monitoring/alerts.yml`, umbrales ajustables): API caída, dependencia caída
(BD, Redis, almacenamiento), más del 5 % de errores 5xx, colas de trabajos acumuladas,
grabaciones atascadas o fallidas, documentos de IA fallidos y coste mensual de IA alto.

### Login con Google / Microsoft

Opcional. Registra la aplicación en el proveedor con la URI de retorno
`${PUBLIC_APP_URL}/api/v1/auth/oidc/<google|microsoft>/callback` y rellena `GOOGLE_CLIENT_ID`/`SECRET`
o `MICROSOFT_CLIENT_ID`/`SECRET`/`TENANT`. Los botones aparecen solos en el login cuando hay credenciales.
Solo dan acceso a cuentas ya creadas: Google se vincula por email verificado; Microsoft no
garantiza el email, así que no vincula cuentas existentes por email.

`--wait` espera a los health checks. Las migraciones se ejecutan en el job `migrate` antes de que arranque la API y el worker.

Sin el override de desarrollo (`docker compose up -d --build --wait`) solo nginx publica puertos.
Con `dev.yaml`, PostgreSQL, Redis y Garage se exponen en `127.0.0.1` (puertos `55432`, `56379`, `53900`, configurables con `DEV_*_PORT`).

## Desarrollo local

### API

```sh
cd apps/api
uv sync
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run pytest                                   # tests unitarios
# Los tests de BD borran y recrean el esquema: usa siempre una base de datos aparte.
docker compose exec postgres createdb -U metting metting_test
TEST_DATABASE_URL=postgresql+psycopg://metting:<POSTGRES_PASSWORD>@localhost:55432/metting_test \
  uv run pytest                                 # + tests contra PostgreSQL (BD desechable)
```

Nueva migración (con el stack de desarrollo levantado):

```sh
DATABASE_URL=postgresql+psycopg://metting:<POSTGRES_PASSWORD>@localhost:55432/metting \
  uv run alembic revision --autogenerate -m "descripción"
```

Revisa siempre la migración generada antes de aplicarla.

### Web

```sh
cd apps/web
npm ci
API_PROXY_TARGET=http://localhost:8080 npm run dev   # http://localhost:3000, /api → stack
npm run lint && npm run check:i18n && npm test && npm run typecheck && npm run build
npm run gen:api    # regenera los tipos TS tras cambiar la API (CI comprueba que están al día)
```

### Pruebas E2E (Playwright)

Contra la aplicación levantada con Docker Compose. Crean (o reactivan) la cuenta
`playwright.e2e@example.com` con `scripts/users.sh` y recorren: acceso sin registro, importar
un audio y transcribirlo, buscar, corregir una frase, exportar, grabar con un micrófono
simulado, cambiar la contraseña, crear y eliminar una organización y la vista móvil.

```sh
cd apps/web
npx playwright install chromium     # la primera vez
npm run e2e                         # E2E_BASE_URL=… para otro entorno
```

La transcripción usa el motor configurado: con OpenAI cuesta ~0,01 $ por ejecución. La CI las
ejecuta con el motor local (sin coste).

> Next.js 16 tiene cambios incompatibles con versiones anteriores (`middleware` → `proxy`, `cacheComponents`).
> Consulta `apps/web/node_modules/next/dist/docs/` antes de usar una API.

## Transcripción e IA

| Uso | Proveedor por defecto | Alternativa |
| --- | --- | --- |
| Transcripción (ASR) | `faster_whisper` local en `worker-asr` (el audio no sale) | `openai`: API de OpenAI o servidor compatible (`OPENAI_BASE_URL`) |
| Resúmenes, actas, tareas | `ollama` (`OLLAMA_BASE_URL`, `gemma3:4b`) | `openai` (`OPENAI_LLM_MODEL`) |

- Ollama no transcribe audio: los modelos de texto (gemma3) se usan solo para generar documentos.
- `WHISPER_MODEL`: `small` es un buen equilibrio en CPU; `medium`/`large-v3` son más precisos
  y más lentos. El modelo se descarga la primera vez al volumen `whisper_models`.
- `OLLAMA_NUM_CTX` (16384 por defecto): Ollama usa 4096 tokens si no se indica, insuficiente
  para una reunión de una hora.
- Comprobar la configuración: `docker compose exec worker-asr python -m app.ai.check`.

### Resumen, acta e identificación de hablantes

- **Bajo demanda**, desde la página de cada grabación, se genera un **resumen** (con puntos
  clave) o un **acta** (asistentes, temas, decisiones, tareas con responsable y fecha,
  preguntas abiertas). Con `AI_AUTO_GENERATE=true` se generan solos al transcribir. Cada punto cita los segmentos de la transcripción de los que
  sale; en la web enlazan al momento correspondiente. Se pueden regenerar.
- `DIARIZATION_PROVIDER` decide cómo se identifica a quién habla:
  - `none`: sin hablantes.
  - `openai`: `gpt-4o-transcribe-diarize` transcribe e identifica a la vez (sustituye a
    Whisper; el audio va a OpenAI). Probado: separa correctamente dos voces.
  - `pyannote`: local, en la GPU, tras Whisper. Requiere `infra/compose/gpu.yaml` y un
    `HF_TOKEN` de una cuenta de huggingface.co que haya aceptado las condiciones de
    `PYANNOTE_MODEL` (https://huggingface.co/pyannote/speaker-diarization-community-1).
- Los hablantes aparecen como "Hablante 1, 2…"; en la página de la grabación se les puede
  poner nombre. El acta y el resumen usan esos nombres al regenerarlos.
- Al cerrar una grabación se encola `process_recording` (cola `asr`): une la captura del
  navegador (o prepara la importación), transcribe y guarda una versión nueva. Reprocesar
  (`POST /v1/recordings/{id}/reprocess`) crea otra versión sin borrar las anteriores.

## Convenciones de la API

- Todas las respuestas llevan `X-Correlation-ID` (se respeta el del cliente si es válido).
- Errores con formato único: `{"error": {"code", "message", "details", "correlation_id"}}`.
- `/healthz` (proceso vivo) y `/readyz` (dependencias accesibles).
- Sesión en cookie `HttpOnly` (`ma_session`). Las peticiones que modifican estado envían
  `X-CSRF-Token` con el valor de la cookie `ma_csrf` (se rota al iniciar sesión: léela siempre).
- Autorización por organización en `app/orgs/permissions.py`: quien no es miembro recibe 404.

## Captura de audio

- El navegador graba en segmentos de 30 s (`apps/web/src/lib/recorder/`). Cada segmento usa un
  `MediaRecorder` nuevo para ser decodificable por sí solo; el siguiente arranca antes de parar
  el anterior para no dejar huecos.
- Cada segmento se guarda en IndexedDB **antes** de subirse y solo se borra cuando el servidor
  confirma su SHA-256. La cola reintenta con backoff exponencial (máx. 60 s), se reanuda al
  volver la red o al recargar, y espera si la sesión caduca.
- `POST /v1/recordings/{id}/segments` es idempotente (id generado en el cliente + checksum) y
  valida el contenedor real por sus bytes. `POST …/complete` recibe cuántos segmentos produjo
  cada pista y responde 409 `missing_segments` si falta alguno.
- Una captura interrumpida (micrófono, permiso, cierre) se reanuda en una pista nueva.
- **Importación** (`/import`): el archivo se sube en bloques de 8 MB por el mismo endpoint
  (idempotente y reanudable). Al cerrar, el worker une los bloques verificando su checksum,
  lo analiza con ClamAV, valida con ffprobe que tiene audio y no supera la duración máxima, y
  extrae el audio a Opus mono. El original se borra; queda solo el audio.
- **Antivirus**: en producción activa el perfil con `COMPOSE_PROFILES=antivirus` (ya viene en
  `.env.example`). Si ClamAV no responde, las importaciones se rechazan (`scan_unavailable`)
  tras reintentos: nunca se aceptan sin analizar. En desarrollo (`dev.yaml`) está desactivado.
- Garage no implementa SSE-S3: en on-premise el cifrado en reposo va en el volumen. Con S3
  gestionado, usa `S3_SERVER_SIDE_ENCRYPTION=AES256` o `aws:kms`.
