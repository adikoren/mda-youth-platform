"""Volunteer-facing published schedules, personal history and in-app notifications, plus the
manager board API. Draft endpoints live under /api/manage and require manages_day."""
import uuid
from datetime import date, datetime, timedelta

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
from app.days.models import ActivityDay
from app.notifications.models import OutboxMessage
from app.notifications.outbox import EVENT_TEXT_HE
from app.registration import service as registration_service
from app.scheduling import service, views

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
    return {"items": [views.day_summary(d, now) for d in days], "total_visible": total}


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


@router.post("/manage/requests/{request_id}/resolve")
def resolve_request(request_id: uuid.UUID, actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db),
                    now: datetime = Depends(now_dep)):
    registration_service.resolve_request(db, actor, request_id, now)
    return {"ok": True}


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


@manage.get("/days")
def managed_days(start: date | None = None, end: date | None = None, actor: Actor = Depends(get_active_actor),
                 db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    start, end = _range(start, end, now)
    days = list(db.scalars(select(ActivityDay).where(queries.managed_days(actor),
                                                     ActivityDay.local_date.between(start, end))
                           .order_by(ActivityDay.local_date)))
    flags = views.manager_day_flags(db, days)
    return [views.day_summary(d, now) | flags[d.id] for d in days]


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
