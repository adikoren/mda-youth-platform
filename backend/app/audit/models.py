import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import check_in, tstz, uuid_pk


class AuditEvent(Base):
    """Minimized change log. visibility='admin_only' rows (e.g. personal-summary actions) must
    never appear in manager-facing feeds."""

    __tablename__ = "audit_events"
    __table_args__ = (CheckConstraint(check_in("visibility", ("normal", "admin_only")), name="visibility"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str] = mapped_column(String(48))
    object_id: Mapped[str | None] = mapped_column(String(64))
    visibility: Mapped[str] = mapped_column(String(16), default="normal")
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = tstz()
