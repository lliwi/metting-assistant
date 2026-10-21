# Especificación técnica — Aplicación web de transcripción y apuntes con IA

**Versión:** 1.0  
**Fecha:** 21 de octubre de 2026  
**Estado:** Base para diseño, estimación y desarrollo

## 1. Propósito y alcance

Desarrollar una aplicación web responsive, instalable como PWA, que permita capturar o importar audio, transcribirlo, identificar participantes y generar documentación útil mediante IA: apuntes, resúmenes, actas, tareas, decisiones y preguntas abiertas.

El caso de uso principal son reuniones, clases y entrevistas de **aproximadamente una hora**. La aplicación debe funcionar desde ordenador y móvil, conservando el audio ya grabado si la conectividad se interrumpe.

### Límites de la versión web

- En escritorio y Android, una hora de audio es un escenario objetivo fiable si la aplicación permanece activa.
- En iOS/Safari no se puede garantizar la grabación con pantalla bloqueada o en segundo plano: el sistema puede suspender la página.
- Cerrar el navegador, revocar el permiso de micrófono o una llamada entrante puede interrumpir la captura. La aplicación debe preservar los segmentos ya confirmados y permitir reanudar.
- No se promete grabación continua de larga duración con móvil bloqueado; ese requisito requeriría una aplicación nativa.

## 2. Perfiles de usuario

- **Usuario individual:** graba, consulta, edita y exporta su contenido.
- **Administrador de organización:** gestiona miembros, políticas de retención, integraciones, plantillas y auditoría.
- **Auditor/lector:** acceso de solo lectura a recursos compartidos cuando la organización lo permita.

## 3. Requisitos funcionales

### 3.1 Identidad y organizaciones

- Registro mediante email y contraseña, Google, Microsoft y Apple, según mercado objetivo.
- Inicio de sesión mediante OIDC/OAuth 2.1.
- MFA TOTP y WebAuthn opcional en el MVP; obligatorio y configurable por organización en entorno empresarial.
- Espacios personales y de organización, con aislamiento lógico entre organizaciones.
- Roles iniciales: `owner`, `admin`, `member`, `viewer`.

### 3.2 Captura e importación de audio

- Solicitud explícita de permiso de micrófono antes de iniciar una grabación.
- Controles para iniciar, pausar, reanudar y finalizar una sesión.
- Indicador persistente de estado, duración, nivel de entrada, conectividad y segmentos pendientes.
- Captura mediante `MediaRecorder` en formato WebM/Opus cuando esté disponible; fallback compatible por navegador.
- Importación de MP3, M4A, AAC, WAV, OGG, WebM y vídeo MP4/MOV, con extracción de audio en backend.
- Duración objetivo por sesión: 60 minutos; límite configurable por plan y política.
- Grabación segmentada en bloques de 30–60 segundos; nunca mantener una hora completa en memoria del navegador.
- Al finalizar cada bloque, subida inmediata y con reintento al backend.
- Copia temporal en IndexedDB hasta recibir confirmación del servidor.
- Reanudación de una grabación fallida como nueva pista dentro de la misma sesión.

### 3.3 Biblioteca y edición

- Listado de sesiones con título, fecha, duración, estado, etiquetas y propietario.
- Carpetas, etiquetas, favoritos, archivado y papelera con recuperación durante el periodo de retención.
- Búsqueda por título, etiqueta, texto transcrito, participante y rango temporal.
- Reproductor con salto al instante referido por un fragmento de transcripción.
- Edición manual de transcripción, títulos, nombres de hablantes, etiquetas y resultados de IA.
- Historial de cambios de transcripción y resúmenes.

### 3.4 Transcripción y análisis

- Cola asíncrona para normalización, transcripción, diarización, indexación y generación de IA.
- Soporte inicial: español, catalán e inglés; detección de idioma configurable.
- Transcripción con puntuación, párrafos, marcas temporales y confianza cuando el proveedor lo aporte.
- Diarización de interlocutores, con asignación y renombrado manual.
- Estados visibles: `subiendo`, `pendiente`, `procesando`, `transcrito`, `fallido` y `cancelado`.
- Reprocesado de una sesión con otro idioma, plantilla o proveedor, sujeto a permisos y coste.

### 3.5 Resultados de IA

- Plantillas iniciales: resumen ejecutivo, apuntes, acta de reunión, tareas y decisiones.
- Cada tarea debe incluir responsable, fecha límite si se ha mencionado y referencia temporal a la fuente.
- Preguntas abiertas y riesgos identificados cuando se incluyan en la plantilla.
- Chat contextual sobre una sesión; las respuestas deben limitarse al contenido seleccionado y citar marcas temporales.
- Todos los resultados de IA deben ser editables y exportables.
- Versionado de prompts y plantillas para trazabilidad y reproducibilidad.
- Aviso visible: el contenido generado puede contener errores y requiere revisión humana.

### 3.6 Compartición e integración

- Exportar a TXT, Markdown, DOCX, PDF y subtítulos SRT/VTT.
- Enlaces compartidos con caducidad, contraseña, revocación y permisos de lectura o comentario.
- Integraciones de fase posterior: Google Drive, OneDrive, Notion, Microsoft Teams, Slack y webhook.
- API documentada para integraciones corporativas, protegida mediante OAuth y scopes.

## 4. Requisitos no funcionales

| Área | Objetivo |
| --- | --- |
| Disponibilidad | 99,9 % mensual para API y procesamiento, excluyendo mantenimiento anunciado. |
| Rendimiento | La interfaz debe reflejar la confirmación de cada segmento en menos de 3 s en una red móvil razonable. |
| Procesamiento | Objetivo inicial: transcripción de una hora en menos de 10 min tras recibir el último segmento, dependiente del proveedor. |
| Escalabilidad | Workers horizontales independientes para conversión, ASR, diarización y LLM. |
| Accesibilidad | WCAG 2.2 AA y navegación mediante teclado. |
| Internacionalización | Interfaz inicialmente en español y catalán; estructura i18n preparada para más idiomas. |
| Observabilidad | Logs estructurados, métricas, trazas distribuidas, alertas, paneles y seguimiento de coste por trabajo. |
| Calidad | Pruebas unitarias, integración, E2E, carga, seguridad y aceptación con corpus de audio real. |

## 5. Seguridad, privacidad y cumplimiento

- Mostrar un aviso de consentimiento antes de grabar y exigir que el usuario confirme que cuenta con autorización de los participantes.
- Cifrado TLS 1.2+ en tránsito y cifrado AES-256 o equivalente en reposo.
- Gestionar claves con KMS/Vault; rotación y separación de claves por entorno.
- RBAC por organización, workspace, sesión y enlace compartido; mínimo privilegio por defecto.
- Auditoría de inicios de sesión, acceso a audio, exportaciones, comparticiones, modificaciones, eliminaciones y acciones administrativas.
- URLs de descarga firmadas, de corta duración y vinculadas a permisos efectivos.
- Análisis antivirus de archivos importados, validación MIME real, límites de tamaño/duración y protección contra ficheros malformados.
- Rate limiting, WAF, protección CSRF, cabeceras seguras, gestión de secretos y escaneo de dependencias.
- Retención configurable; borrado lógico, periodo de recuperación y borrado definitivo verificable.
- Exportación de datos y mecanismos para atender derechos RGPD.
- Residencia de datos en la UE si el tratamiento lo requiere; DPA con proveedores y evaluación de transferencias internacionales.
- No usar audios, transcripciones ni resultados de clientes para entrenar modelos sin consentimiento explícito, granular y revocable.

## 6. Arquitectura propuesta

### Componentes

1. **Frontend PWA**: Next.js/React y TypeScript. Grabación con `MediaRecorder`, persistencia temporal en IndexedDB y subida por bloques.
2. **API**: FastAPI/Python o NestJS/Node.js. Recomendación: FastAPI para facilitar integración con pipelines de IA.
3. **PostgreSQL**: usuarios, organizaciones, permisos, metadatos, estados de trabajo, auditoría y plantillas.
4. **Almacenamiento S3 compatible**: audio segmentado, audio normalizado, exportaciones y adjuntos, con cifrado y políticas de ciclo de vida.
5. **Cola y workers**: Redis + Celery/RQ (Python) o BullMQ (Node). Trabajos idempotentes y reintentos con backoff.
6. **Procesamiento multimedia**: FFmpeg aislado para normalización, extracción y concatenación.
7. **ASR y diarización**: proveedor externo, Whisper autoalojado o servicio interno, seleccionable por configuración.
8. **LLM**: proveedor externo o modelo autoalojado para resúmenes y extracción estructurada, siempre detrás de una interfaz de proveedor.
9. **Búsqueda**: PostgreSQL full-text para MVP; OpenSearch cuando el volumen o la relevancia lo justifiquen.
10. **Observabilidad**: OpenTelemetry, Prometheus, Grafana y agregación centralizada de logs.

### Flujo de una grabación

1. El navegador crea una sesión y captura un segmento de 30–60 s.
2. El segmento se cifra en tránsito, se sube y se confirma mediante un identificador idempotente.
3. El frontend conserva la copia en IndexedDB hasta la confirmación.
4. Al cerrar la sesión, un worker normaliza/concatena el audio y desencadena ASR y diarización.
5. La transcripción se indexa y se generan los documentos solicitados con IA.
6. La interfaz recibe actualizaciones por SSE o WebSocket y permite revisar, editar o exportar.

## 7. Modelo de datos mínimo

| Entidad | Campos principales |
| --- | --- |
| `users` | id, email, nombre, estado, MFA, created_at |
| `organizations` | id, nombre, configuración, retención |
| `memberships` | user_id, organization_id, rol |
| `recordings` | id, organización, propietario, título, estado, idioma, duración, created_at |
| `audio_segments` | id, recording_id, orden, duración, objeto S3, checksum, estado |
| `transcripts` | id, recording_id, versión, idioma, contenido, proveedor, estado |
| `transcript_segments` | transcript_id, inicio_ms, fin_ms, hablante, texto, confianza |
| `ai_outputs` | id, recording_id, tipo, plantilla, versión_prompt, contenido, estado |
| `share_links` | id, recurso, permisos, expiración, hash_password, revocado |
| `audit_events` | id, actor, acción, recurso, IP/pseudónimo, fecha, resultado |

## 8. API mínima

- `POST /v1/recordings` — crear sesión.
- `POST /v1/recordings/{id}/segments` — subir segmento idempotente.
- `POST /v1/recordings/{id}/complete` — cerrar captura y encolar procesamiento.
- `GET /v1/recordings/{id}` — detalle y estado.
- `GET /v1/recordings/{id}/transcript` — transcripción y marcas temporales.
- `PATCH /v1/transcripts/{id}` — editar contenido transcrito.
- `POST /v1/recordings/{id}/ai-outputs` — solicitar resumen/apuntes/acta.
- `GET /v1/recordings/{id}/exports/{format}` — crear o descargar exportación.
- `POST /v1/recordings/{id}/share-links` — crear enlace compartido.
- `DELETE /v1/recordings/{id}` — solicitar eliminación conforme a permisos y retención.

La API debe exponer OpenAPI, aplicar validación estricta, devolver errores consistentes y propagar un `correlation_id` por solicitud.

## 9. Despliegue con Docker

### 9.1 Servicios en Docker Compose

Para desarrollo y primera instalación autocontenida se utilizará Docker Compose con los siguientes servicios:

| Servicio | Imagen/tecnología | Responsabilidad |
| --- | --- | --- |
| `web` | Next.js | PWA y UI. |
| `api` | FastAPI + Uvicorn/Gunicorn | API y autenticación. |
| `worker` | Celery + Python | Transcripción, IA y exportaciones. |
| `postgres` | PostgreSQL 16 | Datos transaccionales. |
| `redis` | Redis 7 | Cola, caché y coordinación. |
| `minio` | MinIO | Almacenamiento S3 compatible en desarrollo/on-premise. |
| `nginx` | Nginx/Traefik | TLS, reverse proxy, rate limit y cabeceras. |
| `otel-collector` | OpenTelemetry Collector | Telemetría opcional. |

### 9.2 `compose.yaml` de referencia

```yaml
services:
  web:
    build: ./apps/web
    environment:
      NEXT_PUBLIC_API_URL: https://app.example.com/api
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped

  api:
    build: ./apps/api
    env_file: .env.production
    command: gunicorn app.main:app -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:8000
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      minio:
        condition: service_healthy
    restart: unless-stopped

  worker:
    build: ./apps/api
    env_file: .env.production
    command: celery -A app.worker.celery_app worker --loglevel=INFO --concurrency=2
    depends_on:
      api:
        condition: service_healthy
    restart: unless-stopped

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: transcribe
      POSTGRES_USER: transcribe
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U transcribe -d transcribe"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  redis:
    image: redis:7-alpine
    command: redis-server --appendonly yes --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "--no-auth-warning", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: ${MINIO_ROOT_USER}
      MINIO_ROOT_PASSWORD: ${MINIO_ROOT_PASSWORD}
    volumes:
      - minio_data:/data
    healthcheck:
      test: ["CMD", "mc", "ready", "local"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  nginx:
    image: nginx:1.27-alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./infra/nginx:/etc/nginx/conf.d:ro
      - ./certs:/etc/ssl/private:ro
    depends_on:
      - web
      - api
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  minio_data:
```

### 9.3 Reglas de despliegue

- Usar imágenes con versiones fijadas y digest en producción; no usar `latest`.
- No incluir secretos dentro de imágenes, repositorio ni archivos Compose versionados.
- Mantener `.env.production` fuera del repositorio; preferir Docker Secrets, Vault o el gestor de secretos del cloud.
- Ejecutar migraciones de base de datos como job controlado antes de actualizar la API.
- Configurar copias de seguridad cifradas de PostgreSQL y almacenamiento de objetos, con pruebas periódicas de restauración.
- Exponer solamente el reverse proxy; PostgreSQL, Redis, MinIO y workers deben estar en redes internas.
- Ejecutar contenedores sin privilegios, con filesystem de solo lectura cuando sea viable, `cap_drop: [ALL]`, límites de CPU/memoria y políticas de reinicio.
- Separar perfiles Compose: `dev`, `test`, `prod` y `observability`.
- En producción con alta disponibilidad, migrar los servicios con estado a servicios gestionados o Kubernetes; mantener workers escalables de forma independiente.

### 9.4 Variables de entorno mínimas

```dotenv
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://transcribe:CHANGE_ME@postgres:5432/transcribe
REDIS_URL=redis://:CHANGE_ME@redis:6379/0
S3_ENDPOINT=http://minio:9000
S3_BUCKET=recordings
S3_ACCESS_KEY=CHANGE_ME
S3_SECRET_KEY=CHANGE_ME
JWT_SIGNING_KEY=CHANGE_ME
OIDC_ISSUER=https://identity.example.com
ASR_PROVIDER=whisper|managed_provider
LLM_PROVIDER=managed_provider|self_hosted
POSTGRES_PASSWORD=CHANGE_ME
REDIS_PASSWORD=CHANGE_ME
MINIO_ROOT_USER=CHANGE_ME
MINIO_ROOT_PASSWORD=CHANGE_ME
```

## 10. MVP y fases

### MVP

1. PWA con registro e inicio de sesión.
2. Grabación segmentada e importación de audio.
3. Almacenamiento cifrado y procesamiento asíncrono.
4. Transcripción en español con marcas temporales.
5. Resumen, apuntes y tareas mediante plantillas.
6. Biblioteca, búsqueda, edición y exportación Markdown/PDF.
7. Roles básicos, auditoría esencial, borrado y retención inicial.
8. Docker Compose para desarrollo y despliegue inicial.

### Fase 2

- Catalán e inglés, diarización mejorada y chat contextual.
- Enlaces compartidos, SSO/SAML y gestión avanzada de organizaciones.
- Integraciones y webhooks.
- DLP, BYOK, residencia de datos configurable y despliegue privado.
- Aplicación nativa para grabación robusta con el móvil bloqueado.

## 11. Criterios de aceptación clave

- Una sesión de 60 minutos completada en navegador de escritorio o Android, con la app activa, conserva todos los segmentos confirmados y genera una transcripción consultable.
- Al perder la red durante cinco minutos, los segmentos se conservan localmente y se sincronizan al volver la conexión.
- El usuario puede reproducir un fragmento desde cualquier párrafo de la transcripción.
- El resumen y las tareas presentan enlaces a sus marcas temporales de origen.
- Un usuario sin permisos no puede obtener audio, transcripciones, exportaciones ni URLs firmadas de otra organización.
- La eliminación completa de una sesión se registra en auditoría y respeta la política de retención.
- El despliegue con Docker Compose inicia los servicios, espera sus health checks y deja accesible la aplicación exclusivamente a través del reverse proxy.
