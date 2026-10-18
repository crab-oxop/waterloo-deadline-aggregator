"""Study-block planner — turns "what's due" into "when to do it". Pure: tasks + busy times in,
suggested work blocks out (plan_service.py wires it to the store; nothing here touches I/O).

Rules (NEXT.md task 3):
- Free time = the hours you're awake (wake-up + 30 min to bedtime − 30 min; bedtimes after midnight
  work), America/Toronto, minus classes, calendar events, your own events and a 15-minute buffer
  around each, over the next N days.
- Highest priority first, earliest deadline as the tie-break. Each task is split into blocks of
  30 min – 2 h, placed as early as possible, aiming to finish ~12 h before it's due; if that's too
  tight, anything up to the due time is used.
- A task that can't fit is reported with how many minutes it's short — only when it's due inside
  the planning horizon (work due later can still happen later).
- Accepted blocks are fixed: never moved, they count toward their task, and one that now overlaps
  something busy is reported as a conflict instead of being moved. Dismissed slots aren't reused.

Everything is timezone-aware; working hours are built per local date with zoneinfo, so days
around a DST change (Nov 1, 2026) get the right UTC offsets."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Toronto")
STEP = timedelta(minutes=5)  # blocks start and end on 5-minute marks


@dataclass(frozen=True)
class Settings:
    wake: time = time(8, 30)
    sleep: time = time(22, 30)   # earlier than `wake` means after midnight
    settle: timedelta = timedelta(minutes=30)  # no blocks right after waking or right before bed
    buffer: timedelta = timedelta(minutes=15)
    min_block: timedelta = timedelta(minutes=30)
    max_block: timedelta = timedelta(hours=2)
    days: int = 7
    finish_early: timedelta = timedelta(hours=12)
    # Lead time: work isn't suggested earlier than this before it's due (unless there's no room
    # later) — so December quizzes don't get crammed into this week. ~1.5 days per hour of work.
    lead_per_hour: timedelta = timedelta(hours=36)
    min_lead: timedelta = timedelta(days=2)
    max_lead: timedelta = timedelta(days=10)
    # After the user resizes or moves a block, the rest of that task is still suggested — but not glued
    # to the block he edited (that looked like the block had "split").
    edit_gap: timedelta = timedelta(hours=1)
    # Never plan more than this much work into one day (accepted blocks count toward it).
    max_per_day: timedelta = timedelta(hours=6)

    def lead(self, minutes: int) -> timedelta:
        return min(max(self.lead_per_hour * (minutes / 60), self.min_lead), self.max_lead)

    @property
    def overnight(self) -> bool:
        return self.sleep <= self.wake

    def awake(self, d: date) -> tuple[datetime, datetime]:
        """When you're up on day `d`: wake-up that morning → bedtime (the next day if after midnight)."""
        end_day = d + timedelta(days=1) if self.overnight else d
        return datetime.combine(d, self.wake, TZ), datetime.combine(end_day, self.sleep, TZ)

    def working(self, d: date) -> tuple[datetime, datetime]:
        up, bed = self.awake(d)
        return up + self.settle, bed - self.settle

    def day_of(self, dt: datetime) -> date:
        """The day a moment "belongs" to: before your wake-up time it's still the previous day
        (so 12:30 AM with a 1:30 AM bedtime counts toward the evening before)."""
        return (dt.astimezone(TZ) - timedelta(hours=self.wake.hour, minutes=self.wake.minute)).date()


@dataclass(frozen=True)
class Task:
    key: str             # item id
    title: str
    due: datetime
    minutes: int         # calibrated estimate of the whole task
    priority: float
    context: str | None = None
    max_block: int | None = None  # minutes; a goal's session length (None = the planner setting)
    per_day: int | None = None    # minutes; spread evenly — at most this much of it per day (exam prep)


@dataclass(frozen=True)
class Goal:
    """Study time to work towards "when possible" — after deadline work. `quota` is how many
    minutes are wanted per planning day; what isn't placed one day carries to the next."""
    key: str             # goal id
    title: str
    quota: dict          # date -> minutes wanted that day
    session: int = 45    # minutes per session (one session per goal per day)
    context: str | None = None


@dataclass(frozen=True)
class Busy:
    start: datetime
    end: datetime
    label: str = ""


@dataclass(frozen=True)
class Block:
    key: str             # the task's item id
    start: datetime
    end: datetime
    state: str = "suggested"
    bid: str = ""        # stored id — kept when the user resizes or moves the block
    edited: bool = False  # The user resized/moved it by hand

    @property
    def id(self) -> str:
        """Stable id (same task + same slot → same id), hex so it's a valid Google Calendar event id."""
        if self.bid:
            return self.bid
        raw = f"block|{self.key}|{self.start.isoformat()}|{self.end.isoformat()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class Shortfall:
    key: str
    title: str
    due: datetime
    needed: int          # minutes still needed when planning started
    short: int           # minutes that didn't fit


@dataclass(frozen=True)
class Conflict:
    block: Block
    with_: str


@dataclass
class PlanResult:
    blocks: list[Block] = field(default_factory=list)          # new suggestions
    shortfalls: list[Shortfall] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)


Interval = tuple[datetime, datetime]


def _ceil(dt: datetime) -> datetime:
    base = dt.replace(second=0, microsecond=0)
    if base < dt:
        base += timedelta(minutes=1)
    return base + timedelta(minutes=(5 - base.minute % 5) % 5)


def _floor(dt: datetime) -> datetime:
    base = dt.replace(second=0, microsecond=0)
    return base - timedelta(minutes=base.minute % 5)


def working_windows(now: datetime, settings: Settings) -> list[Interval]:
    """The working window of each day in the horizon (today + days-1 more), from now on. With a
    bedtime after midnight, yesterday's window can still be open in the small hours."""
    today = now.astimezone(TZ).date()
    out = []
    for n in range(-1 if settings.overnight else 0, settings.days):
        d: date = today + timedelta(days=n)
        start, end = settings.working(d)
        start = max(start, _ceil(now))
        if end - start >= STEP:
            out.append((start, end))
    return out


def subtract(windows: list[Interval], blocked: list[Interval]) -> list[Interval]:
    """windows minus the union of blocked intervals, snapped inward to 5-minute marks."""
    blocked = sorted(blocked)
    out = []
    for ws, we in windows:
        cur = ws
        for bs, be in blocked:
            if be <= cur or bs >= we:
                continue
            if bs > cur:
                out.append((cur, bs))
            cur = max(cur, be)
            if cur >= we:
                break
        if cur < we:
            out.append((cur, we))
    snapped = [(_ceil(s), _floor(e)) for s, e in out]
    return [(s, e) for s, e in snapped if e - s >= STEP]


def _overlaps(a: Interval, b: Interval) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _allocate(free: list[Interval], task: Task, need: timedelta, deadline: datetime,
              s: Settings, not_before: datetime | None = None,
              used: dict | None = None, mine: dict | None = None) -> tuple[list[Block], timedelta]:
    """Carve blocks for one task out of `free` (mutated), earliest first, between `not_before`
    and `deadline`. Free time before `not_before` in a used window stays free. `used` (day →
    planned time, mutated) keeps every day under s.max_per_day. `mine` (day → this task's time,
    mutated) keeps a spread task under its per_day share."""
    used = used if used is not None else {}
    blocks: list[Block] = []
    # The shortest block allowed: min_block, unless the whole task is shorter than that. A leftover
    # smaller than this is rounded UP to a full block rather than scheduled as a 5-minute sliver.
    floor = min(s.min_block, timedelta(minutes=task.minutes))
    i = 0
    while i < len(free) and need > timedelta(0):
        ws, we = free[i]
        begin = max(ws, _ceil(not_before)) if not_before else ws
        limit = min(we, deadline)
        smallest = floor
        if limit - begin < smallest:
            i += 1
            continue
        cur = begin
        while need > timedelta(0) and limit - cur >= smallest:
            room = s.max_per_day - used.get(s.day_of(cur), timedelta(0))
            if mine is not None and task.per_day:
                room = min(room, timedelta(minutes=task.per_day) - mine.get(s.day_of(cur), timedelta(0)))
            if room < smallest:
                break  # this day is full — on to the next window (the next day)
            longest = min(s.max_block, timedelta(minutes=task.max_block)) if task.max_block else s.max_block
            length = min(longest, max(need, smallest), limit - cur, room)
            leftover = need - length
            if timedelta(0) < leftover < floor and length - (floor - leftover) >= floor:
                length -= floor - leftover  # leave a full-size last block rather than a sliver
            length = timedelta(minutes=int(length.total_seconds() // 60) // 5 * 5)
            if length < smallest or length <= timedelta(0):
                break
            blocks.append(Block(task.key, cur, cur + length))
            used[s.day_of(cur)] = used.get(s.day_of(cur), timedelta(0)) + length
            if mine is not None:
                mine[s.day_of(cur)] = mine.get(s.day_of(cur), timedelta(0)) + length
            need -= length
            cur += length
            if need > timedelta(0):
                cur += s.buffer  # a short break before the next block of the same task
        if cur > begin:  # this window was used: keep what's left before and after
            pieces = [p for p in ((ws, begin), (_ceil(cur), we)) if p[1] - p[0] >= STEP]
            free[i:i + 1] = pieces
            i += len(pieces)
        else:
            i += 1
    return blocks, need


def plan(tasks: list[Task], busy: list[Busy], now: datetime, settings: Settings | None = None,
         accepted: list[Block] = (), dismissed: list[Block] = (), goals: list[Goal] = ()) -> PlanResult:
    s = settings or Settings()
    result = PlanResult()
    windows = working_windows(now, s)
    # Anything due before the end of the last planned day counts as "inside the horizon".
    horizon_end = datetime.combine(now.astimezone(TZ).date() + timedelta(days=s.days), time(0), TZ)
    blocked = ([(b.start - s.buffer, b.end + s.buffer) for b in busy]
               + [(a.start - s.buffer, a.end + s.buffer) for a in accepted]  # a break around booked blocks too
               + [(d.start, d.end) for d in dismissed])
    free = subtract(windows, blocked)

    for a in accepted:  # never moved; flagged when they now collide with something
        for b in busy:
            if a.end > now and _overlaps((a.start, a.end), (b.start, b.end)):
                result.conflicts.append(Conflict(a, b.label))
                break

    done = {}
    used: dict = {}  # planning day -> work already planned that day
    for a in accepted:
        done[a.key] = done.get(a.key, timedelta(0)) + (a.end - a.start)
        if a.end > now:
            used[s.day_of(a.start)] = used.get(s.day_of(a.start), timedelta(0)) + (a.end - a.start)

    for t in sorted(tasks, key=lambda t: (-t.priority, t.due)):
        if t.due <= now:
            continue  # overdue: nothing left to schedule before it
        need = timedelta(minutes=t.minutes) - done.get(t.key, timedelta(0))
        if need <= timedelta(0):
            continue
        needed = need
        target = t.due - s.finish_early
        start_by = t.due - s.lead(t.minutes)
        avoid = [(a.start - s.edit_gap, a.end + s.edit_gap) for a in accepted if a.key == t.key and a.edited]
        pool = subtract(free, avoid) if avoid else free  # _allocate uses up `pool`
        got_all: list[Block] = []
        # 1) inside the lead window, done ~12 h early; 2) inside it, up to the due time;
        # 3) only if still short and it's due within the plan: any earlier free time from now on.
        #    (Work due after the horizon waits — a later re-plan will reach its lead window.)
        passes = [(start_by, target), (start_by, t.due)] + ([(None, t.due)] if t.due <= horizon_end else [])
        mine: dict | None = None
        if t.per_day:  # spread from today up to the due time; the even share is a cap, lifted last if short
            mine = {}
            for a in accepted:
                if a.key == t.key and a.end > now:
                    mine[s.day_of(a.start)] = mine.get(s.day_of(a.start), timedelta(0)) + (a.end - a.start)
            passes = [(None, t.due), (None, t.due, False)]
        for not_before, deadline, *capped in passes:
            if need <= timedelta(0) or deadline <= now:
                continue
            got, need = _allocate(pool, t, need, deadline, s, not_before, used,
                                  mine if not capped or capped[0] else None)
            got_all += got
        if avoid and got_all:  # the shared free time loses what this task just took
            free[:] = subtract(free, [(b.start, b.end) for b in got_all])
        if got_all:  # a short break after each block before the next task's work
            free[:] = subtract(free, [(b.end, b.end + s.buffer) for b in got_all])
        result.blocks += got_all
        if need > timedelta(0) and t.due <= horizon_end:
            result.shortfalls.append(Shortfall(t.key, t.title, t.due, int(needed.total_seconds() // 60),
                                               int(need.total_seconds() // 60)))
    result.blocks += plan_goals(goals, free, used, s, now, accepted)
    result.blocks.sort(key=lambda b: b.start)
    return result


def plan_goals(goals: list[Goal], free: list[Interval], used: dict, s: Settings, now: datetime,
               accepted: list[Block] = ()) -> list[Block]:
    """Study sessions for goals, in whatever free time deadline work left (`free`, mutated):
    at most one session per goal per day, only once enough of the daily quota has built up
    (unplaced quota carries over), and never past the daily cap."""
    out: list[Block] = []
    if not goals:
        return out
    days = sorted({s.day_of(a) for a, _ in free})
    carry = {g.key: 0.0 for g in goals}
    for d in days:
        for g in goals:
            already = sum((a.end - a.start).total_seconds() / 60 for a in accepted
                          if a.key == g.key and s.day_of(a.start) == d)
            want = carry[g.key] + g.quota.get(d, 0) - already
            session = timedelta(minutes=g.session)
            if want < g.session * 0.6:  # not enough yet for a worthwhile session: save it up
                carry[g.key] = max(0.0, want)
                continue
            length = min(session, timedelta(minutes=round(want / 5) * 5) if want < g.session else session)
            placed = None
            for i, (ws, we) in enumerate(free):
                if s.day_of(ws) != d or we - ws < length:
                    continue
                room = s.max_per_day - used.get(d, timedelta(0))
                if room < length:
                    break
                placed = Block(g.key, ws, ws + length)
                rest = [p for p in ((_ceil(ws + length + s.buffer), we),) if p[1] - p[0] >= STEP]
                free[i:i + 1] = rest
                used[d] = used.get(d, timedelta(0)) + length
                break
            if placed:
                out.append(placed)
                carry[g.key] = max(0.0, want - placed.minutes)
            else:
                carry[g.key] = want
    return out
