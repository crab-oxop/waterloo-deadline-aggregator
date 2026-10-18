from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from aggregator import gcal, pages, plan_service, planner
from aggregator.models import Item
from aggregator.planner import Block, Goal, Settings, Task
from aggregator.store import Store

TZ = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 5, 9, tzinfo=TZ)  # Monday 9:00


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def item(key, due, **kw):
    return Item(source=kw.pop("source", "d2l"), source_key=key, type=kw.pop("type", "assignment"),
                title=kw.pop("title", key), due_date=due, **kw)


class FakeCalendar:
    def __init__(self):
        self.events = {}

    def upsert_event(self, event_id, body):
        self.events[event_id] = body

    def delete_event(self, event_id):
        self.events.pop(event_id, None)


# --- planner (pure) --------------------------------------------------------------------

def test_daily_cap_spreads_work_over_days():
    s = Settings(max_per_day=timedelta(hours=2))
    r = planner.plan([Task("a", "A", at(2026, 10, 9, 21), 360, 1.0)], [], NOW, s)
    per_day: dict = {}
    for b in r.blocks:
        per_day[s.day_of(b.start)] = per_day.get(s.day_of(b.start), 0) + b.minutes
    assert per_day and max(per_day.values()) <= 120 and sum(per_day.values()) == 360


def test_goal_sessions_fill_free_time_once_per_day_after_deadline_work():
    s = Settings(days=3)
    days = [date(2026, 10, 5) + timedelta(days=n) for n in range(3)]
    g = Goal("g", "Review MATH 137", {d: 45 for d in days}, session=45, context="MATH 137")
    r = planner.plan([Task("a", "A03", at(2026, 10, 6, 21), 120, 5.0)], [], NOW, s, goals=[g])
    sessions = [b for b in r.blocks if b.key == "g"]
    assert len(sessions) == 3 and all(b.minutes == 45 for b in sessions)
    assert len({s.day_of(b.start) for b in sessions}) == 3  # one per day
    work = [b for b in r.blocks if b.key == "a"]
    assert sum(b.minutes for b in work) == 120  # deadline work still fully planned


def test_small_goal_quota_saves_up_into_a_real_session():
    s = Settings(days=7)
    days = [date(2026, 10, 5) + timedelta(days=n) for n in range(7)]
    g = Goal("g", "Read ahead", {d: 15 for d in days}, session=45)  # ~1.75 h/week
    sessions = [b for b in planner.plan([], [], NOW, s, goals=[g]).blocks]
    assert sessions and all(b.minutes >= 27 for b in sessions)  # never 15-minute crumbs
    assert len(sessions) < 7


def test_day_of_follows_wake_time():
    s = Settings(wake=time(10), sleep=time(1, 30))
    assert s.day_of(at(2026, 10, 6, 0, 30)) == date(2026, 10, 5)  # 12:30 AM = the night before
    assert s.day_of(at(2026, 10, 6, 11)) == date(2026, 10, 6)


# --- goals in the app --------------------------------------------------------------------

def test_goal_crud_validation_and_planning():
    store = Store(":memory:")
    gid = plan_service.add_goal(store, "  Review MATH 137 ", "MATH 137", "weekly", 3, None, 45)
    [g] = store.goals()
    assert (g["title"], g["kind"], g["hours"], g["session_min"]) == ("Review MATH 137", "weekly", 3, 45)
    plan_service.replan(store, NOW)
    sessions = [b for b in store.plan_blocks(("suggested",)) if b.key == gid]
    assert sessions  # the planner made time for it
    store.set_goal_active(gid, False)
    plan_service.replan(store, NOW)
    assert not [b for b in store.plan_blocks(("suggested",)) if b.key == gid]  # paused: no sessions
    for bad in (("", None, "weekly", 3, None, 45), ("x", None, "weekly", 0, None, 45),
                ("x", None, "by_date", 3, None, 45), ("x", None, "monthly", 3, None, 45),
                ("x", None, "weekly", 3, None, 5)):
        with pytest.raises(ValueError):
            plan_service.add_goal(store, *bad)
    assert store.delete_goal(gid) and store.goals() == []


def test_exam_goal_replaces_the_default_study_estimate():
    store = Store(":memory:")
    exam = item("mid", at(2026, 10, 8, 19), type="exam", title="PHYS 121 Midterm 1", context="PHYS 121")
    store.sync_source("d2l", [exam])
    [sug] = plan_service.suggested_goals(store, NOW)
    assert sug["item"].id == exam.id and sug["hours"] == 6
    gid = plan_service.add_goal(store, sug["title"], "PHYS 121", "by_date", 6, date(2026, 10, 8), 60, exam.id)
    assert plan_service.suggested_goals(store, NOW) == []
    plan_service.replan(store, NOW)
    keys = {b.key for b in store.plan_blocks(("suggested",))}
    assert gid in keys and exam.id not in keys  # studied through the goal, not twice
    prog = plan_service.goal_progress(store, store.goals()[0], NOW)
    assert prog["target"] == 360


def test_study_blocks_are_labelled_and_mirrored():
    store = Store(":memory:")
    gid = plan_service.add_goal(store, "Practice CS 135", "CS 135", "weekly", 3, None, 45)
    plan_service.replan(store, NOW)
    b = next(b for b in store.plan_blocks(("suggested",)) if b.key == gid)
    plan_service.accept(store, b.id)
    cal = FakeCalendar()
    gcal.push(store, cal, NOW)
    assert cal.events[b.id]["summary"] == "Study: [CS 135] Practice CS 135"
    subj = plan_service.subjects(store)[gid]
    assert pages.block_label(subj) == "Study: Practice CS 135"


def test_plan_my_day_accepts_fills_caps_and_undoes():
    store = Store(":memory:")
    plan_service.save_settings(store, "08:30", "22:30", 2, 7, "relaxed", 4)  # 4 h/day cap
    store.sync_source("d2l", [item("a", at(2026, 10, 9, 21), title="A03", estimated_duration=300),
                              item("b", at(2026, 10, 12, 21), title="Lab report", estimated_duration=240)])
    r = plan_service.plan_day(store, NOW)
    today = [b for b in store.plan_blocks(("accepted",)) if b.start.date() == NOW.date()]
    assert r["blocks"] > 0 and today
    assert sum(b.minutes for b in today) <= 240  # the daily cap holds
    assert all(at(2026, 10, 5, 9) <= b.start and b.end <= at(2026, 10, 5, 22) for b in today)
    starts = sorted(today, key=lambda b: b.start)
    assert all(nxt.start >= cur.end for cur, nxt in zip(starts, starts[1:]))  # no overlaps
    assert plan_service.day_plan_active(store, NOW)
    assert plan_service.undo_day(store, NOW) == r["blocks"]
    assert not [b for b in store.plan_blocks(("accepted",)) if b.start.date() == NOW.date()]


def test_goals_page_renders():
    store = Store(":memory:")
    store.sync_source("d2l", [item("q", datetime.now(TZ) + timedelta(days=5), type="quiz", title="MATH 137 Quiz 4",
                                   context="MATH 137")])
    plan_service.add_goal(store, "Review lectures", "MATH 137", "weekly", 2, None, 30)
    html = pages.render_goals(store)
    assert "Review lectures" in html and "Study for MATH 137 Quiz 4" in html and "New goal" in html


def test_an_empty_read_never_wipes_a_source():
    from aggregator.store import EmptySync
    store = Store(":memory:")
    store.sync_source("d2l", [item("a", NOW + timedelta(days=2), title="A03")])
    with pytest.raises(EmptySync, match="kept the 1 item"):
        store.sync_source("d2l", [])
    assert [i.title for i in store.list_items()] == ["A03"]
    store.sync_source("d2l", [], allow_empty=True)  # only when empty truly means empty
    assert store.list_items() == []


def test_failing_google_push_shows_as_needing_sign_in(monkeypatch):
    from aggregator import login
    store = Store(":memory:")
    monkeypatch.setattr(login.gcal, "is_configured", lambda: True)
    assert login._gcal_check(store).state == "ok"
    store.record_failure("gcal", "RefreshError: ('invalid_grant: Token has been expired or revoked.')")
    c = login._gcal_check(store)
    assert (c.state, c.action) == ("needs_login", "google")
    store.mark_ok("gcal")
    assert login._gcal_check(store).state == "ok"


def test_every_block_is_followed_by_a_break():
    from aggregator import planner as P
    r = P.plan([Task("a", "A", at(2026, 10, 6, 21), 120, 3.0), Task("b", "B", at(2026, 10, 6, 21), 120, 2.0),
                Task("c", "C", at(2026, 10, 6, 21), 60, 1.0)], [], NOW, Settings(days=2))
    blocks = sorted(r.blocks, key=lambda b: b.start)
    assert all(nxt.start - cur.end >= timedelta(minutes=15) for cur, nxt in zip(blocks, blocks[1:])
               if cur.start.date() == nxt.start.date())


def test_goal_due_today_gets_time_today_before_later_work():
    store = Store(":memory:")
    store.sync_source("d2l", [item("a", at(2026, 10, 7, 21), title="A03", estimated_duration=240, grade_weight=5),
                              item("b", at(2026, 10, 8, 21), title="Lab", estimated_duration=240, grade_weight=5)])
    gid = plan_service.add_goal(store, "Complete Warg onboarding", None, "by_date", 4, date(2026, 10, 5), 120)
    plan_service.replan(store, NOW)
    mine = [b for b in store.plan_blocks(("suggested",)) if b.key == gid]
    assert sum(b.minutes for b in mine) == 240  # all of it, today
    assert all(b.start.date() == NOW.date() for b in mine)
    assert all(b.minutes <= 120 for b in mine)  # in sessions of the goal's length


def test_plan_my_day_books_goal_time_before_assignments_fill_the_day():
    store = Store(":memory:")
    store.sync_source("d2l", [item("a", at(2026, 10, 6, 21), title="A03", estimated_duration=480, grade_weight=10),
                              item("b", at(2026, 10, 7, 21), title="Lab", estimated_duration=480, grade_weight=10)])
    gid = plan_service.add_goal(store, "Review MATH 137", "MATH 137", "weekly", 4, None, 60)
    plan_service.plan_day(store, NOW)
    today = [b for b in store.plan_blocks(("accepted",)) if b.start.date() == NOW.date()]
    assert any(b.key == gid for b in today)  # goal time made it in
    assert sum(b.minutes for b in today) <= 360  # still under the daily cap


def test_weekly_goal_catches_up_over_the_rest_of_the_week():
    store = Store(":memory:")
    plan_service.add_goal(store, "Practice", None, "weekly", 3, None, 45)
    thursday = at(2026, 10, 8, 9)
    [g] = plan_service.goal_tasks(store, thursday, plan_service.load_settings(store))
    assert round(g.quota[date(2026, 10, 8)]) == 45  # 180 min owed over Thu-Sun
    assert round(g.quota[date(2026, 10, 12)]) == round(180 / 7)  # next week: an even share


def test_a_goal_can_be_placed_in_two_sittings_by_hand():
    store = Store(":memory:")
    gid = plan_service.add_goal(store, "Warg onboarding", None, "by_date", 2, date(2026, 10, 5), 120)
    a = plan_service.add_block_for(store, gid, at(2026, 10, 5, 13), at(2026, 10, 5, 13, 30), now=NOW)
    b = plan_service.add_block_for(store, gid, at(2026, 10, 5, 19), at(2026, 10, 5, 19, 30), now=NOW)
    mine = [x for x in store.plan_blocks(("accepted",)) if x.key == gid]
    assert {x.id for x in mine} == {a, b} and all(x.edited for x in mine)
    with pytest.raises(ValueError):  # not twice in the same spot
        plan_service.add_block_for(store, gid, at(2026, 10, 5, 13, 15), at(2026, 10, 5, 13, 45), now=NOW)
    with pytest.raises(ValueError):
        plan_service.add_block_for(store, "f" * 32, at(2026, 10, 5, 9), at(2026, 10, 5, 9, 30), now=NOW)
    plan_service.replan(store, NOW)
    rest = [x for x in store.plan_blocks(("suggested",)) if x.key == gid]
    assert sum(x.minutes for x in rest) == 60  # 2 h goal - 2 x 30 min placed by hand


def test_dropping_time_shrinks_a_shortfall():
    store = Store(":memory:")
    a = item("a", at(2026, 10, 5, 12), title="A03", estimated_duration=240)
    store.sync_source("d2l", [a])
    before = plan_service.replan(store, NOW)
    [sf] = before.shortfalls
    plan_service.add_block_for(store, a.id, at(2026, 10, 5, 7), at(2026, 10, 5, 8), now=at(2026, 10, 5, 6))  # an early hour, placed by hand at 6 AM
    after = plan_service.replan(store, NOW)
    assert (after.shortfalls[0].short if after.shortfalls else 0) < sf.short  # the hand-placed hour counts
    assert after.shortfalls[0].needed == sf.needed - 60


def test_time_that_has_passed_is_refused():
    store = Store(":memory:")
    gid = plan_service.add_goal(store, "Warg", None, "weekly", 2, None, 30)
    past = datetime.now(TZ) - timedelta(hours=2)
    with pytest.raises(ValueError, match="already passed"):
        plan_service.add_block_for(store, gid, past, past + timedelta(minutes=30))
    running = datetime.now(TZ) - timedelta(minutes=10)  # started already but still going: fine
    assert plan_service.add_block_for(store, gid, running, running + timedelta(minutes=30))
