import threading
from datetime import datetime, timedelta, timezone

from aggregator import pages, relevance, sync
from aggregator.models import Item
from aggregator.store import Store

NOW = datetime.now(timezone.utc)


def item(title, source="outlook", context=None, key=None, **kw):
    return Item(source=source, source_key=key or title, type=kw.pop("type", "meeting"), title=title,
                context=context, due_date=NOW + timedelta(days=2), **kw)


def test_classifies_academic_and_clutter():
    keep = [item("MATH 135 Midterm", "odyssey", "MATH 135"), item("W3", "crowdmark", "MATH 135"),
            item("Last day to drop a class with a 100% refund", "manual", "Quest", extra={"from": "email scan"}),
            item("Club onboarding", "manual")]  # typed in by the user himself: always kept
    hide = [item("Tuesday Session: Quiz Solutions"), item("Friday Session: Practice Quiz Solutions"),
            item("Sign up for movie night", "manual", "Campus Life", extra={"from": "email scan"}),
            item("Floor social + cupcake decorating", "manual", "Campus Life", extra={"from": "x"}),
            item("Lunch with a friend")]
    assert all(relevance.shown(i) for i in keep)
    assert not any(relevance.shown(i) for i in hide)
    assert relevance.reason_label(hide[0]) == "Optional session"
    assert relevance.reason_label(hide[2]) == "Not academic"


def test_override_survives_a_rescrape_and_hides_from_lists():
    store = Store(":memory:")
    sessions = item("Tuesday Session: Quiz Solutions")
    store.sync_source("outlook", [sessions, item("CS 135 office hours", context="CS 135")])
    assert [i.title for i in store.list_items()] == ["CS 135 office hours"]
    store.set_relevance(sessions.id, "show")
    store.sync_source("outlook", [sessions, item("CS 135 office hours", context="CS 135")])  # rescrape
    assert {i.title for i in store.list_items()} == {"Tuesday Session: Quiz Solutions", "CS 135 office hours"}
    assert len(store.list_items(include_hidden=True)) == 2


def test_deadlines_page_offers_hidden_items():
    store = Store(":memory:")
    store.sync_source("outlook", [item("Tuesday Session: Quiz Solutions"), item("CS 135 A02", context="CS 135")])
    html = pages.render(store, {})
    assert "1 non-academic item hidden" in html and "Quiz Solutions" not in html
    html = pages.render(store, {"hidden": "1"})
    assert "Quiz Solutions" in html and "Optional session" in html and "Academic only" in html


def test_repeated_add_makes_one_reminder():
    store = Store(":memory:")
    form = {"title": "Finish lab report", "date": "2026-10-02", "time": "17:00"}
    first = pages.add_reminder(store, form)
    assert pages.add_reminder(store, dict(form)) == first
    assert len([i for i in store.list_items(include_past=True) if i.type == "reminder"]) == 1


class SlowSource:
    SOURCE = "d2l"

    def __init__(self):
        self.release = threading.Event()

    def is_configured(self):
        return True

    def collect(self):
        self.release.wait(5)
        return [item("CS 135 A02", "d2l", "CS 135")]


def test_sync_fetches_without_holding_the_lock(monkeypatch):
    store, lock, slow = Store(":memory:"), threading.RLock(), SlowSource()
    monkeypatch.setattr(sync, "CONNECTORS", {"d2l": slow})
    t = threading.Thread(target=sync.sync_all, args=(store, False, lock))
    t.start()
    got = lock.acquire(timeout=1)  # pages can still read while the source is slow
    assert got
    lock.release()
    slow.release.set()
    t.join(5)
    assert [i.title for i in store.list_items()] == ["CS 135 A02"]


def test_recently_failed_source_is_skipped_until_forced(monkeypatch):
    store = Store(":memory:")
    calls = []

    class Broken:
        def is_configured(self): return True
        def collect(self): calls.append(1); raise RuntimeError("signed out")

    monkeypatch.setattr(sync, "CONNECTORS", {"crowdmark": Broken()})
    sync.sync_all(store, calendar=False)
    sync.sync_all(store, calendar=False)  # within the back-off window: not retried
    assert calls == [1]
    sync.sync_all(store, calendar=False, force=True)  # e.g. right after a sign-in
    assert calls == [1, 1]
