from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aggregator import exam_prep, plan_service
from aggregator.models import Item
from aggregator.store import Store

TZ = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 5, 9, tzinfo=TZ)  # Monday 9:00


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def exam(key, due, title=None, course="MATH 137", **kw):
    return Item(source="odyssey", source_key=key, type=kw.pop("type", "exam"), title=title or f"{course} Midterm",
                context=course, due_date=due, **kw)


def test_kind_from_title_then_date():
    assert exam_prep.exam_kind(exam("a", at(2026, 10, 20, 19), "MATH 137 Midterm 1")) == "midterm"
    assert exam_prep.exam_kind(exam("b", at(2026, 12, 12, 9), "MATH 137 Final Exam")) == "final"
    assert exam_prep.exam_kind(exam("c", at(2026, 12, 12, 9), "MATH 137")) == "final"  # in the December exam period
    assert exam_prep.exam_kind(exam("d", at(2026, 10, 20, 19), "MATH 137 Test 2")) == "midterm"  # unsure → midterm
    assert exam_prep.exam_kind(exam("e", at(2026, 10, 20, 19), "PHYS 121 Quiz 3", type="quiz")) == "quiz"


def test_suggestion_inside_the_window_only_and_skips_unscheduled():
    store = Store(":memory:")
    store.sync_source("odyssey", [exam("near", at(2026, 10, 15, 19)), exam("far", at(2026, 10, 25, 19), course="CS 135"),
                                  exam("tba", None, "PHYS 121 Final", course="PHYS 121")])
    [s] = exam_prep.suggestions(store, NOW)
    assert s["item"].source_key == "near" and s["kind"] == "midterm" and s["hours"] == 8 and s["days"] == 10
    assert len(exam_prep.suggestions(store, NOW + timedelta(days=7))) == 2  # the far one comes into range


def test_added_or_dismissed_is_not_offered_again_until_the_date_changes():
    store = Store(":memory:")
    a, b = exam("a", at(2026, 10, 15, 19)), exam("b", at(2026, 10, 16, 19), course="CS 135")
    store.sync_source("odyssey", [a, b])
    gid = exam_prep.add_goal(store, a)
    g = store.goals()[0]
    assert (g["id"], g["hours"], g["until"], g["item_id"]) == (gid, 8, date(2026, 10, 15), a.id)
    assert g["title"] == "Prepare for MATH 137 midterm"
    exam_prep.dismiss(store, b)
    assert exam_prep.suggestions(store, NOW) == []
    store.set_goal_active(gid, False)  # a paused goal still counts as handled
    assert exam_prep.suggestions(store, NOW) == []
    moved = exam("b", at(2026, 10, 17, 19), course="CS 135")
    store.sync_source("odyssey", [a, moved])
    [s] = exam_prep.suggestions(store, NOW)  # moved: asked again
    assert s["item"].id == b.id


def test_goal_follows_a_moved_exam_and_stops_once_it_has_passed():
    store = Store(":memory:")
    a = exam("a", at(2026, 10, 15, 19))
    store.sync_source("odyssey", [a])
    gid = exam_prep.add_goal(store, a, hours=5)
    store.sync_source("odyssey", [exam("a", at(2026, 10, 13, 9, 30))])
    plan_service.replan(store, NOW)
    g = store.goals()[0]
    assert g["until"] == date(2026, 10, 13) and g["hours"] == 5 and g["active"]
    # study is due before the exam starts, not at the end of that day
    assert plan_service.subjects(store)[gid].due_date == at(2026, 10, 13, 9, 30)
    assert all(b.end <= at(2026, 10, 13, 9, 30) for b in store.plan_blocks(("suggested",)) if b.key == gid)
    plan_service.replan(store, at(2026, 10, 13, 12))
    assert store.goals() == []  # out of date: removed


def test_countdown_counts_focused_minutes_on_the_goal():
    store = Store(":memory:")
    a = exam("a", at(2026, 10, 14, 19))
    store.sync_source("odyssey", [a, exam("later", at(2026, 10, 30, 19), course="CS 135")])
    c = exam_prep.countdown(store, NOW)
    assert (c["name"], c["when"], c["studied"]) == ("MATH 137 midterm", "in 9 days", None)
    gid = exam_prep.add_goal(store, a)
    start = datetime.now(TZ) - timedelta(hours=2)
    store.add_focus("s1", None, gid, "", 90, start)
    store.update_focus("s1", ended_at=start + timedelta(minutes=90))
    c = exam_prep.countdown(store, NOW)
    assert (c["studied"], c["target"]) == (90, 480)
    assert exam_prep.until_text(at(2026, 10, 6, 10), NOW) == "tomorrow"


def test_settings_validate():
    store = Store(":memory:")
    s = exam_prep.save_settings(store, 10, 3, 9, 20)
    assert exam_prep.load_settings(store).hours("final") == 20 and s.window == 10
    for bad in ((0, 2, 8, 15), (14, 2, 0, 15), (14, 2, 8, 200)):
        try:
            exam_prep.save_settings(store, *bad)
            raise AssertionError(bad)
        except ValueError:
            pass


def test_learn_exam_notices_are_not_offered():
    store = Store(":memory:")
    store.sync_source("d2l", [Item(source="d2l", source_key="n", type="exam", title="Final examinations begin",
                                   due_date=at(2026, 10, 9, 23, 59))])
    assert exam_prep.suggestions(store, NOW) == [] and exam_prep.countdown(store, NOW) is None


def test_exam_goal_time_is_spread_over_the_days_before():
    store = Store(":memory:")
    a = exam("a", at(2026, 10, 12, 19))  # 7 days out
    store.sync_source("odyssey", [a])
    gid = exam_prep.add_goal(store, a, hours=6)
    plan_service.replan(store, NOW)
    per_day: dict = {}
    for b in store.plan_blocks(("suggested",)):
        if b.key == gid:
            d = b.start.astimezone(TZ).date()
            per_day[d] = per_day.get(d, 0) + b.minutes
    assert sum(per_day.values()) == 360 and len(per_day) >= 5  # not crammed into a day or two
    assert max(per_day.values()) <= 60
