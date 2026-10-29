import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.types import AUTHORITY_ROLES, LEVELS, USER_STATUSES, check_in, tstz, uuid_pk


class User(Base):
    __tablename__ = "users"
    __table_args__ = (CheckConstraint(check_in("status", USER_STATUSES), name="status"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)  # stored normalized (lower/stripped)
    full_name: Mapped[str] = mapped_column(String(120))
    phone: Mapped[str | None] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    requested_weekday: Mapped[int | None] = mapped_column(SmallInteger)  # signup hint only, grants nothing
    approved_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = tstz(nullable=True)
    created_at: Mapped[datetime] = tstz()


class AuthSession(Base):
    """Server-side session. Only a SHA-256 hash of the cookie token is stored."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = uuid_pk()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = tstz()
    last_activity_at: Mapped[datetime] = tstz()
    revoked_at: Mapped[datetime | None] = tstz(nullable=True)


class VolunteerLevel(Base):
    """Exactly one current level per user (valid_to IS NULL); history kept for transition dates."""

    __tablename__ = "volunteer_level_history"
    __table_args__ = (
        CheckConstraint(check_in("level", LEVELS), name="level"),
        Index("uq_level_current", "user_id", unique=True, postgresql_where=text("valid_to IS NULL")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    level: Mapped[str] = mapped_column(String(16))
    valid_from: Mapped[datetime] = tstz()
    valid_to: Mapped[datetime | None] = tstz(nullable=True)
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class AuthorityRole(Base):
    """Stacking authority roles: admin, youth_manager. Day-manager authority is the set of
    current manager_day_scopes rows (see DECISIONS.md D-03)."""

    __tablename__ = "authority_roles"
    __table_args__ = (
        CheckConstraint(check_in("role", AUTHORITY_ROLES), name="role"),
        Index("uq_role_current", "user_id", "role", unique=True, postgresql_where=text("valid_to IS NULL")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(32))
    valid_from: Mapped[datetime] = tstz()
    valid_to: Mapped[datetime | None] = tstz(nullable=True)
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class DayMembership(Base):
    """Home day. One current membership per user; history preserved on transfer."""

    __tablename__ = "day_memberships"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        Index("uq_membership_current", "user_id", unique=True, postgresql_where=text("valid_to IS NULL")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)
    valid_from: Mapped[datetime] = tstz()
    valid_to: Mapped[datetime | None] = tstz(nullable=True)
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class ManagerDayScope(Base):
    __tablename__ = "manager_day_scopes"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        Index("uq_scope_current", "user_id", "weekday", unique=True, postgresql_where=text("valid_to IS NULL")),
        # One permanent weekday per person, and one permanent Day Manager per weekday (decision 2026-10-17).
        Index("uq_one_permanent_scope_per_user", "user_id", unique=True, postgresql_where=text("valid_to IS NULL")),
        Index("uq_one_permanent_manager_per_weekday", "weekday", unique=True, postgresql_where=text("valid_to IS NULL")),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)
    valid_from: Mapped[datetime] = tstz()
    valid_to: Mapped[datetime | None] = tstz(nullable=True)
    granted_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))


class TemporaryDayManagerAppointment(Base):
    """A manual, time-limited appointment of a mentor to manage one weekday (decision of 2026-10-17). Separate from
    the permanent Day Manager scope and from the volunteer's base role, home day and permanent appointment.
    Valid only while starts_at <= now < ends_at and not revoked. Rows are kept as history (never deleted).
    Overlaps for the same weekday are refused by the service (per-weekday lock), not by a uniqueness constraint."""

    __tablename__ = "temporary_day_manager_appointments"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday"),
        CheckConstraint("ends_at > starts_at", name="interval"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)
    starts_at: Mapped[datetime] = tstz()
    ends_at: Mapped[datetime] = tstz()
    created_at: Mapped[datetime] = tstz()
    created_by_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime | None] = tstz(nullable=True)
    updated_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    revoked_at: Mapped[datetime | None] = tstz(nullable=True)
    revoked_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class PasswordReset(Base):
    """A single-use password-reset token. Only its SHA-256 hash is stored; the token itself is sent by email."""

    __tablename__ = "password_resets"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = tstz()
    expires_at: Mapped[datetime] = tstz()
    used_at: Mapped[datetime | None] = tstz(nullable=True)


class ExternalIdentity(Base):
    """A linked sign-in provider identity. Keyed by the provider's stable subject id, never by email."""

    __tablename__ = "external_identities"
    __table_args__ = (Index("uq_external_identity", "provider", "subject", unique=True),)

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(16))
    subject: Mapped[str] = mapped_column(String(255))
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = tstz()


class OAuthState(Base):
    """One sign-in attempt: state and nonce are stored hashed and are single-use (CSRF and replay protection)."""

    __tablename__ = "oauth_states"

    id: Mapped[uuid.UUID] = uuid_pk()
    state_hash: Mapped[str] = mapped_column(String(64), unique=True)
    nonce_hash: Mapped[str] = mapped_column(String(64))
    purpose: Mapped[str] = mapped_column(String(16))  # "login" or "link"
    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = tstz()
    expires_at: Mapped[datetime] = tstz()
    used_at: Mapped[datetime | None] = tstz(nullable=True)
