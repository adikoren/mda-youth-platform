"""SQL visibility predicates. Lists, counts and searches MUST filter with these so they never
reveal more than the matching single-object policy in app.access.policies."""
from datetime import datetime

from sqlalchemy import ColumnElement, and_, exists, extract, false, or_, true

from app.access.context import Actor
from app.core.timeutil import SATURDAY
from app.days.models import ActivityDay
from app.registration.models import Invitation
from app.scheduling.liveness import live_boarding
from app.scheduling.models import Assignment, BoardingEntry


def _weekday_col():
    return extract("dow", ActivityDay.local_date)


def published_schedule_visible(actor: Actor, now: datetime) -> ColumnElement[bool]:
    if not actor.is_active:
        return false()
    published = ActivityDay.latest_published_version_id.is_not(None)
    if actor.is_admin or actor.is_youth_manager:
        return published
    clauses = [_weekday_col() == SATURDAY]
    if actor.managed_weekdays:
        clauses.append(_weekday_col().in_(sorted(actor.managed_weekdays)))
    if actor.home_weekday is not None:
        clauses.append(_weekday_col() == actor.home_weekday)
    # A recorded published assignment to THIS date keeps access to that date's schedule (also in history),
    # with no time limit. Other dates on the same weekday are not granted. Retention ends it (see D-64).
    clauses.append(exists().where(
        Assignment.version_id == ActivityDay.latest_published_version_id,
        Assignment.user_id == actor.user_id,
    ))
    clauses.append(exists().where(  # a trainee on the published boarding order (scheduled participation)
        BoardingEntry.version_id == ActivityDay.latest_published_version_id,
        BoardingEntry.user_id == actor.user_id,
        live_boarding(BoardingEntry),
    ))
    return and_(published, or_(*clauses))


def managed_days(actor: Actor) -> ColumnElement[bool]:
    if actor.is_admin:
        return true()
    if not actor.managed_weekdays:
        return false()
    return _weekday_col().in_(sorted(actor.managed_weekdays))


def registration_readable_days(actor: Actor) -> ColumnElement[bool]:
    if actor.is_admin or actor.is_youth_manager:
        return true()
    return managed_days(actor)


def registerable_days(actor: Actor, now: datetime) -> ColumnElement[bool]:
    """Dates the actor may answer availability for (eligibility only, not timing)."""
    if not actor.is_active or actor.level is None:
        return false()
    if actor.is_admin:  # admin override, same rule as policies.can_register_for_day
        return true()
    clauses = [_weekday_col() == SATURDAY]
    if actor.home_weekday is not None:
        clauses.append(_weekday_col() == actor.home_weekday)
    clauses.append(
        exists().where(
            Invitation.user_id == actor.user_id,
            Invitation.activity_day_id == ActivityDay.id,
            Invitation.status.in_(("pending", "confirmed")),
            Invitation.expires_at > now,
        )
    )
    return or_(*clauses)


__all__ = ["published_schedule_visible", "managed_days", "registration_readable_days", "registerable_days", "select"]
