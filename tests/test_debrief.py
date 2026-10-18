from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from aggregator import debrief, exam_prep, pages, plan_service
from aggregator.models import Item
from aggregator.store import Store

TZ = ZoneInfo("America/Toronto")


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def sitting(key, start, minutes=110, course="MATH 137", title=None):
    return Item(source="odyssey", source_key=key, type="exam", title=title or f"{course} Midterm", context=course,
                due_date=start, extra={"end": (start + timedelta(minutes=minutes)).isoformat(), "location": "PAC 1"})


def test_asks_once_the_test_has_ended_and_only_then():
    store = Store(":memory:")
    t = sitting("m", at(2026, 10, 14, 19))
    store.sync_source("odyssey", [t])
    assert debrief.pending(store, at(2026, 10, 14, 20)) == []  # still writing it
    assert [i.id for i in debrief.pending(store, at(2026, 10, 14, 21))] == [t.id]  # 8:50 PM: over
    assert debrief.next_end(store, at(2026, 10, 14, 12)) == at(2026, 10, 14, 20, 50)
    assert debrief.scheduled_minutes(store.get_item(t.id)) == 110
    assert debrief.pending(store, at(2026, 10, 23, 9)) == []  # more than a week later: not asked


def test_save_later_skip():
    store = Store(":memory:")
    a, b = sitting("a", at(2026, 10, 14, 19)), sitting("b", at(2026, 10, 15, 9), course="CS 135")
    store.sync_source("odyssey", [a, b])
    now = at(2026, 10, 15, 20)
    debrief.save(store, store.get_item(a.id), "ok, ran short on Q4", "do more past exams", "", "95")
    debrief.later(store, store.get_item(b.id), now)
    assert debrief.pending(store, now) == []
    assert [i.id for i in debrief.pending(store, now + timedelta(hours=5))] == [b.id]  # asked again later
    debrief.skip(store, store.get_item(b.id))
    assert debrief.pending(store, now + timedelta(hours=5)) == []
    [d] = store.debriefs()  # skipped ones aren't listed
    assert (d["went"], d["improve"], d["minutes"]) == ("ok, ran short on Q4", "do more past exams", 95)
    assert debrief.last_improvement(store, "MATH 137") == "do more past exams"
    with pytest.raises(ValueError):
        debrief.save(store, store.get_item(a.id), "", "", "", "lots")


def test_learn_quizzes_without_a_sitting_are_not_asked_about():
    store = Store(":memory:")
    store.sync_source("d2l", [Item(source="d2l", source_key="q", type="quiz", title="Quiz 3", due_date=at(2026, 10, 9, 23, 59))])
    assert debrief.pending(store, at(2026, 10, 10, 9)) == []


def test_dialog_shows_on_pages_and_a_page_waits_for_the_next_test(monkeypatch):
    store = Store(":memory:")
    t = sitting("m", at(2026, 10, 14, 19))
    store.sync_source("odyssey", [t])
    monkeypatch.setattr(pages, "now_utc", lambda: at(2026, 10, 14, 12))
    html = pages.debrief_dialog(store, "/today")
    assert "<dialog" not in html and "location.reload" in html  # reloads itself once the test ends
    monkeypatch.setattr(pages, "now_utc", lambda: at(2026, 10, 15, 8))
    html = pages.debrief_dialog(store, "/today")
    assert "How did MATH 137 Midterm go?" in html and 'name="minutes"' in html and 'value="110"' in html
    assert "showModal" in html


def test_out_of_date_goals_are_removed():
    store = Store(":memory:")
    old = plan_service.add_goal(store, "Warg onboarding", None, "by_date", 2, date(2026, 10, 4), 30)
    keep = plan_service.add_goal(store, "Lab prep", None, "by_date", 2, date(2026, 10, 6), 30)
    weekly = plan_service.add_goal(store, "Review", None, "weekly", 2, None, 30)
    store.set_goal_active(old, False)  # paused ones go too
    exam_prep.follow_exams(store, at(2026, 10, 5, 9))
    assert {g["id"] for g in store.goals()} == {keep, weekly}
