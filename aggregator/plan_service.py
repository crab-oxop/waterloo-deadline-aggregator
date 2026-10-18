"""Connects the pure planner (planner.py) to the store: builds tasks from open items, busy time from
classes (Quest) and timed events (Outlook meetings, Odyssey sittings), keeps plan_blocks in step,
and holds the planner settings. Re-planning is cheap, so pages call replan() every time they're
drawn — the plan always reflects the latest items, estimates and calendar."""
from __future__ import annotations

import secrets as _secrets
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone

from . import estimates, planner
from .models import NON_WORK_TYPES, Item
from .planner import TZ, Block, Busy, Goal, PlanResult, Settings, Task
from .store import Store

SETTINGS_KEY = "planner"
LIMITS = {"max_block_min": (30, 480), "days": (1, 14), "max_day_min": (60, 16 * 60)}


# How early work is suggested before each deadline. Proactive is the default: start sooner, aim to
# be done a full day early. (Lead ≈ days of runway per hour of work, within [min, max].)
STYLES = {
    "proactive": {"lead_per_hour": timedelta(hours=48), "min_lead": timedelta(days=3),
                  "max_lead": timedelta(days=14), "finish_early": timedelta(hours=24)},
    "balanced": {"lead_per_hour": timedelta(hours=36), "min_lead": timedelta(days=2),
                 "max_lead": timedelta(days=10), "finish_early": timedelta(hours=12)},
    "relaxed": {"lead_per_hour": timedelta(hours=24), "min_lead": timedelta(days=1),
                "max_lead": timedelta(days=5), "finish_early": timedelta(hours=6)},
}
STYLE_LABELS = {"proactive": "Proactive — start early", "balanced": "Balanced", "relaxed": "Relaxed — closer to deadlines"}
DEFAULT_STYLE = "proactive"


def style_of(store: Store) -> str:
    raw = store.get_setting(SETTINGS_KEY, {}) or {}
    return raw.get("style") if raw.get("style") in STYLES else DEFAULT_STYLE


def load_settings(store: Store) -> Settings:
    raw = store.get_setting(SETTINGS_KEY, {}) or {}
    s = replace(Settings(), **STYLES[style_of(store)])
    try:
        return replace(
            s,
            wake=time.fromisoformat(raw["wake"]) if raw.get("wake") else s.wake,
            sleep=time.fromisoformat(raw["sleep"]) if raw.get("sleep") else s.sleep,
            max_block=timedelta(minutes=raw["max_block_min"]) if raw.get("max_block_min") else s.max_block,
            days=int(raw["days"]) if raw.get("days") else s.days,
            max_per_day=timedelta(minutes=raw["max_day_min"]) if raw.get("max_day_min") else s.max_per_day)
    except (ValueError, TypeError):
        return s  # a bad saved value never breaks the planner


def awake_hours(wake: time, sleep: time) -> float:
    minutes = (sleep.hour * 60 + sleep.minute) - (wake.hour * 60 + wake.minute)
    return (minutes if minutes > 0 else minutes + 24 * 60) / 60


def save_settings(store: Store, wake: str, sleep: str, max_block_hours: float, days: int,
                  style: str | None = None, max_day_hours: float | None = None) -> None:
    up, bed = time.fromisoformat(wake), time.fromisoformat(sleep)
    if up == bed:
        raise ValueError("wake-up and bedtime can't be the same")
    if awake_hours(up, bed) < 4:
        raise ValueError("that leaves less than 4 hours awake — check the bedtime (after midnight is fine)")
    minutes = round(max_block_hours * 60)
    lo, hi = LIMITS["max_block_min"]
    if not lo <= minutes <= hi:
        raise ValueError(f"longest block must be between {lo / 60:g} and {hi / 60:g} hours")
    lo, hi = LIMITS["days"]
    if not lo <= days <= hi:
        raise ValueError(f"days to plan must be between {lo} and {hi}")
    if style is not None and style not in STYLES:
        raise ValueError("unknown planning style")
    current = store.get_setting(SETTINGS_KEY, {}) or {}
    day_min = round(max_day_hours * 60) if max_day_hours is not None else current.get("max_day_min", 6 * 60)
    lo, hi = LIMITS["max_day_min"]
    if not lo <= day_min <= hi:
        raise ValueError(f"max work per day must be between {lo // 60} and {hi // 60} hours")
    store.set_setting(SETTINGS_KEY, {"wake": up.strftime("%H:%M"), "sleep": bed.strftime("%H:%M"),
                                     "max_block_min": minutes, "days": days,
                                     "style": style or style_of(store), "max_day_min": day_min})


STUDY_TYPES = {"quiz", "exam", "goal"}  # blocks for these are studying, not finishing a deliverable


def tasks_from(items: list[Item], cal: estimates.Calibration, covered: set[str] = frozenset(),
               now: datetime | None = None, importance: dict | None = None) -> list[Task]:
    """Open, dated units of work. Items without an estimate use the type default (estimates.py).
    Items a goal covers (`covered`, e.g. "Prepare for the midterm: 6 h") are studied via the goal.
    With `now`, items are ranked by their urgency at that moment, the same way goals are."""
    from . import priority

    def score(i: Item) -> float:
        if now is None:
            return i.priority_score
        imp = i.importance if i.importance is not None else (importance or {}).get(i.context)
        return priority.score(i.grade_weight, imp, i.due_date, i.status, now, i.type)
    return [Task(i.id, i.title, i.due_date, estimates.estimate_minutes(i, cal), score(i), i.context)
            for i in items
            if i.status != "done" and i.due_date is not None and i.type not in NON_WORK_TYPES
            and i.id not in covered]


# --- goals --------------------------------------------------------------------------

GOAL_KINDS = {"weekly": "hours per week", "by_date": "hours before a date"}


@dataclass
class GoalSubject:
    """A goal, shaped like an Item where the plan pages, calendar and nudges need one."""
    id: str
    title: str
    context: str | None
    type: str = "goal"
    due_date: datetime | None = None
    source_link: str | None = None
    status: str = "not_started"


def goal_due(store: Store, g: dict) -> datetime | None:
    """When a by-date goal is due: the start of the exam it's linked to (study has to be done
    before the exam, not by the end of that day), else the end of its day."""
    if g["item_id"] and (item := store.get_item(g["item_id"])) is not None and item.due_date:
        return item.due_date
    return datetime.combine(g["until"], time(23, 59), TZ) if g["until"] else None


def subjects(store: Store, items: list[Item] | None = None) -> dict:
    """Everything a block can belong to: items, plus active goals."""
    items = items if items is not None else store.list_items(include_past=True)
    out: dict = {i.id: i for i in items}
    for g in store.goals(active_only=True):
        out[g["id"]] = GoalSubject(g["id"], g["title"], g["course"], due_date=goal_due(store, g))
    return out


def is_study(subject) -> bool:
    return getattr(subject, "type", "") in STUDY_TYPES


def goal_progress(store: Store, goal: dict, now: datetime | None = None) -> dict:
    """Minutes done (accepted blocks already over) and planned (accepted, still ahead) — this week
    for weekly goals, in total for by-date goals — against the target."""
    now = now or datetime.now(timezone.utc)
    s = load_settings(store)
    blocks = [b for b in store.plan_blocks(("accepted",)) if b.key == goal["id"]]
    if goal["kind"] == "weekly":
        week0 = s.day_of(now) - timedelta(days=s.day_of(now).weekday())
        blocks = [b for b in blocks if week0 <= s.day_of(b.start) < week0 + timedelta(days=7)]
    done = sum(b.minutes for b in blocks if b.end <= now)
    planned = sum(b.minutes for b in blocks if b.end > now)
    return {"target": round(goal["hours"] * 60), "done": done, "planned": planned}


GOAL_WEIGHT = 5.0  # a by-date goal ranks like a 5% assignment due at the same time


def goal_tasks(store: Store, now: datetime, s: Settings) -> list[Goal]:
    """Weekly goals → per-day quotas for the planner's "when possible" pass. What's still owed this
    week is spread over the days left in the week (so a slow start catches up); later weeks get an
    even share. (By-date goals are deadline work — see goal_deadlines.)"""
    out = []
    today = s.day_of(now)
    days = [today + timedelta(days=n) for n in range(s.days)]
    week_end = today + timedelta(days=6 - today.weekday())  # Sunday
    for g in store.goals(active_only=True):
        if g["kind"] != "weekly":
            continue
        prog = goal_progress(store, g, now)
        owed = max(0, prog["target"] - prog["done"] - prog["planned"])
        left_this_week = (week_end - today).days + 1
        quota = {d: (owed / left_this_week if d <= week_end else g["hours"] * 60 / 7) for d in days}
        out.append(Goal(g["id"], g["title"], quota, int(g["session_min"]), g["course"]))
    return out


def goal_deadlines(store: Store, now: datetime) -> list[Task]:
    """By-date goals are deadline work: a Task due at the end of their day, sized by the hours still
    open, in sessions of the goal's length, ranked by urgency like an assignment."""
    from . import priority
    out = []
    importance = store.context_importance()
    for g in store.goals(active_only=True):
        if g["kind"] != "by_date" or not g["until"]:
            continue
        due = goal_due(store, g)
        if due <= now:
            continue
        prog = goal_progress(store, g, now)
        remaining = max(0, prog["target"] - prog["done"])  # accepted-but-ahead time counts in the planner itself
        if remaining <= 0:
            continue
        score = priority.score(GOAL_WEIGHT, importance.get(g["course"]), due, "not_started", now)
        per_day = None
        if g["item_id"]:  # exam prep: an even share for each day from today up to the exam
            days = max(1, (due.astimezone(TZ).date() - now.astimezone(TZ).date()).days)
            per_day = max(30, -(-remaining // days // 5) * 5)
        out.append(Task(g["id"], g["title"], due, remaining, score, g["course"], int(g["session_min"]), per_day))
    return out


def add_goal(store: Store, title: str, course: str | None, kind: str, hours: float,
             until: date | None, session_min: int, item_id: str | None = None) -> str:
    title = " ".join(title.split())
    if not title:
        raise ValueError("give the goal a name")
    if len(title) > 120:
        raise ValueError("the name is too long")
    if kind not in GOAL_KINDS:
        raise ValueError("pick hours per week or hours before a date")
    if not 0.25 <= hours <= 80:
        raise ValueError("hours must be between 0.25 and 80")
    if kind == "by_date" and until is None:
        raise ValueError("pick the date to be ready by")
    if not 15 <= session_min <= 240:
        raise ValueError("a session must be 15 minutes to 4 hours")
    goal_id = _secrets.token_hex(16)  # hex: valid as a Google Calendar event id for its blocks
    store.save_goal(goal_id, title, (course or "").strip() or None, kind, hours,
                    until if kind == "by_date" else None, session_min, item_id)
    return goal_id


def suggested_goals(store: Store, now: datetime | None = None, within: int = 21) -> list[dict]:
    """Upcoming quizzes and exams without a goal yet — one click turns each into a study goal."""
    from . import exam_prep
    now = now or datetime.now(timezone.utc)
    linked = {g["item_id"] for g in store.goals() if g["item_id"]}
    prep = exam_prep.load_settings(store)
    out = []
    for i in store.list_items():
        if (i.type in ("quiz", "exam") and i.status != "done" and i.due_date and i.id not in linked
                and now < i.due_date < now + timedelta(days=within)):
            if exam_prep.is_exam(i):  # the same hours the exam-prep card offers
                out.append({"item": i, "hours": prep.hours(exam_prep.exam_kind(i)),
                            "title": f"Prepare for {exam_prep.exam_name(i)}"})
            else:
                hours = 6.0 if i.type == "exam" and any(w in i.title.lower() for w in ("midterm", "final", "exam")) else 1.5
                out.append({"item": i, "hours": hours, "title": f"Study for {i.title}"})
    return sorted(out, key=lambda g: g["item"].due_date)



def busy_from(store: Store, items: list[Item], now: datetime, s: Settings) -> list[Busy]:
    out = []
    today = now.astimezone(TZ).date()
    classes = store.classes()
    for n in range(s.days):
        d = today + timedelta(days=n)
        for c in classes:
            if c.meets_on(d):
                out.append(Busy(datetime.combine(d, c.start, TZ), datetime.combine(d, c.end, TZ),
                                f"{c.course} {c.component}"))
    for ev in store.plan_events():  # The user's own events
        out.append(Busy(ev["start"], ev["end"], ev["title"]))
    for i in items:  # timed events with a real length: Outlook meetings, Odyssey sittings
        end = i.extra.get("end")
        if i.due_date and end:
            try:
                out.append(Busy(i.due_date, datetime.fromisoformat(end), i.title))
            except ValueError:
                pass
    return out


def cleanup(store: Store, items: list[Item] | None = None, now: datetime | None = None) -> None:
    """Remaining blocks of items that are done (or gone/hidden) disappear; old dismissals expire."""
    now = now or datetime.now(timezone.utc)
    items = items if items is not None else store.list_items(include_past=True)
    open_ids = {i.id for i in items if i.status != "done"} | {g["id"] for g in store.goals(active_only=True)}
    stale = [b.id for b in store.plan_blocks(("suggested", "accepted"))
             if b.key not in open_ids and b.end > now]
    stale += [b.id for b in store.plan_blocks(("dismissed",)) if b.end < now - timedelta(days=1)]
    if stale:
        store.delete_blocks(stale)


def replan(store: Store, now: datetime | None = None) -> PlanResult:
    from . import exam_prep
    now = now or datetime.now(timezone.utc)
    exam_prep.follow_exams(store, now)  # exam goals keep the exam's date; passed exams' goals stop
    s = load_settings(store)
    items = store.list_items(include_past=True)
    cleanup(store, items, now)
    cal = estimates.calibrate(store.done_items())
    accepted = store.plan_blocks(("accepted",))
    dismissed = store.plan_blocks(("dismissed",))
    covered = {g["item_id"] for g in store.goals(active_only=True) if g["item_id"]}
    result = planner.plan(tasks_from(items, cal, covered, now, store.context_importance()) + goal_deadlines(store, now),
                          busy_from(store, items, now, s), now, s, accepted, dismissed, goal_tasks(store, now, s))
    store.replace_suggestions(result.blocks)
    return result


def accept(store: Store, block_id: str) -> bool:
    return store.set_block_state(block_id, "accepted", only_from="suggested")


def dismiss(store: Store, block_id: str) -> bool:
    """Dismiss a suggestion, or un-accept an accepted block (its calendar event goes too)."""
    return store.set_block_state(block_id, "dismissed")


def day_start(store: Store, now: datetime | None = None) -> datetime:
    """When the current planning day began: your wake-up time on the day `now` belongs to (so at
    1 AM Saturday it's Friday's wake-up — the night still belongs to Friday)."""
    s = load_settings(store)
    now = now or datetime.now(timezone.utc)
    return datetime.combine(s.day_of(now), s.wake, TZ)


def accept_day(store: Store, day_start: datetime) -> int:
    day_end = day_start + timedelta(days=1)
    n = 0
    for b in store.plan_blocks(("suggested",)):
        if day_start <= b.start < day_end:
            n += accept(store, b.id)
    return n


def accepted_events(store: Store) -> list[tuple[Block, object]]:
    """Accepted blocks still worth mirroring to Google Calendar, with their items or goals."""
    subs = subjects(store)
    return [(b, subs[b.key]) for b in store.plan_blocks(("accepted",)) if b.key in subs]


# --- editing the plan by hand ----------------------------------------------------------

MAX_SPAN = timedelta(hours=16)
LATE_HOURS = 3  # the plan's days run until 3 AM the next morning


def local_span(day: date, start_min: int, end_min: int) -> tuple[datetime, datetime]:
    """A wall-clock span on the plan grid → aware datetimes. Minutes count from midnight of `day`
    and may pass 24 h (after midnight). Built from wall-clock times, so DST days come out right."""
    def at(m: int) -> datetime:
        wall = datetime.combine(day, time(0)) + timedelta(minutes=m)
        return wall.replace(tzinfo=TZ)
    start, end = at(start_min), at(end_min)
    check_span(start, end)
    return start, end


def parse_times(day: str, start: str, end: str) -> tuple[datetime, datetime]:
    """Form input (date + HH:MM + HH:MM) → aware datetimes. An end at or before the start is the
    next day (e.g. 23:00 → 01:00). Like the grid, a day runs until 3 AM: a start before then is
    that night, after midnight (day Oct 2, 01:00 → Oct 3, 1 AM)."""
    d = date.fromisoformat(day)
    t0, t1 = time.fromisoformat(start), time.fromisoformat(end)
    m0, m1 = t0.hour * 60 + t0.minute, t1.hour * 60 + t1.minute
    if m0 < LATE_HOURS * 60:
        m0, m1 = m0 + 24 * 60, m1 + 24 * 60
    if m1 <= m0:
        m1 += 24 * 60
    return local_span(d, m0, m1)


def check_span(start: datetime, end: datetime) -> None:
    if end <= start:
        raise ValueError("the end has to be after the start")
    if end - start < timedelta(minutes=5):
        raise ValueError("make it at least 5 minutes long")
    if end - start > MAX_SPAN:
        raise ValueError("that's longer than 16 hours")


def check_not_past(end: datetime, now: datetime | None = None) -> None:
    """A block that would be over already would just vanish from the plan — say so instead."""
    if end <= (now or datetime.now(timezone.utc)):
        raise ValueError("that time has already passed — put it later")


def edit_block(store: Store, block_id: str, start: datetime, end: datetime,
               now: datetime | None = None) -> bool:
    """Resize or move a block. It becomes the user's (accepted). If he shrinks it, the planner still
    suggests the rest of the task — but at least an hour away, so it doesn't look split."""
    check_span(start, end)
    check_not_past(end, now)
    return store.move_block(block_id, start, end)


def add_block_for(store: Store, key: str, start: datetime, end: datetime,
                  now: datetime | None = None) -> str:
    """The user drops an item or goal onto the grid (e.g. from "Won't fit", or a goal he wants in two
    sittings). It's his block: accepted, never moved by the planner, and it counts toward the item."""
    if key not in subjects(store):
        raise ValueError("that item or goal no longer exists")
    check_span(start, end)
    check_not_past(end, now)
    clash = next((b for b in store.plan_blocks(("accepted",)) if b.key == key and b.start < end and start < b.end), None)
    if clash:
        raise ValueError("there's already a block for it at that time")
    return store.add_block(key, start, end, "accepted", edited=True)


def add_event(store: Store, title: str, start: datetime, end: datetime) -> str:
    title = " ".join(title.split())
    if not title:
        raise ValueError("give the event a name")
    if len(title) > 120:
        raise ValueError("the name is too long")
    check_span(start, end)
    event_id = _secrets.token_hex(16)  # hex: also a valid Google Calendar event id
    store.save_plan_event(event_id, title, start, end)
    return event_id


def edit_event(store: Store, event_id: str, start: datetime, end: datetime, title: str | None = None) -> bool:
    current = next((e for e in store.plan_events() if e["id"] == event_id), None)
    if current is None:
        return False
    title = " ".join((title if title is not None else current["title"]).split())
    if not title or len(title) > 120:
        raise ValueError("give the event a name (up to 120 characters)")
    check_span(start, end)
    store.save_plan_event(event_id, title, start, end)
    return True


def delete_event(store: Store, event_id: str) -> bool:
    return store.delete_plan_event(event_id)


# --- "Free now": the planner nudges you when there's time to work right now ---------------------

MIN_NOW = timedelta(minutes=25)  # a gap shorter than this isn't worth starting something


def free_now(store: Store, now: datetime | None = None) -> dict | None:
    """What to do right now, decided by the planner:
    - a planned block starting within 15 minutes → {"kind": "block", ...} (start it / it's on),
    - otherwise, if you're awake and free for at least 25 minutes → the most important open task
      that still needs time, and how long you have → {"kind": "free", ...}.
    None when you're busy, asleep, or there's nothing to do."""
    now = now or datetime.now(timezone.utc)
    s = load_settings(store)
    items = subjects(store)
    blocks = [b for b in store.plan_blocks(("suggested", "accepted")) if b.key in items and b.end > now]
    soon = next((b for b in sorted(blocks, key=lambda b: b.start) if b.start <= now + timedelta(minutes=15)), None)
    if soon:
        return {"kind": "block", "block": soon, "item": items[soon.key],
                "live": soon.start <= now, "minutes": soon.minutes}

    window = next(((a, z) for a, z in planner.working_windows(now, s) if a <= now + planner.STEP < z), None)
    if window is None:
        return None  # asleep / outside your day
    busy = busy_from(store, [i for i in items.values() if isinstance(i, Item)], now, s)
    taken = [(b.start - s.buffer, b.end + s.buffer) for b in busy] + [(b.start, b.end) for b in blocks]
    gaps = planner.subtract([(max(now, window[0]), window[1])], taken)
    gap = next((g for g in gaps if g[0] <= now + timedelta(minutes=10)), None)
    if gap is None or gap[1] - gap[0] < MIN_NOW:
        return None
    cal = estimates.calibrate(store.done_items())
    done: dict[str, timedelta] = {}
    for b in store.plan_blocks(("accepted",)):
        done[b.key] = done.get(b.key, timedelta(0)) + (b.end - b.start)
    covered = {g["item_id"] for g in store.goals(active_only=True) if g["item_id"]}
    candidates = [t for t in tasks_from([i for i in items.values() if isinstance(i, Item)], cal, covered, now, store.context_importance())
                  + goal_deadlines(store, now)
                  if t.due > now and timedelta(minutes=t.minutes) - done.get(t.key, timedelta(0)) >= timedelta(minutes=15)]
    if not candidates:  # no deadline work: time for a goal, if one still wants time today
        for g in goal_tasks(store, now, s):
            want = g.quota.get(s.day_of(now), 0)
            if want >= 15:
                length = min(gap[1] - gap[0], timedelta(minutes=g.session))
                return {"kind": "free", "item": items[g.key], "start": gap[0], "until": gap[1],
                        "minutes": int(length.total_seconds() // 60)}
        return None
    best = max(candidates, key=lambda t: (t.priority, -t.due.timestamp()))
    remaining = timedelta(minutes=best.minutes) - done.get(best.key, timedelta(0))
    length = min(gap[1] - gap[0], s.max_block, max(remaining, s.min_block))
    return {"kind": "free", "item": items[best.key], "start": gap[0], "until": gap[1],
            "minutes": int(length.total_seconds() // 60)}


def start_now(store: Store, item_id: str, now: datetime | None = None) -> str:
    """Book a block for `item_id` from now: as long as the free gap, the task's remaining time and
    the longest-block setting allow."""
    now = now or datetime.now(timezone.utc)
    tip = free_now(store, now)
    if not tip:
        raise ValueError("there's no free time right now")
    if tip["kind"] == "block":
        if tip["block"].key != item_id:
            raise ValueError("a planned block is about to start")
        accept(store, tip["block"].id)
        return tip["block"].id
    if tip["item"].id != item_id and item_id not in subjects(store):
        raise ValueError("unknown item")
    start = planner._ceil(max(now, tip["start"]))
    end = start + timedelta(minutes=tip["minutes"])
    end = min(end, tip["until"])
    check_span(start, end)
    return store.add_block(item_id, start, end, "accepted")


# --- "Plan my day" -------------------------------------------------------------------

def plan_day(store: Store, now: datetime | None = None) -> dict:
    """On request, plan out the rest of today, in this order:
    1) goal time first — one session for each weekly goal that still owes time this week — so
       deadline work can't crowd it out of the daily cap;
    2) re-plan around it and accept today's suggestions (deadline work, by-date goals);
    3) pull more deadline work forward into whatever free time is left, most urgent first,
       ignoring lead times, with a break between blocks, until the day's cap.
    Everything it adds is marked so "Undo" takes exactly that back."""
    now = now or datetime.now(timezone.utc)
    s = load_settings(store)
    day = s.day_of(now)
    items = store.list_items(include_past=True)
    windows = [w for w in planner.working_windows(now, s) if s.day_of(w[0]) == day]
    busy = busy_from(store, items, now, s)
    added: list[str] = []

    def snapshot():
        """Free time today, time already planned today, and time planned per task — right now."""
        accepted = store.plan_blocks(("accepted",))
        free = planner.subtract(windows, [(x.start - s.buffer, x.end + s.buffer) for x in busy]
                                + [(x.start - s.buffer, x.end + s.buffer) for x in accepted]
                                + [(x.start, x.end) for x in store.plan_blocks(("dismissed",))])
        used = {day: sum((x.end - x.start for x in accepted if s.day_of(x.start) == day and x.end > now), timedelta(0))}
        done: dict[str, timedelta] = {}
        for x in accepted:
            done[x.key] = done.get(x.key, timedelta(0)) + (x.end - x.start)
        return accepted, free, used, done

    def book(got: list, free: list, done: dict) -> None:
        for x in got:
            added.append(store.add_block(x.key, x.start, x.end, "accepted", origin="day"))
            done[x.key] = done.get(x.key, timedelta(0)) + (x.end - x.start)
        if got:  # a short break before whatever comes next
            free[:] = planner.subtract(free, [(x.end, x.end + s.buffer) for x in got])

    # 1) goal time first
    accepted, free, used, done = snapshot()
    for g in store.goals(active_only=True):
        if g["kind"] != "weekly" or any(s.day_of(x.start) == day for x in accepted if x.key == g["id"]):
            continue
        prog = goal_progress(store, g, now)
        owed = prog["target"] - prog["done"] - prog["planned"]
        if owed < 15:
            continue
        session = Task(g["id"], g["title"], datetime.combine(day + timedelta(days=1), time(4), TZ),
                       min(owed, int(g["session_min"])), 0.0, g["course"], int(g["session_min"]))
        got, _ = planner._allocate(free, session, timedelta(minutes=session.minutes), session.due, s, None, used)
        book(got, free, done)

    # 2) re-plan around it, then take today's suggestions
    replan(store, now)
    taken = [x.id for x in store.plan_blocks(("suggested",))
             if s.day_of(x.start) == day and x.end > now and accept(store, x.id)]
    store.set_block_origin(taken, "day")
    added += taken

    # 3) pull deadline work forward into what's left
    accepted, free, used, done = snapshot()
    cal = estimates.calibrate(store.done_items())
    covered = {g["item_id"] for g in store.goals(active_only=True) if g["item_id"]}
    for task in sorted(tasks_from(items, cal, covered, now, store.context_importance()) + goal_deadlines(store, now), key=lambda x: (-x.priority, x.due)):
        if used.get(day, timedelta(0)) >= s.max_per_day:
            break
        need = timedelta(minutes=task.minutes) - done.get(task.key, timedelta(0))
        if task.due <= now or need < timedelta(minutes=15):
            continue
        got, _ = planner._allocate(free, task, need, task.due, s, None, used)
        book(got, free, done)
    mine = set(added)
    minutes = sum(x.minutes for x in store.plan_blocks(("accepted",)) if x.id in mine)
    return {"blocks": len(added), "minutes": minutes}


def day_plan_active(store: Store, now: datetime | None = None) -> bool:
    now = now or datetime.now(timezone.utc)
    return any(b.end > now for b in store.blocks_with_origin("day") if b.state == "accepted")


def undo_day(store: Store, now: datetime | None = None) -> int:
    """Take back what "Plan my day" added (blocks still ahead and not edited by hand since)."""
    now = now or datetime.now(timezone.utc)
    gone = [b.id for b in store.blocks_with_origin("day") if b.state == "accepted" and b.end > now and not b.edited]
    store.delete_blocks(gone)
    return len(gone)
