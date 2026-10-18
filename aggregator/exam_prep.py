"""Midterm prep: when an exam is coming, offer a study goal for it, keep the goal on the exam's
date, and show a countdown. Suggest only — a goal is created when the user presses Add."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .models import Item
from .planner import TZ

SETTINGS_KEY = "exam_prep"
DISMISSED_KEY = "exam_prep_dismissed"
DEFAULT_HOURS = {"quiz": 2.0, "midterm": 8.0, "final": 15.0}
DEFAULT_WINDOW = 14  # days ahead to offer a goal

# Waterloo's final-exam periods (month, first day, last day): a test in one of these with no
# "midterm"/"quiz" in its name is treated as a final.
FINAL_PERIODS = ((4, 8, 30), (8, 1, 20), (12, 5, 23))


@dataclass
class PrepSettings:
    window: int = DEFAULT_WINDOW
    quiz: float = DEFAULT_HOURS["quiz"]
    midterm: float = DEFAULT_HOURS["midterm"]
    final: float = DEFAULT_HOURS["final"]

    def hours(self, kind: str) -> float:
        return getattr(self, kind)


def load_settings(store) -> PrepSettings:
    raw = store.get_setting(SETTINGS_KEY, {}) or {}
    d = PrepSettings()
    return PrepSettings(**{k: raw.get(k, getattr(d, k)) for k in d.__dict__})


def save_settings(store, window: int, quiz: float, midterm: float, final: float) -> PrepSettings:
    if not 1 <= window <= 60:
        raise ValueError("look ahead 1 to 60 days")
    for name, h in (("quiz", quiz), ("midterm", midterm), ("final", final)):
        if not 0.5 <= h <= 80:
            raise ValueError(f"{name} hours must be between 0.5 and 80")
    s = PrepSettings(int(window), float(quiz), float(midterm), float(final))
    store.set_setting(SETTINGS_KEY, s.__dict__)
    return s


# --- which items are exams, and what kind (pure) ---------------------------------------------

def is_exam(item: Item) -> bool:
    """A sitting on Odyssey's exam schedule (some come through as quizzes or tests). LEARN's
    "exam" calendar entries are left out: they're mostly notices like "Final examinations begin"."""
    return item.source == "odyssey"


def exam_kind(item: Item) -> str:
    """quiz | midterm | final, from the title, then the date. Unsure → midterm."""
    t = item.title.lower()
    if "final" in t:
        return "final"
    if "midterm" in t or "mid-term" in t:
        return "midterm"
    if "quiz" in t:
        return "quiz"
    if item.due_date:
        d = item.due_date.astimezone(TZ).date()
        if any(d.month == m and a <= d.day <= b for m, a, b in FINAL_PERIODS):
            return "final"
    return "midterm"


def exam_name(item: Item) -> str:
    """"MATH 137 midterm" — the course plus the kind; the item title when there's no course."""
    return f"{item.context} {exam_kind(item)}" if item.context else item.title


def _upcoming(items, now: datetime, within: timedelta | None = None) -> list[Item]:
    """Live, scheduled exams still ahead (unscheduled ones — no time yet — are skipped)."""
    return sorted((i for i in items if is_exam(i) and i.status != "done" and i.due_date and i.due_date > now
                   and (within is None or i.due_date <= now + within)), key=lambda i: i.due_date)


# --- suggestions ---------------------------------------------------------------------------

def suggestions(store, now: datetime | None = None) -> list[dict]:
    """Exams inside the look-ahead window with no goal linked (active or not) and not dismissed
    at their current date. Each: item, kind, hours, days (until the exam)."""
    now = now or datetime.now(timezone.utc)
    s = load_settings(store)
    linked = {g["item_id"] for g in store.goals() if g["item_id"]}
    dismissed = store.get_setting(DISMISSED_KEY, {}) or {}
    out = []
    for i in _upcoming(store.list_items(), now, timedelta(days=s.window)):
        if i.id in linked or dismissed.get(i.id) == i.due_date.isoformat():
            continue
        kind = exam_kind(i)
        days = (i.due_date.astimezone(TZ).date() - now.astimezone(TZ).date()).days
        out.append({"item": i, "kind": kind, "hours": s.hours(kind), "days": days})
    return out


def dismiss(store, item: Item) -> None:
    """Not this one — until the exam moves (a new date makes it a new question)."""
    dismissed = store.get_setting(DISMISSED_KEY, {}) or {}
    dismissed[item.id] = item.due_date.isoformat() if item.due_date else ""
    store.set_setting(DISMISSED_KEY, dismissed)


def add_goal(store, item: Item, hours: float | None = None) -> str:
    """The study goal for an exam: hours before the exam, linked to it so it follows the date."""
    from . import plan_service
    if not item.due_date:
        raise ValueError("that exam has no date yet")
    kind = exam_kind(item)
    hours = load_settings(store).hours(kind) if hours is None else hours
    return plan_service.add_goal(store, f"Prepare for {exam_name(item)}", item.context, "by_date", hours,
                                 item.due_date.astimezone(TZ).date(), 60 if hours >= 4 else 30, item.id)


def follow_exams(store, now: datetime | None = None) -> None:
    """Goals linked to an exam keep its date if Odyssey moves it. Out-of-date goals are removed:
    an exam goal once the exam has started, any other by-date goal once its day has passed."""
    now = now or datetime.now(timezone.utc)
    today = now.astimezone(TZ).date()
    for g in store.goals():
        item = store.get_item(g["item_id"]) if g["item_id"] else None
        if item is not None and item.due_date:
            if item.due_date <= now:
                store.delete_goal(g["id"])
                continue
        elif g["kind"] == "by_date" and g["until"] and g["until"] < today:
            store.delete_goal(g["id"])
            continue
        if item is None or not item.due_date or not g["active"]:
            continue
        day = item.due_date.astimezone(TZ).date()
        if g["until"] != day:
            store.save_goal(g["id"], g["title"], g["course"], g["kind"], g["hours"], day, g["session_min"], g["item_id"])


# --- countdown -------------------------------------------------------------------------------

def countdown(store, now: datetime | None = None) -> dict | None:
    """The next scheduled exam, how far away it is, and study done on its goal (focused minutes)."""
    from . import focus
    now = now or datetime.now(timezone.utc)
    upcoming = _upcoming(store.list_items(), now)
    if not upcoming:
        return None
    item = upcoming[0]
    goal = next((g for g in store.goals() if g["item_id"] == item.id), None)
    return {"item": item, "name": exam_name(item), "when": until_text(item.due_date, now),
            "studied": focus.focused_minutes(store, goal["id"]) if goal else None,
            "target": round(goal["hours"] * 60) if goal else None}


def until_text(when: datetime, now: datetime) -> str:
    days = (when.astimezone(TZ).date() - now.astimezone(TZ).date()).days
    if days <= 0:
        hours = max(1, round((when - now).total_seconds() / 3600))
        return f"in {hours} h"
    return "tomorrow" if days == 1 else f"in {days} days"
