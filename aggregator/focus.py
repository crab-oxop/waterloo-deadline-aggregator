"""Focus sessions: from "it's on the plan" to "I'm working on it" in one action.

Double-click a Work plan block → a start screen (session goal, phone-away prompt, what to open)
→ a running session: one countdown for the whole block (no fixed Pomodoro rounds), Break/Resume,
+15 min, Finish → "Did you finish the item?". This module holds the pure parts — timer maths,
the deadline line, the Claude message and link — and the session state helpers; the pages and
routes live in pages.py / dashboard.py.

The timer is always computed from timestamps stored in the database, never from a JavaScript
counter, so it survives a reload, a redraw or reopening the window. All arithmetic is on aware
UTC datetimes, so a DST change mid-session (Nov 1) doesn't skew it."""
from __future__ import annotations

import os
import secrets
import sys
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from . import estimates, planner
from .models import Item
from .planner import TZ

SETTINGS_KEY = "focus"
DEFAULT_MUSIC = "https://www.youtube.com/results?search_query=lofi+hip+hop+radio"
CLAUDE_LIMIT = 14_000      # characters after URL-encoding (the documented rough limit)
CLAUDE_BUDGET = 12_000     # stay well under it
WARN_LAST = timedelta(minutes=10)
EXTEND_BY = timedelta(minutes=15)

DEFAULT_TEMPLATE = """I'm starting a focus session and would like you to be my tutor.

What I'm working on: {title}
Course: {course}
Type: {type}
Due: {due}
{weight}Time estimate: {estimate}
Link: {link}

My goal for this session: {goal}

Please act as a tutor: explain concepts, help me get unstuck with hints and questions, and check my reasoning when I share it. Please don't write the solutions or give me final answers — my course may have its own rules about AI help, so keep it to guidance I can learn from.

Also due in the next few days (for context only):
{others}"""


# --- settings -----------------------------------------------------------------------

@dataclass
class FocusSettings:
    music_url: str = DEFAULT_MUSIC
    open_assignment: bool = True
    open_music: bool = True
    open_claude: bool = True
    template: str = DEFAULT_TEMPLATE
    chime: bool = True


def load_settings(store) -> FocusSettings:
    raw = store.get_setting(SETTINGS_KEY, {}) or {}
    d = FocusSettings()
    return FocusSettings(
        music_url=raw.get("music_url") or d.music_url,
        open_assignment=raw.get("open_assignment", d.open_assignment),
        open_music=raw.get("open_music", d.open_music),
        open_claude=raw.get("open_claude", d.open_claude),
        template=raw.get("template") or d.template,
        chime=raw.get("chime", d.chime))


def save_settings(store, **changes) -> FocusSettings:
    current = load_settings(store).__dict__.copy()
    for k, v in changes.items():
        if k not in current:
            raise ValueError(f"unknown focus setting {k}")
        current[k] = v
    url = (current["music_url"] or "").strip()
    if url and not url.lower().startswith(("http://", "https://")):
        raise ValueError("the music link must start with http:// or https://")
    if len(current["template"]) > 4000:
        raise ValueError("the Claude message template is too long (4000 characters max)")
    current["music_url"] = url or DEFAULT_MUSIC
    store.set_setting(SETTINGS_KEY, current)
    return FocusSettings(**current)


# --- timer maths (pure) -----------------------------------------------------------------

@dataclass
class TimerState:
    planned: int        # seconds: the block's length plus any +15s
    worked: int         # seconds of focused time so far (breaks excluded)
    remaining: int      # seconds left; negative = over time
    on_break: bool
    break_for: int      # seconds into the current break (0 when not on one)
    warning: bool       # last 10 minutes
    over: bool          # past zero: counting up

    @property
    def over_by(self) -> int:
        return max(0, -self.remaining)


def timer(session: dict, now: datetime | None = None) -> TimerState:
    """Where the countdown is, from the stored timestamps alone."""
    # Work in UTC: Python subtracts two datetimes that share a zone (e.g. both America/Toronto) on
    # the wall clock, which would make a session across the DST change an hour too long or short.
    def utc(dt):
        return dt.astimezone(timezone.utc) if dt else None
    session = {**session, "started_at": utc(session["started_at"]), "ended_at": utc(session.get("ended_at")),
               "break_started_at": utc(session.get("break_started_at"))}
    now = utc(now) or datetime.now(timezone.utc)
    end = session.get("ended_at") or now
    break_started = session.get("break_started_at")
    paused = timedelta(seconds=session.get("paused_seconds") or 0)
    current_break = (end - break_started) if break_started and not session.get("ended_at") else timedelta(0)
    worked = max(timedelta(0), (end - session["started_at"]) - paused - current_break)
    planned = timedelta(minutes=session["planned_minutes"] + (session.get("extended_minutes") or 0))
    remaining = planned - worked
    return TimerState(
        planned=int(planned.total_seconds()), worked=int(worked.total_seconds()),
        remaining=int(remaining.total_seconds()), on_break=bool(break_started and not session.get("ended_at")),
        break_for=int(current_break.total_seconds()),
        warning=timedelta(0) < remaining <= WARN_LAST, over=remaining <= timedelta(0))


def fmt_clock(seconds: int) -> str:
    seconds = abs(int(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


# --- the deadline line -----------------------------------------------------------------

def _human_due(due: datetime, now: datetime) -> str:
    delta = due - now
    if delta <= timedelta(0):
        return "Overdue"
    hours = delta.total_seconds() / 3600
    if hours < 1:
        return f"Due in {int(delta.total_seconds() // 60)} min"
    if hours < 24:
        return f"Due in {hours:.0f} h"
    days = local_days_between(now, due)
    return "Due tomorrow" if days == 1 else f"Due in {days} days"


def local_days_between(a: datetime, b: datetime) -> int:
    return (b.astimezone(TZ).date() - a.astimezone(TZ).date()).days


def deadline_line(store, subject, now: datetime | None = None) -> str:
    """'Due in 2 days · about 3h of work left · 4h free before then' — from the planner's data."""
    from . import plan_service

    now = now or datetime.now(timezone.utc)
    due = getattr(subject, "due_date", None)
    if subject is None or due is None:
        return ""
    parts = [_human_due(due, now)]
    if due <= now:
        return parts[0]
    focused = focused_minutes(store, subject.id)
    if getattr(subject, "type", "") == "goal":
        g = next((g for g in store.goals() if g["id"] == subject.id), None)
        total = round(g["hours"] * 60) if g else 0
    else:
        total = estimates.estimate_minutes(subject, estimates.calibrate(store.done_items()))
    left = max(0, total - focused)
    parts.append(f"about {estimates.fmt_minutes(left) or '0m'} of work left" if left else "estimate used up")
    s = plan_service.load_settings(store)
    items = store.list_items(include_past=True)
    busy = plan_service.busy_from(store, items, now, s)
    windows = [(a, min(z, due)) for a, z in planner.working_windows(now, s) if a < due]
    free = planner.subtract(windows, [(b.start - s.buffer, b.end + s.buffer) for b in busy])
    free_min = int(sum((z - a for a, z in free), timedelta(0)).total_seconds() // 60)
    parts.append(f"{estimates.fmt_minutes(free_min) or '0m'} free before then")
    return " · ".join(parts)


# --- Claude message and link --------------------------------------------------------------

def claude_message(template: str, subject, goal: str, others: list[str], estimate: str = "",
                   budget: int = CLAUDE_BUDGET) -> str:
    """Fill the tutor template. If the encoded result is too long, drop "also due" lines first
    (from the end), then shorten the session goal, then cut the message itself."""
    due = getattr(subject, "due_date", None)
    weight = getattr(subject, "grade_weight", None)
    fields = {
        "title": getattr(subject, "title", "") or "(untitled)",
        "course": getattr(subject, "context", None) or "—",
        "type": {"goal": "study goal"}.get(getattr(subject, "type", ""), getattr(subject, "type", "") or "—"),
        "due": f"{due.astimezone(TZ):%a %b %d, %I:%M %p}" if due else "no due date",
        "weight": f"Grade weight: {weight:g}%\n" if weight is not None else "",
        "estimate": estimate or "—",
        "link": getattr(subject, "source_link", None) or "—",
        "goal": goal.strip() or "(not set)",
        "others": "",
    }

    def render(other_lines: list[str]) -> str:
        f = dict(fields, others="\n".join(f"- {o}" for o in other_lines) or "- nothing else soon")
        try:
            return template.format(**f).strip()
        except (KeyError, IndexError, ValueError):  # a broken custom template: fall back to the default
            return DEFAULT_TEMPLATE.format(**f).strip()

    lines = list(others[:5])
    text = render(lines)
    while len(quote(text, safe="")) > budget and lines:
        lines.pop()
        text = render(lines)
    while len(quote(text, safe="")) > budget and len(fields["goal"]) > 40:
        fields["goal"] = fields["goal"][: len(fields["goal"]) // 2] + "…"
        text = render(lines)
    while len(quote(text, safe="")) > budget:
        text = text[: int(len(text) * 0.9)]
    return text


def claude_url(text: str, scheme: str = "claude") -> str:
    """claude://claude.ai/new?q=… (desktop app) or https://claude.ai/new?q=… (browser)."""
    base = "claude://claude.ai/new" if scheme == "claude" else "https://claude.ai/new"
    return f"{base}?q={quote(text, safe='')}"


def claude_app_installed() -> bool:
    """Is the claude:// scheme handled on this PC (Claude Desktop)? Asks Windows for the handler."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes
    try:
        fn = ctypes.windll.shlwapi.AssocQueryStringW
        fn.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPWSTR,
                       ctypes.POINTER(wintypes.DWORD)]
        size = wintypes.DWORD(512)
        buf = ctypes.create_unicode_buffer(512)
        return fn(0, 4, "claude", "open", buf, ctypes.byref(size)) == 0 and bool(buf.value)  # 4 = friendly app name
    except (OSError, AttributeError):
        return False


def open_url(url: str) -> None:
    """Hand a link to Windows. os.startfile keeps the whole URL (cmd's `start` splits it at '&')."""
    if sys.platform == "win32":
        os.startfile(url)  # noqa: S606 — the URL is built by us, not taken from a page
    else:
        webbrowser.open(url)


# --- session state ------------------------------------------------------------------------

def new_id() -> str:
    return secrets.token_hex(16)


def focused_minutes(store, item_id: str) -> int:
    """All focused time logged on an item (or goal) across its sessions."""
    now = datetime.now(timezone.utc)
    return sum(timer(s, now).worked for s in store.focus_sessions(item_id=item_id)) // 60


class SessionRunning(ValueError):
    """Only one focus session at a time."""
    def __init__(self, session: dict):
        super().__init__("a focus session is already running")
        self.session = session


@dataclass
class EventSubject:
    """One of the user's own events (gym, a reading session he blocked out), shaped like an Item for
    the focus pages. No course, due date or link — just its name."""
    id: str
    title: str
    context: str | None = None
    type: str = "event"
    due_date: datetime | None = None
    source_link: str | None = None
    status: str = "not_started"


def _event(store, event_id: str) -> dict | None:
    return next((e for e in store.plan_events() if e["id"] == event_id), None)


def slot(store, slot_id: str):
    """What a session runs on: a plan block, or one of the user's own events (as a Block whose key
    and id are the event's id)."""
    from .planner import Block
    block = store.get_block(slot_id)
    if block is not None:
        return block
    e = _event(store, slot_id)
    return Block(e["id"], e["start"], e["end"], "accepted", e["id"]) if e else None


def subject_for(store, key: str):
    """The item, goal or own event a session is about (None once it's gone)."""
    from . import plan_service
    subj = plan_service.subjects(store).get(key)
    if subj is None and (e := _event(store, key)):
        subj = EventSubject(e["id"], e["title"])
    return subj


def start(store, block_id: str, goal: str = "", now: datetime | None = None) -> str:
    """Begin a session on a plan block or an own event. A suggested block becomes accepted (it's
    happening)."""
    from . import plan_service

    now = now or datetime.now(timezone.utc)
    running = store.running_focus()
    if running:
        raise SessionRunning(running)
    block = slot(store, block_id)
    if block is None or block.state == "dismissed":
        raise ValueError("that block is no longer on the plan")
    if block.state == "suggested":
        plan_service.accept(store, block.id)
    sid = new_id()
    store.add_focus(sid, block.id, block.key, (goal or "").strip()[:200], max(5, block.minutes), now)
    return sid


def take_break(store, sid: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    s = store.get_focus(sid)
    if s and not s["ended_at"] and not s["break_started_at"]:
        store.update_focus(sid, break_started_at=now, break_count=s["break_count"] + 1)


def resume(store, sid: str, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    s = store.get_focus(sid)
    if s and not s["ended_at"] and s["break_started_at"]:
        paused = s["paused_seconds"] + int((now - s["break_started_at"]).total_seconds())
        store.update_focus(sid, paused_seconds=paused, break_started_at=None)


def extend(store, sid: str, now: datetime | None = None) -> None:
    s = store.get_focus(sid)
    if s and not s["ended_at"]:
        store.update_focus(sid, extended_minutes=s["extended_minutes"] + int(EXTEND_BY.total_seconds() // 60))


def _settle_block(store, s: dict, ended: datetime) -> None:
    """If the session ran long or was extended, stretch the block to when it really ended, so the
    plan and Google Calendar match what happened (the same path as dragging its edge)."""
    if not s["block_id"]:
        return
    block = slot(store, s["block_id"])
    if block is None:
        return
    ran_long = timer(s, ended).over or s["extended_minutes"]
    if ran_long and ended > block.end:
        if store.get_block(block.id) is not None:
            store.move_block(block.id, block.start, ended)
        else:  # an own event: stretch it the same way
            e = _event(store, block.id)
            store.save_plan_event(block.id, e["title"], block.start, ended)


def finish(store, sid: str, outcome: str, now: datetime | None = None) -> dict:
    """End the session. outcome: done | not_yet | abandoned. Returns what the caller should do next."""
    from . import plan_service

    if outcome not in ("done", "not_yet", "abandoned"):
        raise ValueError("unknown outcome")
    now = now or datetime.now(timezone.utc)
    s = store.get_focus(sid)
    if s is None:
        raise ValueError("no such session")
    if s["ended_at"]:
        return {"next": "/plan"}
    if s["break_started_at"]:  # a break in progress ends with the session and isn't counted
        resume(store, sid, now)
        s = store.get_focus(sid)
    store.update_focus(sid, ended_at=now, outcome=outcome)
    s = store.get_focus(sid)
    _settle_block(store, s, now)
    subject = plan_service.subjects(store).get(s["item_id"])  # live items and goals only
    item = subject if isinstance(subject, Item) else None  # an item since removed from its source: nothing to mark
    if outcome == "done" and item is not None:
        store.set_status(item.id, "done")
        minutes = focused_minutes(store, item.id)
        if minutes:  # prefill "how long did it take" — a manual answer there replaces this, not adds to it
            store.set_actual_duration(item.id, minutes)
        plan_service.replan(store, now)
        return {"next": f"/done?id={item.id}&next=/plan"}
    if outcome == "done" and getattr(subject, "type", "") == "goal":
        g = next((g for g in store.goals() if g["id"] == s["item_id"]), None)
        if g and g["kind"] == "by_date":
            store.set_goal_active(g["id"], False)  # finished: stop planning it
    plan_service.replan(store, now)  # remaining work gets new blocks; accepted ones stay put
    return {"next": "/plan"}
