from datetime import datetime, timedelta, timezone

import pytest

from aggregator import reminders
from aggregator.models import Item
from aggregator.store import Store

LOCAL = reminders.LOCAL_TZ
NOW = datetime(2026, 9, 23, 20, 0, tzinfo=LOCAL)  # Wednesday evening


def rem(title, due=None, status="not_started"):
    return Item(source="manual", source_key=f"t:{title}", type="reminder", title=title,
                due_date=due, status=status)


@pytest.mark.parametrize("due,expected", [
    (None, "No set date"),
    (datetime(2026, 9, 23, 9, 0, tzinfo=LOCAL), "Overdue"),
    (datetime(2026, 9, 23, 23, 59, tzinfo=LOCAL), "Today"),
    (datetime(2026, 9, 24, 8, 0, tzinfo=LOCAL), "Tomorrow"),
    (datetime(2026, 9, 27, 23, 59, tzinfo=LOCAL), "This week"),   # Sunday
    (datetime(2026, 9, 28, 21, 0, tzinfo=LOCAL), "Next week"),    # Monday
    (datetime(2026, 10, 4, 12, 0, tzinfo=LOCAL), "Next week"),    # next Sunday
    (datetime(2026, 10, 5, 23, 59, tzinfo=LOCAL), "Later"),
])
def test_horizon(due, expected):
    assert reminders.horizon(due, NOW) == expected


def test_relative_phrasing():
    assert reminders.relative(NOW + timedelta(minutes=20), NOW) == "in 20 min"
    assert reminders.relative(NOW + timedelta(hours=1), NOW) == "in 1 hour"
    assert reminders.relative(NOW + timedelta(days=2, hours=3), NOW) == "in 2 days"
    assert reminders.relative(NOW - timedelta(days=1), NOW) == "1 day ago"


def test_group_orders_naturally():
    items = [rem("b later", NOW + timedelta(days=30)), rem("zebra"), rem("apple"),
             rem("soon", NOW + timedelta(hours=2)), rem("sooner", NOW + timedelta(hours=1)),
             rem("finished", NOW + timedelta(days=1), status="done")]
    groups, done = reminders.group(items, NOW)
    assert [i.title for i in groups["Today"]] == ["sooner", "soon"]
    assert [i.title for i in groups["No set date"]] == ["apple", "zebra"]
    assert [i.title for i in done] == ["finished"]


def test_ongoing_habits_sit_apart_from_undated_tasks():
    habit = Item(source="manual", source_key="h", type="reminder", title="Bring MathMatize device",
                 extra={"ongoing": True})
    groups, _ = reminders.group([habit, rem("Check W2 due date")], NOW)
    assert [i.title for i in groups["Keep in mind"]] == ["Bring MathMatize device"]
    assert [i.title for i in groups["No set date"]] == ["Check W2 due date"]
    assert list(groups)[-2:] == ["No set date", "Keep in mind"]  # habits come last


def test_make_due():
    assert reminders.make_due("", "") is None
    assert reminders.make_due("2026-09-25", "") == datetime(2026, 9, 25, 23, 59, tzinfo=LOCAL)
    assert reminders.make_due("2026-09-25", "18:00") == datetime(2026, 9, 25, 18, 0, tzinfo=LOCAL)
    with pytest.raises(ValueError):
        reminders.make_due("", "18:00")


def test_upsert_manual_survives_source_syncs():
    store = Store(":memory:")
    r = rem("Club waiver", NOW + timedelta(days=2))
    store.upsert_manual(r)
    store.sync_source("d2l", [])  # a source sync must never touch manual items
    got = store.get_item(r.id)
    assert got is not None and got.title == "Club waiver"
    store.set_status(r.id, "done")
    store.upsert_manual(Item(**{**r.__dict__, "title": "Club waiver (edited)"}))
    got = store.get_item(r.id)
    assert (got.title, got.status) == ("Club waiver (edited)", "done")  # status kept
    with pytest.raises(ValueError):
        store.upsert_manual(Item(source="d2l", source_key="x", type="reminder", title="nope"))
