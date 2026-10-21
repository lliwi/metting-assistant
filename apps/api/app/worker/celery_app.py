from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("metting_agent", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Los trabajos son idempotentes: confirmar tras ejecutar permite reintentar si el worker cae.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_default_queue="default",
    # Colas separadas para escalar cada tipo de worker de forma independiente.
    task_routes={
        "app.worker.tasks.media.*": {"queue": "media"},
        "app.worker.tasks.asr.*": {"queue": "asr"},
        "app.worker.tasks.llm.*": {"queue": "llm"},
        "app.worker.tasks.exports.*": {"queue": "exports"},
    },
)
celery_app.autodiscover_tasks(["app.worker"], related_name="tasks")
