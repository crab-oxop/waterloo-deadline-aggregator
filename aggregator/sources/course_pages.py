"""Course web pages (read-only) — e.g. CS 135's assignments page, which lists every assignment
with its due date and is only reachable after Waterloo sign-in (the CS web server uses the same
SAML login as Odyssey). Like Odyssey, the sign-in's cookies die with the browser window, so the
pages are read during each Waterloo sign-in, in the same window. Nothing is ever submitted.

Add another course page by adding a Page to PAGES. The parser reads any table with a "Due"
column; each captured page is also saved to <data dir>/course_pages/ so a layout it can't read can
be fixed later."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Item
from ..paths import DATA_DIR
from . import uw_browser
from .ics import guess_type

SOURCE = "web"
TZ = ZoneInfo("America/Toronto")
CONFIGURED_MARKER = uw_browser.PROFILE_DIR / ".course_pages"
SAVE_DIR = DATA_DIR / "course_pages"
REFRESH_AFTER = timedelta(days=3)
DEFAULT_DUE_TIME = (21, 0)  # CS 135 assignments are due at 9:00 PM when a row gives only a date


@dataclass(frozen=True)
class Page:
    key: str      # short id, also the saved file's name
    course: str   # "CS 135"
    url: str


PAGES = [Page("cs135-assign", "CS 135", "https://student.cs.uwaterloo.ca/~cs135/assign/index.html")]

DUE_WORDS = ("due", "deadline")
TITLE_WORDS = ("assignment", "name", "title", "item", "task", "what")
TBA = re.compile(r"^\s*(tba|tbd|n/?a|-+|—)?\s*$", re.I)


def is_configured() -> bool:
    return CONFIGURED_MARKER.exists()


def mark_configured() -> None:
    CONFIGURED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    CONFIGURED_MARKER.write_text("read during Waterloo sign-in\n")


def is_stale(last_success: str | None, now: datetime | None = None) -> bool:
    if not last_success:
        return True
    return (now or datetime.now(timezone.utc)) - datetime.fromisoformat(last_success) > REFRESH_AFTER


def on_page(url: str, page: Page) -> bool:
    a, b = urlsplit(url), urlsplit(page.url)
    path, want = unquote(a.path).rstrip("/"), unquote(b.path).rstrip("/")
    return a.netloc.split(":")[0] == b.netloc and path in (want, want.rsplit("/", 1)[0])


def _text(el) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ").strip() if el else ""


def parse_due(text: str, now: datetime) -> datetime | None:
    """'Wed, Oct 1 at 9:00 PM', '2026-10-01 21:00', 'Oct 1, 9pm', 'October 1' → Waterloo time.
    A missing year means the nearest one to now; a missing time means DEFAULT_DUE_TIME."""
    from dateutil import parser

    if TBA.match(text):
        return None
    cleaned = re.sub(r"\b(at|by|@)\b", " ", text, flags=re.I)
    cleaned = re.sub(r"\((?:[^)]*)\)", " ", cleaned)  # "(extended)" and similar notes
    has_time = bool(re.search(r"\d{1,2}(:\d{2})?\s*([ap]\.?m\.?)|\b\d{1,2}:\d{2}\b|noon|midnight", cleaned, re.I))
    cleaned = re.sub(r"\bnoon\b", "12:00 PM", cleaned, flags=re.I)
    cleaned = re.sub(r"\bmidnight\b", "11:59 PM", cleaned, flags=re.I)
    local_now = now.astimezone(TZ)
    default = local_now.replace(hour=DEFAULT_DUE_TIME[0], minute=DEFAULT_DUE_TIME[1], second=0, microsecond=0,
                                tzinfo=None)
    try:
        dt = parser.parse(cleaned, fuzzy=True, default=default)
    except (ValueError, OverflowError):
        return None
    if not has_time:
        dt = dt.replace(hour=DEFAULT_DUE_TIME[0], minute=DEFAULT_DUE_TIME[1])
    if not re.search(r"\b(19|20)\d{2}\b", cleaned):  # no year given: pick the one closest to now
        candidates = [dt.replace(year=local_now.year + d) for d in (-1, 0, 1)]
        dt = min(candidates, key=lambda c: abs(c - local_now.replace(tzinfo=None)))
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def parse(html: str, page: Page, now: datetime | None = None) -> list[Item]:
    now = now or datetime.now(timezone.utc)
    soup = BeautifulSoup(html, "html.parser")
    items: list[Item] = []
    seen: set[str] = set()
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header = [_text(c).lower() for c in rows[0].find_all(["th", "td"])]
        due_cols = [i for i, h in enumerate(header) if any(w in h for w in DUE_WORDS)]
        if not due_cols:
            continue
        title_col = next((i for i, h in enumerate(header) if any(w in h for w in TITLE_WORDS)), 0)
        topic_col = next((i for i, h in enumerate(header) if h.startswith("topic")), None)
        pts_col = next((i for i, h in enumerate(header) if h in ("pts", "points", "marks", "weight")), None)
        for tr in rows[1:]:
            cells = tr.find_all(["td", "th"])
            if len(cells) <= max(due_cols[0], title_col):
                continue
            title = _text(cells[title_col])
            if re.fullmatch(r"[a-z]\d{1,2}", title):  # "a02" → "A02"
                title = title.upper()
            topic = _text(cells[topic_col]) if topic_col is not None and topic_col < len(cells) else ""
            pts = _text(cells[pts_col]) if pts_col is not None and pts_col < len(cells) else ""
            due_text = _text(cells[due_cols[0]])
            if not title or title.lower() in header:
                continue
            due = parse_due(due_text, now)
            if due is None and not TBA.match(due_text):
                continue  # a row whose "due" cell isn't a date (e.g. a note spanning the table)
            link = next((a.get("href") for a in tr.find_all("a", href=True)
                         if "submit" not in (a.get("href") or "").lower()), None)
            key = f"{page.key}:{title.lower()}"
            if key in seen:
                continue
            seen.add(key)
            items.append(Item(
                source=SOURCE, source_key=key, type=guess_type(title, default="assignment"),
                title=f"{title} — {topic}" if topic else title,
                context=page.course, due_date=due,
                source_link=urljoin(page.url, link) if link else page.url,
                extra={"page": page.url, "due_text": due_text,
                       **({"points": pts} if re.fullmatch(r"\d+(\.\d+)?", pts) else {})}))
    return items


def save_capture(page: Page, html: str) -> Path:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    path = SAVE_DIR / f"{page.key}.html"
    path.write_text(html, encoding="utf-8")
    return path
