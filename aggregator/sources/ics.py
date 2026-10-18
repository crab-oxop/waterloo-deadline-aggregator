"""Generic iCalendar feed reader shared by every ICS-based connector (LEARN, published Outlook
calendars, ...). Read-only. Recurring events are expanded into one item per occurrence."""
from __future__ import annotations

import re
import urllib.error
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

from ..models import Item
from ..secrets import redact_url

LOCAL_TZ = ZoneInfo("America/Toronto")
COURSE_RE = re.compile(r"\b([A-Z]{2,5})\s?-?\s?(\d{2,3}[A-Z]?)\b")
# Occurrences outside this window aren't materialized — keeps years-old history and
# endless weekly repeats out of the store.
WINDOW_PAST = timedelta(days=14)
WINDOW_FUTURE = timedelta(days=180)
TYPE_RULES = (  # first match wins
    ("quiz", re.compile(r"\bquiz", re.I)),
    ("exam", re.compile(r"\b(exam|midterm|final|test)\b", re.I)),
    ("assignment", re.compile(r"\b(assignment|dropbox|due|submission|problem set|lab|project|essay)\b", re.I)),
)


class FeedError(RuntimeError):
    pass


def fetch_feed(url_or_path: str, timeout: float = 30) -> bytes:
    if not url_or_path.lower().startswith(("http://", "https://", "webcal://")):
        return Path(url_or_path).read_bytes()
    url = re.sub(r"^webcal://", "https://", url_or_path, flags=re.I)
    req = urllib.request.Request(url, headers={"User-Agent": "waterloo-deadline-aggregator/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError) as e:
        reason = getattr(e, "code", None) or getattr(e, "reason", None) or type(e).__name__
        raise FeedError(f"fetching {redact_url(url)} failed: {reason}") from None
    if b"BEGIN:VCALENDAR" not in body[:2048]:
        # Expired/invalid tokens tend to return a login HTML page rather than an error status.
        raise FeedError(f"{redact_url(url)} did not return a calendar — the feed link may have expired")
    return body


def to_datetime(value) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=LOCAL_TZ)
    if isinstance(value, date):  # all-day event: treat as due end of that day
        return datetime.combine(value, time(23, 59), tzinfo=LOCAL_TZ)
    raise TypeError(f"unsupported date value: {value!r}")


def find_course(*texts: str) -> str | None:
    for t in texts:
        if t and (m := COURSE_RE.search(t)):
            return f"{m.group(1)} {m.group(2)}"
    return None


def guess_type(summary: str, default: str = "meeting") -> str:
    for kind, rx in TYPE_RULES:
        if rx.search(summary):
            return kind
    return default


def parse_feed(data: bytes, source: str, link_re: re.Pattern | None = None,
               now: datetime | None = None) -> list[Item]:
    now = now or datetime.now(timezone.utc)
    cal = icalendar.Calendar.from_ical(data)

    # A UID shared by a series (RRULE) or its overridden instances (RECURRENCE-ID) needs the
    # occurrence start in its key; one-off events keep the bare UID so identities stay stable.
    recurring_uids = {str(ev.get("UID", "")) for ev in cal.walk("VEVENT")
                      if ev.get("RRULE") or ev.get("RDATE") or ev.get("RECURRENCE-ID")}

    occurrences = recurring_ical_events.of(cal, skip_bad_series=True).between(
        now - WINDOW_PAST, now + WINDOW_FUTURE)
    items: list[Item] = []
    seen: set[str] = set()
    for ev in occurrences:
        summary = str(ev.get("SUMMARY", "")).strip() or "(untitled)"
        description = str(ev.get("DESCRIPTION", ""))
        location = str(ev.get("LOCATION", ""))
        url = str(ev["URL"]) if ev.get("URL") else None
        if not url and link_re and (m := link_re.search(description)):
            url = m.group(0)

        start = ev.get("DTSTART") or ev.get("DTEND")
        when = to_datetime(start.dt) if start else None
        end_prop = ev.get("DTEND")
        # A timed event's real end (not all-day ones, not zero-length deadlines): the planner
        # treats that span as busy, and the calendar mirror shows its length.
        until = (to_datetime(end_prop.dt) if end_prop is not None and isinstance(end_prop.dt, datetime)
                 and isinstance(start.dt, datetime) else None)
        uid = str(ev.get("UID", "")).strip()
        key = uid or url
        if not key:
            continue  # no stable identity — skip rather than risk duplicates on rescrape
        if uid in recurring_uids and when:
            key += "@" + when.astimezone(timezone.utc).isoformat()
        if key in seen:
            continue
        seen.add(key)

        items.append(Item(
            source=source, source_key=key, type=guess_type(summary), title=summary,
            context=find_course(location, summary, description),
            due_date=when, source_link=url,
            extra={**({"location": location} if location else {}),
                   **({"end": until.isoformat()} if until and when and until > when else {})},
        ))
    return items
