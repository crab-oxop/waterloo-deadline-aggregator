import threading

import pytest

from aggregator import connections, login
from aggregator.connections import ConnectionManager, looks_like_link
from aggregator.login import Check
from aggregator.store import Store


def test_looks_like_link():
    assert looks_like_link("https://outlook.office365.com/owa/calendar/x/calendar.ics")
    assert looks_like_link("webcal://learn.uwaterloo.ca/feed.ics?token=1")
    for bad in ("\x16", "", "calendar.ics", "C:\\Windows\\win.ini", "javascript:alert(1)", "https://"):
        assert not looks_like_link(bad), bad


def test_save_link_rejects_non_links_before_fetching(monkeypatch):
    mgr = ConnectionManager(Store(":memory:"))
    monkeypatch.setattr(connections, "set_secret", lambda *a: pytest.fail("must not save"))
    with pytest.raises(ValueError, match="doesn't look like a link"):
        mgr.save_link("outlook", "\x16")


def test_needs_attention_respects_skipped_outlook(monkeypatch, tmp_path):
    monkeypatch.setattr(login, "PREFS", tmp_path / "prefs.json")
    mgr = ConnectionManager(Store(":memory:"))
    mgr.checks = [Check("outlook", "Outlook calendar", "optional", "not set up", "paste_outlook")]
    assert mgr.needs_attention()
    mgr.skip("outlook")
    assert not mgr.needs_attention()


def test_waterloo_sign_in_runs_once_in_background(monkeypatch):
    started, release = threading.Event(), threading.Event()
    calls = []

    def fake_sign_in(store, need_crowdmark, need_quest, lock=None, cancelled=lambda: False, progress=lambda p: None,
                     need_odyssey=False, need_web=False):
        calls.append((need_crowdmark, need_quest))
        started.set()
        while not release.is_set() and not cancelled():
            release.wait(0.05)
        return {"crowdmark": "cancelled" if cancelled() else "connected — 7 assessments visible"}

    monkeypatch.setattr(login, "waterloo_sign_in", fake_sign_in)
    monkeypatch.setattr(login, "check_all", lambda store, *a, **k: [])
    synced = []
    mgr = ConnectionManager(Store(":memory:"), after_signin=lambda: synced.append(1))
    mgr.checks = [Check("crowdmark", "Crowdmark", "needs_login", "signed out", "waterloo"),
                  Check("quest", "Quest schedule", "ok", "Fall 2026 captured")]
    assert mgr.start_waterloo_signin()
    assert started.wait(5) and mgr.signin_running
    assert not mgr.start_waterloo_signin()  # a second click while the window is open does nothing
    release.set()
    for _ in range(100):
        if not mgr.signin_running and synced:
            break
        threading.Event().wait(0.05)
    assert calls == [(True, False)] and synced == [1]
    assert mgr.signin_results["crowdmark"].startswith("connected")


def test_cancel_stops_a_waiting_sign_in(monkeypatch):
    def fake_sign_in(store, need_crowdmark, need_quest, lock=None, cancelled=lambda: False, progress=lambda p: None,
                     need_odyssey=False, need_web=False):
        for _ in range(200):
            if cancelled():
                return {"crowdmark": "not connected (cancelled)"}
            threading.Event().wait(0.02)
        return {"crowdmark": "timed out"}

    monkeypatch.setattr(login, "waterloo_sign_in", fake_sign_in)
    monkeypatch.setattr(login, "check_all", lambda store, *a, **k: [])
    mgr = ConnectionManager(Store(":memory:"))
    mgr.start_waterloo_signin()
    mgr.cancel_signin()
    for _ in range(100):
        if not mgr.signin_running:
            break
        threading.Event().wait(0.05)
    assert not mgr.signin_running and "cancelled" in mgr.signin_results["crowdmark"]


def test_no_connection_check_while_sign_in_window_is_open(monkeypatch):
    monkeypatch.setattr(login, "check_all", lambda *a, **k: pytest.fail("would fight the open browser profile"))
    mgr = ConnectionManager(Store(":memory:"))
    mgr.signin_running = True
    mgr.refresh()  # silently skipped
