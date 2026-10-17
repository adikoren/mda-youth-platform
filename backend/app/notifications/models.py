import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import check_in, tstz, uuid_pk

# queued   - intent stored, not yet handed to any channel
# accepted - a real provider accepted it (NOT proof of delivery to the phone)
# failed   - gave up after retries
# simulated- no phone channel connected; visible in-app only, never claimed as sent
OUTBOX_STATUSES = ("queued", "accepted", "failed", "simulated")


class OutboxMessage(Base):
    __tablename__ = "notification_outbox"
    __table_args__ = (
        CheckConstraint(check_in("status", OUTBOX_STATUSES), name="status"),
        Index("ix_outbox_due", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    event_type: Mapped[str] = mapped_column(String(48))
    recipient_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True)
    # Minimal references only (ids, dates, shift kind). Never journal/clinical text.
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = tstz()
    last_error: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = tstz()
    processed_at: Mapped[datetime | None] = tstz(nullable=True)
    read_at: Mapped[datetime | None] = tstz(nullable=True)


class PushSubscription(Base):
    """One browser/device registered for Web Push, belonging to one account. The endpoint is unique: a device
    belongs to one account at a time (switching accounts re-assigns it). Never returned to clients."""

    __tablename__ = "push_subscriptions"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1024), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = tstz()
    last_seen_at: Mapped[datetime] = tstz()
