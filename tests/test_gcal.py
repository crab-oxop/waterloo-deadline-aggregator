import re
from datetime import datetime, timedelta, timezone

import pytest

from aggregator import gcal
from aggregator.models import Item
from aggregator.store import Store

NOW = datetime.now(timezone.utc)


class FakeCalendar:
    def __init__(self):
        self.events: dict[str, dict] = {}
        self.calls: list[str] = []

    def upsert_event(self, event_id, body):
        self.calls.append("upsert")
        self.events[event_id] = body

    def delete_event(self, event_id):
        self.calls.append("delete")
        self.events.pop(event_id, None)


def item(key, days, **kw):
    return Item(source="d2l", source_key=key, type="assignment", title=f"Item {key}",
                context="CS 135", due_date=NOW + timedelta(days=days), **kw)


@pytest.fixture
def store():
    return Store(":memory:")


def test_push_is_idempotent_and_mirrors_changes(store):
    cal = FakeCalendar()
    a, b = item("a", 2), item("b", 5)
    store.sync_source("d2l", [a, b, item("stale", -30)])

    assert gcal.push(store, cal) == {"created_or_updated": 2, "unchanged": 0, "deleted": 0}
    assert set(cal.events) == {a.id, b.id}  # stale history is never backfilled
    assert cal.events[a.id]["summary"] == "[CS 135] Item a"
    assert cal.events[a.id]["reminders"] == {"useDefault": False, "overrides": []}

    assert gcal.push(store, cal) == {"created_or_updated": 0, "unchanged": 2, "deleted": 0}

    store.set_grade_weight(a.id, 10)
    store.sync_source("d2l", [a])  # b disappears from LEARN
    assert gcal.push(store, cal) == {"created_or_updated": 1, "unchanged": 0, "deleted": 1}
    assert set(cal.events) == {a.id}
    assert "Weight: 10%" in cal.events[a.id]["description"]

    store.set_status(a.id, "done")  # done items leave the calendar
    assert gcal.push(store, cal) == {"created_or_updated": 0, "unchanged": 0, "deleted": 1}
    assert cal.events == {}


def test_event_ids_are_valid_google_ids(store):
    # Google event IDs: base32hex (0-9, a-v), length 5–1024.
    assert re.fullmatch(r"[0-9a-v]{5,1024}", item("x", 1).id)


def test_items_without_due_date_are_not_pushed(store):
    cal = FakeCalendar()
    store.sync_source("d2l", [Item(source="d2l", source_key="n", type="announcement", title="No date")])
    assert gcal.push(store, cal)["created_or_updated"] == 0
