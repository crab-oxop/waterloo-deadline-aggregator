"""Month-grid logic for the calendar view: which days to draw and which items fall on each.
Weeks start on Sunday, matching Google Calendar's default in Canada."""
from __future__ import annotations

import calendar
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from .models import Item

LOCAL_TZ = ZoneInfo("America/Toronto")
MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_CAL = calendar.Calendar(firstweekday=calendar.SUNDAY)


def parse_month(raw: str | None, today: date) -> tuple[int, int]:
    """'2026-10' → (2026, 10). Anything malformed falls back to the current month."""
    if raw and (m := MONTH_RE.match(raw)) and 1 <= int(m.group(2)) <= 12 and 2000 <= int(m.group(1)) <= 2100:
        return int(m.group(1)), int(m.group(2))
    return today.year, today.month


def shift(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def weeks(year: int, month: int) -> list[list[date]]:
    """Full weeks covering the month, including spill-over days from neighbouring months."""
    return _CAL.monthdatescalendar(year, month)


def by_day(items: list[Item]) -> dict[date, list[Item]]:
    """Bucket dated items by their local (Toronto) day, earliest first within a day."""
    out: dict[date, list[Item]] = defaultdict(list)
    for it in items:
        if it.due_date is not None:
            out[it.due_date.astimezone(LOCAL_TZ).date()].append(it)
    for bucket in out.values():
        bucket.sort(key=lambda i: (i.due_date, i.title.lower()))
    return out


def local_today(now: datetime | None = None) -> date:
    return (now or datetime.now(timezone.utc)).astimezone(LOCAL_TZ).date()
