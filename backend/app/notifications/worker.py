"""Background worker: derives due reminders from persisted state, then delivers queued outbox rows.

Restart-safe: nothing lives in memory. Due work is recomputed from the database each pass and
dedup keys make repeated passes harmless. Rows are claimed with FOR UPDATE SKIP LOCKED so two
overlapping workers never process the same message. Exactly-once *external* delivery is not
promised; a provider call that times out after acceptance may be retried."""
import argparse
import logging
import time as time_mod
from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.access.clock import get_clock
from app.accounts.models import DayMembership, User, VolunteerLevel
from app.core.config import get_settings
from app.core.db import session_factory
from app.core.timeutil import SATURDAY, local_dt, local_midnight_after, local_today, local_weekday
from app.days.models import ActivityDay
from app.scheduling.retention import purge_expired
from app.notifications.models import OutboxMessage
from app.notifications.outbox import day_manager_ids, enqueue
from app.registration.models import Invitation
from app.scheduling.models import ScheduleVersion

log = logging.getLogger("mda.worker")
MAX_ATTEMPTS = 5


class ChannelUnavailable(Exception):
    pass


class PushChannel:
    """Interface for a real provider (Web Push/VAPID, FCM/APNs...). Not implemented yet: the
    provider is chosen only after a real iPhone/Android trial (milestone D)."""

    name = "none"

    def send(self, msg: OutboxMessage) -> None:
        raise ChannelUnavailable


def get_channel() -> PushChannel | None:
    return None if get_settings().push_channel == "none" else PushChannel()


# ---------------- due-work derivation ----------------

def _eligible_volunteers(db: Session, day: ActivityDay) -> list:
    wd = local_weekday(day.local_date)
    active_with_level = (select(User.id).join(VolunteerLevel, (VolunteerLevel.user_id == User.id)
                                              & VolunteerLevel.valid_to.is_(None))
                         .where(User.status == "active"))
    if wd == SATURDAY:
        return list(db.scalars(active_with_level))
    members = list(db.scalars(active_with_level.join(DayMembership, (DayMembership.user_id == User.id)
                                                     & DayMembership.valid_to.is_(None))
                              .where(DayMembership.weekday == wd)))
    invited = list(db.scalars(select(Invitation.user_id).where(
        Invitation.activity_day_id == day.id, Invitation.kind == "invitation",
        Invitation.status.in_(("pending", "confirmed")))))
    return members + invited


def enqueue_due_reminders(db: Session, now: datetime) -> int:
    s = get_settings()
    n = enqueue_journal_reminders(db, now)
    horizon = now - timedelta(days=2)
    days = db.scalars(select(ActivityDay).where(ActivityDay.registration_opens_at <= now,
                                                ActivityDay.registration_opens_at >= now - timedelta(days=10)))
    for day in days:
        if now >= local_midnight_after(day.local_date):
            continue
        base = {"date": day.local_date.isoformat(), "activity_day_id": str(day.id)}
        # Registration opened: only announce openings that are recent (avoid flooding on setup).
        if day.registration_opens_at >= horizon and now < day.registration_closes_at:
            n += enqueue(db, event_type="registration_opened", recipients=_eligible_volunteers(db, day),
                         dedup_prefix=f"reg_open:{day.id}", payload=base, now=now)
        # Publication target: a reminder to managers, NEVER an automatic publication.
        if day.publish_target_at <= now and day.latest_published_version_id is None:
            n += enqueue(db, event_type="publish_target_reminder",
                         recipients=day_manager_ids(db, local_weekday(day.local_date)),
                         dedup_prefix=f"pub_target:{day.id}", payload=base, now=now)

    # Dirty draft idle for N minutes. One reminder per idle period: the key includes last_edit_at,
    # so a new edit followed by another idle period produces one new reminder, never a flood.
    idle_before = now - timedelta(minutes=s.draft_idle_minutes)
    for draft, day in db.execute(select(ScheduleVersion, ActivityDay)
                                 .join(ActivityDay, ActivityDay.id == ScheduleVersion.activity_day_id)
                                 .where(ScheduleVersion.status == "draft", ScheduleVersion.edit_count > 0,
                                        ScheduleVersion.last_edit_at <= idle_before)).all():
        recipients = [draft.last_edit_by_id] if draft.last_edit_by_id else day_manager_ids(db, local_weekday(day.local_date))
        n += enqueue(db, event_type="draft_idle_reminder", recipients=recipients,
                     dedup_prefix=f"draft_idle:{draft.id}:{draft.last_edit_at.timestamp():.0f}",
                     payload={"date": day.local_date.isoformat(), "activity_day_id": str(day.id)}, now=now)
    db.commit()
    return n


def enqueue_journal_reminders(db: Session, now: datetime) -> int:
    """At shift end and at 09:00 the next morning, only while content is incomplete. Each
    participant is reminded about their own part; no mentor reminder without a formal mentor.
    Late worker passes do not send stale reminders (each phase is valid for a bounded period)."""
    from app.access.journal import formal_mentors
    from app.days.models import Shift
    from app.mentoring.service import completeness
    from app.scheduling.models import Assignment
    from app.scheduling.liveness import live_assignment

    n = 0
    today = local_today(now)
    rows = db.execute(select(Shift, ActivityDay).join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
                      .where(ActivityDay.local_date.in_([today, today - timedelta(days=1)]),
                             ActivityDay.latest_published_version_id.is_not(None))).all()
    for shift, day in rows:
        morning = local_dt(day.local_date + timedelta(days=1), time(9, 0))
        if shift.ends_at <= now < morning:
            phase = "end"
        elif morning <= now < morning + timedelta(hours=3):
            phase = "morning"
        else:
            continue
        trainees = db.scalars(select(Assignment.user_id)
                              .join(VolunteerLevel, (VolunteerLevel.user_id == Assignment.user_id)
                                    & VolunteerLevel.valid_to.is_(None) & (VolunteerLevel.level == "trainee"))
                              .where(Assignment.version_id == day.latest_published_version_id,
                                     Assignment.shift_id == shift.id, live_assignment())).all()
        base = {"date": day.local_date.isoformat(), "shift": shift.kind, "shift_id": str(shift.id)}
        for trainee_id in trainees:
            comp = completeness(db, trainee_id, shift)
            if not comp.trainee_complete:
                n += enqueue(db, event_type="journal_reminder_trainee", recipients=[trainee_id],
                             dedup_prefix=f"journal_t:{shift.id}:{trainee_id}:{phase}", payload=base, now=now)
            if comp.mentor_required and not comp.mentor_complete:
                n += enqueue(db, event_type="journal_reminder_mentor", recipients=formal_mentors(db, trainee_id, shift.id),
                             dedup_prefix=f"journal_m:{shift.id}:{trainee_id}:{phase}",
                             payload={**base, "trainee_id": str(trainee_id)}, now=now)
    return n


# ---------------- delivery ----------------

def deliver_batch(db: Session, now: datetime, limit: int = 100) -> dict:
    channel = get_channel()
    rows = list(db.scalars(select(OutboxMessage).where(OutboxMessage.status == "queued",
                                                       OutboxMessage.next_attempt_at <= now)
                           .order_by(OutboxMessage.created_at).limit(limit).with_for_update(skip_locked=True)))
    stats = {"accepted": 0, "simulated": 0, "failed": 0, "retry": 0}
    for msg in rows:
        if channel is None:
            msg.status, msg.channel, msg.processed_at = "simulated", "none", now
            stats["simulated"] += 1
            continue
        try:
            channel.send(msg)
            msg.status, msg.channel, msg.processed_at = "accepted", channel.name, now
            stats["accepted"] += 1
        except Exception as exc:  # noqa: BLE001 - provider errors are recorded, never swallowed silently
            msg.attempts += 1
            msg.last_error = f"{type(exc).__name__}: {exc}"[:500]
            if msg.attempts >= MAX_ATTEMPTS:
                msg.status, msg.processed_at = "failed", now
                stats["failed"] += 1
            else:
                msg.next_attempt_at = now + timedelta(minutes=2 ** msg.attempts)
                stats["retry"] += 1
    db.commit()
    return stats


def run_once(now: datetime | None = None) -> dict:
    now = now or get_clock().now()
    with session_factory()() as db:
        created = enqueue_due_reminders(db, now)
        stats = deliver_batch(db, now)
    with session_factory()() as db:
        purged = purge_expired(db, now)  # schedule retention: two months after a date's last shift
    return {"reminders_created": created, "schedule_versions_purged": purged["versions"], **stats}


def main() -> None:
    parser = argparse.ArgumentParser(description="MDA background worker")
    parser.add_argument("--once", action="store_true", help="run a single pass and exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if args.once:
        log.info("pass: %s", run_once())
        return
    poll = get_settings().worker_poll_seconds
    while True:
        try:
            log.info("pass: %s", run_once())
        except Exception:  # keep the loop alive; the next pass recomputes due work
            log.exception("worker pass failed")
        time_mod.sleep(poll)


if __name__ == "__main__":
    main()
