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
npm run dev        # http://localhost:3000
npm run lint && npm run typecheck && npm run build
```

> Next.js 16 tiene cambios incompatibles con versiones anteriores (`middleware` → `proxy`, `cacheComponents`).
> Consulta `apps/web/node_modules/next/dist/docs/` antes de usar una API.

## Convenciones de la API

- Todas las respuestas llevan `X-Correlation-ID` (se respeta el del cliente si es válido).
- Errores con formato único: `{"error": {"code", "message", "details", "correlation_id"}}`.
- `/healthz` (proceso vivo) y `/readyz` (dependencias accesibles).
