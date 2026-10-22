ARG PYTHON_IMAGE=python:3.13.16-slim-bookworm

FROM ${PYTHON_IMAGE} AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app

FROM ${PYTHON_IMAGE} AS runtime
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
COPY --from=builder /app /app

FROM runtime AS api
USER 10001
EXPOSE 8000
CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", "-w", "4", "-b", "0.0.0.0:8000", "--worker-tmp-dir", "/tmp"]

FROM runtime AS worker
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /scratch && chown 10001 /scratch
USER 10001
CMD ["celery", "-A", "app.worker.celery_app", "worker", "--loglevel=INFO", "--concurrency=2", "-Q", "default,media,asr,llm,exports"]
