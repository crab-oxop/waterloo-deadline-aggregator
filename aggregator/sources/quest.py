"""Quest class schedule (read-only). Quest sits behind Waterloo SSO + Duo and its session
doesn't survive a browser restart, so this is deliberately NOT part of the periodic sync:
The user runs `setup-quest` when his schedule changes (new term, add/drop), signs in himself,
and the schedule is read from Quest's "My Class Schedule" List View and stored locally.
That keeps automated Quest logins as rare as possible (SPEC § Risks)."""
from __future__ import annotations

import re
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time

from bs4 import BeautifulSoup

from ..paths import DATA_DIR

LAST_HTML = DATA_DIR / "quest_schedule.html"  # kept locally for troubleshooting a parse failure
SCHEDULE_URL = "https://quest.pecs.uwaterloo.ca/psc/SS/ACADEMIC/SA/c/SA_LEARNER_SERVICES.SSR_SSENRL_LIST.GBL"
LOGIN_TIMEOUT_S = 10 * 60
UNREADABLE_AFTER_S = 45  # signed in to Quest but no readable schedule this long → save the page and stop

DAY_CODES = {"M": 0, "T": 1, "W": 2, "Th": 3, "F": 4, "S": 5, "Sa": 5, "Su": 6}
SCHED_RE = re.compile(r"^(?P<days>(?:Th|Su|Sa|M|T|W|F|S)+)\s+"
                      r"(?P<start>\d{1,2}:\d{2}\s*[AP]M)\s*-\s*(?P<end>\d{1,2}:\d{2}\s*[AP]M)$", re.I)
COURSE_RE = re.compile(r"^\s*([A-Z]{2,6})\s+(\d{2,3}[A-Z]?)\s*-\s*(.+?)\s*$")


@dataclass
class ClassMeeting:
    course: str            # "CS 135"
    title: str             # "Designing Functional Programs"
    component: str         # LEC / TUT / LAB / TST ...
    section: str
    class_nbr: str
    days: list[int]        # Monday = 0
    start: time
    end: time
    location: str = ""
    instructor: str = ""
    start_date: date | None = None
    end_date: date | None = None
    extra: dict = field(default_factory=dict)

    def meets_on(self, day: date) -> bool:
        in_range = (self.start_date is None or self.start_date <= day) and \
                   (self.end_date is None or day <= self.end_date)
        return in_range and day.weekday() in self.days


def term_label(today: date) -> str:
    """Waterloo terms: Winter (Jan–Apr), Spring (May–Aug), Fall (Sep–Dec)."""
    season = "Winter" if today.month <= 4 else "Spring" if today.month <= 8 else "Fall"
    return f"{season} {today.year}"


def _days(codes: str) -> list[int]:
    return [DAY_CODES[c] for c in re.findall(r"Th|Su|Sa|M|T|W|F|S", codes)]


def _clock(s: str) -> time:
    return datetime.strptime(s.replace(" ", "").upper(), "%I:%M%p").time()


def _date(s: str) -> date | None:
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%Y/%m/%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _text(el) -> str:
    return el.get_text(" ", strip=True).replace("\xa0", " ").strip() if el else ""


def _cells(tr) -> list[str] | None:
    """Texts of a row's own cells; None for layout rows that wrap whole nested tables."""
    cells = tr.find_all(["td", "th"], recursive=False)
    if not cells or any(c.find("table") for c in cells):
        return None
    return [_text(c) for c in cells]


CLASS_COLS = {"class nbr": "class_nbr", "section": "section", "component": "component", "days & times": "sched",
              "room": "room", "instructor": "instructor", "start/end date": "dates"}


def parse_schedule(html: str) -> list[ClassMeeting]:
    """Parse Quest's 'My Class Schedule' (List View) by its visible structure, row by row:
    a course heading ("CS 135 - Designing Functional Programs"), its Status row (Enrolled /
    Dropped / Waiting), then the class table under "Class Nbr · Section · Component · Days & Times ·
    Room · Instructor · Start/End Date". A row with blank Class Nbr/Section/Component is another
    meeting time of the class above it (e.g. a second midterm). Independent of Quest's element ids."""
    soup = BeautifulSoup(html, "html.parser")
    meetings: list[ClassMeeting] = []
    course = title = status = ""
    cols: dict[str, int] | None = None
    expect_status = False
    prev = {"component": "", "section": "", "class_nbr": ""}
    for tr in soup.find_all("tr"):
        cells = _cells(tr)
        if cells is None:
            continue
        nonblank = [c for c in cells if c]
        if len(nonblank) == 1 and (m := COURSE_RE.match(nonblank[0])):
            course, title, status, cols = f"{m.group(1)} {m.group(2)}", m.group(3), "", None
            prev = {"component": "", "section": "", "class_nbr": ""}
            continue
        if not course:
            continue
        lowered = [c.lower() for c in cells]
        if lowered and lowered[0] == "status":
            expect_status = True
            continue
        if expect_status:
            status, expect_status = cells[0], False
            continue
        if "days & times" in lowered and "component" in lowered:
            cols = {CLASS_COLS[h]: i for i, h in enumerate(lowered) if h in CLASS_COLS}
            continue
        if cols is None or len(cells) <= max(cols.values()):
            continue
        if status and status.lower() not in ("enrolled", "waiting"):
            continue  # dropped / withdrawn
        get = lambda key: cells[cols[key]] if key in cols else ""  # noqa: E731
        component = get("component") or prev["component"]
        section = get("section") or prev["section"]
        class_nbr = get("class_nbr") or prev["class_nbr"]
        prev = {"component": component, "section": section, "class_nbr": class_nbr}
        sm = SCHED_RE.match(get("sched"))
        if not sm:
            continue  # TBA / online
        dates = [d for d in (_date(x) for x in get("dates").split("-")) if d]
        instructor = get("instructor")
        meetings.append(ClassMeeting(
            course=course, title=title, component=component.upper(), section=section, class_nbr=class_nbr,
            days=_days(sm.group("days")), start=_clock(sm.group("start")), end=_clock(sm.group("end")),
            location="" if get("room").upper() == "TBA" else get("room"),
            instructor="" if instructor.lower() == "to be announced" else instructor,
            start_date=dates[0] if dates else None, end_date=dates[-1] if dates else None,
            extra={"status": status} if status else {}))
    return meetings


# --- interactive capture ----------------------------------------------------------

def _frame_html(frame) -> str:
    """A frame's HTML, or "" while it's navigating. Unlike element queries, this fails fast
    instead of hanging (a locator query on Quest's page once froze the whole sign-in)."""
    try:
        return frame.content()
    except Exception:
        return ""


def _state(page):
    """(state, frame): 'schedule' | 'term' | 'calendar_view' | 'quest' (another Quest page) | 'other'.
    Decided from each frame's HTML — what's actually on screen, not Quest's internal ids."""
    try:
        frames = list(page.frames)
    except Exception:
        return "other", None
    quest_frames = [f for f in frames if "quest.pecs.uwaterloo.ca" in (f.url or "")]
    if not quest_frames:
        return "other", None
    found = {}
    for f in quest_frames:
        html = _frame_html(f)
        if not html:
            continue
        if ("Days &amp; Times" in html or "Days & Times" in html) and "Start/End Date" in html:
            return "schedule", f
        if "SSR_DUMMY_RECV1$sels$" in html:
            found.setdefault("term", f)
        elif "Weekly Calendar View" in html or "SSR_SCHED_FORMAT" in html or ">List View<" in html:
            found.setdefault("calendar_view", f)
    for state in ("term", "calendar_view"):
        if state in found:
            return state, found[state]
    return "quest", None


def _choose_term(frame, label: str) -> None:
    """Pick the term on Quest's term list. Rows are found in the HTML; only the two clicks talk
    to the browser, each with a timeout."""
    soup = BeautifulSoup(_frame_html(frame), "html.parser")
    for span in soup.select('span[id^="TERM_CAR$"]'):
        if label.lower() in _text(span).lower():
            n = span["id"].split("$")[1]
            frame.locator(f'input[id="SSR_DUMMY_RECV1$sels${n}$$0"]').check(timeout=5000)
            frame.locator("#DERIVED_SSS_SCT_SSR_PB_GO").click(timeout=5000)
            return
    raise RuntimeError(f"{label} isn't in Quest's term list")


LAST_PNG = DATA_DIR / "quest_page.png"


def _dump(page) -> None:
    """Keep what the browser showed (every frame's HTML + a screenshot), so a layout Quest
    changed can be diagnosed later. Local only, in the data dir (paths.py), never in OneDrive."""
    parts, errors = [], []
    try:
        parts.append(f"<!-- page: {page.url} | title: {page.title()} -->")
    except Exception as e:
        errors.append(f"page: {type(e).__name__}: {e}")
    for frame in list(page.frames):
        try:
            parts.append(f"<!-- frame: {frame.url} -->\n{frame.content()}")
        except Exception as e:
            errors.append(f"frame {getattr(frame, 'url', '?')}: {type(e).__name__}: {e}")
    if errors:
        parts.append("<!-- errors:\n" + "\n".join(errors) + "\n-->")
    LAST_HTML.parent.mkdir(parents=True, exist_ok=True)
    LAST_HTML.write_text("\n".join(parts), encoding="utf-8")
    try:
        page.screenshot(path=str(LAST_PNG), full_page=True)
    except Exception:
        pass


def _wait(page, seconds: float) -> None:
    """Wait through Playwright so it keeps processing browser events (page.url, navigations);
    a plain sleep freezes what the sync API reports."""
    try:
        page.wait_for_timeout(seconds * 1000)
    except Exception:
        _time.sleep(seconds)


class Unreadable(RuntimeError):
    pass


class Watcher:
    """Looks at a page once per call and nudges it toward a readable schedule. Returns the schedule
    HTML when it has it, None otherwise; raises Unreadable after sitting signed-in on a Quest page
    it can't read for UNREADABLE_AFTER_S (the page + a screenshot are saved first)."""

    def __init__(self, label: str, log=lambda msg: None):
        self.label, self.log = label, log
        self.chose_term = False
        self.last_nudge = _time.monotonic()
        self.on_quest_since: float | None = None
        self.last_state = None

    def step(self, page) -> str | None:
        state, frame = _state(page)
        if state != self.last_state:
            self.log(f"quest: {state} ({page.url[:120]})")
            self.last_state = state
        now = _time.monotonic()
        if state == "other":  # e.g. the Waterloo login / Duo pages — the 45 s clock only runs on Quest itself
            self.on_quest_since = None
            return None
        if self.on_quest_since is None:
            self.on_quest_since = now
        if state == "schedule":
            _wait(page, 1)  # let the page settle
            html = _frame_html(frame)
            if not parse_schedule(html):
                return None  # still filling in — look again next tick
            LAST_HTML.parent.mkdir(parents=True, exist_ok=True)
            LAST_HTML.write_text(html, encoding="utf-8")
            return html
        if now - self.on_quest_since > UNREADABLE_AFTER_S:
            _dump(page)
            raise Unreadable("signed in to Quest, but its schedule page isn't in a layout this app can read yet "
                             f"— saved it to {LAST_HTML} so the reader can be fixed")
        if state == "term" and not self.chose_term:
            _choose_term(frame, self.label)
            self.chose_term = True
        elif state == "calendar_view" and now - self.last_nudge > 5:
            try:  # Quest remembers the Weekly Calendar View; the List View is what we can read
                frame.get_by_label("List View").check(timeout=5000)
            except Exception as e:
                try:  # the radio's label isn't linked on some Quest pages — click it by its value
                    frame.locator('input[id^="DERIVED_REGFRM1_SSR_SCHED_FORMAT"][value="L"]').check(timeout=5000)
                except Exception:
                    self.log(f"quest: couldn't switch to List View ({type(e).__name__})")
            self.last_nudge = now
        elif state == "quest" and now - self.last_nudge > 15:
            self.log("quest: on another Quest page — opening My Class Schedule")
            page.goto(SCHEDULE_URL, wait_until="domcontentloaded")  # e.g. landed on the Quest homepage
            self.last_nudge = _time.monotonic()
        return None


def capture_in(page, label: str, timeout_s: float = LOGIN_TIMEOUT_S, cancelled=lambda: False, log=lambda m: None) -> str:
    """Drive one open page to the schedule (signing in if the user hasn't yet) and return its HTML."""
    page.goto(SCHEDULE_URL, wait_until="domcontentloaded")
    watcher = Watcher(label, log)
    deadline = _time.monotonic() + timeout_s
    while _time.monotonic() < deadline:
        if cancelled():
            raise TimeoutError("cancelled")
        html = watcher.step(page)
        if html:
            return html
        _wait(page, 1)
    _dump(page)
    raise TimeoutError(f"didn't reach a readable Quest schedule (page saved to {LAST_HTML})")


def capture(today: date | None = None) -> str:
    """Stand-alone capture: open a visible browser; the user signs in (SSO + Duo); returns the HTML."""
    from playwright.sync_api import sync_playwright

    from . import uw_browser

    label = term_label(today or date.today())
    with sync_playwright() as p:
        ctx = uw_browser.launch(p, headless=False)
        page = uw_browser.first_page(ctx)
        page.bring_to_front()
        print(f"Sign in to Quest (Waterloo login + Duo) in the browser window — reading your {label} schedule…",
              flush=True)
        try:
            return capture_in(page, label)
        except TimeoutError:
            raise SystemExit("Didn't reach the Quest schedule within 10 minutes — try again.") from None
        finally:
            ctx.close()


def save(store, html: str, today: date | None = None) -> list[ClassMeeting]:
    """Parse a captured schedule and swap it into the store. Empty parse → nothing changes."""
    meetings = parse_schedule(html)
    if meetings:
        store.replace_classes(term_label(today or date.today()), meetings)
    return meetings
