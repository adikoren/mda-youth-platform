"""Named access rules. Deny by default. Every protected endpoint goes through these functions
(single objects) or through app.access.queries (lists, counts, searches).

Convention: if the actor may not know a resource exists -> 404; if they can see it but may not
perform the action -> 403. Admin is an audited override of scopes, privacy and time windows, but
never of scheduling invariants (those are enforced in services and the database)."""
from datetime import datetime

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.core.errors import forbidden, not_found
from app.core.timeutil import SATURDAY, local_midnight_after, local_weekday
from app.days.models import ActivityDay
from app.registration.models import Invitation
from app.scheduling.models import Assignment


# ---------- accounts / authority ----------

def require_active(actor: Actor) -> None:
    if not actor.is_active:
        raise forbidden("account_not_active", "החשבון ממתין לאישור")


def can_approve_signups(actor: Actor) -> bool:
    # Initial rollout: admin only. Delegation per scope is a later, explicit change.
    return actor.is_admin


def can_appoint_day_managers(actor: Actor) -> bool:
    return actor.is_admin or actor.is_youth_manager


def can_change_level(actor: Actor, target_home_weekday: int | None) -> bool:
    if actor.is_admin:
        return True
    return target_home_weekday is not None and target_home_weekday in actor.managed_weekdays


def can_search_users(actor: Actor) -> bool:
    return actor.is_admin or actor.is_youth_manager or actor.is_day_manager


# ---------- days / schedules ----------

def manages_day(actor: Actor, day: ActivityDay) -> bool:
    """Draft read/edit/publish, registration edits, timing edits for that date."""
    return actor.is_admin or local_weekday(day.local_date) in actor.managed_weekdays


def require_manage_day(actor: Actor, day: ActivityDay) -> None:
    if manages_day(actor, day):
        return
    # A youth manager or member may legitimately know the date exists; others get 404.
    raise forbidden() if can_read_day_registrations(actor, day) else not_found()


def can_read_day_registrations(actor: Actor, day: ActivityDay) -> bool:
    return actor.is_admin or actor.is_youth_manager or manages_day(actor, day)


def is_published_assignee(db: Session, actor: Actor, day: ActivityDay) -> bool:
    if day.latest_published_version_id is None:
        return False
    return bool(
        db.scalar(
            select(
                exists().where(
                    Assignment.version_id == day.latest_published_version_id,
                    Assignment.user_id == actor.user_id,
                )
            )
        )
    )


def guest_access_end(day: ActivityDay) -> datetime:
    """Ordinary guests see the full date schedule through 23:59 of the date: the exclusive end is
    the next local midnight (not the 23:00 shift end)."""
    return local_midnight_after(day.local_date)


def can_view_published_schedule(db: Session, actor: Actor, day: ActivityDay, now: datetime) -> bool:
    if not actor.is_active or day.latest_published_version_id is None:
        return False
    if actor.is_admin or actor.is_youth_manager or manages_day(actor, day):
        return True
    weekday = local_weekday(day.local_date)
    if weekday == SATURDAY:
        return True  # Saturday published schedules: all active volunteers
    if actor.home_weekday == weekday:
        return True  # current home-day members: permanent
    return now < guest_access_end(day) and is_published_assignee(db, actor, day)


def has_open_invitation(db: Session, actor: Actor, day: ActivityDay, now: datetime) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    Invitation.user_id == actor.user_id,
                    Invitation.activity_day_id == day.id,
                    Invitation.status.in_(("pending", "confirmed")),
                    Invitation.expires_at > now,
                )
            )
        )
    )


def can_register_for_day(db: Session, actor: Actor, day: ActivityDay, now: datetime) -> bool:
    """Who may give availability for a date (timing is checked separately).

    Admin override (authorization): an admin may answer for any date, without a home day, an
    invitation or a shift. A volunteer level is still required (a data rule: availability feeds the
    shift pools, which only contain volunteers)."""
    if not actor.is_active or actor.level is None:
        return False
    if actor.is_admin:
        return True
    weekday = local_weekday(day.local_date)
    if weekday == SATURDAY or actor.home_weekday == weekday:
        return True
    return has_open_invitation(db, actor, day, now)
