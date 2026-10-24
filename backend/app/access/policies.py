"""Named access rules. Deny by default. Every protected endpoint goes through these functions
(single objects) or through app.access.queries (lists, counts, searches).

Convention: if the actor may not know a resource exists -> 404; if they can see it but may not
perform the action -> 403. Admin is an audited override of scopes, privacy and time windows, but
never of scheduling invariants (those are enforced in services and the database)."""

import uuid
from datetime import datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.core.errors import forbidden, not_found
from app.core.timeutil import SATURDAY, local_weekday
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


def can_approve_for_weekday(actor: Actor, weekday: int | None) -> bool:
    """Signup approval scope: Admin any day; a Day Manager (permanent or active temporary appointment) only the
    weekday they manage right now. Never grants a role, and a requested weekday grants no access by itself."""
    if not actor.is_active or weekday is None:
        return False
    return actor.is_admin or weekday in actor.managed_weekdays


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
    """Recorded published assignment to this date, or a place on its published boarding order."""
    from app.scheduling.liveness import live_boarding
    from app.scheduling.models import BoardingEntry
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
        ) or db.scalar(
            select(
                exists().where(
                    BoardingEntry.version_id == day.latest_published_version_id,
                    BoardingEntry.user_id == actor.user_id,
                    live_boarding(BoardingEntry),
                )
            )
        )
    )


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
    # Assigned to this date (recorded assignment): access does not expire with a time window.
    return is_published_assignee(db, actor, day)


def can_view_profile(db: Session, actor: Actor, user_id: uuid.UUID) -> bool:
    """Personal profile (name, level, home day, contact). Self, Admin and Youth Manager always; a Day Manager
    only for people registered or assigned on a date they manage. Anyone else gets not-found."""
    from app.registration.models import Registration
    from app.scheduling.models import Assignment
    from sqlalchemy import extract
    if not actor.is_active:
        return False
    if actor.user_id == user_id or actor.is_admin or actor.is_youth_manager:
        return True
    if not actor.managed_weekdays:
        return False
    dow = extract("dow", ActivityDay.local_date).in_(sorted(actor.managed_weekdays))
    on_registration = exists().where(Registration.user_id == user_id, Registration.activity_day_id == ActivityDay.id)
    on_schedule = exists().where(Assignment.version_id == ActivityDay.latest_published_version_id,
                                 Assignment.user_id == user_id)
    return db.scalar(select(ActivityDay.id).where(dow, or_(on_registration, on_schedule)).limit(1)) is not None


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


def can_see_open_registration(db: Session, actor: Actor, day: ActivityDay, now: datetime) -> bool:
    """Whether a date appears in the registration list. Admin sees every date, regardless of home day, invitations,
    assignments or volunteer level. Listing never registers anyone: answering still needs can_register_for_day."""
    if not actor.is_active:
        return False
    if actor.is_admin:
        return True
    return can_register_for_day(db, actor, day, now)


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


def can_view_day_volunteers(actor: Actor) -> bool:
    """The selected-day volunteer list under "אחראי נוער": Youth Manager and Admin only. A Day Manager does not
    get it merely by managing a day."""
    return actor.is_active and (actor.is_admin or actor.is_youth_manager)
