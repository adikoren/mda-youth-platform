import uuid
from datetime import datetime, time

from sqlalchemy import Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, String, Time, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import check_in, tstz, uuid_pk


class Registration(Base):
    """One response container per user and activity day; per-shift choices live in RegistrationChoice."""

    __tablename__ = "registrations"
    __table_args__ = (
        UniqueConstraint("user_id", "activity_day_id"),
        CheckConstraint(check_in("source", ("self", "manager")), name="source"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    activity_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activity_days.id"), index=True)
    source: Mapped[str] = mapped_column(String(16), default="self")
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = tstz()
    updated_at: Mapped[datetime] = tstz()
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class RegistrationChoice(Base):
    __tablename__ = "registration_choices"
    __table_args__ = (UniqueConstraint("registration_id", "shift_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    registration_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("registrations.id"), index=True)
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id"), index=True)
    available: Mapped[bool] = mapped_column(Boolean)
    arrival_time: Mapped[time | None] = mapped_column(Time)  # ordinary evening only
    is_late: Mapped[bool] = mapped_column(Boolean, default=False)  # last change made at/after closing
    first_answered_at: Mapped[datetime] = tstz()
    updated_at: Mapped[datetime] = tstz()


class ManagerAddedParticipant(Base):
    """A person put into a shift's pool by a manager. Provenance is kept; this never rewrites
    the person's own availability answer."""

    __tablename__ = "manager_added_participants"
    __table_args__ = (
        Index("uq_added_current", "shift_id", "user_id", unique=True, postgresql_where=text("removed_at IS NULL")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    added_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    added_at: Mapped[datetime] = tstz()
    removed_at: Mapped[datetime | None] = tstz(nullable=True)
    removed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class Invitation(Base):
    """kind='invitation': personal request to register for another day.
    kind='direct_add': confirmation request after a manager added a guest directly
    (confirmation preferred, never required; no refusal flow)."""

    __tablename__ = "invitations"
    __table_args__ = (
        CheckConstraint(check_in("kind", ("invitation", "direct_add")), name="kind"),
        CheckConstraint(check_in("status", ("pending", "confirmed", "expired", "cancelled")), name="status"),
        Index("uq_invitation_open", "user_id", "activity_day_id", "kind", unique=True,
              postgresql_where=text("status IN ('pending','confirmed')")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    activity_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activity_days.id"), index=True)
    shift_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shifts.id"))
    invited_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    kind: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = tstz()
    responded_at: Mapped[datetime | None] = tstz(nullable=True)
    expires_at: Mapped[datetime] = tstz()


class QueueOrder(Base):
    """Manager's manual waiting/boarding order per shift. Not versioned, so cancellation and
    republication never reset it."""

    __tablename__ = "queue_order"

    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id"), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), primary_key=True)
    rank: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[datetime] = tstz()
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AvailabilityAttention(Base):
    """'ביטל זמינות — נדרש טיפול': assigned person withdrew availability after publication.
    Stays pending until a manager resolves it or a publication removes the assignment."""

    __tablename__ = "availability_attention"
    __table_args__ = (
        CheckConstraint(check_in("status", ("pending", "resolved")), name="status"),
        Index("uq_attention_pending", "shift_id", "user_id", unique=True, postgresql_where=text("status = 'pending'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    published_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedule_versions.id"))
    registration_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = tstz()
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    resolution: Mapped[str | None] = mapped_column(String(32))


# pending until the Day Manager decides. "resolved" is the legacy "handled" from before decisions existed.
REQUEST_STATUSES = ("pending", "approved", "rejected", "resolved")


class RegistrationRequest(Base):
    """A volunteer's written request to the day manager after registration closed (the 'שליחת בקשה
    לאחראי' form). Submitted only by an explicit button press; never created by opening the tab.
    The text stays here; notification payloads carry ids only (DECISIONS D-26)."""

    __tablename__ = "registration_requests"
    __table_args__ = (CheckConstraint(check_in("status", REQUEST_STATUSES), name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()  # client-generated, so a retried submit is recognised
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    activity_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activity_days.id"), index=True)
    # The shift the request is about. Null only for requests made before per-shift requests existed.
    shift_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("shifts.id"), index=True)
    message: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_at: Mapped[datetime] = tstz()
    resolved_at: Mapped[datetime | None] = tstz(nullable=True)
    resolved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
