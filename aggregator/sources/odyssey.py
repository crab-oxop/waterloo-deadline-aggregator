"""Odyssey assessment schedule (read-only): every quiz, midterm and final the Registrar has
scheduled for the user, with room + seat once assigned (~3 business days before). Odyssey signs in
through Waterloo SSO, whose cookies last only as long as the browser window — so it can't be
read in the background. Instead every Waterloo sign-in (e.g. when Crowdmark's ~8 h session
runs out) refreshes it on the way, in the same window. Nothing is ever submitted."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Item
from ..paths import DATA_DIR
from . import uw_browser
from .ics import FeedError, guess_type

SOURCE = "odyssey"
BASE = "https://odyssey.uwaterloo.ca"
SCHEDULE_URL = f"{BASE}/teaching/schedule"
LOGIN_URL = f"{BASE}/mellon/login?ReturnTo=%2Fteaching%2Fschedule"
CONFIGURED_MARKER = uw_browser.PROFILE_DIR / ".odyssey"
REFRESH_AFTER = timedelta(days=3)  # seats are posted ~3 business days before a sitting
TZ = ZoneInfo("America/Toronto")

COURSE_RE = re.compile(r"^([A-Z]{2,6})\s+(\d{2,3}[A-Z]?)\b")
WHEN_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\s+(\d{1,2}:\d{2})\s*[–-]\s*(\d{1,2}:\d{2})$")


def is_configured() -> bool:
    return CONFIGURED_MARKER.exists()


def mark_configured() -> None:
    CONFIGURED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    CONFIGURED_MARKER.write_text("signed in via Waterloo SSO\n")


def on_schedule(url: str) -> bool:
    return url.split("?")[0].rstrip("/") == SCHEDULE_URL


def _text(el) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ").strip() if el else ""


def parse(html: str) -> list[Item]:
    """Rows of the 'Exam · Duration · When · Room · Seat · Sequence' table. 'TBA' times (finals
    before the exam schedule is out) become items with no date yet; they fill in on a later sync."""
    soup = BeautifulSoup(html, "html.parser")
    if "Assessment Schedule" not in soup.get_text():
        raise FeedError("Odyssey didn't show the assessment schedule (signed out?)")
    items: list[Item] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        header = [_text(c).lower() for c in rows[0].find_all(["th", "td"])]
        if "exam" not in header or "when" not in header:
            continue
        col = {h: i for i, h in enumerate(header)}
        for tr in rows[1:]:
            cells = [_text(c) for c in tr.find_all(["td", "th"])]
            if len(cells) <= col["when"] or not cells[col["exam"]]:
                continue
            get = lambda name: cells[col[name]] if name in col and col[name] < len(cells) else ""  # noqa: E731
            title = get("exam")
            due = end = None
            if m := WHEN_RE.match(get("when")):
                day = datetime.strptime(m.group(1), "%Y-%m-%d").date()
                due = datetime.combine(day, datetime.strptime(m.group(2), "%H:%M").time(), TZ)
                end = datetime.combine(day, datetime.strptime(m.group(3), "%H:%M").time(), TZ)
            room = get("room") if get("room").upper() != "TBA" else ""
            extra = {"duration": get("duration"), "room": room, "seat": get("seat"),
                     "sequence": get("sequence"), "end": end.isoformat() if end else None,
                     "location": " · ".join(x for x in (room, f"Seat {get('seat')}" if get("seat") else "") if x)}
            course = COURSE_RE.match(title)
            items.append(Item(
                source=SOURCE, source_key=title, type=guess_type(title, default="exam"), title=title,
                context=f"{course.group(1)} {course.group(2)}" if course else None,
                due_date=due, source_link=SCHEDULE_URL, extra={k: v for k, v in extra.items() if v}))
    return items


def is_stale(last_success: str | None, now: datetime | None = None) -> bool:
    if not last_success:
        return True
    return (now or datetime.now(timezone.utc)) - datetime.fromisoformat(last_success) > REFRESH_AFTER


LAST_HTML = DATA_DIR / "odyssey_schedule.html"


def save_capture(html: str) -> None:
    """Keep the last Odyssey page (local only) so a layout change can be diagnosed."""
    LAST_HTML.parent.mkdir(parents=True, exist_ok=True)
    LAST_HTML.write_text(html, encoding="utf-8")
