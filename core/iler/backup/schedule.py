"""When the daily backup runs: 04:00 in the club's time zone."""

from datetime import datetime, time, timedelta, tzinfo

BACKUP_AT = time(4, 0)


def next_run(now: datetime, tz: tzinfo, at: time = BACKUP_AT) -> datetime:
    """First moment strictly after `now` when the wall clock in `tz` shows `at`."""
    local = now.astimezone(tz)
    candidate = datetime.combine(local.date(), at, tzinfo=tz)
    if candidate <= local:
        candidate = datetime.combine(local.date() + timedelta(days=1), at, tzinfo=tz)
    return candidate
