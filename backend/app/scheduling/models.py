import uuid
from datetime import datetime, time

from sqlalchemy import (
    CheckConstraint,
    Time,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import check_in, tstz, uuid_pk


class ScheduleVersion(Base):
    """A date's schedule. At most one working draft per day; published versions are immutable
    snapshots (enforced by a DB trigger on slots/assignments). Only the version referenced by
    activity_days.latest_published_version_id controls access."""

    __tablename__ = "schedule_versions"
    __table_args__ = (
        UniqueConstraint("activity_day_id", "revision"),
        CheckConstraint(check_in("status", ("draft", "published", "superseded")), name="status"),
        Index("uq_one_draft_per_day", "activity_day_id", unique=True, postgresql_where=text("status = 'draft'")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    activity_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activity_days.id"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    purged_at: Mapped[datetime | None] = tstz(nullable=True)  # set when retention deleted this version's rows
    status: Mapped[str] = mapped_column(String(16), default="draft")
    base_published_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("schedule_versions.id"))
    created_at: Mapped[datetime] = tstz()
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    edit_count: Mapped[int] = mapped_column(Integer, default=0)  # >0 => dirty (unpublished edits)
    last_edit_at: Mapped[datetime | None] = tstz(nullable=True)
    last_edit_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    published_at: Mapped[datetime | None] = tstz(nullable=True)
    published_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AmbulanceSlot(Base):
    """A schedule position (not a fleet vehicle). slot_key is the logical identity across versions."""

    __tablename__ = "ambulance_slots"
    __table_args__ = (
        UniqueConstraint("version_id", "slot_key"),
        UniqueConstraint("id", "version_id", "shift_id", name="uq_slot_identity"),
        CheckConstraint("capacity BETWEEN 0 AND 8", name="capacity"),
        CheckConstraint(check_in("status", ("active", "cancelled")), name="status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id"), index=True)
    slot_key: Mapped[uuid.UUID] = mapped_column(Uuid, default=uuid.uuid4)
    ordinal: Mapped[int] = mapped_column(SmallInteger)
    capacity: Mapped[int] = mapped_column(SmallInteger, default=3)
    status: Mapped[str] = mapped_column(String(16), default="active")
    # Additional ambulance operated by a volunteer (decision 2026-10-16). Optional. Start only = from that time
    # until the end of the shift; start and end = the explicit range.
    operator_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    operator_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    operator_end: Mapped[time | None] = mapped_column(Time, nullable=True)


class Assignment(Base):
    """Rows exist only while the person is assigned in that version. Removing someone from a draft
    deletes the draft row; published versions keep their rows forever (history)."""

    __tablename__ = "assignments"
    __table_args__ = (
        # HARD BLOCK: one assignment per person per shift per version (no two slots in one shift).
        UniqueConstraint("version_id", "shift_id", "user_id", name="uq_assignment_person_shift"),
        # Slot, shift and version must agree.
        ForeignKeyConstraint(
            ["slot_id", "version_id", "shift_id"],
            ["ambulance_slots.id", "ambulance_slots.version_id", "ambulance_slots.shift_id"],
            name="fk_assignment_slot_identity",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id"))
    slot_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = tstz()
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AssignmentCancellation(Base):
    """A volunteer cancelled their own published assignment (before the shift starts). The published
    version itself stays immutable (its rows are history); this row takes the person out of every live
    view of that version at once. Cleared by the next publication, which rebuilds the crew without them."""

    __tablename__ = "assignment_cancellations"
    __table_args__ = (UniqueConstraint("version_id", "shift_id", "user_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    cancelled_at: Mapped[datetime] = tstz()


class BoardingEntry(Base):
    """The trainee boarding order (סדר עלייה) of one schedule version. Membership is scheduled participation
    even without an ambulance. Rows belong to a version: the working copy is editable, and a published version
    keeps its list."""

    __tablename__ = "boarding_entries"
    __table_args__ = (UniqueConstraint("version_id", "user_id"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("schedule_versions.id"), index=True)
    shift_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("shifts.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    position: Mapped[int] = mapped_column(SmallInteger)
