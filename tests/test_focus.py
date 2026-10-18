from datetime import date, datetime, timedelta, timezone
from urllib.parse import parse_qs, quote, unquote, urlsplit
from zoneinfo import ZoneInfo

import pytest

from aggregator import focus, plan_service
from aggregator.models import Item
from aggregator.store import Store

TZ = ZoneInfo("America/Toronto")


def at(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=TZ)


def session(start, minutes=60, **kw):
    return {"started_at": start, "planned_minutes": minutes, "paused_seconds": 0, "break_started_at": None,
            "extended_minutes": 0, "ended_at": None, **kw}


# --- timer maths -----------------------------------------------------------------------------

def test_timer_running_paused_resumed_extended_and_over():
    t0 = at(2026, 10, 5, 14)
    s = session(t0, 60)
    st = focus.timer(s, t0 + timedelta(minutes=20))
    assert (st.worked, st.remaining, st.on_break, st.warning, st.over) == (1200, 2400, False, False, False)
    s["break_started_at"] = t0 + timedelta(minutes=20)  # on a break for 7 minutes
    st = focus.timer(s, t0 + timedelta(minutes=27))
    assert st.on_break and st.break_for == 420 and st.worked == 1200  # the break isn't work
    s.update(break_started_at=None, paused_seconds=420)  # resumed
    st = focus.timer(s, t0 + timedelta(minutes=60))
    assert st.worked == 53 * 60 and st.warning and not st.over  # last 10 minutes
    s["extended_minutes"] = 15
    assert focus.timer(s, t0 + timedelta(minutes=60)).remaining == 22 * 60
    st = focus.timer(s, t0 + timedelta(minutes=90))
    assert st.over and st.over_by == 8 * 60 and focus.fmt_clock(st.remaining) == "8:00"  # counting up


def test_timer_is_unaffected_by_the_dst_change():
    start = at(2026, 11, 1, 0, 30)  # EDT; clocks fall back at 2:00
    s = session(start, 180)
    later = start.astimezone(timezone.utc) + timedelta(hours=2)  # 2 real hours later (1:30 EST on the wall)
    assert later.astimezone(TZ).hour == 1
    for now in (later, later.astimezone(TZ)):  # the same instant, however it's expressed
        st = focus.timer(s, now)
        assert st.worked == 2 * 3600 and st.remaining == 3600


def test_ended_sessions_are_frozen():
    t0 = at(2026, 10, 5, 14)
    s = session(t0, 30, ended_at=t0 + timedelta(minutes=40))
    assert focus.timer(s, t0 + timedelta(hours=5)).worked == 40 * 60


# --- Claude link ------------------------------------------------------------------------------

class Subject:
    def __init__(self, **kw):
        self.id, self.title, self.context, self.type = "x" * 32, "A03 & Q#2", "MATH 135", "assignment"
        self.due_date, self.grade_weight, self.source_link = at(2026, 10, 7, 21), 5.0, None
        self.__dict__.update(kw)


def test_claude_link_encodes_everything_and_round_trips():
    subj = Subject(context="FRAN 192 — Français", source_link="https://learn.uwaterloo.ca/d2l/x?a=1&b=2#top")
    text = focus.claude_message(focus.DEFAULT_TEMPLATE, subj, "Finish Q1–Q3 & check #4\nthen review", ["MATH 137 Quiz 4"])
    url = focus.claude_url(text)
    assert url.startswith("claude://claude.ai/new?q=")
    raw = url.split("?q=", 1)[1]
    assert " " not in raw and "&" not in raw and "#" not in raw and "\n" not in raw
    assert unquote(raw) == text
    assert "Français" in text and "A03 & Q#2" in text and "https://learn.uwaterloo.ca/d2l/x?a=1&b=2#top" in text
    assert "don't write the solutions" in text  # tutor, not an answer machine
    assert focus.claude_url(text, scheme="web").startswith("https://claude.ai/new?q=")


def test_claude_link_stays_under_the_limit_and_trims_the_also_due_list_first():
    subj = Subject()
    others = [f"Thing {i} " + "x" * 3000 for i in range(5)]
    text = focus.claude_message(focus.DEFAULT_TEMPLATE, subj, "Short goal", others)
    assert len(quote(text, safe="")) <= focus.CLAUDE_BUDGET < focus.CLAUDE_LIMIT
    assert "Short goal" in text and "A03 & Q#2" in text  # the essentials survive
    assert text.count("Thing ") < 5  # the context list went first


def test_claude_message_handles_missing_fields_and_bad_templates():
    subj = Subject(source_link=None, grade_weight=None, due_date=None, context=None, type="goal")
    text = focus.claude_message("{title} | {nope}", subj, "", [])
    assert "A03" in text and "no due date" in text and "study goal" in text  # fell back to the default
    assert "Grade weight" not in text


# --- sessions ----------------------------------------------------------------------------------

def item(key, due, **kw):
    return Item(source="d2l", source_key=key, type=kw.pop("type", "assignment"), title=kw.pop("title", key),
                due_date=due, **kw)


def _block_for(store, key):
    plan_service.replan(store, datetime.now(timezone.utc))
    return next(b for b in store.plan_blocks(("suggested", "accepted")) if b.key == key)


def test_only_one_session_runs_and_it_survives_a_restart(tmp_path):
    db = tmp_path / "a.db"
    store = Store(db)
    soon = datetime.now(timezone.utc) + timedelta(days=2)
    a, b = item("a", soon, title="A03"), item("b", soon + timedelta(hours=3), title="Lab")
    store.sync_source("d2l", [a, b])
    sid = focus.start(store, _block_for(store, a.id).id, "Q1 and Q2")
    assert store.get_block(store.get_focus(sid)["block_id"]).state == "accepted"  # starting accepts it
    with pytest.raises(focus.SessionRunning) as e:
        focus.start(store, _block_for(store, b.id).id)
    assert e.value.session["id"] == sid
    reopened = Store(db)  # the app restarts
    assert reopened.running_focus()["id"] == sid and reopened.running_focus()["goal"] == "Q1 and Q2"


def test_finish_done_prefills_duration_from_all_sessions():
    store = Store(":memory:")
    a = item("a", datetime.now(timezone.utc) + timedelta(days=2), title="A03")
    store.sync_source("d2l", [a])
    now = datetime.now(timezone.utc)
    blk = _block_for(store, a.id)
    first = focus.start(store, blk.id, now=now - timedelta(hours=3))
    focus.finish(store, first, "not_yet", now=now - timedelta(hours=2, minutes=20))  # 40 min
    second = focus.start(store, blk.id, now=now - timedelta(minutes=50))
    focus.take_break(store, second, now - timedelta(minutes=30))
    focus.resume(store, second, now - timedelta(minutes=20))  # a 10-minute break
    nxt = focus.finish(store, second, "done", now=now)
    assert nxt["next"] == f"/done?id={a.id}&next=/plan"
    done = store.get_item(a.id)
    assert done.status == "done" and done.actual_duration == 40 + 40  # sessions summed, breaks excluded


def test_finish_not_yet_replans_and_keeps_accepted_blocks():
    store = Store(":memory:")
    a = item("a", datetime.now(timezone.utc) + timedelta(days=3), title="A03", estimated_duration=300)
    store.sync_source("d2l", [a])
    blk = _block_for(store, a.id)
    sid = focus.start(store, blk.id)
    focus.finish(store, sid, "not_yet")
    assert store.get_item(a.id).status != "done"
    assert store.get_block(blk.id).state == "accepted"
    assert any(b.key == a.id for b in store.plan_blocks(("suggested",)))  # remaining work re-planned


def test_running_long_stretches_the_block():
    store = Store(":memory:")
    a = item("a", datetime.now(timezone.utc) + timedelta(days=2), title="A03")
    store.sync_source("d2l", [a])
    blk = _block_for(store, a.id)
    start = blk.start
    sid = focus.start(store, blk.id, now=start)
    focus.extend(store, sid)
    ended = start + timedelta(minutes=blk.minutes + 20)
    focus.finish(store, sid, "not_yet", now=ended)
    assert store.get_block(blk.id).end == ended


def test_goal_blocks_items_without_links_and_deleted_items():
    store = Store(":memory:")
    gid = plan_service.add_goal(store, "Review lectures", "MATH 137", "by_date", 1,
                                (datetime.now(TZ) + timedelta(days=2)).date(), 60)
    blk = _block_for(store, gid)
    sid = focus.start(store, blk.id)
    subj = plan_service.subjects(store)[gid]
    assert getattr(subj, "source_link", None) is None  # nothing to open: skipped, not an error
    assert focus.deadline_line(store, subj).startswith("Due in")
    focus.finish(store, sid, "done")
    assert not store.goals()[0]["active"]  # a by-date goal marked done stops being planned
    # a block whose item has since disappeared
    store2 = Store(":memory:")
    a = item("a", datetime.now(timezone.utc) + timedelta(days=2), title="A03")
    store2.sync_source("d2l", [a])
    b2 = _block_for(store2, a.id)
    sid2 = focus.start(store2, b2.id)
    store2.sync_source("d2l", [item("z", datetime.now(timezone.utc) + timedelta(days=9))])
    assert focus.finish(store2, sid2, "done")["next"] == "/plan"  # nothing to mark; no crash
    with pytest.raises(ValueError):
        focus.start(store2, "f" * 32)


def test_focus_settings_roundtrip_and_validation():
    store = Store(":memory:")
    assert focus.load_settings(store).music_url == focus.DEFAULT_MUSIC
    focus.save_settings(store, music_url="https://www.youtube.com/playlist?list=PL123", open_claude=False)
    s = focus.load_settings(store)
    assert s.music_url.endswith("PL123") and not s.open_claude and s.open_music
    with pytest.raises(ValueError):
        focus.save_settings(store, music_url="javascript:alert(1)")
