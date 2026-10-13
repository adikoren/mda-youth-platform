"""Per-shift availability. Closing is a planning cutoff, not a lock: answers after closing are
saved, flagged late and notify the day's managers. Nothing here changes a published assignment."""
import uuid
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.policies import can_register_for_day
from app.audit.service import record
from app.core.errors import bad_request, conflict, not_found
from app.core.timeutil import local_midnight_after, local_weekday
from app.days.models import ActivityDay, Shift
from app.notifications.outbox import day_manager_ids, enqueue
from app.registration.models import AvailabilityAttention, Invitation, Registration, RegistrationChoice
from app.scheduling.models import Assignment


def window_state(day: ActivityDay, now: datetime) -> str:
    if now < day.registration_opens_at:
        return "not_open"
    if now >= local_midnight_after(day.local_date):
        return "ended"
    if now >= day.registration_closes_at:
        return "closed_late_allowed"
    return "open"


def selectable_shifts(db: Session, day: ActivityDay) -> list[Shift]:
    order = {"morning": 0, "reinforcement": 1, "evening": 2}
    return sorted(db.scalars(select(Shift).where(Shift.activity_day_id == day.id, Shift.self_selectable.is_(True))),
                  key=lambda s: order[s.kind])


def my_registration(db: Session, actor: Actor, day: ActivityDay, now: datetime) -> dict:
    if not can_register_for_day(db, actor, day, now):
        raise not_found("day_not_found", "היום לא נמצא")
    reg = db.scalar(select(Registration).where(Registration.user_id == actor.user_id,
                                               Registration.activity_day_id == day.id))
    choices = {}
    if reg is not None:
        for c in db.scalars(select(RegistrationChoice).where(RegistrationChoice.registration_id == reg.id)):
            choices[c.shift_id] = c
    shifts = []
    for s in selectable_shifts(db, day):
        c = choices.get(s.id)
        shifts.append({
            "shift_id": str(s.id), "kind": s.kind, "starts_at": s.starts_at, "ends_at": s.ends_at,
            "arrival_at": s.arrival_at, "allows_late_arrival": s.allows_late_arrival,
            "available": None if c is None else c.available,
            "arrival_time": c.arrival_time.strftime("%H:%M") if c and c.arrival_time else None,
            "is_late": bool(c and c.is_late),
        })
    return {
        "activity_day_id": str(day.id), "date": day.local_date.isoformat(), "kind": day.kind,
        "weekday": local_weekday(day.local_date),
        "window": window_state(day, now), "opens_at": day.registration_opens_at,
        "closes_at": day.registration_closes_at, "revision": reg.revision if reg else 0, "shifts": shifts,
    }


def save_registration(db: Session, actor: Actor, day: ActivityDay, answers: list[dict],
                      expected_revision: int | None, now: datetime) -> dict:
    if not can_register_for_day(db, actor, day, now):
        raise not_found("day_not_found", "היום לא נמצא")
    state = window_state(day, now)
    if state == "not_open":
        raise conflict("registration_not_open", "ההרשמה ליום זה עוד לא נפתחה")
    if state == "ended":
        raise conflict("day_ended", "התאריך כבר עבר")
    shifts = {s.id: s for s in selectable_shifts(db, day)}
    parsed: dict[uuid.UUID, tuple[bool, time | None]] = {}
    for a in answers:
        shift = shifts.get(a["shift_id"])
        if shift is None:
            raise bad_request("shift_not_selectable", "אי אפשר להירשם בעצמך למשמרת זו")
        arrival = a.get("arrival_time")
        if arrival is not None and (not shift.allows_late_arrival or not a["available"]):
            raise bad_request("arrival_not_allowed", "בחירת שעת הגעה אפשרית רק במשמרת ערב ביום רגיל")
        parsed[shift.id] = (bool(a["available"]), arrival)

    # Concurrency: create-if-missing, then lock the container row for the rest of the transaction.
    db.execute(insert(Registration).values(
        id=uuid.uuid4(), user_id=actor.user_id, activity_day_id=day.id, source="self", revision=0,
        created_at=now, updated_at=now, updated_by_id=actor.user_id,
    ).on_conflict_do_nothing(index_elements=["user_id", "activity_day_id"]))
    reg = db.scalar(select(Registration).where(Registration.user_id == actor.user_id,
                                               Registration.activity_day_id == day.id).with_for_update())
    if expected_revision is not None and expected_revision != reg.revision:
        raise conflict("stale_registration", "ההרשמה עודכנה ממכשיר אחר. נא לרענן", {"revision": reg.revision})

    existing = {c.shift_id: c for c in db.scalars(select(RegistrationChoice).where(
        RegistrationChoice.registration_id == reg.id))}
    late = state == "closed_late_allowed"
    changed: list[uuid.UUID] = []
    withdrawn: list[uuid.UUID] = []
    for shift_id, (available, arrival) in parsed.items():
        c = existing.get(shift_id)
        if c is not None and c.available == available and c.arrival_time == arrival:
            continue  # idempotent retry / no-op: other shift choices are preserved untouched
        if c is None:
            c = RegistrationChoice(registration_id=reg.id, shift_id=shift_id, available=available,
                                   arrival_time=arrival, is_late=late, first_answered_at=now, updated_at=now)
            db.add(c)
        else:
            if c.available and not available:
                withdrawn.append(shift_id)
            c.available, c.arrival_time, c.is_late, c.updated_at = available, arrival, late, now
        changed.append(shift_id)

    if not changed:
        db.rollback()
        return {"revision": reg.revision, "changed": False, "late": late}

    reg.revision += 1
    reg.updated_at, reg.updated_by_id = now, actor.user_id
    db.flush()
    payload = {"date": day.local_date.isoformat(), "activity_day_id": str(day.id), "user_id": str(actor.user_id)}
    managers = day_manager_ids(db, local_weekday(day.local_date))
    if late:
        enqueue(db, event_type="late_registration", recipients=managers,
                dedup_prefix=f"late_reg:{reg.id}:{reg.revision}", payload=payload, now=now)
    _flag_withdrawals(db, actor, day, reg, withdrawn, managers, payload, now)

    # Answering a personal invitation counts as confirming it.
    for inv in db.scalars(select(Invitation).where(Invitation.user_id == actor.user_id,
                                                   Invitation.activity_day_id == day.id,
                                                   Invitation.kind == "invitation", Invitation.status == "pending")):
        inv.status, inv.responded_at = "confirmed", now
    record(db, actor_id=actor.user_id, action="registration_save", object_type="registration", object_id=reg.id,
           now=now, meta={"late": late, "revision": reg.revision,
                          "choices": {shifts[s].kind: parsed[s][0] for s in changed}})
    db.commit()
    return {"revision": reg.revision, "changed": True, "late": late}


def _flag_withdrawals(db, actor, day, reg, withdrawn, managers, payload, now) -> None:
    if not withdrawn or day.latest_published_version_id is None:
        return
    assigned_shifts = set(db.scalars(select(Assignment.shift_id).where(
        Assignment.version_id == day.latest_published_version_id, Assignment.user_id == actor.user_id,
        Assignment.shift_id.in_(withdrawn))))
    for shift_id in assigned_shifts:
        db.execute(insert(AvailabilityAttention).values(
            id=uuid.uuid4(), shift_id=shift_id, user_id=actor.user_id,
            published_version_id=day.latest_published_version_id, registration_revision=reg.revision,
            status="pending", created_at=now,
        ).on_conflict_do_nothing(index_elements=["shift_id", "user_id"], index_where=AvailabilityAttention.status == "pending"))
        enqueue(db, event_type="availability_withdrawn", recipients=managers,
                dedup_prefix=f"withdrawn:{shift_id}:{actor.user_id}:{reg.revision}",
                payload={**payload, "shift_id": str(shift_id)}, now=now)


def confirm_invitation(db: Session, actor: Actor, invitation_id: uuid.UUID, now: datetime) -> None:
    inv = db.get(Invitation, invitation_id)
    if inv is None or inv.user_id != actor.user_id:
        raise not_found()
    if inv.status == "pending" and inv.expires_at > now:
        inv.status, inv.responded_at = "confirmed", now
        record(db, actor_id=actor.user_id, action="invitation_confirm", object_type="invitation", object_id=inv.id,
               now=now)
        db.commit()


def active_invitations(db: Session, actor: Actor, now: datetime) -> list[dict]:
    rows = db.execute(select(Invitation, ActivityDay, Shift.kind)
                      .join(ActivityDay, ActivityDay.id == Invitation.activity_day_id)
                      .outerjoin(Shift, Shift.id == Invitation.shift_id)
                      .where(Invitation.user_id == actor.user_id, Invitation.status == "pending",
                             Invitation.expires_at > now)
                      .order_by(ActivityDay.local_date)).all()
    return [{"id": str(inv.id), "kind": inv.kind, "activity_day_id": str(day.id), "date": day.local_date.isoformat(),
             "weekday": local_weekday(day.local_date), "shift": kind} for inv, day, kind in rows]
