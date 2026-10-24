# Metting Agent — Plan de desarrollo

**Basado en:** `especificacion-app-transcripcion-web.md` v1.0
**Fecha:** 21 de octubre de 2026
**Alcance de este plan:** MVP completo (sección 10 de la especificación) + preparación de la Fase 2.

---

## 1. Decisiones de arquitectura

| Tema | Decisión | Motivo |
| --- | --- | --- |
| Repositorio | Monorepo: `apps/web`, `apps/api`, `packages/`, `infra/` | Un solo versionado para contrato API, Compose y CI. |
| Frontend | Next.js (App Router) + React + TypeScript, PWA con service worker | Indicado en la especificación. |
| API | FastAPI + SQLAlchemy 2 + Alembic + Pydantic v2 | Recomendación de la especificación; mismo lenguaje que los workers. |
| Workers | Celery + Redis, colas separadas: `media`, `asr`, `llm`, `exports` | Escalado horizontal independiente por tipo de trabajo (sección 4). |
| Contrato | OpenAPI generado por FastAPI → cliente TypeScript generado (`openapi-typescript`) en `packages/api-client` | Evita desalineación front/back. |
| Autenticación | OIDC propio con Authlib en la API (email+contraseña, Google, Microsoft); sesión por cookie `HttpOnly` + CSRF; Apple y SAML en Fase 2 | Cumple OAuth 2.1/OIDC sin depender de un IdP externo en el MVP. Alternativa a valorar: Keycloak/Zitadel como contenedor. |
| ASR | Interfaz `AsrProvider` con dos implementaciones: proveedor gestionado y `faster-whisper` autoalojado | `ASR_PROVIDER` configurable (sección 9.4). |
| Diarización | `pyannote` (autoalojado) o la del proveedor ASR, detrás de `DiarizationProvider` | MVP: diarización básica; mejora en Fase 2. |
| LLM | Interfaz `LlmProvider`; implementación por defecto con la API de Claude (`claude-sonnet-5-5`), salida estructurada JSON validada con Pydantic | Extracción de tareas/decisiones con referencias temporales verificables. |
| Tiempo real | SSE para estados de grabación y trabajos | Más simple que WebSocket; flujo unidireccional servidor→cliente. |
| Búsqueda | PostgreSQL full-text (`tsvector`, configuraciones `spanish`/`simple`) | Suficiente para el MVP (sección 6.9). |
| i18n | Diccionarios nativos de Next.js (`app/[lang]`, `proxy.ts`) con `es` y `ca` | Patrón oficial de Next 16; sin dependencia extra ni riesgos con `cacheComponents`. |
| Almacenamiento S3 (dev/on-premise) | Garage v2 (`dxflrs/garage`) en modo `--single-node` | MinIO dejó de publicar imágenes de la edición comunitaria; en producción, S3 gestionado. |

### Estructura del repositorio

```
metting-agent/
├── apps/
│   ├── web/                 # Next.js PWA
│   └── api/
│       ├── app/
│       │   ├── main.py
│       │   ├── core/        # config, seguridad, logging, correlation_id
│       │   ├── auth/
│       │   ├── orgs/
│       │   ├── recordings/  # sesiones, segmentos, subida
│       │   ├── transcripts/
│       │   ├── ai/          # plantillas, prompts versionados, salidas
│       │   ├── exports/
│       │   ├── audit/
│       │   ├── providers/   # asr/, diarization/, llm/, storage/
│       │   └── worker/      # celery_app y tareas
│       ├── migrations/      # Alembic
│       └── tests/
├── packages/
│   └── api-client/          # cliente TS generado desde OpenAPI
├── infra/
│   ├── nginx/
│   ├── compose/             # perfiles dev, test, prod, observability
│   └── otel/
├── tests/
│   ├── e2e/                 # Playwright
│   ├── load/                # k6
│   └── audio-corpus/        # referencias (no versionar audio real)
└── compose.yaml
```

---

## 2. Fases y entregables

Estimación orientativa para un equipo de 2–3 personas (1 frontend, 1 backend, 0,5 IA/DevOps). Iteraciones de 2 semanas.

### Fase 0 — Cimientos (iteración 1)

- Monorepo, linters y formateo (ruff, mypy, eslint, prettier), pre-commit.
- `compose.yaml` + override `infra/compose/dev.yaml` y perfil `observability`: `web`, `api`, `migrate`, `worker`, `postgres`, `redis`, `garage`, `nginx`.
- Health checks en todos los servicios (incluido `api`, que la especificación usa en `depends_on` pero no define).
- FastAPI base: configuración por entorno, logs estructurados JSON, middleware `correlation_id`, formato de error único, `/healthz` y `/readyz`.
- Alembic y primera migración: `users`, `organizations`, `memberships`, `audit_events`.
- CI (GitHub Actions): lint, tests, build de imágenes, escaneo de dependencias (pip-audit, npm audit) e imágenes (Trivy).
- Next.js base: layout, i18n `es`/`ca`, sistema de diseño accesible, manifest PWA.

**Hecho cuando:** `docker compose --profile dev up` arranca todo, espera health checks y la web responde solo a través de `nginx`.

### Fase 1 — Identidad y organizaciones (iteración 2)

- Registro y login con email+contraseña (Argon2id), verificación de email, recuperación de contraseña.
- Login con Google y Microsoft (OIDC).
- MFA TOTP opcional; WebAuthn opcional si da tiempo (si no, pasa a Fase 2).
- Sin registro público: cuentas creadas por un administrador (`scripts/users.sh`), cada una con su espacio personal; organizaciones con roles `owner`, `admin`, `member`, `viewer`.
- Capa de autorización central (`authorize(actor, action, resource)`) usada por todos los endpoints; filtro obligatorio por `organization_id` en el repositorio de datos.
- Auditoría de inicios de sesión y acciones administrativas.
- Rate limiting en login (Redis) y protección CSRF.

**Hecho cuando:** test de integración demuestra que un usuario de la organización A recibe 404 en cualquier recurso de la organización B.

### Fase 2 — Captura y subida segmentada (iteraciones 3–4) — *ruta crítica*

**Frontend**
- Pantalla de consentimiento obligatoria antes de grabar (confirmación de autorización de participantes).
- Grabador: `getUserMedia` + `MediaRecorder` (WebM/Opus; fallback `audio/mp4` en Safari), segmentos de 30–60 s con `timeslice` o reinicio controlado del recorder para que cada segmento sea decodificable de forma independiente.
- Cola local en IndexedDB (`segment_id`, `recording_id`, `track`, `order`, `blob`, `checksum`, `status`); borrado solo tras confirmación del servidor.
- Uploader con reintentos y backoff exponencial, reanudación al recuperar red (`online` + sondeo), Background Sync donde exista.
- Indicadores persistentes: duración, vúmetro (AnalyserNode), conectividad, segmentos pendientes.
- Pausa/reanudar/finalizar; Wake Lock API para evitar suspensión de pantalla; aviso explícito de limitaciones en iOS.
- Reanudación tras fallo como nueva pista (`track`) en la misma sesión.

**Backend**
- `POST /v1/recordings`, `POST /v1/recordings/{id}/segments` (idempotente por `segment_id` + checksum SHA-256), `POST /v1/recordings/{id}/complete`, `GET /v1/recordings/{id}`.
- Almacenamiento en MinIO con cifrado SSE y claves por entorno; prefijos por organización.
- Validación MIME real (libmagic/ffprobe), límites de tamaño y duración por plan.
- SSE `GET /v1/recordings/{id}/events` con estado de segmentos y trabajos.

**Importación**
- Subida de MP3, M4A, AAC, WAV, OGG, WebM, MP4, MOV mediante subida multiparte a S3 con URL firmada.
- Análisis antivirus (ClamAV como servicio interno) antes de procesar.

**Hecho cuando:** se cumplen los criterios de aceptación 1 y 2 (sesión de 60 min en escritorio y Android; corte de red de 5 min sin pérdida).

### Fase 3 — Pipeline de procesamiento (iteraciones 5–6)

- Cadena Celery idempotente: `normalize` (FFmpeg: concatenación de pistas, 16 kHz mono) → `transcribe` → `diarize` → `align` → `index` → `generate_ai_outputs`.
- FFmpeg en contenedor aislado sin red, usuario sin privilegios, límites de CPU/memoria y timeout.
- Estados de sesión: `subiendo`, `pendiente`, `procesando`, `transcrito`, `fallido`, `cancelado`, como máquina de estados explícita.
- Proveedor ASR configurable; MVP en español con marcas temporales, puntuación y confianza.
- Diarización básica con etiquetas `Hablante 1..n`.
- Tablas `transcripts` (versionadas) y `transcript_segments`; índice `tsvector`.
- Registro de coste y duración por trabajo (métricas Prometheus).
- Reprocesado con otro idioma/proveedor (crea nueva versión de transcripción).

**Hecho cuando:** una hora de audio se transcribe en < 10 min tras el último segmento con el proveedor elegido, medido sobre el corpus de prueba.

### Fase 4 — Biblioteca, reproductor y edición (iteraciones 6–7)

- Listado de sesiones con filtros (fecha, estado, etiquetas, propietario), carpetas, favoritos, archivado y papelera.
- Búsqueda por título, etiqueta, texto, participante y rango temporal.
- Vista de sesión: reproductor (audio servido con URL firmada de corta duración y soporte `Range`) sincronizado con la transcripción; clic en párrafo → salto al instante.
- Edición de transcripción, títulos y nombres de hablantes; historial de versiones (`PATCH /v1/transcripts/{id}` con control de concurrencia optimista por `version`).
- Accesibilidad WCAG 2.2 AA: navegación por teclado en reproductor y editor, atajos documentados.

**Hecho cuando:** criterio de aceptación 3 (reproducir desde cualquier párrafo).

### Fase 5 — Resultados de IA (iteraciones 7–8)

- Plantillas versionadas en base de datos: resumen ejecutivo, apuntes, acta, tareas y decisiones.
- Prompts versionados (`template_id`, `prompt_version`) guardados junto a cada `ai_output`.
- Transcripción enviada al LLM con identificadores de segmento (`[S123 00:12:34]`); el modelo devuelve JSON con `source_segment_ids`; la API valida que existan y calcula los enlaces temporales.
- Tareas con responsable, fecha límite si se menciona y referencia a la fuente.
- Resultados editables con historial; aviso visible de revisión humana.
- `POST /v1/recordings/{id}/ai-outputs`.
- Evaluación: conjunto de 10–20 transcripciones de referencia con resultados esperados; test de regresión al cambiar prompts.

**Hecho cuando:** criterio de aceptación 4 (resumen y tareas enlazan a sus marcas temporales).

### Fase 6 — Exportación, retención y endurecimiento (iteraciones 9–10)

- Exportación Markdown y PDF (WeasyPrint) como trabajo asíncrono; `GET /v1/recordings/{id}/exports/{format}` con URL firmada. TXT, SRT y VTT son baratos y pueden incluirse; DOCX queda para Fase 2.
- Borrado lógico, periodo de recuperación, borrado definitivo (BD + objetos S3 + versiones) mediante tarea programada, con evento de auditoría.
- Políticas de retención por organización; exportación de datos personales (RGPD).
- Auditoría de acceso a audio, exportaciones, modificaciones y eliminaciones.
- Cabeceras de seguridad en Nginx (CSP, HSTS, etc.), TLS, rate limiting global.
- Perfil Compose `prod`: imágenes con digest, sin `latest` (incluido MinIO), `cap_drop: [ALL]`, `read_only`, redes internas, secretos fuera del repositorio, job de migraciones previo a la API.
- Copias de seguridad cifradas de PostgreSQL y MinIO, con prueba de restauración documentada.
- Perfil `observability`: OpenTelemetry Collector, Prometheus, Grafana, Loki; paneles de colas, latencia, errores y coste.

**Hecho cuando:** criterios de aceptación 5, 6 y 7.

### Fase 7 — Estabilización y lanzamiento MVP (iteración 11)

- Pruebas E2E completas (Playwright) en Chromium, Firefox, WebKit y Android real.
- Pruebas de carga (k6): N grabaciones simultáneas subiendo segmentos; worker bajo carga.
- Pruebas de seguridad: OWASP ZAP, revisión de autorización entre organizaciones, pentest ligero.
- Aceptación con corpus de audio real (reuniones, clases, entrevistas; distintos micrófonos y ruido).
- Documentación de despliegue y operación (runbooks).

---

## 3. Calendario resumido

| Iteración | Semanas | Contenido |
| --- | --- | --- |
| 1 | 1–2 | Fase 0 |
| 2 | 3–4 | Fase 1 |
| 3–4 | 5–8 | Fase 2 (captura) |
| 5–6 | 9–12 | Fase 3 (pipeline) · inicio Fase 4 |
| 7–8 | 13–16 | Fase 4 · Fase 5 (IA) |
| 9–10 | 17–20 | Fase 6 (exportación, seguridad, operación) |
| 11 | 21–22 | Fase 7 (estabilización) |

**MVP estimado: ~22 semanas.** Frontend y backend trabajan en paralelo a partir de la Fase 2 gracias al cliente generado desde OpenAPI.

---

## 4. Fase 2 del producto (post-MVP)

Orden recomendado por valor/esfuerzo:

1. Catalán e inglés en ASR y detección de idioma.
2. Chat contextual sobre la sesión, limitado al contenido y con citas temporales (reutiliza el esquema de `source_segment_ids`).
3. Enlaces compartidos (caducidad, contraseña, revocación, permisos de lectura/comentario).
4. Exportación DOCX; diarización mejorada.
5. API pública con OAuth y scopes; webhooks.
6. Integraciones: Google Drive, OneDrive, Notion, Teams, Slack.
7. SSO/SAML, MFA obligatorio por organización, login con Apple.
8. DLP, BYOK, residencia de datos configurable, despliegue privado / Kubernetes.
9. Aplicación nativa para grabación con el móvil bloqueado.

---

## 5. Riesgos principales

| Riesgo | Impacto | Mitigación |
| --- | --- | --- |
| Segmentos WebM no decodificables por separado (solo el primero lleva cabecera) | Pérdida o corrupción de audio | Reiniciar `MediaRecorder` por segmento, o conservar cabecera de inicialización y concatenar en backend; probar desde la iteración 3. |
| Suspensión de la página en iOS/Safari | Grabaciones incompletas | Wake Lock, aviso claro, reanudación como nueva pista; no prometer segundo plano. |
| Coste y latencia del ASR/LLM | Margen y objetivo de 10 min | Medir coste por trabajo desde la Fase 3; colas separadas; proveedor intercambiable. |
| Calidad de diarización en español con ruido | Actas con hablantes erróneos | Renombrado/reasignación manual; evaluación con corpus real. |
| Alucinaciones en tareas/decisiones | Pérdida de confianza | Referencias a segmentos validadas en backend; aviso de revisión; tests de regresión de prompts. |
| Fuga entre organizaciones | Crítico (RGPD) | Autorización centralizada, tests automatizados de aislamiento, URLs firmadas vinculadas a permisos. |
| Transferencias internacionales a proveedores IA | Cumplimiento | DPA, región UE cuando exista, opción autoalojada (Whisper) desde el MVP. |

---

## 6. Puntos a decidir antes de empezar

1. **Proveedor ASR del MVP:** gestionado (rápido de integrar, coste por minuto, transferencia de datos) o `faster-whisper` en GPU propia (control de datos, coste de infraestructura).
2. **IdP:** OIDC implementado en la API o contenedor Keycloak/Zitadel.
3. **Residencia de datos UE** obligatoria desde el MVP o no (condiciona proveedores ASR/LLM).
4. **Planes y límites** (duración máxima, almacenamiento) para el MVP.
5. **Alcance de exportación en el MVP:** solo Markdown/PDF (especificación) o añadir TXT/SRT/VTT.

## 7. Estado

| Fase | Estado | Notas |
| --- | --- | --- |
| 0 — Cimientos | ✅ Completada (21/10/2026) | Stack Compose arranca con health checks; CI definida. Pendiente: pre-commit, añadir S3 a `/readyz` cuando exista el cliente de almacenamiento (Fase 2), TLS en nginx para producción. |
| 5 — Resultados de IA (adelantada) | 🟡 En curso | Resumen y acta con citas a la transcripción (Ollama u OpenAI), regeneración y hablantes con nombre. Diarización OpenAI verificada; pyannote local pendiente de HF_TOKEN. |
| 3 — Transcripción | 🟡 En curso | ASR: faster-whisper local (CPU, `small`) u OpenAI; LLM: Ollama (`gemma3:4b` en 192.168.0.160) u OpenAI. Pipeline unir/preparar → transcribir, versiones y reprocesado. Pendiente: diarización. |
| 2 — Captura | ✅ Completada (22/10/2026) | Grabación segmentada, cola IndexedDB, subida idempotente, reanudación por pistas, SSE. Importación por bloques con ClamAV, ffprobe y extracción de audio. Verificado en Chrome: corte de red, interrupción del micrófono, decodificación independiente de cada segmento e importación. |
| 1 — Identidad | ✅ Completada (21/10/2026) | Registro/verificación, login con Argon2id, recuperación, sesiones revocables, CSRF, MFA TOTP con códigos de recuperación, OIDC Google/Microsoft, organizaciones con roles e invitaciones, auditoría y rate limiting. Probado en navegador de extremo a extremo. Pendiente: WebAuthn (pasa a Fase 2), login con Apple y SAML (Fase 2). |

## 8. Mejoras y pendientes (revisión del 23/10/2026)

Orden acordado:

1. **Robustez** ✅ — barrido de trabajos perdidos (celery beat), cerrojos por trabajo,
   visibility timeout de 6 h, borrado de segmentos tras unir el audio, audio local
   asociado a su usuario y aviso al cerrar sesión con audio pendiente.
2. **Reproductor y búsqueda** ✅ — audio servido por la API con `Range` (M4A/AAC, válido en
   Safari), saltar desde transcripción, resumen y acta al audio; búsqueda por texto,
   título, hablante y fechas con frases resaltadas.
3. **Edición y exportación** ✅ — renombrar grabaciones; corregir texto y hablante de cada
   frase con historial (quién, cuándo, antes/después); editar resumen y acta con versiones
   anteriores; exportar a PDF, DOCX, Markdown, TXT y subtítulos SRT/VTT eligiendo qué
   incluir.
4. **Modo híbrido local** ✅ — motor por grabación: local (Whisper turbo en GPU + pyannote;
   5 min de audio en ~30 s, sin coste y sin que el audio salga) u OpenAI (diarize; ~1,4× la
   duración y pierde la identidad de hablantes en audios de más de ~1 h). Se elige al volver
   a transcribir; si pyannote falla de forma permanente, se guarda la transcripción sin
   hablantes. PyTorch, torchaudio y torchcodec se instalan en su variante CUDA 12 para
   compartir cuDNN con ctranslate2. Probado: 2 voces, mismos 11 turnos que OpenAI; 5 min de
   audio diarizados en ~11 s (1,6 GiB de VRAM).
5. **Producción y pruebas** — en curso.
   - ✅ Coste por trabajo: tokens, minutos y coste estimado (AI_PRICES) por transcripción y
     documento; consumo mensual por organización (propietarios y administradores).
   - ✅ Copias de seguridad cifradas (base de datos + objetos) con verificación de restauración.
   - ✅ Eliminar organizaciones (cascada en BD + purga del almacenamiento) e italiano como
     idioma del audio (peticiones del 24/10).
   - Pendiente: monitorización, Playwright, carga, seguridad, aceptación con audio real, TLS
     real (dominio) y remoto para la CI.

Mejoras de calidad: vocabulario por organización (nombres propios para el ASR), riesgos en
el acta, ofrecer regenerar al renombrar hablantes, aviso al terminar (email/push),
detección automática de idioma, auditoría WCAG 2.2 AA, chat sobre la reunión.

## 9. Incoherencias detectadas en la especificación

- El `compose.yaml` de referencia usa `condition: service_healthy` sobre `api`, pero `api` no define `healthcheck`.
- `minio/minio:latest` contradice la regla 9.3 de no usar `latest`.
- `web` no define `healthcheck` y `nginx` depende de él sin condición; en producción conviene esperar a que esté sano.
- El worker usa la misma imagen que la API pero necesita FFmpeg y, si se usa Whisper, dependencias de GPU: separar en `apps/worker` o usar una imagen multi-stage con target distinto.
- La sección 3.6 lista DOCX y SRT/VTT, pero el MVP solo menciona Markdown/PDF; se ha tratado DOCX como Fase 2.
