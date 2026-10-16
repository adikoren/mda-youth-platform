"""Per-shift availability. Closing is a planning cutoff, not a lock: answers after closing are
saved, flagged late and notify the day's managers. Nothing here changes a published assignment."""
import uuid
from datetime import datetime, time

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.access.context import Actor
from app.access.policies import can_register_for_day, require_manage_day
from app.audit.service import record
from app.accounts.models import User
from app.core.errors import bad_request, conflict, not_found
from app.core.timeutil import local_midnight_after, local_weekday
from app.days.models import ActivityDay, Shift
from app.notifications.outbox import day_manager_ids, enqueue
from app.registration.models import AvailabilityAttention, Invitation, Registration, RegistrationChoice, RegistrationRequest
from app.scheduling.models import Assignment


def shift_state(shift: Shift, now: datetime) -> str:
    """Per-shift cutoff, from the shift's own start instant (Asia/Jerusalem wall clock, stored as UTC).
    From starts_at on, registration and late requests for that shift are closed for volunteers."""
    if now >= shift.ends_at:
        return "ended"
    if now >= shift.starts_at:
        return "started"
    return "before"


_SHIFT_CLOSED = {
    "started": ("shift_started", "המשמרת כבר התחילה"),
    "ended": ("shift_ended", "המשמרת הסתיימה"),
}


def _refuse_if_shift_closed(shift: Shift, now: datetime, actor: Actor) -> None:
    """Authorization override: Admin keeps direct management of registrations (DECISIONS D-53)."""
    state = shift_state(shift, now)
    if state != "before" and not actor.is_admin:
        code, text = _SHIFT_CLOSED[state]
        raise conflict(code, text, {"state": state})


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
            "state": shift_state(s, now),
            "available": None if c is None else c.available,
            "arrival_time": c.arrival_time.strftime("%H:%M") if c and c.arrival_time else None,
            "is_late": bool(c and c.is_late),
        })
    return {
        "activity_day_id": str(day.id), "date": day.local_date.isoformat(), "kind": day.kind,
        "weekday": local_weekday(day.local_date),
        "window": window_state(day, now), "opens_at": day.registration_opens_at,
        "closes_at": day.registration_closes_at, "revision": reg.revision if reg else 0, "shifts": shifts,
        "server_now": now,  # the clock the cutoffs were judged against (the UI uses it, not the phone's clock)
        # The volunteer's own written requests for this day, so the screen never offers a duplicate.
        "requests": [{"id": str(q.id), "status": q.status, "created_at": q.created_at} for q in db.scalars(
            select(RegistrationRequest).where(RegistrationRequest.user_id == actor.user_id,
                                              RegistrationRequest.activity_day_id == day.id)
            .order_by(RegistrationRequest.created_at))],
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
        _refuse_if_shift_closed(shift, now, actor)
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


# ---------- written requests to the day manager after registration closed ----------
# Only an explicit submit creates anything. Opening the registration screen never does. A request
# is allowed only in the closed window (after the planning cutoff, before the date ends); before
# opening or during the open window the volunteer answers directly, and after the date it is too late.

def _same_text(a: str, b: str) -> bool:
    return " ".join(a.split()) == " ".join(b.split())


def submit_request(db: Session, actor: Actor, day: ActivityDay, request_id: uuid.UUID, shift_id: uuid.UUID,
                   message: str, now: datetime, confirm_duplicate: bool = False) -> tuple[RegistrationRequest, bool]:
    """Idempotent by the client-generated request_id: a retried submit returns the stored request
    (200) instead of creating a second one or notifying the managers again (201 when created).

    Several requests per day are allowed (intentional follow-ups). An identical text that is still
    pending for the same day is refused with 409 duplicate_request, so an accidental second press
    does not create a second request, unless the volunteer confirms it (confirm_duplicate)."""
    if not can_register_for_day(db, actor, day, now):
        raise not_found("day_not_found", "היום לא נמצא")
    existing = db.get(RegistrationRequest, request_id)
    if existing is not None:
        if existing.user_id == actor.user_id and existing.activity_day_id == day.id:
            db.rollback()
            return existing, False
        raise conflict("request_id_conflict", "מזהה הבקשה כבר בשימוש")
    state = window_state(day, now)
    if state == "not_open":
        raise conflict("registration_not_open", "ההרשמה ליום זה עוד לא נפתחה")
    if state == "open":
        raise conflict("registration_open", "ההרשמה עדיין פתוחה. אפשר לסמן זמינות ישירות")
    if state == "ended":
        raise conflict("day_ended", "התאריך כבר עבר")
    shift = db.get(Shift, shift_id)
    if shift is None or shift.activity_day_id != day.id:
        raise not_found("shift_not_found", "המשמרת לא נמצאה")
    # Late requests are for shifts that have not started yet. Checked at submit time, so a form that
    # was open when the shift started is refused.
    if shift_state(shift, now) != "before":
        code, text_he = _SHIFT_CLOSED[shift_state(shift, now)]
        raise conflict(code, text_he, {"state": shift_state(shift, now)})
    text = (message or "").strip()
    if len(text) < 2:
        raise bad_request("request_empty", "יש לכתוב את הבקשה")
    if not confirm_duplicate:
        pending = db.scalars(select(RegistrationRequest).where(
            RegistrationRequest.user_id == actor.user_id, RegistrationRequest.shift_id == shift.id,
            RegistrationRequest.status == "pending")).all()
        twin = next((p for p in pending if _same_text(p.message, text)), None)
        if twin is not None:
            db.rollback()
            raise conflict("duplicate_request", "כבר נשלחה בקשה זהה שעדיין לא טופלה. לשלוח שוב?",
                           {"existing_id": str(twin.id)})
    req = RegistrationRequest(id=request_id, user_id=actor.user_id, activity_day_id=day.id, shift_id=shift.id,
                              message=text, status="pending", created_at=now)
    db.add(req)
    db.flush()
    managers = day_manager_ids(db, local_weekday(day.local_date))
    enqueue(db, event_type="registration_request", recipients=managers, dedup_prefix=f"reg_request:{req.id}",
            payload={"date": day.local_date.isoformat(), "activity_day_id": str(day.id), "shift": shift.kind,
                     "request_id": str(req.id), "user_id": str(actor.user_id)}, now=now)
    # The audit row records that a request was made, not its text (text is private to the volunteer and managers).
    record(db, actor_id=actor.user_id, action="registration_request", object_type="activity_day", object_id=day.id,
           now=now, meta={"request": str(req.id)})
    db.commit()
    return req, True


def day_requests(db: Session, actor: Actor, day: ActivityDay) -> list[dict]:
    """Every request for the day, pending first, so handled requests stay available for review."""
    require_manage_day(actor, day)
    rows = db.execute(select(RegistrationRequest, User.full_name).join(User, User.id == RegistrationRequest.user_id)
                      .where(RegistrationRequest.activity_day_id == day.id)
                      .order_by((RegistrationRequest.status == "resolved"), RegistrationRequest.created_at)).all()
    kinds = {s.id: s.kind for s in db.scalars(select(Shift).where(Shift.activity_day_id == day.id))}
    return [{"id": str(r.id), "user_id": str(r.user_id), "full_name": name, "message": r.message,
             "shift": kinds.get(r.shift_id), "status": r.status, "created_at": r.created_at,
             "resolved_at": r.resolved_at} for r, name in rows]


def resolve_request(db: Session, actor: Actor, request_id: uuid.UUID, now: datetime) -> None:
    req = db.get(RegistrationRequest, request_id)
    if req is None:
        raise not_found("request_not_found", "הבקשה לא נמצאה")
    day = db.get(ActivityDay, req.activity_day_id)
    require_manage_day(actor, day)
    if req.status == "pending":
        req.status, req.resolved_at, req.resolved_by_id = "resolved", now, actor.user_id
        record(db, actor_id=actor.user_id, action="registration_request_resolve", object_type="registration_request",
               object_id=req.id, now=now)
    db.commit()
