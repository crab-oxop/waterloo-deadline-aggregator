from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aggregator import priority
from aggregator.dashboard import render
from aggregator.models import Item, make_item_id
from aggregator.secrets import redact_url
from aggregator.sources import d2l_ics
from aggregator.store import Store

FIXTURE = Path(__file__).parent / "fixtures" / "d2l_sample.ics"


@pytest.fixture
def items():
    return d2l_ics.parse_feed(FIXTURE.read_bytes(), now=datetime(2026, 9, 22, tzinfo=timezone.utc))


@pytest.fixture
def store():
    return Store(":memory:")


def test_parse_feed(items):
    by_title = {i.title: i for i in items}
    assert len(items) == 4  # event with no UID/URL is skipped

    a2 = by_title["Assignment 02 - Due"]
    assert (a2.type, a2.context) == ("assignment", "CS 135")
    assert a2.due_date == datetime(2026, 9, 25, 3, 0, tzinfo=timezone.utc)
    assert a2.source_link.startswith("https://learn.uwaterloo.ca/d2l/le/calendar/")

    quiz = by_title["Weekly Quiz 3 - Availability Ends"]
    assert (quiz.type, quiz.context) == ("quiz", "MATH 137")
    assert quiz.due_date.utcoffset() == timedelta(hours=-4)  # EDT
    assert "quizzing" in quiz.source_link

    mid = by_title["PHYS121 Midterm"]
    assert (mid.type, mid.context) == ("exam", "PHYS 121")
    assert (mid.due_date.hour, mid.due_date.minute) == (23, 59)  # all-day → end of day

    assert by_title["Tutorial session"].type == "meeting"


def test_rescrape_updates_in_place_and_keeps_user_fields(store, items):
    assert store.sync_source("d2l", items) == {"inserted": 4, "updated": 0, "deactivated": 0}
    a2 = next(i for i in items if i.title.startswith("Assignment 02"))
    store.set_status(a2.id, "in_progress")

    # Instructor renames the item and moves the deadline: same UID → same item.
    moved = Item(**{**a2.__dict__, "title": "Assignment 02 (extended) - Due",
                    "due_date": a2.due_date + timedelta(days=2)})
    assert store.sync_source("d2l", [moved, *[i for i in items if i is not a2]]) == \
        {"inserted": 0, "updated": 4, "deactivated": 0}

    got = {i.id: i for i in store.list_items(include_past=True)}
    assert len(got) == 4
    assert got[a2.id].title == "Assignment 02 (extended) - Due"
    assert got[a2.id].status == "in_progress"  # user-owned field survives rescrape
    events = store.db.execute("SELECT from_status, to_status FROM status_events").fetchall()
    assert [tuple(e) for e in events] == [("not_started", "in_progress")]


def test_item_removed_from_feed_is_deactivated(store, items):
    store.sync_source("d2l", items)
    assert store.sync_source("d2l", items[1:])["deactivated"] == 1
    assert len(store.list_items(include_past=True)) == 3
    assert len(store.list_items(include_inactive=True, include_past=True)) == 4


def test_identity_ignores_title_and_date():
    assert make_item_id("d2l", "uid-1") == make_item_id("d2l", "uid-1")
    assert make_item_id("d2l", "uid-1") != make_item_id("gmail", "uid-1")


def test_priority():
    now = datetime(2026, 9, 22, tzinfo=timezone.utc)
    soon, later = now + timedelta(days=1), now + timedelta(days=10)
    assert priority.score(15, 1, soon, "not_started", now) > priority.score(15, 1, later, "not_started", now)
    assert priority.score(15, 1, soon, "not_started", now) > priority.score(None, 1, soon, "not_started", now)
    assert priority.score(15, 1, soon, "done", now) == 0
    assert priority.score(None, None, None, "not_started", now) > 0  # never invisible
    overdue = priority.score(1, 1, now - timedelta(days=3), "not_started", now)
    assert overdue == priority.score(1, 1, now + timedelta(minutes=5), "not_started", now)
    assert priority.score(15, 1, now - timedelta(days=8), "not_started", now) == 0  # stale history
    assert priority.score(1, 1, now - timedelta(hours=2), "not_started", now, "meeting") == 0  # already over
    assert priority.score(1, 1, now + timedelta(hours=2), "not_started", now, "meeting") > 0
    assert priority.is_past(now - timedelta(days=8), now) and not priority.is_past(now - timedelta(days=6), now)


def test_failure_is_flagged_on_dashboard(store, items):
    store.sync_source("d2l", items)
    store.record_failure("d2l", "FeedError: feed link may have expired")
    page = render(store)
    assert "LEARN sync failed" in page and "1 source needs attention" in page


def test_feed_errors_never_leak_token():
    assert redact_url("https://learn.uwaterloo.ca/feed.ics?token=SECRET") == \
        "https://learn.uwaterloo.ca/feed.ics?<redacted>"
    with pytest.raises(d2l_ics.FeedError) as e:
        d2l_ics.fetch_feed("https://127.0.0.1:1/feed.ics?token=SECRET", timeout=2)
    assert "SECRET" not in str(e.value)
