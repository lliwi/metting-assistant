"""Retención: borra las grabaciones más antiguas que el plazo de su organización."""

import uuid
from collections.abc import Callable
from datetime import datetime

import structlog
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.audit import record_audit_event
from app.auth.sessions import now
from app.models import Organization, Recording, RecordingStatus

log = structlog.get_logger(__name__)

# Por ejecución: con muchas caducadas a la vez, el resto queda para la siguiente.
BATCH = 500


def recording_prefix(org_id: uuid.UUID, recording_id: uuid.UUID) -> str:
    return f"orgs/{org_id}/recordings/{recording_id}/"


def apply_retention(
    db: Session, purge: Callable[[str], None], *, current: datetime | None = None
) -> int:
    """Devuelve cuántas grabaciones ha borrado. retention_days vacío o 0 = sin borrado."""
    current = current or now()
    expired = db.scalars(
        select(Recording)
        .join(Organization, Organization.id == Recording.organization_id)
        .where(
            Organization.retention_days > 0,
            Recording.created_at + func.make_interval(0, 0, 0, Organization.retention_days)
            < current,
            # Mientras se transcribe, el worker escribe en la grabación: se borra mañana.
            Recording.status != RecordingStatus.PROCESSING,
        )
        .order_by(Recording.created_at)
        .limit(BATCH)
        .with_for_update(of=Recording, skip_locked=True)
    ).all()
    prefixes = []
    for rec in expired:
        record_audit_event(
            db,
            action="recording.deleted",
            organization_id=rec.organization_id,
            resource_type="recording",
            resource_id=rec.id,
            metadata={
                "title": rec.title,
                "duration_ms": rec.duration_ms,
                "reason": "retention",
            },
        )
        prefixes.append(recording_prefix(rec.organization_id, rec.id))
    if expired:
        # Igual que el borrado manual: cascada en la base de datos y el consumo se conserva.
        db.execute(delete(Recording).where(Recording.id.in_([r.id for r in expired])))
    db.commit()
    for prefix in prefixes:
        purge(prefix)
    if prefixes:
        log.info("retention_applied", recordings=len(prefixes))
    return len(prefixes)
