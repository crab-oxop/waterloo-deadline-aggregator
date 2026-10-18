"""Groups reminders the way a person thinks about them: what's slipped, what's today,
tomorrow, the rest of this week, next week, later — then one-off things with no date yet,
and last, standing habits to keep in mind (extra["ongoing"]) that never really 'finish'."""
from __future__ import annotations

import hashlib
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import Item

LOCAL_TZ = ZoneInfo("America/Toronto")
GROUPS = ("Overdue", "Today", "Tomorrow", "This week", "Next week", "Later", "No set date", "Keep in mind")
UNDATED = ("No set date", "Keep in mind")


def horizon(due: datetime | None, now: datetime, ongoing: bool = False) -> str:
    if ongoing:
        return "Keep in mind"
    if due is None:
        return "No set date"
    if due < now:
        return "Overdue"
    today = now.astimezone(LOCAL_TZ).date()
    day = due.astimezone(LOCAL_TZ).date()
    end_of_week = today + timedelta(days=6 - today.weekday())  # weeks end Sunday
    if day == today:
        return "Today"
    if day == today + timedelta(days=1):
        return "Tomorrow"
    if day <= end_of_week:
        return "This week"
    if day <= end_of_week + timedelta(days=7):
        return "Next week"
    return "Later"


def relative(due: datetime, now: datetime) -> str:
    """'in 3 hours', 'in 2 days', '1 day ago' — the phrasing people actually use."""
    delta = due - now
    past = delta.total_seconds() < 0
    secs = abs(delta.total_seconds())
    if secs < 3600:
        n, unit = max(1, round(secs / 60)), "min"
    elif secs < 23.5 * 3600:
        n, unit = round(secs / 3600), "hour"
    else:
        n, unit = max(1, round(secs / 86400)), "day"
    label = f"{n} {unit}{'' if n == 1 or unit == 'min' else 's'}"
    return f"{label} ago" if past else f"in {label}"


def group(items: list[Item], now: datetime | None = None) -> tuple[dict[str, list[Item]], list[Item]]:
    """Open reminders by horizon (soonest first; undated alphabetical) + done ones, newest first."""
    now = now or datetime.now(timezone.utc)
    groups: dict[str, list[Item]] = {g: [] for g in GROUPS}
    done: list[Item] = []
    for it in items:
        if it.status == "done":
            done.append(it)
        else:
            groups[horizon(it.due_date, now, bool(it.extra.get("ongoing")))].append(it)
    for name, bucket in groups.items():
        bucket.sort(key=(lambda i: i.title.lower()) if name in UNDATED else (lambda i: i.due_date))
    done.sort(key=lambda i: i.due_date or now, reverse=True)
    return groups, done


def make_due(date_str: str, time_str: str) -> datetime | None:
    """Form input → aware datetime. A date with no time means 'by end of that day'."""
    if not date_str:
        if time_str:
            raise ValueError("pick a date to go with the time")
        return None
    d = datetime.strptime(date_str, "%Y-%m-%d").date()
    t = datetime.strptime(time_str, "%H:%M").time() if time_str else time(23, 59)
    return datetime.combine(d, t, tzinfo=LOCAL_TZ)


def new_key(title: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return "rem:" + hashlib.sha256(f"{title}\x1f{now.isoformat()}".encode()).hexdigest()[:16]
