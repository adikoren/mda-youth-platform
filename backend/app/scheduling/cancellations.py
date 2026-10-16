"""Volunteer cancellation of their own registration or assignment, and the one rule that makes a
cancelled assignment disappear from every live read of a published version.

Rules (decided 2026-10-16):
- Only before the shift starts (server time, the shift's own start instant); refused at or after it.
- Before publication: the registration choice is withdrawn; nothing is published.
- After publication: the person is taken out of the live schedule at once, and the Day Manager gets a
  warning. The published version itself is not edited (it is history).
- The next publication rebuilds the crew without them; until then the Day Manager cannot re-assign them
  to the same shift on the old version (they must publish first)."""
import uuid
from datetime import datetime

from sqlalchemy import and_, delete, select
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.audit.service import record
from app.core.errors import conflict, not_found
from app.days.models import ActivityDay, Shift
from app.notifications.outbox import day_manager_ids, enqueue
from app.registration.models import Registration, RegistrationChoice
from app.registration.service import shift_state
from app.core.timeutil import local_weekday
from app.scheduling.liveness import is_cancelled, live_assignment
from app.scheduling.models import AmbulanceSlot, Assignment, AssignmentCancellation
from app.scheduling.service import current_draft, lock_day


def volunteer_cancel(db: Session, actor: Actor, shift: Shift, now: datetime) -> dict:
    day = lock_day(db, shift.activity_day_id)  # serialise with publication
    state = shift_state(shift, now)
    if state != "before":
        code, text = ("shift_started", "המשמרת כבר התחילה. אי אפשר לבטל עכשיו") if state == "started" \
            else ("shift_ended", "המשמרת הסתיימה")
        raise conflict(code, text, {"state": state})
    result = {"withdrawn": False, "published_cancelled": False, "removed_from_draft": False}
    reg = db.scalar(select(Registration).where(Registration.user_id == actor.user_id,
                                               Registration.activity_day_id == day.id))
    if reg is not None:
        choice = db.scalar(select(RegistrationChoice).where(RegistrationChoice.registration_id == reg.id,
                                                            RegistrationChoice.shift_id == shift.id))
        if choice is not None and choice.available and choice.cancelled_at is None:
            # Explicit "בוטל": the original answer stays on the record; the cancellation is added beside it.
            choice.cancelled_at, choice.cancelled_by_id, choice.updated_at = now, actor.user_id, now
            reg.revision += 1
            reg.updated_at, reg.updated_by_id = now, actor.user_id
            result["withdrawn"] = True
    # The unpublished working copy must not keep the volunteer either (publishing it would bring them back).
    draft = current_draft(db, day.id)
    if draft is not None:
        removed = db.execute(delete(Assignment).where(Assignment.version_id == draft.id,
                                                      Assignment.shift_id == shift.id,
                                                      Assignment.user_id == actor.user_id)).rowcount
        result["removed_from_draft"] = bool(removed)
    version_id = day.latest_published_version_id
    if version_id is not None:
        assigned = db.scalar(select(Assignment.id).where(Assignment.version_id == version_id,
                                                         Assignment.shift_id == shift.id,
                                                         Assignment.user_id == actor.user_id))
        if assigned is not None and not is_cancelled(db, version_id, shift.id, actor.user_id):
            db.add(AssignmentCancellation(version_id=version_id, shift_id=shift.id, user_id=actor.user_id,
                                          cancelled_at=now))
            db.flush()
            managers = day_manager_ids(db, local_weekday(day.local_date))
            enqueue(db, event_type="volunteer_cancelled", recipients=managers,
                    dedup_prefix=f"vol_cancel:{version_id}:{shift.id}:{actor.user_id}",
                    payload={"date": day.local_date.isoformat(), "activity_day_id": str(day.id),
                             "shift": shift.kind, "user_id": str(actor.user_id)}, now=now)
            result["published_cancelled"] = True
    if not (result["withdrawn"] or result["published_cancelled"] or result["removed_from_draft"]):
        db.rollback()
        raise not_found("nothing_to_cancel", "אין שיבוץ או הרשמה למשמרת הזו לביטול")
    record(db, actor_id=actor.user_id, action="volunteer_cancel", object_type="shift", object_id=shift.id, now=now,
           meta={"withdrawn": result["withdrawn"], "published": result["published_cancelled"]})
    db.commit()
    return result


def next_assignment(db: Session, actor: Actor, now: datetime) -> dict | None:
    """The nearest live published assignment that has not ended (an active shift counts)."""
    row = db.execute(
        select(Shift, ActivityDay, AmbulanceSlot.ordinal)
        .join(ActivityDay, ActivityDay.id == Shift.activity_day_id)
        .join(Assignment, and_(Assignment.version_id == ActivityDay.latest_published_version_id,
                               Assignment.shift_id == Shift.id))
        .join(AmbulanceSlot, AmbulanceSlot.id == Assignment.slot_id)
        .where(Assignment.user_id == actor.user_id, Shift.ends_at > now, live_assignment())
        .order_by(Shift.starts_at).limit(1)).first()
    if row is None:
        return None
    s, d, ordinal = row
    return {"activity_day_id": str(d.id), "shift_id": str(s.id), "date": d.local_date.isoformat(),
            "weekday": local_weekday(d.local_date), "kind": s.kind, "starts_at": s.starts_at, "ends_at": s.ends_at,
            "ambulance": ordinal, "state": shift_state(s, now),
            "cancellable": shift_state(s, now) == "before"}
