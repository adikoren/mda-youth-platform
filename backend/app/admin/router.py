"""People/authority management and calendar configuration.

/api/admin/*    admin interface (full control, audited)
/api/day-managers  youth manager + admin: appoint/replace/remove day managers
/api/users/search  managers: find volunteers to add/invite
/api/templates, /api/days/*  timing/classification (admin, or the scoped day manager)"""
import uuid
from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.access import policies
from app.access.context import Actor
from app.access.dependencies import get_active_actor, now_dep
from app.accounts import service as accounts
from app.accounts.models import AuthorityRole, DayMembership, ManagerDayScope, User, VolunteerLevel
from app.audit.models import AuditEvent
from app.audit.service import record
from app.core.db import get_db
from app.core.errors import forbidden, not_found
from app.core.timeutil import WEEKDAY_NAMES_HE
from app.days import service as days
from app.days.models import RegistrationTemplate
from app.notifications.models import OutboxMessage
from app.scheduling.service import get_day, lock_day
from app.scheduling.views import day_summary

router = APIRouter(prefix="/api", tags=["admin"])


def require_admin(actor: Actor = Depends(get_active_actor)) -> Actor:
    if not actor.is_admin:
        raise not_found()  # the admin interface does not reveal itself to others
    return actor


def _user_rows(db: Session, where) -> list[dict]:
    q = (select(User, VolunteerLevel.level, DayMembership.weekday)
         .outerjoin(VolunteerLevel, (VolunteerLevel.user_id == User.id) & VolunteerLevel.valid_to.is_(None))
         .outerjoin(DayMembership, (DayMembership.user_id == User.id) & DayMembership.valid_to.is_(None))
         .where(where).order_by(User.full_name))
    rows = db.execute(q).all()
    ids = [u.id for u, _, _ in rows]
    roles: dict[uuid.UUID, list[str]] = {}
    for uid, role in db.execute(select(AuthorityRole.user_id, AuthorityRole.role).where(
            AuthorityRole.user_id.in_(ids), AuthorityRole.valid_to.is_(None))):
        roles.setdefault(uid, []).append(role)
    scopes: dict[uuid.UUID, list[int]] = {}
    for uid, wd in db.execute(select(ManagerDayScope.user_id, ManagerDayScope.weekday).where(
            ManagerDayScope.user_id.in_(ids), ManagerDayScope.valid_to.is_(None))):
        scopes.setdefault(uid, []).append(wd)
    return [{"id": str(u.id), "full_name": u.full_name, "email": u.email, "phone": u.phone, "status": u.status,
             "level": level, "home_weekday": wd, "requested_weekday": u.requested_weekday,
             "roles": sorted(roles.get(u.id, [])), "managed_weekdays": sorted(scopes.get(u.id, [])),
             "created_at": u.created_at} for u, level, wd in rows]


# ---------------- search for managers ----------------

@router.get("/users/search")
def search_users(q: str = Query("", max_length=60), actor: Actor = Depends(get_active_actor),
                 db: Session = Depends(get_db)):
    if not policies.can_search_users(actor):
        raise forbidden()
    like = f"%{q.strip()}%"
    rows = _user_rows(db, (User.status == "active") & or_(User.full_name.ilike(like), User.email.ilike(like)))[:30]
    # Minimal profile for scheduling; contact details stay out of search results.
    return [{k: r[k] for k in ("id", "full_name", "level", "home_weekday")} for r in rows]


class LevelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str = Field(pattern="^(trainee|youth|mentor)$")


@router.put("/manage/users/{user_id}/level")
def manager_set_level(user_id: uuid.UUID, body: LevelIn, actor: Actor = Depends(get_active_actor),
                      db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    accounts.get_user(db, user_id)
    if not policies.can_change_level(actor, accounts.current_home_weekday(db, user_id)):
        raise forbidden()
    accounts.set_level(db, user_id, body.level, actor.user_id, now)
    db.commit()
    return {"ok": True}


# ---------------- day managers (youth manager / admin) ----------------

class ManagersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: list[uuid.UUID] = Field(max_length=10)


@router.get("/day-managers")
def list_day_managers(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db)):
    if not policies.can_appoint_day_managers(actor):
        raise not_found()
    out = []
    for wd in range(7):
        ids = accounts.day_managers_for(db, wd)
        rows = _user_rows(db, User.id.in_(ids)) if ids else []
        out.append({"weekday": wd, "weekday_name": WEEKDAY_NAMES_HE[wd],
                    "managers": [{"id": r["id"], "full_name": r["full_name"]} for r in rows]})
    return out


@router.put("/day-managers/{weekday}")
def set_day_managers(weekday: int, body: ManagersIn, actor: Actor = Depends(get_active_actor),
                     db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    if not policies.can_appoint_day_managers(actor):
        raise not_found()
    accounts.set_day_managers(db, weekday, body.user_ids, actor.user_id, now)
    db.commit()
    return {"ok": True}


# ---------------- calendar configuration ----------------

def _tpl_out(t: RegistrationTemplate) -> dict:
    return {"weekday": t.weekday, "weekday_name": WEEKDAY_NAMES_HE[t.weekday],
            "open_days_before": t.open_days_before, "open_time": t.open_time.strftime("%H:%M"),
            "close_days_before": t.close_days_before, "close_time": t.close_time.strftime("%H:%M"),
            "target_days_before": t.target_days_before, "target_time": t.target_time.strftime("%H:%M")}


class TemplateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    open_days_before: int | None = Field(default=None, ge=1, le=14)
    open_time: time | None = None
    close_days_before: int | None = Field(default=None, ge=0, le=13)
    close_time: time | None = None
    target_days_before: int | None = Field(default=None, ge=0, le=13)
    target_time: time | None = None


@router.get("/templates")
def get_templates(actor: Actor = Depends(get_active_actor), db: Session = Depends(get_db)):
    if not (actor.is_admin or actor.is_day_manager or actor.is_youth_manager):
        raise not_found()
    rows = db.scalars(select(RegistrationTemplate).where(RegistrationTemplate.valid_to.is_(None))
                      .order_by(RegistrationTemplate.weekday))
    return [_tpl_out(t) for t in rows]


@router.put("/templates/{weekday}")
def put_template(weekday: int, body: TemplateIn, actor: Actor = Depends(get_active_actor),
                 db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    if not (actor.is_admin or weekday in actor.managed_weekdays):
        raise forbidden()
    tpl = days.update_template(db, weekday, body.model_dump(exclude_none=True), actor.user_id, now)
    db.commit()
    return _tpl_out(tpl)


class TimingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    registration_opens_at: datetime | None = None
    registration_closes_at: datetime | None = None
    publish_target_at: datetime | None = None


class KindIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(regular|friday|saturday|holiday|holiday_eve)$")


@router.patch("/days/{day_id}/timing")
def day_timing(day_id: uuid.UUID, body: TimingIn, actor: Actor = Depends(get_active_actor),
               db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    day = lock_day(db, day_id)
    policies.require_manage_day(actor, day)
    days.change_timing(db, day, body.registration_opens_at, body.registration_closes_at, body.publish_target_at,
                       actor.user_id, now)
    db.commit()
    return day_summary(day, now)


@router.patch("/days/{day_id}/kind")
def day_kind(day_id: uuid.UUID, body: KindIn, actor: Actor = Depends(get_active_actor),
             db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    day = lock_day(db, day_id)
    policies.require_manage_day(actor, day)
    days.change_kind(db, day, body.kind, actor.user_id, now)
    db.commit()
    return day_summary(day, now)


# ---------------- admin interface ----------------

admin = APIRouter(prefix="/api/admin", tags=["admin"])


@admin.get("/users")
def admin_users(status: str | None = Query(None, pattern="^(pending|active|inactive)$"), q: str = "",
                actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    where = User.full_name.ilike(f"%{q.strip()}%") | User.email.ilike(f"%{q.strip()}%")
    if status:
        where = where & (User.status == status)
    return _user_rows(db, where)


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str = Field(pattern="^(trainee|youth|mentor)$")
    home_weekday: int | None = Field(default=None, ge=0, le=6)


@admin.post("/users/{user_id}/approve")
def approve(user_id: uuid.UUID, body: ApproveIn, actor: Actor = Depends(require_admin), db: Session = Depends(get_db),
            now: datetime = Depends(now_dep)):
    if not policies.can_approve_signups(actor):
        raise forbidden()
    user = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if user is None:
        raise not_found()
    accounts.approve_user(db, user, body.level, body.home_weekday, actor.user_id, now)
    db.commit()
    return _user_rows(db, User.id == user_id)[0]


class AdminUserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    full_name: str | None = Field(default=None, min_length=2, max_length=120)
    phone: str | None = Field(default=None, max_length=32)
    status: str | None = Field(default=None, pattern="^(active|inactive)$")
    level: str | None = Field(default=None, pattern="^(trainee|youth|mentor)$")
    home_weekday: int | None = Field(default=None, ge=-1, le=6)  # -1 = remove home day
    is_admin: bool | None = None
    is_youth_manager: bool | None = None


@admin.patch("/users/{user_id}")
def admin_update_user(user_id: uuid.UUID, body: AdminUserIn, actor: Actor = Depends(require_admin),
                      db: Session = Depends(get_db), now: datetime = Depends(now_dep)):
    user = accounts.get_user(db, user_id)
    data = body.model_dump(exclude_unset=True)
    for f in ("full_name", "phone"):
        if f in data:
            setattr(user, f, data[f])
    if "status" in data and data["status"] != user.status:
        if user.status == "pending":
            raise forbidden("use_approve", "יש לאשר הרשמה דרך מסך האישור")
        record(db, actor_id=actor.user_id, action="status_change", object_type="user", object_id=user.id, now=now,
               meta={"from": user.status, "to": data["status"]})
        user.status = data["status"]
    if data.get("level"):
        accounts.set_level(db, user.id, data["level"], actor.user_id, now)
    if "home_weekday" in data:
        hw = data["home_weekday"]
        accounts.set_home_weekday(db, user.id, None if hw in (None, -1) else hw, actor.user_id, now)
    if data.get("is_admin") is not None:
        if user.id == actor.user_id and not data["is_admin"]:
            raise forbidden("self_demote", "אי אפשר להסיר הרשאת אדמין מעצמך")
        accounts.set_authority_role(db, user.id, "admin", data["is_admin"], actor.user_id, now)
    if data.get("is_youth_manager") is not None:
        accounts.set_authority_role(db, user.id, "youth_manager", data["is_youth_manager"], actor.user_id, now)
    record(db, actor_id=actor.user_id, action="admin_user_edit", object_type="user", object_id=user.id, now=now,
           meta={"fields": sorted(data)})
    db.commit()
    return _user_rows(db, User.id == user_id)[0]


class GenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: date
    end: date


@admin.post("/days/generate")
def generate(body: GenerateIn, actor: Actor = Depends(require_admin), db: Session = Depends(get_db),
             now: datetime = Depends(now_dep)):
    created = days.generate_days(db, body.start, body.end, now, actor.user_id)
    db.commit()
    return {"created": created}


@admin.get("/days/{day_id}")
def admin_day(day_id: uuid.UUID, actor: Actor = Depends(require_admin), db: Session = Depends(get_db),
              now: datetime = Depends(now_dep)):
    return day_summary(get_day(db, day_id), now)


@admin.get("/outbox")
def outbox(limit: int = Query(100, le=500), actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(OutboxMessage, User.full_name).join(User, User.id == OutboxMessage.recipient_user_id)
                      .order_by(OutboxMessage.created_at.desc()).limit(limit)).all()
    return [{"id": str(m.id), "event_type": m.event_type, "recipient": name, "status": m.status,
             "attempts": m.attempts, "channel": m.channel, "created_at": m.created_at, "last_error": m.last_error}
            for m, name in rows]


@admin.get("/audit")
def audit(limit: int = Query(200, le=1000), actor: Actor = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(AuditEvent, User.full_name).outerjoin(User, User.id == AuditEvent.actor_user_id)
                      .order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [{"id": str(e.id), "actor": name, "action": e.action, "object_type": e.object_type,
             "object_id": e.object_id, "visibility": e.visibility, "meta": e.meta, "created_at": e.created_at}
            for e, name in rows]
