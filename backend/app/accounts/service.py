"""Account, level, home-day and authority mutations. Every change closes the previous current
row (valid_to) and inserts a new one, so transition dates and history are preserved."""
import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.accounts.models import AuthorityRole, DayMembership, ManagerDayScope, User, VolunteerLevel
from app.audit.service import record
from app.core.errors import bad_request, conflict, not_found
from app.core.types import AUTHORITY_ROLES, LEVELS


def get_user(db: Session, user_id: uuid.UUID) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise not_found("user_not_found", "המשתמש לא נמצא")
    return user


def current_level(db: Session, user_id: uuid.UUID) -> str | None:
    return db.scalar(select(VolunteerLevel.level).where(VolunteerLevel.user_id == user_id,
                                                        VolunteerLevel.valid_to.is_(None)))


def current_home_weekday(db: Session, user_id: uuid.UUID) -> int | None:
    return db.scalar(select(DayMembership.weekday).where(DayMembership.user_id == user_id,
                                                         DayMembership.valid_to.is_(None)))


def set_level(db: Session, user_id: uuid.UUID, level: str, by: uuid.UUID | None, now: datetime) -> bool:
    if level not in LEVELS:
        raise bad_request("bad_level", "רמת מתנדב לא חוקית")
    previous = current_level(db, user_id)
    if previous == level:
        return False
    if previous in ("mentor",) and level != "mentor" and _is_day_manager(db, user_id):
        raise conflict("day_manager_must_be_mentor", "אחראי יום חייב להיות חונך. יש להסיר קודם את מינוי האחראי")
    db.execute(update(VolunteerLevel).where(VolunteerLevel.user_id == user_id, VolunteerLevel.valid_to.is_(None))
               .values(valid_to=now))
    db.add(VolunteerLevel(user_id=user_id, level=level, valid_from=now, changed_by_id=by))
    record(db, actor_id=by, action="level_change", object_type="user", object_id=user_id, now=now,
           meta={"from": previous, "to": level})
    return True


def set_home_weekday(db: Session, user_id: uuid.UUID, weekday: int | None, by: uuid.UUID | None,
                     now: datetime) -> bool:
    if weekday is not None and not 0 <= weekday <= 6:
        raise bad_request("bad_weekday", "יום לא חוקי")
    previous = current_home_weekday(db, user_id)
    if previous == weekday:
        return False
    db.execute(update(DayMembership).where(DayMembership.user_id == user_id, DayMembership.valid_to.is_(None))
               .values(valid_to=now))
    if weekday is not None:
        db.add(DayMembership(user_id=user_id, weekday=weekday, valid_from=now, changed_by_id=by))
    record(db, actor_id=by, action="home_day_change", object_type="user", object_id=user_id, now=now,
           meta={"from": previous, "to": weekday})
    return True


def active_admin_ids(db: Session) -> list[uuid.UUID]:
    return list(db.scalars(
        select(AuthorityRole.user_id).join(User, User.id == AuthorityRole.user_id)
        .where(AuthorityRole.role == "admin", AuthorityRole.valid_to.is_(None), User.status == "active")))


def ensure_admin_remains(db: Session, user_id: uuid.UUID) -> None:
    """Data integrity, not authorization: the system must always keep at least one active admin.
    Call before removing an admin role or deactivating an account. Any admin may remove another
    admin (or themselves) while another active admin exists."""
    admins = active_admin_ids(db)
    if user_id in admins and len(admins) == 1:
        raise conflict("last_admin", "לא ניתן להסיר או להשבית את האדמין האחרון במערכת")


def set_authority_role(db: Session, user_id: uuid.UUID, role: str, granted: bool, by: uuid.UUID | None,
                       now: datetime) -> bool:
    if role not in AUTHORITY_ROLES:
        raise bad_request("bad_role", "תפקיד לא חוקי")
    has = db.scalar(select(AuthorityRole.id).where(AuthorityRole.user_id == user_id, AuthorityRole.role == role,
                                                   AuthorityRole.valid_to.is_(None)))
    if not granted and has is not None and role == "admin":
        ensure_admin_remains(db, user_id)
    if granted and has is None:
        db.add(AuthorityRole(user_id=user_id, role=role, valid_from=now, granted_by_id=by))
    elif not granted and has is not None:
        db.execute(update(AuthorityRole).where(AuthorityRole.id == has).values(valid_to=now, revoked_by_id=by))
    else:
        return False
    record(db, actor_id=by, action="role_grant" if granted else "role_revoke", object_type="user",
           object_id=user_id, now=now, meta={"role": role})
    return True


def _is_day_manager(db: Session, user_id: uuid.UUID) -> bool:
    return db.scalar(select(ManagerDayScope.id).where(ManagerDayScope.user_id == user_id,
                                                      ManagerDayScope.valid_to.is_(None)).limit(1)) is not None


def day_managers_for(db: Session, weekday: int) -> list[uuid.UUID]:
    return list(db.scalars(select(ManagerDayScope.user_id).where(ManagerDayScope.weekday == weekday,
                                                                 ManagerDayScope.valid_to.is_(None))))


def set_day_managers(db: Session, weekday: int, user_ids: list[uuid.UUID], by: uuid.UUID | None,
                     now: datetime) -> None:
    """Appoint/replace/remove the managers of one weekday. Every day manager is a mentor."""
    if not 0 <= weekday <= 6:
        raise bad_request("bad_weekday", "יום לא חוקי")
    wanted = set(user_ids)
    if len(wanted) > 1:
        raise conflict("one_day_manager_per_weekday", "לכל יום יש אחראי יום קבוע אחד בלבד")
    for uid in wanted:
        user = get_user(db, uid)
        if user.status != "active":
            raise conflict("user_not_active", f"{user.full_name}: החשבון אינו פעיל")
        if current_level(db, uid) != "mentor":
            raise conflict("day_manager_must_be_mentor", f"{user.full_name}: אחראי יום חייב להיות חונך")
        other = db.scalar(select(ManagerDayScope.weekday).where(
            ManagerDayScope.user_id == uid, ManagerDayScope.valid_to.is_(None), ManagerDayScope.weekday != weekday))
        if other is not None:
            raise conflict("already_permanent_manager", f"{user.full_name}: כבר אחראי יום קבוע ביום אחר")
    current = set(day_managers_for(db, weekday))
    for uid in current - wanted:
        db.execute(update(ManagerDayScope).where(ManagerDayScope.user_id == uid, ManagerDayScope.weekday == weekday,
                                                 ManagerDayScope.valid_to.is_(None))
                   .values(valid_to=now, revoked_by_id=by))
        record(db, actor_id=by, action="day_manager_remove", object_type="user", object_id=uid, now=now,
               meta={"weekday": weekday})
    for uid in wanted - current:
        db.add(ManagerDayScope(user_id=uid, weekday=weekday, valid_from=now, granted_by_id=by))
        record(db, actor_id=by, action="day_manager_appoint", object_type="user", object_id=uid, now=now,
               meta={"weekday": weekday})


def approve_user(db: Session, user: User, level: str, home_weekday: int | None, by: uuid.UUID,
                 now: datetime) -> None:
    if user.status != "pending":
        raise conflict("not_pending", "החשבון אינו ממתין לאישור")
    set_level(db, user.id, level, by, now)
    set_home_weekday(db, user.id, home_weekday, by, now)
    user.status = "active"
    user.approved_by_id = by
    user.approved_at = now
    record(db, actor_id=by, action="signup_approve", object_type="user", object_id=user.id, now=now,
           meta={"level": level, "home_weekday": home_weekday})
