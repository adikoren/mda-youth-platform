"""Local-time helpers. Instants are stored in UTC; product rules are expressed in Asia/Jerusalem.

Convention: weekday numbers are 0=Sunday ... 6=Saturday (matches PostgreSQL EXTRACT(DOW)).
Always build local wall-clock times first and then convert, never "UTC instant + N hours",
so daylight-saving transitions are handled correctly.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.core.config import get_settings

UTC = timezone.utc
WEEKDAY_NAMES_HE = ["ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"]
SUNDAY, MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY, SATURDAY = range(7)


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def local_weekday(d: date) -> int:
    return (d.weekday() + 1) % 7


def local_dt(d: date, t: time) -> datetime:
    """Local wall-clock (date, time) -> aware UTC instant."""
    return datetime.combine(d, t, tzinfo=tz()).astimezone(UTC)


def local_midnight_after(d: date) -> datetime:
    """Exclusive end of a local date: 00:00 of the next local day, as UTC."""
    return local_dt(d + timedelta(days=1), time(0, 0))


def to_local(instant: datetime) -> datetime:
    return instant.astimezone(tz())


def local_today(now: datetime) -> date:
    return to_local(now).date()
