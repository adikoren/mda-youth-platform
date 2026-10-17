"""Persist notification intent inside the caller's transaction. Delivery happens later in the
worker, after commit. dedup_key makes retries of the same logical event harmless; keys for
schedule events include the version id so a later publication legitimately notifies again."""
import uuid
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.accounts.models import AuthorityRole, ManagerDayScope, User
from app.notifications.models import OutboxMessage

# Hebrew text for in-app display. Payloads carry only references.
EVENT_TEXT_HE = {
    "registration_opened": "ההרשמה נפתחה",
    "late_registration": "שינוי הרשמה אחרי מועד הסגירה",
    "registration_request": "בקשה מחניך/מתנדב לגבי הרשמה ליום",
    "shift_type_added": "נוסף סוג משמרת ביום שלך",
    "volunteer_cancelled": "מתנדב ביטל את השיבוץ שלו למשמרת (לידיעתך בלבד)",
    "availability_withdrawn": "ביטל זמינות — נדרש טיפול",
    "schedule_published": "הסידור פורסם",
    "assigned": "שובצת למשמרת",
    "unassigned": "השיבוץ שלך בוטל",
    "schedule_changed": "שינוי בסידור המשמרת",
    "new_ambulance": "נוסף אמבולנס למשמרת שאת/ה ממתין/ה לה",
    "schedule_changed_waiting": "שינוי בסידור משמרת שאת/ה ממתין/ה לה",
    "invitation": "הוזמנת להירשם ליום נוסף",
    "direct_add_confirm": "נוספת למשמרת — נא לאשר",
    "publish_target_reminder": "הגיע מועד היעד לפרסום הסידור",
    "draft_idle_reminder": "יש שינויים בסידור שלא פורסמו",
    "journal_reminder_trainee": "חסר תיאור מקרה ביומן הנסיעות שלך",
    "journal_reminder_mentor": "חסר תיעוד חניכה למשמרת",
}


def enqueue(
    db: Session,
    *,
    event_type: str,
    recipients: Iterable[uuid.UUID],
    dedup_prefix: str,
    payload: dict,
    now: datetime,
) -> int:
    """Insert one row per recipient; existing dedup keys are skipped (ON CONFLICT DO NOTHING).
    Returns the number of new rows."""
    rows = [
        {
            "id": uuid.uuid4(),
            "event_type": event_type,
            "recipient_user_id": r,
            "dedup_key": f"{dedup_prefix}:{r}",
            "payload": payload,
            "status": "queued",
            "attempts": 0,
            "next_attempt_at": now,
            "created_at": now,
        }
        for r in dict.fromkeys(recipients)
    ]
    if not rows:
        return 0
    stmt = (insert(OutboxMessage).values(rows).on_conflict_do_nothing(index_elements=["dedup_key"])
            .returning(OutboxMessage.id))
    return len(db.execute(stmt).all())


def day_manager_ids(db: Session, weekday: int, now: datetime | None = None) -> list[uuid.UUID]:
    """Managers to notify for a weekday: the permanent Day Manager plus any temporary manager whose appointment is
    valid at that moment (deduplicated). Falls back to admins when nobody manages that day."""
    from app.access.clock import get_clock
    from app.accounts.models import TemporaryDayManagerAppointment
    moment = now or get_clock().now()
    permanent = list(
        db.scalars(
            select(ManagerDayScope.user_id)
            .join(User, User.id == ManagerDayScope.user_id)
            .where(ManagerDayScope.weekday == weekday, ManagerDayScope.valid_to.is_(None), User.status == "active")
        )
    )
    temporary = list(
        db.scalars(
            select(TemporaryDayManagerAppointment.user_id)
            .join(User, User.id == TemporaryDayManagerAppointment.user_id)
            .where(TemporaryDayManagerAppointment.weekday == weekday,
                   TemporaryDayManagerAppointment.revoked_at.is_(None),
                   TemporaryDayManagerAppointment.starts_at <= moment,
                   TemporaryDayManagerAppointment.ends_at > moment,
                   User.status == "active")
        )
    )
    ids = list(dict.fromkeys(permanent + temporary))  # deduplicated, order kept
    if ids:
        return ids
    return list(
        db.scalars(
            select(AuthorityRole.user_id)
            .join(User, User.id == AuthorityRole.user_id)
            .where(AuthorityRole.role == "admin", AuthorityRole.valid_to.is_(None), User.status == "active")
        )
    )
