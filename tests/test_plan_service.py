from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from aggregator import estimates, gcal, pages, plan_service
from aggregator.models import Item
from aggregator.sources import ics
from aggregator.sources.quest import ClassMeeting
from aggregator.store import Store

TZ = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 5, 9, tzinfo=TZ)  # Monday


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


def test_item_without_estimate_uses_type_default():
    [t] = plan_service.tasks_from([item("a", NOW + timedelta(days=2))], estimates.calibrate([]))
    assert t.minutes == estimates.DEFAULT_MINUTES["assignment"]
    reminder = item("r", NOW + timedelta(days=1), source="manual", type="reminder")
    assert plan_service.tasks_from([reminder], estimates.calibrate([])) == []  # not a unit of work


def test_busy_comes_from_classes_and_timed_events():
    store = Store(":memory:")
    store.replace_classes("Fall 2026", [ClassMeeting(course="CS 135", title="", component="LEC", section="1",
                                                     class_nbr="1", days=[0, 2], start=time(11, 30), end=time(12, 50))])
    meeting = item("m", NOW + timedelta(hours=5), source="outlook", type="meeting", title="Advisor",
                   extra={"end": (NOW + timedelta(hours=6)).isoformat()})
    busy = plan_service.busy_from(store, [meeting], NOW, plan_service.load_settings(store))
    labels = sorted(b.label for b in busy)
    assert labels.count("CS 135 LEC") == 2 and "Advisor" in labels  # Mon + Wed within Mon–Sun


def test_ics_keeps_the_end_of_timed_events_only():
    data = (b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x1\r\nSUMMARY:Office hours\r\n"
            b"DTSTART:20261006T180000Z\r\nDTEND:20261006T190000Z\r\nEND:VEVENT\r\n"
            b"BEGIN:VEVENT\r\nUID:x2\r\nSUMMARY:A02 - Due\r\nDTSTART:20261006T010000Z\r\nDTEND:20261006T010000Z\r\nEND:VEVENT\r\n"
            b"END:VCALENDAR\r\n")
    items = {i.title: i for i in ics.parse_feed(data, "outlook", now=datetime(2026, 10, 1, tzinfo=timezone.utc))}
    assert items["Office hours"].extra["end"] == "2026-10-06T19:00:00+00:00"
    assert "end" not in items["A02 - Due"].extra  # a deadline is a point in time


def test_replan_accept_done_and_calendar_mirror():
    store = Store(":memory:")
    a = item("a", NOW + timedelta(days=3), title="A03")
    store.sync_source("d2l", [a])
    result = plan_service.replan(store, NOW)
    assert result.blocks and not result.shortfalls
    first = store.plan_blocks(("suggested",))[0]
    assert plan_service.accept(store, first.id)
    plan_service.replan(store, NOW)  # re-plan keeps the accepted block where it is
    assert [b.id for b in store.plan_blocks(("accepted",))] == [first.id]

    cal = FakeCalendar()
    gcal.push(store, cal, NOW)
    work = cal.events[first.id]
    assert work["summary"] == "Work: A03" and work["start"]["dateTime"] == first.start.isoformat()
    assert a.id in cal.events  # the deadline itself is still there, separately

    store.set_status(a.id, "done")  # done → its remaining blocks and their events go
    gcal.push(store, cal, NOW)
    assert first.id not in cal.events and store.plan_blocks(("accepted", "suggested")) == []


def test_dismiss_and_accept_day():
    store = Store(":memory:")
    store.sync_source("d2l", [item("a", NOW + timedelta(days=1, hours=12)), item("b", NOW + timedelta(days=2))])
    plan_service.replan(store, NOW)
    sug = store.plan_blocks(("suggested",))
    assert plan_service.dismiss(store, sug[0].id)
    plan_service.replan(store, NOW)
    assert all(b.start != sug[0].start for b in store.plan_blocks(("suggested",)))  # that slot isn't reused
    day0 = datetime(2026, 10, 5, tzinfo=TZ)
    n = plan_service.accept_day(store, day0)
    today = [b for b in store.plan_blocks(("accepted",)) if day0 <= b.start < day0 + timedelta(days=1)]
    assert n == len(today) > 0


def test_settings_validation_and_roundtrip():
    store = Store(":memory:")
    plan_service.save_settings(store, "08:30", "21:00", 1.5, 5)
    s = plan_service.load_settings(store)
    assert (s.wake, s.sleep, s.max_block, s.days) == (time(8, 30), time(21), timedelta(minutes=90), 5)
    plan_service.save_settings(store, "10:00", "01:30", 2, 7)  # bedtime after midnight is fine
    assert plan_service.load_settings(store).overnight
    for bad in (("09:00", "09:00", 2, 7), ("09:00", "11:00", 2, 7), ("09:00", "22:00", 20, 7)):
        with pytest.raises(ValueError):
            plan_service.save_settings(store, *bad)


def test_pages_show_the_plan_and_shortfalls():
    store = Store(":memory:")
    now = datetime.now(timezone.utc)
    store.sync_source("d2l", [item("big", now + timedelta(hours=3), title="Huge project", estimated_duration=None)])
    store.set_estimated_duration(Item(source="d2l", source_key="big", type="assignment", title="x").id, 600)
    html = pages.render_plan(store)
    assert "Work plan" in html and "Huge project" in html and "short" in html
    assert pages.fmt_short(45) == "about 45 min" and pages.fmt_short(95) == "about 1.5 h"


def test_grid_times_convert_on_the_wall_clock_even_on_dst_day():
    d = datetime(2026, 11, 1).date()  # clocks fall back at 2 AM
    start, end = plan_service.local_span(d, 9 * 60, 10 * 60 + 30)
    assert start == datetime(2026, 11, 1, 9, tzinfo=TZ) and end - start == timedelta(minutes=90)
    assert start.utcoffset() == timedelta(hours=-5)
    late = plan_service.parse_times("2026-10-05", "23:00", "01:00")  # ends after midnight
    assert late[1] - late[0] == timedelta(hours=2) and late[1].date().day == 6
    with pytest.raises(ValueError):
        plan_service.parse_times("2026-10-05", "10:00", "10:02")


def test_resizing_a_block_makes_it_yours_and_keeps_its_id():
    store = Store(":memory:")
    a = item("a", NOW + timedelta(days=2), title="A03", estimated_duration=180)
    store.sync_source("d2l", [a])
    plan_service.replan(store, NOW)
    b = store.plan_blocks(("suggested",))[0]
    assert plan_service.edit_block(store, b.id, b.start, b.start + timedelta(minutes=30), now=NOW)  # shrink to 30 min
    [mine] = store.plan_blocks(("accepted",))
    assert mine.id == b.id and mine.minutes == 30
    plan_service.replan(store, NOW)
    rest = store.plan_blocks(("suggested",))
    assert sum(x.minutes for x in rest) == 150  # the rest is still suggested…
    gap = timedelta(hours=1)
    assert all(x.start >= mine.end + gap or x.end <= mine.start - gap for x in rest)  # …but not glued to it
    assert [x.id for x in store.plan_blocks(("accepted",))] == [b.id]  # never moved by the planner


def test_own_events_are_busy_editable_and_on_the_calendar():
    store = Store(":memory:")
    s0, e0 = NOW + timedelta(hours=1), NOW + timedelta(hours=3)
    ev = plan_service.add_event(store, "  Gym  ", s0, e0)
    [row] = store.plan_events()
    assert row["title"] == "Gym" and row["id"] == ev
    busy = plan_service.busy_from(store, [], NOW, plan_service.load_settings(store))
    assert any(b.label == "Gym" for b in busy)
    store.sync_source("d2l", [item("a", NOW + timedelta(hours=8), title="A03", estimated_duration=60)])
    plan_service.replan(store, NOW)
    assert all(not (b.start < e0 and s0 < b.end) for b in store.plan_blocks(("suggested",)))  # planned around it
    cal = FakeCalendar()
    gcal.push(store, cal, NOW)
    assert cal.events[ev]["summary"] == "Gym"
    assert plan_service.edit_event(store, ev, s0, e0 + timedelta(hours=1), "Gym + swim")
    gcal.push(store, cal, NOW)
    assert cal.events[ev]["summary"] == "Gym + swim"
    assert plan_service.delete_event(store, ev)
    gcal.push(store, cal, NOW)
    assert ev not in cal.events
    with pytest.raises(ValueError):
        plan_service.add_event(store, "   ", s0, e0)


def test_push_never_holds_the_lock_while_talking_to_google():
    import threading
    store = Store(":memory:")
    store.sync_source("d2l", [item("a", NOW + timedelta(days=1), title="A03")])
    lock = threading.RLock()
    seen = []

    class SlowCalendar(FakeCalendar):
        def upsert_event(self, event_id, body):
            t = threading.Thread(target=lambda: seen.append(lock.acquire(timeout=1) and not lock.release()))
            t.start()
            t.join()
            super().upsert_event(event_id, body)

    gcal.push(store, SlowCalendar(), NOW, lock=lock)
    assert seen and all(seen)  # another thread (a page) could take the lock mid-push


def test_default_style_is_proactive_and_can_be_changed():
    store = Store(":memory:")
    s = plan_service.load_settings(store)
    assert plan_service.style_of(store) == "proactive"
    assert s.finish_early == timedelta(hours=24) and s.min_lead == timedelta(days=3)
    plan_service.save_settings(store, "08:30", "22:30", 2, 7, "relaxed")
    assert plan_service.load_settings(store).finish_early == timedelta(hours=6)
    plan_service.save_settings(store, "08:30", "22:30", 2, 7)  # style kept when not given
    assert plan_service.style_of(store) == "relaxed"
    with pytest.raises(ValueError):
        plan_service.save_settings(store, "08:30", "22:30", 2, 7, "yolo")


def test_free_now_suggests_the_most_important_work_and_start_now_books_it():
    store = Store(":memory:")
    now = datetime(2026, 10, 5, 14, 2, tzinfo=TZ)  # Monday afternoon, nothing on
    store.sync_source("d2l", [item("a", now + timedelta(days=2), title="A03", grade_weight=10),
                              item("b", now + timedelta(days=3), title="Reading", grade_weight=1)])
    tip = plan_service.free_now(store, now)
    assert tip["kind"] == "free" and tip["item"].title == "A03"  # highest priority first
    assert 25 <= tip["minutes"] <= 120
    block_id = plan_service.start_now(store, tip["item"].id, now)
    b = store.get_block(block_id)
    assert b.state == "accepted" and b.start >= now and b.start - now < timedelta(minutes=10)
    after = plan_service.free_now(store, now)
    assert after["kind"] == "block" and after["block"].id == block_id  # now it's "your block is on"


def test_free_now_stays_quiet_when_busy_or_asleep():
    store = Store(":memory:")
    store.sync_source("d2l", [item("a", NOW + timedelta(days=2), title="A03")])
    asleep = datetime(2026, 10, 5, 3, 0, tzinfo=TZ)
    assert plan_service.free_now(store, asleep) is None
    now = datetime(2026, 10, 5, 14, 0, tzinfo=TZ)
    plan_service.add_event(store, "Lab", now - timedelta(minutes=30), now + timedelta(hours=3))
    assert plan_service.free_now(store, now) is None
