from app.notifications.email import deliver_via_smtp
from app.worker.celery_app import celery_app


@celery_app.task(name="app.worker.tasks.ping")  # type: ignore[untyped-decorator]
def ping() -> str:
    """Tarea mínima para comprobar que el worker consume de la cola."""
    return "pong"


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.worker.tasks.send_email",
    autoretry_for=(OSError,),
    retry_backoff=True,
    retry_backoff_max=600,
    max_retries=8,
)
def send_email(to: str, subject: str, body: str) -> None:
    deliver_via_smtp(to, subject, body)
