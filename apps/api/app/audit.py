import uuid
from typing import Any

import structlog
from sqlalchemy.orm import Session

from app.core.request_context import ClientInfo
from app.models import AuditEvent, AuditResult

log = structlog.get_logger(__name__)


def record_audit_event(
    db: Session,
    *,
    action: str,
    result: AuditResult = AuditResult.SUCCESS,
    client: ClientInfo | None = None,
    actor_id: uuid.UUID | None = None,
    organization_id: uuid.UUID | None = None,
    resource_type: str | None = None,
    resource_id: str | uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Añade el evento a la transacción en curso; se persiste con el commit del llamador.

    Los fallos (login incorrecto, acceso denegado) deben confirmarse aunque la operación
    no tenga cambios: el llamador hace commit en esos casos.
    """
    db.add(
        AuditEvent(
            action=action,
            result=result,
            actor_id=actor_id,
            organization_id=organization_id,
            resource_type=resource_type,
            resource_id=str(resource_id) if resource_id is not None else None,
            ip_pseudonym=client.ip_pseudonym if client else None,
            correlation_id=client.correlation_id if client else None,
            event_metadata=metadata or {},
        )
    )
    log.info("audit", action=action, result=result.value, actor_id=str(actor_id or ""))
