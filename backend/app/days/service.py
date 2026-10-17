"""All-day calendar: weekday registration templates, shift templates, activity-day creation."""
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app.audit.service import record
from app.core.errors import bad_request, conflict
from app.core.timeutil import FRIDAY, SATURDAY, WEDNESDAY, local_dt, local_weekday
from app.core.types import DAY_KINDS, SPECIAL_DAY_KINDS
from app.days.models import ActivityDay, RegistrationTemplate, Shift, Station
from app.registration.models import Invitation, ManagerAddedParticipant, QueueOrder, Registration
from app.scheduling.models import ScheduleVersion

STATION_NAME = "מד״א ראשון לציון"


@dataclass(frozen=True)
class ShiftTemplate:
    kind: str
    start: time
    end: time
    arrival: time
    self_selectable: bool
    allows_late_arrival: bool
    baseline_slots: int


# Approved shift templates. Baseline slot counts are editable starting points, not limits.
ORDINARY_SHIFTS = (
    # Ordinary morning: populated explicitly by the manager (no self-registration).
    ShiftTemplate("morning", time(7), time(15), time(6, 30), False, False, 3),
    # Reinforcement: manager-assigned from eligible trainee evening availability.
    ShiftTemplate("reinforcement", time(12), time(20), time(14, 30), False, False, 1),
    ShiftTemplate("evening", time(15), time(23), time(14, 30), True, True, 2),
)
SPECIAL_SHIFTS = (
    ShiftTemplate("morning", time(7), time(15), time(6, 30), True, False, 2),
    ShiftTemplate("evening", time(15), time(23), time(14, 30), True, False, 2),
)


def shift_templates_for(kind: str) -> tuple[ShiftTemplate, ...]:
    return SPECIAL_SHIFTS if kind in SPECIAL_DAY_KINDS else ORDINARY_SHIFTS


def template_for(day_kind: str, shift_kind: str) -> ShiftTemplate:
    return next(t for t in shift_templates_for(day_kind) if t.kind == shift_kind)


def allowed_shift_kinds(day_kind: str) -> tuple[str, ...]:
    """Shift types a date may contain. Reinforcement is ordinary days only (spec §3: no reinforcement on
    Friday, Saturday, holiday or holiday eve)."""
    return tuple(t.kind for t in shift_templates_for(day_kind))


DEFAULT_SHIFT_KINDS = ("evening",)  # evening is mandatory and selected by default; morning/reinforcement are opt-in


def default_kind(d: date) -> str:
    wd = local_weekday(d)
    return "friday" if wd == FRIDAY else "saturday" if wd == SATURDAY else "regular"


# Approved defaults: open 5 days before 20:00 (Wednesday: preceding Friday 14:00),
# close 2 days before 18:00, publication target 1 day before 20:00.
def default_template_rows() -> list[dict]:
    rows = []
    for wd in range(7):
        rows.append(dict(
            weekday=wd,
            open_days_before=5, open_time=time(14) if wd == WEDNESDAY else time(20),
            close_days_before=2, close_time=time(18),
            target_days_before=1, target_time=time(20),
        ))
    return rows


def get_station(db: Session) -> Station:
    station = db.scalar(select(Station).limit(1))
    if station is None:
        station = Station(name=STATION_NAME)
        db.add(station)
        db.flush()
    return station


def ensure_templates(db: Session, now: datetime) -> int:
    created = 0
    for row in default_template_rows():
        has = db.scalar(select(RegistrationTemplate.id).where(RegistrationTemplate.weekday == row["weekday"],
                                                              RegistrationTemplate.valid_to.is_(None)))
        if has is None:
            db.add(RegistrationTemplate(**row, valid_from=now))
            created += 1
    return created


def current_template(db: Session, weekday: int) -> RegistrationTemplate:
    tpl = db.scalar(select(RegistrationTemplate).where(RegistrationTemplate.weekday == weekday,
                                                       RegistrationTemplate.valid_to.is_(None)))
    if tpl is None:
        raise conflict("no_template", "חסרה תבנית הרשמה ליום זה")
    return tpl


def compute_timing(tpl: RegistrationTemplate, d: date) -> tuple[datetime, datetime, datetime]:
    """Local wall-clock times on the calendar days before the activity date (crosses weeks)."""
    opens = local_dt(d - timedelta(days=tpl.open_days_before), tpl.open_time)
    closes = local_dt(d - timedelta(days=tpl.close_days_before), tpl.close_time)
    target = local_dt(d - timedelta(days=tpl.target_days_before), tpl.target_time)
    return opens, closes, target


def update_template(db: Session, weekday: int, values: dict, by: uuid.UUID, now: datetime) -> RegistrationTemplate:
    old = current_template(db, weekday)
    fields = ("open_days_before", "open_time", "close_days_before", "close_time", "target_days_before", "target_time")
    data = {f: values.get(f, getattr(old, f)) for f in fields}
    if not data["open_days_before"] > data["close_days_before"] >= data["target_days_before"] >= 0:
        raise bad_request("bad_offsets", "סדר המועדים חייב להיות: פתיחה, סגירה, יעד פרסום")
    old.valid_to = now
    db.flush()
    new = RegistrationTemplate(weekday=weekday, **data, valid_from=now, created_by_id=by)
    db.add(new)
    record(db, actor_id=by, action="template_update", object_type="registration_template", object_id=weekday,
           now=now, meta={k: str(v) for k, v in data.items()})
    return new


def opens_by_default(kind: str, d: date) -> bool:
    """Morning registration is visible to volunteers by default only on Saturday. On Sunday–Friday a
    morning shift is hidden from registration until a manager explicitly opens it (decision
    2026-10-12); creating the shift never opens it."""
    return kind != "morning" or local_weekday(d) == SATURDAY


def build_shift(day: ActivityDay, t: ShiftTemplate, open_registration: bool = False) -> Shift:
    """One shift from its approved template (times and ambulance baseline are the spec defaults).
    Opening a shift and opening volunteer registration are separate: a shift is created closed for
    reinforcement unless the manager explicitly opens it during setup (open_registration=True)."""
    if t.kind == "reinforcement":
        selectable = open_registration
    else:
        selectable = t.self_selectable and opens_by_default(t.kind, day.local_date)
    return Shift(
        activity_day_id=day.id,
        kind=t.kind,
        arrival_at=local_dt(day.local_date, t.arrival),
        starts_at=local_dt(day.local_date, t.start),
        ends_at=local_dt(day.local_date, t.end),
        self_selectable=selectable,
        allows_late_arrival=t.allows_late_arrival,
        baseline_slots=t.baseline_slots,
    )


def validate_shift_kinds(day_kind: str, kinds: tuple[str, ...]) -> tuple[str, ...]:
    """Evening is mandatory; morning and reinforcement are optional; only the kinds allowed for the day kind."""
    if len(set(kinds)) != len(kinds):
        raise bad_request("duplicate_shift", "סוג משמרת הופיע יותר מפעם אחת")
    if "evening" not in kinds:
        raise bad_request("evening_required", "משמרת ערב היא חובה בכל תאריך")
    allowed = allowed_shift_kinds(day_kind)
    bad = [k for k in kinds if k not in allowed]
    if bad:
        raise bad_request("shift_not_allowed", "סוג המשמרת אינו מותר ליום זה")
    return tuple(k for k in allowed if k in kinds)  # stable order


def _build_shifts(day: ActivityDay, kinds: tuple[str, ...], open_kinds: tuple[str, ...] = ()) -> list[Shift]:
    return [build_shift(day, t, t.kind in open_kinds) for t in shift_templates_for(day.kind) if t.kind in kinds]


def create_day(db: Session, d: date, now: datetime, by: uuid.UUID | None = None, kind: str | None = None,
               shift_kinds: tuple[str, ...] | None = DEFAULT_SHIFT_KINDS,
               open_registration: tuple[str, ...] = ()) -> ActivityDay:
    """shift_kinds=None means every shift type allowed for the day kind (used by seeding and tests that need
    the full day). Otherwise only the given types are created; a date always contains evening."""
    station = get_station(db)
    existing = db.scalar(select(ActivityDay).where(ActivityDay.station_id == station.id, ActivityDay.local_date == d))
    if existing:
        return existing
    kind = kind or default_kind(d)
    if kind not in DAY_KINDS:
        raise bad_request("bad_kind", "סוג יום לא חוקי")
    kinds = validate_shift_kinds(kind, allowed_shift_kinds(kind) if shift_kinds is None else tuple(shift_kinds))
    opens, closes, target = compute_timing(current_template(db, local_weekday(d)), d)
    day = ActivityDay(station_id=station.id, local_date=d, kind=kind, registration_opens_at=opens,
                      registration_closes_at=closes, publish_target_at=target, created_at=now)
    db.add(day)
    db.flush()
    if not set(open_registration) <= set(kinds):
        raise bad_request("open_not_selected", "אפשר לפתוח הרשמה רק למשמרת שנבחרה ליום")
    db.add_all(_build_shifts(day, kinds, tuple(open_registration)))
    record(db, actor_id=by, action="day_create", object_type="activity_day", object_id=day.id, now=now,
           meta={"date": d.isoformat(), "kind": kind, "shifts": list(kinds)})
    return day


def generate_days(db: Session, start: date, end: date, now: datetime, by: uuid.UUID | None = None,
                  shift_kinds: tuple[str, ...] | None = DEFAULT_SHIFT_KINDS) -> int:
    if end < start or (end - start).days > 120:
        raise bad_request("bad_range", "טווח תאריכים לא חוקי (עד 120 יום)")
    count = 0
    d = start
    while d <= end:
        before = db.scalar(select(ActivityDay.id).where(ActivityDay.local_date == d))
        create_day(db, d, now, by, shift_kinds=shift_kinds)
        count += before is None
        d += timedelta(days=1)
    return count


def day_has_activity(db: Session, day_id: uuid.UUID) -> bool:
    """True once anything refers to the date or its shifts (registrations, schedules, invitations,
    manager-added participants, manual order)."""
    shift_ids = select(Shift.id).where(Shift.activity_day_id == day_id)
    checks = [
        exists().where(Registration.activity_day_id == day_id),
        exists().where(ScheduleVersion.activity_day_id == day_id),
        exists().where(Invitation.activity_day_id == day_id),
        exists().where(ManagerAddedParticipant.shift_id.in_(shift_ids)),
        exists().where(QueueOrder.shift_id.in_(shift_ids)),
    ]
    return any(db.scalar(select(c)) for c in checks)


def change_kind(db: Session, day: ActivityDay, kind: str, by: uuid.UUID, now: datetime) -> None:
    """Manual classification (e.g. holiday). Shift structure depends on kind, so it may only change
    before anyone registered or a schedule exists for that date."""
    if kind not in DAY_KINDS:
        raise bad_request("bad_kind", "סוג יום לא חוקי")
    if kind == day.kind:
        return
    if day_has_activity(db, day.id):
        raise conflict("day_in_use", "לא ניתן לשנות סוג יום אחרי שנפתחו הרשמות או סידור")
    # Keep the date's saved shift types, limited to what the new day kind allows (evening always stays).
    saved = {s.kind for s in db.scalars(select(Shift).where(Shift.activity_day_id == day.id))}
    keep = tuple(k for k in allowed_shift_kinds(kind) if k in saved)
    for s in db.scalars(select(Shift).where(Shift.activity_day_id == day.id)):
        db.delete(s)
    db.flush()
    old = day.kind
    day.kind = kind
    db.add_all(_build_shifts(day, validate_shift_kinds(kind, keep)))
    day.row_version += 1
    record(db, actor_id=by, action="day_kind_change", object_type="activity_day", object_id=day.id, now=now,
           meta={"from": old, "to": kind})


def change_timing(db: Session, day: ActivityDay, opens: datetime | None, closes: datetime | None,
                  target: datetime | None, by: uuid.UUID, now: datetime) -> None:
    new_open = opens or day.registration_opens_at
    new_close = closes or day.registration_closes_at
    if new_open >= new_close:
        raise bad_request("bad_window", "מועד הפתיחה חייב להיות לפני מועד הסגירה")
    day.registration_opens_at = new_open
    day.registration_closes_at = new_close
    if target:
        day.publish_target_at = target
    day.row_version += 1
    record(db, actor_id=by, action="day_timing_change", object_type="activity_day", object_id=day.id, now=now,
           meta={"opens": new_open.isoformat(), "closes": new_close.isoformat(),
                 "target": day.publish_target_at.isoformat()})
