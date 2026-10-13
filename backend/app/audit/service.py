import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.audit.models import AuditEvent


def record(
    db: Session,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    object_type: str,
    object_id: object | None,
    now: datetime,
    meta: dict | None = None,
    admin_only: bool = False,
) -> None:
    """Add an audit row to the current transaction. Keep meta minimal: ids and changed field
    names/values only, never passwords, tokens, notes or clinical text."""
    db.add(
        AuditEvent(
            actor_user_id=actor_id,
            action=action,
            object_type=object_type,
            object_id=str(object_id) if object_id is not None else None,
            visibility="admin_only" if admin_only else "normal",
            meta=meta or {},
            created_at=now,
        )
    )
