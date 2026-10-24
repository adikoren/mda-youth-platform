"""Trusted actor context, loaded from the database on every request.

Nothing here comes from the client: roles, scopes and memberships are read fresh, so role
changes, transfers and revocations apply on the next request without re-login."""
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.accounts.models import AuthorityRole, DayMembership, ManagerDayScope, User, VolunteerLevel


@dataclass(frozen=True)
class Actor:
    user_id: uuid.UUID
    full_name: str
    email: str
    status: str
    level: str | None = None
    home_weekday: int | None = None
    is_admin: bool = False
    is_youth_manager: bool = False
    managed_weekdays: frozenset[int] = field(default_factory=frozenset)
    # weekday -> end of a temporary appointment valid now (already included in managed_weekdays)
    temporary_weekdays: dict[int, datetime] = field(default_factory=dict)

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    @property
    def is_day_manager(self) -> bool:
        return bool(self.managed_weekdays)

    @property
    def is_trainee(self) -> bool:
        return self.level == "trainee"


def load_actor(db: Session, user_id: uuid.UUID, now: datetime | None = None) -> Actor | None:
    user = db.get(User, user_id)
    if user is None:
        return None
    level = db.scalar(
        select(VolunteerLevel.level).where(VolunteerLevel.user_id == user_id, VolunteerLevel.valid_to.is_(None))
    )
    home = db.scalar(
        select(DayMembership.weekday).where(DayMembership.user_id == user_id, DayMembership.valid_to.is_(None))
    )
    roles = set(
        db.scalars(
            select(AuthorityRole.role).where(AuthorityRole.user_id == user_id, AuthorityRole.valid_to.is_(None))
        )
    )
    scopes = frozenset(
        db.scalars(
            select(ManagerDayScope.weekday).where(
                ManagerDayScope.user_id == user_id, ManagerDayScope.valid_to.is_(None)
            )
        )
    )
    active = user.status == "active"
    # Temporary appointments count only while valid at server time (checked on every request).
    from app.accounts.temporary import active_weekdays
    from app.access.clock import get_clock
    moment = now or get_clock().now()
    temporary = active_weekdays(db, user_id, moment) if active else {}
    return Actor(
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        status=user.status,
        level=level,
        home_weekday=home,
        # Authority only counts while the account is active.
        is_admin=active and "admin" in roles,
        is_youth_manager=active and "youth_manager" in roles,
        managed_weekdays=(scopes | frozenset(temporary)) if active else frozenset(),
        temporary_weekdays=temporary if active else {},
    )
