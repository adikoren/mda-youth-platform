"""Volunteer-facing published schedules, personal history and in-app notifications, plus the
manager board API. Draft endpoints live under /api/manage and require manages_day."""
import uuid
from datetime import date, datetime, time, timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.access import policies, queries
from app.access.context import Actor
from app.access.dependencies import get_active_actor, now_dep
from app.core.db import get_db
from app.core.errors import not_found
from app.core.timeutil import local_today
from app.days.models import ActivityDay, Shift
from app.notifications.models import OutboxMessage
from app.notifications.outbox import EVENT_TEXT_HE
from app.registration import service as registration_service
from app.scheduling import cancellations, service, views

router = APIRouter(prefix="/api", tags=["schedule"])


def _range(start: date | None, end: date | None, now: datetime) -> tuple[date, date]:
    today = local_today(now)
    start = start or today - timedelta(days=7)
    end = end or today + timedelta(days=14)
    if (end - start).days > 120:
        end = start + timedelta(days=120)
    return start, end


@router.get("/schedule")
def visible_schedules(start: date | None = None, end: date | None = None, actor: Actor = Depends(get_active_actor),
                      db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    start, end = _range(start, end, now)
    pred = queries.published_schedule_visible(actor, now)
    days = list(db.scalars(select(ActivityDay).where(pred, ActivityDay.local_date.between(start, end))
                           .order_by(ActivityDay.local_date)))
    total = db.scalar(select(func.count()).select_from(ActivityDay).where(pred))
    # Historical = every shift of the date has ended (server time). Automatic: no manual archiving.
    last_end = dict(db.execute(select(Shift.activity_day_id, func.max(Shift.ends_at))
                               .where(Shift.activity_day_id.in_([d.id for d in days]))
                               .group_by(Shift.activity_day_id)).all())
    items = []
    for d in days:
        item = views.day_summary(d, now)
        item["historical"] = last_end.get(d.id) is not None and last_end[d.id] <= now
        items.append(item)
    return {"items": items, "total_visible": total}


@router.get("/schedule/{day_id}")
def published(day_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
              now: datetime = Depends(now_dep)):
    day = service.get_day(db, day_id)
    if not policies.can_view_published_schedule(db, actor, day, now):
        raise not_found("schedule_not_found", "הסידור לא נמצא")
    include_phone = actor.is_admin or policies.manages_day(actor, day)
    return views.published_schedule(db, actor, day, now, include_phone=include_phone)


@router.get("/manage/days/{day_id}/requests")
def day_requests(day_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db)):
    """Written requests from volunteers after registration closed (pending first, then handled)."""
    return registration_service.day_requests(db, actor, service.get_day(db, day_id))


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: str = Field(pattern="^(approved|rejected)$")


@router.post("/manage/requests/{request_id}/decision")
def decide_request(request_id: uuid.UUID, body: DecisionIn, actor: Actor = Depends(get_active_actor),
                   db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    """Day Manager (or Admin) approves or rejects a volunteer's request. Publication waits for this."""
    registration_service.decide_request(db, actor, request_id, body.decision, now)
    return {"ok": True}


@router.post("/shifts/{shift_id}/cancel")
def cancel_my_shift(shift_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                    now: datetime = Depends(now_dep)):
    """Volunteer cancels their own registration or assignment for one shift. Allowed only before the
    shift starts (server time); no approval needed. See app.scheduling.cancellations."""
    shift = db.get(Shift, shift_id)
    if shift is None:
        raise not_found("shift_not_found", "המשמרת לא נמצאה")
    return cancellations.volunteer_cancel(db, actor, shift, now)


@router.get("/me/next-assignment")
def my_next_assignment(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                       now: datetime = Depends(now_dep)):
    """The nearest live published assignment that has not ended (an active shift counts), or null."""
    return {"assignment": cancellations.next_assignment(db, actor, now)}


@router.get("/people/{user_id}")
def person_profile(user_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db)):
    """A volunteer's personal profile, for the viewers allowed by app.access.policies.can_view_profile."""
    from app.access.policies import can_view_profile
    from app.accounts.models import User
    from app.core.timeutil import WEEKDAY_NAMES_HE
    from app.scheduling.pool import people
    if db.get(User, user_id) is None or not can_view_profile(db, actor, user_id):
        raise not_found("profile_not_found", "הפרופיל לא נמצא")
    p = people(db, {user_id})[user_id]
    wd = p.home_weekday
    return {"id": str(user_id), "full_name": p.full_name, "level": p.level,
            "home_weekday": wd, "home_weekday_name": WEEKDAY_NAMES_HE[wd] if wd is not None else None,
            "phone": p.phone}


@router.get("/me/history")
def history(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db)):
    return views.my_history(db, actor)


@router.get("/me/notifications")
def notifications(limit: int = Query(30, le=100), actor: Actor = Depends(get_active_actor),
                  db: Session = Depends(get_db)):
    rows = db.scalars(select(OutboxMessage).where(OutboxMessage.recipient_user_id == actor.user_id)
                      .order_by(OutboxMessage.created_at.desc()).limit(limit))
    return [{"id": str(m.id), "event_type": m.event_type, "text": EVENT_TEXT_HE.get(m.event_type, m.event_type),
             "payload": m.payload, "created_at": m.created_at, "read": m.read_at is not None,
             # Truthful delivery state: 'simulated' means no phone channel is connected yet.
             "phone_status": m.status} for m in rows]


@router.post("/me/notifications/read")
def mark_read(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
              now: datetime = Depends(now_dep)):
    db.execute(update(OutboxMessage).where(OutboxMessage.recipient_user_id == actor.user_id,
                                           OutboxMessage.read_at.is_(None)).values(read_at=now))
    db.commit()
    return {"ok": True}


# ---------------- manager board ----------------

manage = APIRouter(prefix="/api/manage", tags=["manage"])


class CapacityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capacity: int  # 0–MAX_CAPACITY, validated in the service with a Hebrew message


class UserRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID


class MoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID
    to_slot_key: uuid.UUID


class OrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: list[uuid.UUID] = Field(max_length=200)


class RegistrationOpenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open: bool


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: uuid.UUID
    expected_edit_count: int


UPCOMING_DAYS = 7    # main list: today through the next seven days (Asia/Jerusalem)
HISTORY_DAYS = 60    # history: completed dates within the schedule retention window


@manage.get("/days")
def managed_days(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                 now: datetime = Depends(now_dep)):
    """The manager's dates in two sections (decision 2026-10-17). "main": today through the next seven days that
    are not completed; "history": dates whose every shift has ended, most recent first. A date leaves "main" by
    its clock (its last shift ends), not by any manual step. Nothing is modified."""
    today = local_today(now)
    days = list(db.scalars(select(ActivityDay).where(
        queries.managed_days(actor),
        ActivityDay.local_date.between(today - timedelta(days=HISTORY_DAYS), today + timedelta(days=UPCOMING_DAYS)))
        .order_by(ActivityDay.local_date)))
    last_end = dict(db.execute(select(Shift.activity_day_id, func.max(Shift.ends_at))
                               .where(Shift.activity_day_id.in_([d.id for d in days]))
                               .group_by(Shift.activity_day_id)).all()) if days else {}
    flags = views.manager_day_flags(db, days)
    out = []
    for d in days:
        completed = last_end.get(d.id) is not None and last_end[d.id] <= now
        if completed:
            section = "history"
        elif today <= d.local_date <= today + timedelta(days=UPCOMING_DAYS):
            section = "main"
        else:
            continue  # beyond the next seven days: not shown in the main list
        out.append(views.day_summary(d, now) | flags[d.id] | {"section": section})
    main = sorted((x for x in out if x["section"] == "main"), key=lambda x: x["date"])
    history = sorted((x for x in out if x["section"] == "history"), key=lambda x: x["date"], reverse=True)
    return main + history


@manage.post("/days/{day_id}/draft")
def start_draft(day_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                now: datetime = Depends(now_dep)):
    service.ensure_draft(db, actor, day_id, now)
    return views.board(db, service.get_day(db, day_id), now)


@manage.get("/days/{day_id}/board")
def get_board(day_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
              now: datetime = Depends(now_dep)):
    day = service.get_day(db, day_id)
    policies.require_manage_day(actor, day)
    return views.board(db, day, now)


@manage.get("/days/{day_id}/registrations")
def day_registrations(day_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db)):
    day = service.get_day(db, day_id)
    if not policies.can_read_day_registrations(actor, day):
        raise not_found()
    return views.registrations_table(db, day)


@manage.post("/days/{day_id}/publish")
def publish(day_id: uuid.UUID, body: PublishIn, actor: Actor = Depends(get_active_actor),
            db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    return service.publish(db, actor, day_id, body.draft_id, body.expected_edit_count, now)


class ShiftTypeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(morning|reinforcement)$")  # evening always exists


@manage.post("/days/{day_id}/shifts", status_code=201)
def add_shift_type(day_id: uuid.UUID, body: ShiftTypeIn, actor: Actor = Depends(get_active_actor),
                   db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    shift = service.add_shift_type(db, actor, day_id, body.kind, now)
    return {"id": str(shift.id), "kind": shift.kind}


@manage.post("/days/{day_id}/invitations", status_code=201)
def invite(day_id: uuid.UUID, body: UserRef, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
           now: datetime = Depends(now_dep)):
    inv = service.invite(db, actor, day_id, body.user_id, now)
    return {"id": str(inv.id), "status": inv.status}


@manage.post("/shifts/{shift_id}/slots", status_code=201)
def add_slot(shift_id: uuid.UUID, body: CapacityIn, actor: Actor = Depends(get_active_actor),
             db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    slot = service.add_slot(db, actor, shift_id, body.capacity, now)
    return {"slot_key": str(slot.slot_key)}


@manage.patch("/slots/{slot_key}")
def slot_capacity(slot_key: uuid.UUID, body: CapacityIn, actor: Actor = Depends(get_active_actor),
                  db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    service.set_slot_capacity(db, actor, slot_key, body.capacity, now)
    return {"ok": True}


class BoardingEntryIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID
    shift_id: uuid.UUID


class BoardingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[BoardingEntryIn] = Field(max_length=200)  # in boarding order


@manage.put("/days/{day_id}/boarding")
def set_boarding(day_id: uuid.UUID, body: BoardingIn, actor: Actor = Depends(get_active_actor),
                 db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    """Replace the date's boarding order (סדר עלייה) in the working copy. Trainees only; not an assignment."""
    service.set_boarding(db, actor, day_id, [(e.user_id, e.shift_id) for e in body.entries], now)
    return {"saved": len(body.entries)}


class OperatorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID | None = None  # None clears the operator
    start: time | None = None
    end: time | None = None


@manage.put("/slots/{slot_key}/operator")
def set_operator(slot_key: uuid.UUID, body: OperatorIn, actor: Actor = Depends(get_active_actor),
                 db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    """The volunteer who operates an additional ambulance, from a start time (until the shift ends) or in an
    explicit range. Draft only."""
    service.set_slot_operator(db, actor, slot_key, body.user_id, body.start, body.end, now)
    return {"ok": True}


@manage.post("/slots/{slot_key}/cancel")
def cancel_slot(slot_key: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                now: datetime = Depends(now_dep)):
    return {"released": [str(u) for u in service.cancel_slot(db, actor, slot_key, now)]}


@manage.post("/slots/{slot_key}/restore")
def restore_slot(slot_key: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                 now: datetime = Depends(now_dep)):
    service.restore_slot(db, actor, slot_key, now)
    return {"ok": True}


@manage.post("/slots/{slot_key}/assign", status_code=201)
def assign(slot_key: uuid.UUID, body: UserRef, actor: Actor = Depends(get_active_actor),
           db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    service.assign(db, actor, slot_key, body.user_id, now)
    return {"ok": True}


@manage.post("/shifts/{shift_id}/move")
def move(shift_id: uuid.UUID, body: MoveIn, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
         now: datetime = Depends(now_dep)):
    service.move(db, actor, shift_id, body.user_id, body.to_slot_key, now)
    return {"ok": True}


@manage.post("/shifts/{shift_id}/unassign")
def unassign(shift_id: uuid.UUID, body: UserRef, actor: Actor = Depends(get_active_actor),
             db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    service.unassign(db, actor, shift_id, body.user_id, now)
    return {"ok": True}


@manage.put("/shifts/{shift_id}/order")
def order(shift_id: uuid.UUID, body: OrderIn, actor: Actor = Depends(get_active_actor),
          db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    service.set_order(db, actor, shift_id, body.user_ids, now)
    return {"ok": True}


@manage.post("/shifts/{shift_id}/registration")
def morning_registration(shift_id: uuid.UUID, body: RegistrationOpenIn, actor: Actor = Depends(get_active_actor),
                         db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    changed = service.set_morning_registration(db, actor, shift_id, body.open, now)
    return {"shift_id": str(shift_id), "registration_open": body.open, "changed": changed}


@manage.post("/shifts/{shift_id}/participants", status_code=201)
def add_participant(shift_id: uuid.UUID, body: UserRef, actor: Actor = Depends(get_active_actor),
                    db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    service.add_participant(db, actor, shift_id, body.user_id, now)
    return {"ok": True}


@manage.delete("/shifts/{shift_id}/participants/{user_id}")
def remove_participant(shift_id: uuid.UUID, user_id: uuid.UUID, actor: Actor = Depends(get_active_actor),
                       db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    service.remove_participant(db, actor, shift_id, user_id, now)
    return {"ok": True}


@manage.post("/attentions/{attention_id}/resolve")
def resolve(attention_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
            now: datetime = Depends(now_dep)):
    service.resolve_attention(db, actor, attention_id, now)
    return {"ok": True}
