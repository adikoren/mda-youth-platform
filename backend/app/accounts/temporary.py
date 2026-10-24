"""Temporary Day Manager appointments (decision of 2026-10-17). Admin and Youth Manager only (enforced by the
router). A mentor may cover one weekday for an interval; the permanent appointment and the base role are untouched.

Validity: starts_at <= now < ends_at and not revoked, evaluated on every request (see app.access.context).
Overlaps for the same weekday are refused under a per-weekday transaction lock, so concurrent creations cannot
both succeed. Adjacent intervals are allowed. Times are entered as Asia/Jerusalem wall clock and stored with zone."""
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.accounts.models import TemporaryDayManagerAppointment, User
from app.accounts.service import current_level
from app.audit.service import record
from app.core.config import get_settings
from app.core.errors import bad_request, conflict, not_found


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def parse_local(value: str) -> datetime:
    """'YYYY-MM-DDTHH:MM' in Asia/Jerusalem -> timezone-aware datetime. Offsets in the input are refused."""
    try:
        naive = datetime.fromisoformat(value)
    except ValueError:
        raise bad_request("bad_time", "התאריך או השעה אינם תקינים")
    if naive.tzinfo is not None:
        raise bad_request("bad_time", "יש להזין שעה לפי שעון ישראל, בלי הפרש שעות")
    return naive.replace(tzinfo=tz())


def status_of(appt: TemporaryDayManagerAppointment, now: datetime) -> str:
    if appt.revoked_at is not None:
        return "revoked"
    if appt.ends_at <= now:
        return "expired"
    if appt.starts_at <= now:
        return "active"
    return "scheduled"


def active_weekdays(db: Session, user_id: uuid.UUID, now: datetime) -> dict[int, datetime]:
    """weekday -> end of the temporary appointment that is valid now (used for the actor's permissions)."""
    rows = db.execute(select(TemporaryDayManagerAppointment.weekday, TemporaryDayManagerAppointment.ends_at).where(
        TemporaryDayManagerAppointment.user_id == user_id, TemporaryDayManagerAppointment.revoked_at.is_(None),
        TemporaryDayManagerAppointment.starts_at <= now, TemporaryDayManagerAppointment.ends_at > now)).all()
    out: dict[int, datetime] = {}
    for weekday, ends in rows:
        out[weekday] = max(ends, out.get(weekday, ends))
    return out


def _check_eligible(db: Session, user_id: uuid.UUID) -> None:
    """Same eligibility as a permanent Day Manager: an active mentor. Nothing is promoted silently."""
    user = db.get(User, user_id)
    if user is None:
        raise not_found("user_not_found", "המתנדב לא נמצא")
    if user.status != "active":
        raise conflict("user_not_active", f"{user.full_name}: החשבון אינו פעיל")
    if current_level(db, user_id) != "mentor":
        raise conflict("day_manager_must_be_mentor", f"{user.full_name}: אחראי יום חייב להיות חונך")


def _lock_weekday(db: Session, weekday: int) -> None:
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"temp_day_manager:{weekday}"})


def _check_interval(weekday: int, starts: datetime, ends: datetime, now: datetime) -> None:
    if not 0 <= weekday <= 6:
        raise bad_request("bad_weekday", "יום לא חוקי")
    if ends <= starts:
        raise bad_request("bad_interval", "שעת הסיום חייבת להיות אחרי שעת ההתחלה")
    if ends <= now:
        raise bad_request("ends_in_past", "לא ניתן לתאם מינוי שכבר הסתיים")


def _check_no_overlap(db: Session, weekday: int, starts: datetime, ends: datetime, exclude: uuid.UUID | None) -> None:
    q = select(TemporaryDayManagerAppointment.id).where(
        TemporaryDayManagerAppointment.weekday == weekday,
        TemporaryDayManagerAppointment.revoked_at.is_(None),
        TemporaryDayManagerAppointment.starts_at < ends,
        starts < TemporaryDayManagerAppointment.ends_at,
    )
    if exclude is not None:
        q = q.where(TemporaryDayManagerAppointment.id != exclude)
    if db.scalar(q.limit(1)) is not None:
        raise conflict("temporary_overlap", "כבר יש מינוי זמני חופף ליום הזה. אפשר לתאם רק אחרי סיומו")


def create(db: Session, actor_id: uuid.UUID, user_id: uuid.UUID, weekday: int, starts: datetime, ends: datetime,
           now: datetime) -> TemporaryDayManagerAppointment:
    _check_interval(weekday, starts, ends, now)
    _check_eligible(db, user_id)
    _lock_weekday(db, weekday)
    _check_no_overlap(db, weekday, starts, ends, None)
    appt = TemporaryDayManagerAppointment(user_id=user_id, weekday=weekday, starts_at=starts, ends_at=ends,
                                          created_at=now, created_by_id=actor_id)
    db.add(appt)
    db.flush()
    record(db, actor_id=actor_id, action="temp_manager_create", object_type="user", object_id=user_id, now=now,
           meta={"appointment": str(appt.id), "weekday": weekday, "starts": starts.isoformat(), "ends": ends.isoformat()})
    db.commit()
    return appt


def _get(db: Session, appt_id: uuid.UUID) -> TemporaryDayManagerAppointment:
    appt = db.get(TemporaryDayManagerAppointment, appt_id)
    if appt is None:
        raise not_found("appointment_not_found", "המינוי לא נמצא")
    return appt


def edit(db: Session, actor_id: uuid.UUID, appt_id: uuid.UUID, weekday: int, starts: datetime, ends: datetime,
         now: datetime) -> TemporaryDayManagerAppointment:
    appt = _get(db, appt_id)
    if appt.revoked_at is not None:
        raise conflict("appointment_revoked", "המינוי בוטל ואינו ניתן לעריכה")
    if appt.ends_at <= now:
        raise conflict("appointment_ended", "המינוי הסתיים ונשמר כהיסטוריה")
    _check_interval(weekday, starts, ends, now)
    _check_eligible(db, appt.user_id)
    old = {"weekday": appt.weekday, "starts": appt.starts_at.isoformat(), "ends": appt.ends_at.isoformat()}
    _lock_weekday(db, weekday)
    _check_no_overlap(db, weekday, starts, ends, appt.id)
    appt.weekday, appt.starts_at, appt.ends_at = weekday, starts, ends
    appt.updated_at, appt.updated_by_id = now, actor_id
    record(db, actor_id=actor_id, action="temp_manager_edit", object_type="user", object_id=appt.user_id, now=now,
           meta={"appointment": str(appt.id), "before": old, "after": {"weekday": weekday, "starts": starts.isoformat(),
                                                                         "ends": ends.isoformat()}})
    db.commit()
    return appt


def revoke(db: Session, actor_id: uuid.UUID, appt_id: uuid.UUID, now: datetime) -> TemporaryDayManagerAppointment:
    appt = _get(db, appt_id)
    if appt.revoked_at is not None:
        raise conflict("already_revoked", "המינוי כבר בוטל")
    if appt.ends_at <= now:
        raise conflict("appointment_ended", "המינוי הסתיים ונשמר כהיסטוריה")
    appt.revoked_at, appt.revoked_by_id = now, actor_id
    record(db, actor_id=actor_id, action="temp_manager_revoke", object_type="user", object_id=appt.user_id, now=now,
           meta={"appointment": str(appt.id), "weekday": appt.weekday})
    db.commit()
    return appt
