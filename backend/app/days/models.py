import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import DAY_KINDS, SHIFT_KINDS, check_in, tstz, uuid_pk


class Station(Base):
    __tablename__ = "stations"
    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(120), unique=True)


class RegistrationTemplate(Base):
    """Per-weekday default timing. Effective times are copied onto each activity day, so editing a
    template never silently changes dates that already exist."""

    __tablename__ = "registration_templates"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint("open_days_before > close_days_before AND close_days_before >= target_days_before",
                        name="offsets_order"),
        Index("uq_template_current", "weekday", unique=True, postgresql_where=text("valid_to IS NULL")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    weekday: Mapped[int] = mapped_column(SmallInteger)
    open_days_before: Mapped[int] = mapped_column(SmallInteger)
    open_time: Mapped[time] = mapped_column(Time)
    close_days_before: Mapped[int] = mapped_column(SmallInteger)
    close_time: Mapped[time] = mapped_column(Time)
    target_days_before: Mapped[int] = mapped_column(SmallInteger)
    target_time: Mapped[time] = mapped_column(Time)
    valid_from: Mapped[datetime] = tstz()
    valid_to: Mapped[datetime | None] = tstz(nullable=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class ActivityDay(Base):
    __tablename__ = "activity_days"
    __table_args__ = (
        UniqueConstraint("station_id", "local_date"),
        CheckConstraint(check_in("kind", DAY_KINDS), name="kind"),
        CheckConstraint("registration_opens_at < registration_closes_at", name="window_order"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    station_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("stations.id"))
    local_date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(16))
    registration_opens_at: Mapped[datetime] = tstz()
    registration_closes_at: Mapped[datetime] = tstz()
    publish_target_at: Mapped[datetime] = tstz()
    latest_published_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("schedule_versions.id", use_alter=True, name="fk_activity_days_latest_published")
    )
    created_at: Mapped[datetime] = tstz()
    row_version: Mapped[int] = mapped_column(Integer, default=1)


class Shift(Base):
    __tablename__ = "shifts"
    __table_args__ = (
        UniqueConstraint("activity_day_id", "kind"),
        CheckConstraint(check_in("kind", SHIFT_KINDS), name="kind"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    activity_day_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activity_days.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    arrival_at: Mapped[datetime] = tstz()
    starts_at: Mapped[datetime] = tstz()
    ends_at: Mapped[datetime] = tstz()
    self_selectable: Mapped[bool] = mapped_column(Boolean)  # volunteers may mark availability
    allows_late_arrival: Mapped[bool] = mapped_column(Boolean)
    baseline_slots: Mapped[int] = mapped_column(SmallInteger)
