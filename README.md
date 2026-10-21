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

### Login con Google / Microsoft

Opcional. Registra la aplicación en el proveedor con la URI de retorno
`${PUBLIC_APP_URL}/api/v1/auth/oidc/<google|microsoft>/callback` y rellena `GOOGLE_CLIENT_ID`/`SECRET`
o `MICROSOFT_CLIENT_ID`/`SECRET`/`TENANT`. Los botones aparecen solos en el login cuando hay credenciales.

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

> Next.js 16 tiene cambios incompatibles con versiones anteriores (`middleware` → `proxy`, `cacheComponents`).
> Consulta `apps/web/node_modules/next/dist/docs/` antes de usar una API.

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
- Garage no implementa SSE-S3: en on-premise el cifrado en reposo va en el volumen. Con S3
  gestionado, usa `S3_SERVER_SIDE_ENCRYPTION=AES256` o `aws:kms`.
