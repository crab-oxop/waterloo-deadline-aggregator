from datetime import date, time

import pytest

from aggregator import graph, login
from aggregator.sources import course_pages, crowdmark, odyssey, quest
from aggregator.sources.quest import ClassMeeting
from aggregator.store import Store

TODAY = date(2026, 9, 24)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Everything connected by default; each test knocks out what it needs."""
    secrets = {"d2l_feed_url": "https://learn/feed.ics", "outlook_ics_url": "https://outlook/cal.ics"}
    monkeypatch.setattr(login, "get_secret", lambda k: secrets.get(k))
    monkeypatch.setattr(login, "PREFS", tmp_path / "prefs.json")
    monkeypatch.setattr(login.gcal, "is_configured", lambda: True)
    monkeypatch.setattr(graph, "is_signed_in", lambda: False)
    monkeypatch.setattr(crowdmark, "is_configured", lambda: True)
    monkeypatch.setattr(odyssey, "is_configured", lambda: True)
    monkeypatch.setattr(odyssey, "is_stale", lambda last: False)
    monkeypatch.setattr(course_pages, "is_configured", lambda: True)
    monkeypatch.setattr(course_pages, "is_stale", lambda last: False)
    store = Store(":memory:")
    store.replace_classes("Fall 2026", [ClassMeeting(course="CS 135", title="", component="LEC", section="1",
                                                     class_nbr="1", days=[0], start=time(10), end=time(11))])
    calls = []
    monkeypatch.setattr(login, "waterloo_sign_in",
                        lambda s, need_crowdmark, need_quest, today=None, need_odyssey=False, need_web=False:
                        calls.append((need_crowdmark, need_quest)) or {"crowdmark": "connected — 7 assessments visible"})
    return store, secrets, calls


def states(checks):
    return {c.key: c.state for c in checks}


def test_all_connected(env, capsys):
    store, _, calls = env
    checks = login.run_startup(store, ask=lambda _: pytest.fail("shouldn't ask"), interactive=True, clipboard=lambda: "",
                               today=TODAY, crowdmark_probe=lambda: True)
    assert set(states(checks).values()) == {"ok"}
    assert "Everything is connected." in capsys.readouterr().out and calls == []


def test_expired_crowdmark_and_new_term_share_one_sign_in(env, capsys):
    store, _, calls = env
    store.replace_classes("Spring 2026", [])  # last term's schedule
    prompts = []
    checks = login.run_startup(store, ask=lambda p: prompts.append(p) or "", interactive=True, clipboard=lambda: "",
                               today=TODAY, crowdmark_probe=lambda: False)
    assert states(checks)["crowdmark"] == "needs_login" and states(checks)["quest"] == "needs_login"
    assert len(prompts) == 1 and "One Duo covers Crowdmark + Quest" in prompts[0]
    assert calls == [(True, True)]  # one browser session for both


def test_declining_sign_in_does_nothing(env):
    store, _, calls = env
    login.run_startup(store, ask=lambda _: "n", interactive=True, clipboard=lambda: "", today=TODAY, crowdmark_probe=lambda: False)
    assert calls == []


def test_not_interactive_only_reports(env, capsys):
    store, _, calls = env
    login.run_startup(store, ask=lambda _: pytest.fail("no human to ask"), interactive=False, clipboard=lambda: "",
                      today=TODAY, crowdmark_probe=lambda: False)
    assert calls == [] and "aggregator login" in capsys.readouterr().out


def test_skipped_outlook_is_remembered(env, capsys):
    store, secrets, _ = env
    del secrets["outlook_ics_url"]
    asked = []
    login.run_startup(store, secret_input=lambda p: asked.append(p) or "skip", interactive=True, clipboard=lambda: "",
                      today=TODAY, crowdmark_probe=lambda: True)
    assert len(asked) == 1  # offered once; typing "skip" skips for good
    login.run_startup(store, secret_input=lambda p: pytest.fail("asked again"), interactive=True, clipboard=lambda: "",
                      today=TODAY, crowdmark_probe=lambda: True)
    assert "Everything is connected." in capsys.readouterr().out
    # `login` (include_skipped) offers it again on purpose
    login.run_startup(store, secret_input=lambda p: asked.append(p) or "", interactive=True, clipboard=lambda: "",
                      include_skipped=True, today=TODAY, crowdmark_probe=lambda: True)
    assert len(asked) == 2


def test_link_is_read_from_clipboard_and_failures_are_not_remembered(env, monkeypatch):
    store, secrets, _ = env
    del secrets["outlook_ics_url"]
    saved = []
    monkeypatch.setattr(login, "set_secret", lambda k, v: saved.append((k, v)))
    from aggregator.sources import ics
    monkeypatch.setattr(ics, "fetch_feed", lambda u: b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")
    login.run_startup(store, secret_input=lambda p: "", clipboard=lambda: "https://outlook/cal.ics",
                      interactive=True, today=TODAY, crowdmark_probe=lambda: True)
    assert saved == [("outlook_ics_url", "https://outlook/cal.ics")]  # Enter → clipboard
    # Ctrl+V into a hidden prompt used to give '\x16': must be treated as "no link", not remembered as a skip.
    del saved[:]
    login.run_startup(store, secret_input=lambda p: "\x16", clipboard=lambda: "", interactive=True,
                      today=TODAY, crowdmark_probe=lambda: True)
    assert saved == [] and not login._prefs().get("outlook_skipped")


def test_unclear_answer_is_asked_again(env):
    store, _, calls = env
    answers = iter(["python -m aggregator serve", "y"])
    login.run_startup(store, ask=lambda p: next(answers), interactive=True, clipboard=lambda: "",
                      today=TODAY, crowdmark_probe=lambda: False)
    assert calls == [(True, False)]


def test_bad_pasted_link_is_not_saved(env, monkeypatch):
    store, secrets, _ = env
    del secrets["d2l_feed_url"]
    saved = []
    monkeypatch.setattr(login, "set_secret", lambda k, v: saved.append(k))
    from aggregator.sources import d2l_ics
    monkeypatch.setattr(d2l_ics, "fetch_feed", lambda u: (_ for _ in ()).throw(d2l_ics.FeedError("expired")))
    login.run_startup(store, secret_input=lambda p: "https://learn/bad.ics", interactive=True, clipboard=lambda: "",
                      today=TODAY, crowdmark_probe=lambda: True)
    assert saved == []


def test_crowdmark_never_connected_needs_login(env, monkeypatch):
    store, _, _ = env
    monkeypatch.setattr(crowdmark, "is_configured", lambda: False)
    checks = login.check_all(store, TODAY, crowdmark_probe=lambda: pytest.fail("no probe when not configured"))
    assert states(checks)["crowdmark"] == "needs_login"
    assert quest.term_label(TODAY) == "Fall 2026" and states(checks)["quest"] == "ok"
