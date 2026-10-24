"""Synthetic local dataset for manual testing of the MDA system.

Development database only: refuses any host other than localhost/127.0.0.1.
Every generated row id is recorded in a manifest file (default ~/mda-local-data/demo-dataset-manifest.json).
Cleanup deletes only ids listed in that manifest, in dependency order. Existing rows are never touched.
All identities use the reserved .test domain; no notification is queued and no Web Push is sent.
"""
import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.accounts import service as accounts
from app.accounts.models import AuthorityRole, AuthSession, DayMembership, User, VolunteerLevel
from app.accounts.security import hash_password
from app.audit.models import AuditEvent
from app.core.config import get_settings
from app.core.timeutil import local_today, local_weekday
from app.days import service as days
from app.days.models import ActivityDay, Shift
from app.mentoring.models import MentoringSummary, Ride
from app.notifications.models import OutboxMessage, PushSubscription
from app.registration.models import Registration, RegistrationChoice
from app.scheduling.models import ScheduleVersion

DEMO_DOMAIN = "example.test"
VOLUNTEER_COUNT = 240
PAST_DAYS = 21
FUTURE_DAYS = 28
DEFAULT_MANIFEST = Path(os.environ.get("MDA_DEMO_MANIFEST")
                        or Path.home() / "mda-local-data" / "demo-dataset-manifest.json")
KEYS = ("users", "days", "registrations", "choices", "rides", "summaries")

FIRST = ["נועה", "איתי", "מאיה", "יואב", "תמר", "עומר", "שירה", "אורי", "ליה", "דניאל", "הילה", "רועי",
         "אביגיל", "גיא", "יעל", "נדב", "רוני", "אלון", "מיכל", "עידו", "שחר", "טל", "אדם", "ענבר"]
LAST = ["כהן", "לוי", "מזרחי", "פרץ", "ביטון", "אברהם", "פרידמן", "שפירא", "דהן", "אזולאי", "חדד", "גבאי"]
RIDE_CASES = [
    "כאב בית חזה, מטופל בהכרה מלאה",
    "נפילה מגובה, פציעה בכף היד",
    "קוצר נשימה אצל מבוגר עם מחלת ריאות",
    "תאונת דרכים קלה, ללא איבוד הכרה",
    "חום גבוה אצל ילד, ללא סימני מצוקה",
    "סחרחורת והתעלפות בחדר הכושר",
    "פצע חתך בעקבות כלי חד",
    "הרעלה קלה, מטופל משותף עם צוות אחר",
]
SUMMARY_TEXT = [
    "חניכה מסודרת, תיעוד מדויק ושיתוף פעולה טוב עם הצוות.",
    "ניהול סצנה בטוח, יש להמשיך לתרגל שאלון מטופל.",
    "תקשורת ברורה מול המטופל, צריך חיזוק בהחלטות בלחץ.",
]


def _pick(*parts) -> int:
    """Deterministic pseudo-random number, so reruns produce the same layout."""
    return int(hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest(), 16)


def guard_local_database() -> None:
    host = urlparse(get_settings().database_url.replace("+psycopg", "")).hostname
    if host not in ("localhost", "127.0.0.1", "::1"):
        raise SystemExit(f"refusing demo dataset: database host {host!r} is not local")


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict:
    if path.exists():
        data = json.loads(path.read_text())
        return {k: list(data.get(k, [])) for k in KEYS}
    return {k: [] for k in KEYS}


def save_manifest(manifest: dict, path: Path = DEFAULT_MANIFEST) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    tmp.replace(path)  # atomic: a crash never leaves a half-written manifest


def demo_name(n: int) -> str:
    """Unique per demo volunteer for n = 1..240 (24 first names x 12 last names), so a list never shows two
    different people with the same name."""
    return f"{FIRST[(n - 1) % len(FIRST)]} {LAST[((n - 1) // len(FIRST)) % len(LAST)]}"


def rename_demo_volunteers(db: Session) -> int:
    """One-off refresh of names for existing demo volunteers (display only; no rows are deleted)."""
    guard_local_database()
    changed = 0
    for n in range(1, VOLUNTEER_COUNT + 1):
        u = db.scalar(select(User).where(User.email == f"demo.v{n:03d}@{DEMO_DOMAIN}"))
        if u is not None and u.full_name != demo_name(n):
            u.full_name = demo_name(n)
            changed += 1
    db.commit()
    return changed


def _volunteer_profile(n: int) -> tuple[str, int | None]:
    """Level by position: 60% youth, 25% trainee, 15% mentor. Home weekday Sun-Fri, round robin."""
    bucket = n % 20
    level = "youth" if bucket < 12 else "trainee" if bucket < 17 else "mentor"
    return level, n % 6


def seed(db: Session, now: datetime, password: str | None = None, path: Path = DEFAULT_MANIFEST) -> dict:
    guard_local_database()
    manifest = load_manifest(path)
    counts = {k: 0 for k in KEYS}
    pw_hash = hash_password(password or secrets.token_urlsafe(9))

    days.get_station(db)
    days.ensure_templates(db, now)
    today = local_today(now)

    # 1. Volunteers: create only the missing emails. Last six are pending (no registrations).
    user_ids: list[uuid.UUID] = []
    for n in range(1, VOLUNTEER_COUNT + 1):
        email = f"demo.v{n:03d}@{DEMO_DOMAIN}"
        existing = db.scalar(select(User).where(User.email == email))
        if existing is not None:
            user_ids.append(existing.id)
            continue
        level, home = _volunteer_profile(n)
        pending = n > VOLUNTEER_COUNT - 6
        name = demo_name(n)
        u = User(email=email, full_name=name, phone=f"050-900{n:04d}", password_hash=pw_hash,
                 status="pending" if pending else "active", requested_weekday=home if pending else None,
                 created_at=now, approved_at=None if pending else now)
        db.add(u)
        db.flush()
        if not pending:
            accounts.set_level(db, u.id, level, None, now)
            accounts.set_home_weekday(db, u.id, home, None, now)
        manifest["users"].append(str(u.id))
        user_ids.append(u.id)
        counts["users"] += 1
    db.flush()

    # 2. Days: only dates that do not exist yet. Full day shape (every shift type) for demo visibility.
    new_days: list[ActivityDay] = []
    for offset in range(-PAST_DAYS, FUTURE_DAYS + 1):
        d = today + timedelta(days=offset)
        if db.scalar(select(ActivityDay.id).where(ActivityDay.local_date == d)) is not None:
            continue
        day = days.create_day(db, d, now, shift_kinds=None)
        new_days.append(day)
        manifest["days"].append(str(day.id))
        counts["days"] += 1
    db.flush()

    # 3. Registrations for each new day, from active volunteers whose home weekday matches or who are picked.
    active = [u for u in db.scalars(select(User).where(User.id.in_(user_ids), User.status == "active"))]
    for day in new_days:
        weekday = local_weekday(day.local_date)
        shifts = list(db.scalars(select(Shift).where(Shift.activity_day_id == day.id)))
        for u in sorted(active, key=lambda x: x.email):
            n = int(u.email.split("@")[0][-3:])
            at_home = accounts.current_home_weekday(db, u.id) == weekday
            if not at_home and _pick(n, day.local_date) % 3:
                continue
            reg = Registration(user_id=u.id, activity_day_id=day.id, source="self", revision=1,
                               created_at=now, updated_at=now, updated_by_id=u.id)
            db.add(reg)
            db.flush()
            manifest["registrations"].append(str(reg.id))
            counts["registrations"] += 1
            for shift in shifts:
                if not shift.self_selectable:
                    continue
                choice = RegistrationChoice(registration_id=reg.id, shift_id=shift.id,
                                            available=_pick(n, day.local_date, shift.kind) % 5 != 0,
                                            is_late=False, first_answered_at=now, updated_at=now)
                db.add(choice)
                db.flush()
                manifest["choices"].append(str(choice.id))
                counts["choices"] += 1

    # 4. Rides on new past days: trainees record one ride on their home day, some with a mentor summary.
    trainees = {u.id: u for u in active if accounts.current_level(db, u.id) == "trainee"}
    seq_by_trainee: dict[uuid.UUID, int] = {}
    for day in new_days:
        if day.local_date >= today:
            continue
        weekday = local_weekday(day.local_date)
        shift = db.scalar(select(Shift).where(Shift.activity_day_id == day.id).order_by(Shift.starts_at).limit(1))
        managers = accounts.day_managers_for(db, weekday)
        for tid in trainees:
            if accounts.current_home_weekday(db, tid) != weekday:
                continue
            n = int(trainees[tid].email.split("@")[0][-3:])
            if _pick(n, day.local_date, "ride") % 2:
                continue
            seq = seq_by_trainee.get(tid) or (db.scalar(select(func.max(Ride.seq)).where(Ride.trainee_id == tid)) or 0)
            seq += 1
            seq_by_trainee[tid] = seq
            ride = Ride(id=uuid.uuid4(), trainee_id=tid, shift_id=shift.id, occurred_on=day.local_date, seq=seq,
                        description=RIDE_CASES[_pick(n, day.local_date) % len(RIDE_CASES)],
                        created_by_id=tid, created_at=now)
            if _pick(n, day.local_date, "cancel") % 17 == 0:  # a few audited cancellations
                ride.cancelled_at, ride.cancelled_by_id, ride.cancel_reason = now, tid, "הוזן פעמיים בטעות"
            db.add(ride)
            db.flush()
            manifest["rides"].append(str(ride.id))
            counts["rides"] += 1
            if managers and _pick(n, day.local_date, "sum") % 3 == 0 and ride.cancelled_at is None:
                summary = MentoringSummary(
                    trainee_id=tid, shift_id=shift.id,
                    strengths=SUMMARY_TEXT[_pick(n, day.local_date) % len(SUMMARY_TEXT)],
                    strengths_none=False, improvements=None, improvements_none=True,
                    updated_at=now, updated_by_id=managers[0])
                db.add(summary)
                db.flush()
                manifest["summaries"].append(f"{tid}|{shift.id}")
                counts["summaries"] += 1

    db.commit()
    save_manifest(manifest, path)
    return counts


def clean(db: Session, path: Path = DEFAULT_MANIFEST) -> dict:
    """Delete only rows listed in the manifest, dependents first. Returns the number of rows removed per table."""
    guard_local_database()
    manifest = load_manifest(path)
    removed: dict[str, int] = {}
    user_ids = [uuid.UUID(x) for x in manifest["users"]]
    day_ids = [uuid.UUID(x) for x in manifest["days"]]
    ride_ids = [uuid.UUID(x) for x in manifest["rides"]]
    choice_ids = [uuid.UUID(x) for x in manifest["choices"]]
    reg_ids = [uuid.UUID(x) for x in manifest["registrations"]]

    def run(label: str, stmt) -> None:
        removed[label] = removed.get(label, 0) + (db.execute(stmt).rowcount or 0)

    for pair in manifest["summaries"]:
        tid, sid = pair.split("|")
        run("mentoring_summaries", delete(MentoringSummary).where(
            MentoringSummary.trainee_id == uuid.UUID(tid), MentoringSummary.shift_id == uuid.UUID(sid)))
    run("rides", delete(Ride).where(Ride.id.in_(ride_ids)))
    run("registration_choices", delete(RegistrationChoice).where(RegistrationChoice.id.in_(choice_ids)))
    run("registrations", delete(Registration).where(Registration.id.in_(reg_ids)))
    if day_ids:
        shift_ids = select(Shift.id).where(Shift.activity_day_id.in_(day_ids))
        run("audit_events_days", delete(AuditEvent).where(AuditEvent.object_id.in_([str(x) for x in day_ids])))
        run("schedule_versions", delete(ScheduleVersion).where(ScheduleVersion.activity_day_id.in_(day_ids)))
        run("shifts", delete(Shift).where(Shift.id.in_(shift_ids)))
        run("activity_days", delete(ActivityDay).where(ActivityDay.id.in_(day_ids)))
    if user_ids:
        ids = [str(x) for x in user_ids]
        run("outbox", delete(OutboxMessage).where(OutboxMessage.recipient_user_id.in_(user_ids)))
        run("push_subscriptions", delete(PushSubscription).where(PushSubscription.user_id.in_(user_ids)))
        run("sessions", delete(AuthSession).where(AuthSession.user_id.in_(user_ids)))
        run("volunteer_level_history", delete(VolunteerLevel).where(VolunteerLevel.user_id.in_(user_ids)))
        run("authority_roles", delete(AuthorityRole).where(AuthorityRole.user_id.in_(user_ids)))
        run("day_memberships", delete(DayMembership).where(DayMembership.user_id.in_(user_ids)))
        run("audit_events_users", delete(AuditEvent).where(
            AuditEvent.actor_user_id.in_(user_ids) | AuditEvent.object_id.in_(ids)))
        run("users", delete(User).where(User.id.in_(user_ids)))
    db.commit()
    if path.exists():
        path.unlink()
    return removed


def publication_audit(db: Session, now: datetime) -> list[dict]:
    """Published dates whose registration window is still open (the case a publication must never create:
    the schedule is out while volunteers can still register). Empty list when consistent. The seed never
    creates schedule versions; this check makes any inconsistent example visible on every rerun."""
    from app.registration.service import window_state
    rows = db.scalars(select(ActivityDay).where(ActivityDay.latest_published_version_id.is_not(None))).all()
    return [{"date": d.local_date.isoformat(), "activity_day_id": str(d.id),
             "closes_at": d.registration_closes_at.isoformat()}
            for d in rows if window_state(d, now) == "open"]


def report(db: Session) -> dict:
    """Counts for the demo-tagged rows only (manifest ids), so the report is correct on a shared database."""
    manifest = load_manifest()
    return {
        "volunteers": len(manifest["users"]),
        "days": len(manifest["days"]),
        "registrations": len(manifest["registrations"]),
        "choices": len(manifest["choices"]),
        "rides": len(manifest["rides"]),
        "rides_cancelled": db.scalar(select(func.count()).select_from(Ride).where(
            Ride.id.in_([uuid.UUID(x) for x in manifest["rides"]]), Ride.cancelled_at.is_not(None))) or 0,
        "summaries": len(manifest["summaries"]),
    }
