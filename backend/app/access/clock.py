"""Injectable clock. All policy and window decisions use server time from here, so tests can
pin exact boundary instants (e.g. 23:59:59 vs 00:00 local, DST nights)."""
from datetime import datetime, timezone


class Clock:
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock(Clock):
    def __init__(self, instant: datetime):
        self.instant = instant

    def now(self) -> datetime:
        return self.instant

    def set(self, instant: datetime) -> None:
        self.instant = instant


_clock: Clock = Clock()


def get_clock() -> Clock:
    return _clock


def set_clock(clock: Clock) -> None:
    global _clock
    _clock = clock
