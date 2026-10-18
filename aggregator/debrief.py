"""Test check-ins: once a test is over, the next time the user opens the app it asks how it went,
what to improve, any notes, and how long it took. Answers stay on this machine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import Item

SNOOZE_KEY = "debrief_snooze"
LATER = timedelta(hours=4)    # "Later": ask again after this
LOOK_BACK = timedelta(days=7)  # tests that ended longer ago than this aren't asked about


def is_test(item: Item) -> bool:
    """Sittings on the Odyssey exam schedule, plus any quiz or exam with a real end time."""
    from . import exam_prep
    return exam_prep.is_exam(item) or (item.type in ("exam", "quiz") and bool(item.extra.get("end")))


def ended_at(item: Item) -> datetime | None:
    end = item.extra.get("end")
    if end:
        try:
            return datetime.fromisoformat(end)
        except ValueError:
            pass
    return item.due_date


def scheduled_minutes(item: Item) -> int | None:
    end = ended_at(item)
    if item.due_date and end and end > item.due_date:
        return int((end - item.due_date).total_seconds() // 60)
    return None


def _snoozed(store, now: datetime) -> dict:
    raw = store.get_setting(SNOOZE_KEY, {}) or {}
    return {k: v for k, v in raw.items() if datetime.fromisoformat(v) > now}


def pending(store, now: datetime | None = None) -> list[Item]:
    """Tests that have ended (in the last week) with no check-in yet, oldest first."""
    now = now or datetime.now(timezone.utc)
    answered = store.debrief_ids()
    snoozed = _snoozed(store, now)
    out = []
    for i in store.list_items(include_past=True):
        end = ended_at(i) if is_test(i) else None
        if end and now - LOOK_BACK <= end <= now and i.id not in answered and i.id not in snoozed:
            out.append(i)
    return sorted(out, key=ended_at)


def next_end(store, now: datetime | None = None) -> datetime | None:
    """When the next test ends (so an open page knows when to ask)."""
    now = now or datetime.now(timezone.utc)
    ends = [e for i in store.list_items() if is_test(i) and (e := ended_at(i)) and e > now]
    return min(ends, default=None)


def save(store, item: Item, went: str, improve: str, notes: str, minutes: str | int | None) -> None:
    went, improve, notes = (" ".join((x or "").split())[:2000] for x in (went, improve, notes))
    mins = None
    if minutes not in (None, ""):
        try:
            mins = int(float(minutes))
        except (TypeError, ValueError):
            raise ValueError("how long it took should be a number of minutes") from None
        if not 1 <= mins <= 720:
            raise ValueError("how long it took should be 1 to 720 minutes")
    store.save_debrief(item.id, went, improve, notes, mins, skipped=False)


def skip(store, item: Item) -> None:
    store.save_debrief(item.id, "", "", "", None, skipped=True)


def later(store, item: Item, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    raw = _snoozed(store, now)
    raw[item.id] = (now + LATER).isoformat()
    store.set_setting(SNOOZE_KEY, raw)


def last_improvement(store, course: str | None) -> str:
    """The most recent "what could be improved" note for a course — shown when the next test
    for it comes up."""
    if not course:
        return ""
    for d in store.debriefs():  # newest first
        item = store.get_item(d["item_id"])
        if item and item.context == course and d["improve"]:
            return d["improve"]
    return ""
