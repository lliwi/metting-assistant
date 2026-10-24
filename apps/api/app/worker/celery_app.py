from celery import Celery
from celery.schedules import crontab

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
    # Con Redis, un trabajo sin confirmar se reentrega al pasar este tiempo. Por defecto es 1 h:
    # una transcripción larga se duplicaría. El cerrojo de app.worker.jobs evita además el
    # trabajo doble si ocurre.
    broker_transport_options={"visibility_timeout": 6 * 3600},
    result_expires=24 * 3600,
    beat_schedule={
        "sweep-stuck-jobs": {"task": "app.worker.tasks.sweep_stuck_jobs", "schedule": 600.0},
        # 3:30 UTC (de madrugada en Europa): borra las grabaciones fuera del plazo de retención.
        "apply-retention": {
            "task": "app.worker.tasks.apply_retention",
            "schedule": crontab(hour=3, minute=30),
        },
    },
    # Colas separadas para escalar cada tipo de worker de forma independiente.
    task_routes={
        "app.worker.tasks.media.*": {"queue": "media"},
        "app.worker.tasks.asr.*": {"queue": "asr"},
        "app.worker.tasks.llm.*": {"queue": "llm"},
        "app.worker.tasks.exports.*": {"queue": "exports"},
    },
)
celery_app.autodiscover_tasks(["app.worker"], related_name="tasks")
