import http.client
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from aggregator import dashboard
from aggregator.models import Item
from aggregator.store import Store

NOW = datetime.now(timezone.utc)


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(dashboard.gcal, "is_configured", lambda: False)  # never touch real Google
    store = Store(":memory:")
    items = [
        Item(source="d2l", source_key="a", type="assignment", title="A1 <script>", context="CS 135",
             due_date=NOW + timedelta(days=2)),
        Item(source="d2l", source_key="b", type="quiz", title="Quiz", context="MATH 137",
             due_date=NOW + timedelta(days=2)),
    ]
    store.sync_source("d2l", items)
    server = ThreadingHTTPServer(("127.0.0.1", 0), None)
    port = server.server_port
    server.RequestHandlerClass = dashboard.make_handler(store, port)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield store, items, port
    server.shutdown()


def request(port, method, path, form=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    body = urlencode(form) if form is not None else None
    hdrs = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    if body is not None:
        hdrs["Content-Type"] = "application/x-www-form-urlencoded"
    conn.request(method, path, body=body, headers=hdrs)
    resp = conn.getresponse()
    return resp.status, resp.getheader("Location"), resp.read().decode()


def test_list_page_escapes_titles(env):
    _, _, port = env
    status, _, page = request(port, "GET", "/")
    assert status == 200
    # The page has its own theme <script>s; what matters is the title never lands unescaped.
    assert "A1 &lt;script&gt;" in page and "A1 <script>" not in page


def test_status_change(env):
    store, items, port = env
    status, loc, _ = request(port, "POST", "/status", {"id": items[1].id, "status": "in_progress"})
    assert (status, loc) == (303, "/")
    status, loc, _ = request(port, "POST", "/status", {"id": items[0].id, "status": "done", "next": "/today"})
    assert (status, loc) == (303, f"/done?id={items[0].id}&next=%2Ftoday")  # asks how long it took
    done = {i.id: i for i in store.list_items()}[items[0].id]
    assert done.status == "done" and done.priority_score == 0
    assert request(port, "POST", "/status", {"id": items[0].id, "status": "bogus"})[0] == 400
    assert request(port, "POST", "/status", {"id": "f" * 32, "status": "done"})[0] == 404


def test_settings_save_changes_priority(env):
    store, items, port = env
    a, b = items
    before = {i.id: i.priority_score for i in store.list_items()}
    assert before[a.id] == before[b.id]

    status, loc, _ = request(port, "POST", "/settings",
                             {f"w:{a.id}": "15", f"w:{b.id}": "", "imp:CS 135": "2", "imp:MATH 137": ""})
    assert (status, loc) == (303, "/settings?saved=1")
    after = {i.id: i for i in store.list_items()}
    assert after[a.id].grade_weight == 15 and after[b.id].grade_weight is None
    assert store.context_importance() == {"CS 135": 2}
    assert after[a.id].priority_score == pytest.approx(before[a.id] * 15 * 2, rel=0.01)

    page = request(port, "GET", "/settings?saved=1")[2]
    assert 'value="15"' in page and 'value="2"' in page and "Saved." in page

    # Clearing a value resets it to unknown/default.
    request(port, "POST", "/settings", {f"w:{a.id}": "", "imp:CS 135": ""})
    assert {i.id: i for i in store.list_items()}[a.id].grade_weight is None
    assert store.context_importance() == {}


def test_settings_rejects_bad_values(env):
    store, items, port = env
    status, _, page = request(port, "POST", "/settings", {f"w:{items[0].id}": "150", "imp:CS 135": "abc"})
    assert status == 400 and page.count("Not saved") == 2
    assert store.list_items()[0].grade_weight is None


def test_rejects_foreign_origin_and_host(env):
    _, items, port = env
    evil = {"Origin": "https://evil.example"}
    assert request(port, "POST", "/status", {"id": items[0].id, "status": "done"}, evil)[0] == 403
    assert request(port, "GET", "/", headers={"Host": "evil.example"})[0] == 403
    same = {"Origin": f"http://127.0.0.1:{port}"}
    assert request(port, "POST", "/status", {"id": items[0].id, "status": "done"}, same)[0] == 303


def test_done_prompt_records_actual_time(env):
    store, items, port = env
    a = items[0]
    request(port, "POST", "/status", {"id": a.id, "status": "done"})
    status, _, page = request(port, "GET", f"/done?id={a.id}&next=/today")
    assert status == 200 and "How long did it actually take?" in page and "~2h" in page
    assert request(port, "POST", "/duration", {"id": a.id, "hours": "inf", "next": "/"})[0] == 400
    assert request(port, "POST", "/duration", {"id": a.id, "hours": "", "next": "/"})[0] == 400
    status, loc, _ = request(port, "POST", "/duration", {"id": a.id, "hours": "2.5", "next": "/today"})
    assert (status, loc) == (303, "/today")
    assert store.get_item(a.id).actual_duration == 150


def test_redirects_stay_on_site(env):
    _, items, port = env
    for evil in ("https://evil.example/", "//evil.example/", "/settings", "javascript:alert(1)"):
        loc = request(port, "POST", "/status", {"id": items[1].id, "status": "in_progress", "next": evil})[1]
        assert loc == "/", evil
    loc = request(port, "POST", "/status", {"id": items[1].id, "status": "in_progress",
                                             "next": "/?course=CS+135&sort=due"})[1]
    assert loc == "/?course=CS+135&sort=due"


def test_filters_and_sort(env):
    _, _, port = env
    page = request(port, "GET", "/?course=CS+135")[2]
    assert "A1 &lt;script&gt;" in page and ">Quiz<" not in page
    page = request(port, "GET", "/?type=quiz&sort=due")[2]
    assert ">Quiz<" in page and "A1 &lt;script&gt;" not in page
    assert "Nothing matches." in request(port, "GET", "/?course=NOPE+101")[2]
    assert request(port, "GET", "/?sort=%3Cscript%3E")[0] == 200  # bad values fall back to defaults


def test_today_view(env):
    store, _, port = env
    now = datetime.now(timezone.utc)
    store.sync_source("d2l", [
        Item(source="d2l", source_key="late", type="assignment", title="Late one", due_date=now - timedelta(days=1)),
        Item(source="d2l", source_key="soon", type="assignment", title="Due later today",
             due_date=now + timedelta(minutes=1)),
        Item(source="d2l", source_key="next", type="assignment", title="Next week", due_date=now + timedelta(days=7)),
    ])
    page = request(port, "GET", "/today")[2]
    assert "Overdue" in page and "Late one" in page and "Due later today" in page
    due_today = page.split("<h2>Due today</h2>", 1)[1].split("</section>", 1)[0]
    assert "Next week" not in due_today and "to do today" in page  # (it may appear as a work block)


def test_reminders_page_add_and_complete(env):
    store, _, port = env
    status, loc, _ = request(port, "POST", "/reminders/add", {
        "title": "Club waiver <b>", "date": "2099-01-02", "time": "", "context": "Club",
        "note": "Desk 4\nsign it"})
    assert (status, loc) == (303, "/reminders?added=1")
    page = request(port, "GET", "/reminders?added=1")[2]
    assert "Club waiver &lt;b&gt;" in page and "Later" in page and "Desk 4" in page and "Added." in page
    rem = next(i for i in store.list_items() if i.type == "reminder")
    assert rem.due_date.hour == 23 and rem.context == "Club"

    # Reminders aren't work: done goes straight back, no "how long did it take?"
    status, loc, _ = request(port, "POST", "/status", {"id": rem.id, "status": "done", "next": "/reminders"})
    assert (status, loc) == (303, "/reminders")
    assert "Done · 1" in request(port, "GET", "/reminders")[2]

    request(port, "POST", "/reminders/add", {"title": "Sit in assigned seat", "date": "2099-01-01", "ongoing": "1"})
    habit = next(i for i in store.list_items() if i.title == "Sit in assigned seat")
    assert habit.due_date is None and habit.extra["ongoing"] is True  # ongoing ignores the date
    assert "Keep in mind" in request(port, "GET", "/reminders")[2]

    assert request(port, "POST", "/reminders/add", {"title": "  "})[0] == 400
    assert request(port, "POST", "/reminders/add", {"title": "x", "date": "", "time": "09:00"})[0] == 400
    assert request(port, "POST", "/reminders/add", {"title": "x", "date": "not-a-date"})[0] == 400


def test_today_shows_classes(env):
    from datetime import time as t
    from aggregator.sources.quest import ClassMeeting
    store, _, port = env
    assert "Connect Quest to see your classes" in request(port, "GET", "/today")[2]  # not connected yet
    everyday = list(range(7))
    store.replace_classes("Fall 2026", [
        ClassMeeting(course="CS 135", title="Designing <Functional> Programs", component="LEC", section="001",
                     class_nbr="1", days=everyday, start=t(0, 0), end=t(23, 59), location="ABC 1100"),
    ])
    page = request(port, "GET", "/today")[2]
    assert "CS 135 · Lecture" in page and "Designing &lt;Functional&gt; Programs" in page and "ABC 1100" in page
    assert 'class="blk k' in page and 'class="now"' in page  # a class block on the timeline + the now line


def test_connections_page_and_bad_link(env):
    _, _, port = env
    status, _, page = request(port, "GET", "/connections")
    assert status == 200 and "Checking your connections" in page  # handler's manager hasn't checked yet
    status, _, page = request(port, "POST", "/connect/link", {"kind": "outlook", "url": "\x16"})
    assert status == 400 and "Not saved" in page and "look like a link" in page
    # Other sites can't trigger a sign-in window.
    assert request(port, "POST", "/connect/waterloo", {}, {"Origin": "https://evil.example"})[0] == 403


def test_calendar_page(env):
    _, _, port = env
    status, _, page = request(port, "GET", "/calendar")
    assert status == 200 and "A1 &lt;script&gt;" in page and 'class="day' in page  # fixture items are ~2 days out
    status, _, page = request(port, "GET", "/calendar?m=2099-02")
    assert status == 200 and "February 2099" in page and "A1 &lt;script&gt;" not in page
    assert request(port, "GET", "/calendar?m=%3Cscript%3E")[0] == 200  # junk falls back to this month


def test_settings_saves_estimates(env):
    store, items, port = env
    status, _, _ = request(port, "POST", "/settings", {f"est:{items[0].id}": "1.5"})
    assert status == 303 and store.get_item(items[0].id).estimated_duration == 90
    status, _, page = request(port, "POST", "/settings", {f"est:{items[0].id}": "-1"})
    assert status == 400 and store.get_item(items[0].id).estimated_duration == 90


def test_plan_grid_edit_add_and_remove(env):
    store, items, port = env
    from aggregator import plan_service
    assert request(port, "GET", "/plan")[0] == 200
    block = store.plan_blocks(("suggested",))[0]
    day = block.start.astimezone(plan_service.TZ).date().isoformat()
    m0 = block.start.astimezone(plan_service.TZ).hour * 60 + block.start.astimezone(plan_service.TZ).minute
    status, _, text = request(port, "POST", "/plan/edit",
                              {"kind": "block", "id": block.id, "day": day, "start_min": m0, "end_min": m0 + 45})
    assert (status, text) == (200, "saved")
    moved = store.get_block(block.id)
    assert moved.state == "accepted" and moved.minutes == 45
    assert request(port, "POST", "/plan/edit", {"kind": "block", "id": block.id, "day": day,
                                                "start_min": m0, "end_min": m0})[0] == 400  # zero length
    status, loc, _ = request(port, "POST", "/plan/save", {"kind": "event", "id": "", "title": "Shift",
                                                          "day": day, "start": "18:00", "end": "21:00"})
    assert (status, loc) == (303, "/plan")
    [ev] = store.plan_events()
    assert ev["title"] == "Shift" and "Shift" in request(port, "GET", "/plan")[2]
    assert request(port, "POST", "/plan/save", {"kind": "event", "id": "", "title": "", "day": day,
                                                "start": "18:00", "end": "21:00"})[0] == 400
    request(port, "POST", "/plan/remove", {"kind": "event", "id": ev["id"]})
    assert store.plan_events() == []
    request(port, "POST", "/plan/remove", {"kind": "block", "id": block.id})
    assert store.get_block(block.id).state == "dismissed"
    assert request(port, "POST", "/plan/edit", {"kind": "block", "id": "nope", "day": day,
                                                "start_min": 1, "end_min": 2})[0] == 400


def test_sleep_settings_save_and_reject(env):
    store, _, port = env
    status, loc, _ = request(port, "POST", "/plan/settings", {"wake": "10:00", "sleep": "01:00",
                                                               "max_block": "1.5", "days": "7"})
    assert (status, loc) == (303, "/plan?saved=1")
    status, _, page = request(port, "POST", "/plan/settings", {"wake": "10:00", "sleep": "11:00",
                                                                "max_block": "1.5", "days": "7"})
    assert status == 400 and "Planner not saved" in page


def test_start_now_and_planning_style_routes(env):
    store, items, port = env
    from aggregator import plan_service
    local_now = datetime.now(plan_service.TZ)
    wake = (local_now - timedelta(hours=3)).strftime("%H:%M")  # awake around "now", whatever time the test runs
    sleep = (local_now + timedelta(hours=5)).strftime("%H:%M")
    plan_service.save_settings(store, wake, sleep, 2, 7)
    page = request(port, "GET", "/today")[2]
    assert "free until" in page and "Start now" in page
    status, loc, _ = request(port, "POST", "/plan/start", {"id": items[0].id, "next": "/today"})
    assert (status, loc) == (303, "/today")
    assert any(b.key == items[0].id for b in store.plan_blocks(("accepted",)))
    status, loc, _ = request(port, "POST", "/plan/settings", {"wake": wake, "sleep": sleep, "max_block": "2",
                                                               "days": "7", "style": "balanced"})
    assert (status, loc) == (303, "/plan?saved=1") and plan_service.style_of(store) == "balanced"
    assert request(port, "POST", "/plan/start", {"id": "nope"})[0] == 400


def test_picking_a_date_makes_a_deadline_goal(env):
    store, _, port = env
    status, loc, _ = request(port, "POST", "/goals/add", {"title": "Complete onboarding", "course": "", "kind": "weekly",
                                                          "hours": "4", "until": "2026-10-05", "session": "120"})
    assert status == 303
    [g] = store.goals()
    assert (g["kind"], str(g["until"])) == ("by_date", "2026-10-05")  # the date wasn't silently dropped


def test_drag_in_and_typed_block_routes(env):
    store, items, port = env
    from aggregator import plan_service
    day = (NOW + timedelta(days=1)).astimezone(plan_service.TZ).date().isoformat()
    status, _, text = request(port, "POST", "/plan/add-block",
                              {"key": items[0].id, "day": day, "start_min": 600, "end_min": 630})
    assert (status, text) == (200, "saved")
    assert any(b.key == items[0].id and b.minutes == 30 and b.edited for b in store.plan_blocks(("accepted",)))
    assert request(port, "POST", "/plan/add-block", {"key": "nope", "day": day, "start_min": 1, "end_min": 30})[0] == 400
    status, loc, _ = request(port, "POST", "/plan/save", {"kind": "block", "id": "", "key": items[1].id,
                                                          "day": day, "start": "14:00", "end": "15:30"})
    assert (status, loc) == (303, "/plan")
    assert any(b.key == items[1].id and b.minutes == 90 for b in store.plan_blocks(("accepted",)))


def _focus_block(store, items, minutes=60):
    from aggregator import plan_service
    store.sync_source("d2l", [Item(source="d2l", source_key="a", type="assignment", title="A1 <script>",
                                   context="CS 135", due_date=items[0].due_date,
                                   source_link="https://learn.uwaterloo.ca/a1"), items[1]])
    start = datetime.now(plan_service.TZ).replace(second=0, microsecond=0)
    return store.add_block(items[0].id, start, start + timedelta(minutes=minutes), "suggested", "auto", False)


def test_focus_start_opens_links_and_runs_a_session(env, monkeypatch):
    store, items, port = env
    from aggregator import focus
    opened = []
    monkeypatch.setattr(focus, "open_url", opened.append)  # never open real links from tests
    monkeypatch.setattr(focus, "claude_app_installed", lambda: True)
    bid = _focus_block(store, items)
    status, _, page = request(port, "GET", f"/focus/start?block={bid}")
    assert status == 200 and "Session goal" in page and "A1 &lt;script&gt;" in page
    status, loc, _ = request(port, "POST", "/focus/start", {"block": bid, "goal": "finish Q1",
                                                            "open_assignment": "1", "open_claude": "1"})
    sid = loc.rsplit("/", 1)[1]
    assert status == 303 and loc == f"/focus/{sid}"
    assert opened[0] == "https://learn.uwaterloo.ca/a1" and opened[1].startswith("claude://claude.ai/new?q=")
    assert len(opened) == 2  # music was off
    assert not focus.load_settings(store).open_music  # the toggles are remembered
    assert store.get_block(bid).state == "accepted"
    import json
    st = json.loads(request(port, "GET", f"/focus/{sid}/state")[2])
    assert st["remaining"] > 3500 and not st["on_break"] and not st["ended"]
    assert "finish Q1" in request(port, "GET", f"/focus/{sid}")[2]
    request(port, "POST", f"/focus/{sid}/break", {})
    assert json.loads(request(port, "GET", f"/focus/{sid}/state")[2])["on_break"]
    request(port, "POST", f"/focus/{sid}/resume", {})
    status, loc, _ = request(port, "POST", f"/focus/{sid}/extend", {"win": "1"})
    assert loc == f"/focus/{sid}?win=1" and store.get_focus(sid)["extended_minutes"] == 15
    assert "Focus session running" in request(port, "GET", "/today")[2]
    status, loc, _ = request(port, "POST", f"/focus/{sid}/finish", {"outcome": "done", "win": "0"})
    assert (status, loc) == (303, f"/done?id={items[0].id}&next=/plan")
    assert next(i for i in store.list_items(include_inactive=True) if i.id == items[0].id).status == "done"
    assert request(port, "POST", f"/focus/{sid}/finish", {"outcome": "bogus"})[0] == 400
    assert request(port, "GET", f"/focus/{'0' * 32}/state")[0] == 404


def test_focus_switch_window_hooks_and_settings(env, monkeypatch):
    store, items, port = env
    from aggregator import focus
    monkeypatch.setattr(focus, "open_url", lambda url: None)
    calls = []
    monkeypatch.setattr(dashboard, "focus_window_open", lambda path: calls.append(("open", path)))
    monkeypatch.setattr(dashboard, "focus_window_close", lambda: calls.append(("close",)))
    monkeypatch.setattr(dashboard, "main_window_load", lambda path: calls.append(("main", path)))
    bid = _focus_block(store, items)
    status, loc, _ = request(port, "POST", "/focus/start", {"block": bid})
    sid = store.running_focus()["id"]
    assert (status, loc) == (303, "/plan") and calls == [("open", f"/focus/{sid}?win=1")]
    other = store.add_block(items[1].id, datetime.now(timezone.utc) + timedelta(hours=3),
                            datetime.now(timezone.utc) + timedelta(hours=4), "suggested", "auto", False)
    assert "already running" in request(port, "GET", f"/focus/start?block={other}")[2]
    assert request(port, "POST", "/focus/start", {"block": other})[1] == f"/focus/start?block={other}"  # not started
    assert store.running_focus()["id"] == sid
    status, loc, _ = request(port, "POST", "/focus/switch", {"old": sid, "block": other})
    assert loc == f"/focus/start?block={other}" and store.running_focus() is None
    assert store.get_focus(sid)["outcome"] == "abandoned"
    request(port, "POST", "/focus/start", {"block": other})
    sid2 = store.running_focus()["id"]
    calls.clear()
    status, _, page = request(port, "POST", f"/focus/{sid2}/finish", {"outcome": "not_yet", "win": "1"})
    assert status == 200 and calls == [("main", "/plan"), ("close",)]
    assert 'class="focus-win"' in request(port, "GET", f"/focus/{sid2}?win=1")[2]
    status, loc, _ = request(port, "POST", "/focus/settings", {"music_url": "https://example.com/m", "open_music": "1",
                                                               "template": "Help with {title}"})
    assert status == 303 and focus.load_settings(store).music_url == "https://example.com/m"
    assert request(port, "POST", "/focus/settings", {"music_url": "ftp://x"})[0] == 400


def test_plan_grid_runs_to_3am_and_late_blocks_sit_on_the_evening(env):
    store, items, port = env
    from aggregator import plan_service
    plan_service.save_settings(store, "08:00", "23:00", 2, 7)
    day = (NOW + timedelta(days=1)).astimezone(plan_service.TZ).date()
    status, _, text = request(port, "POST", "/plan/add-block",
                              {"key": items[0].id, "day": day.isoformat(), "start_min": 24 * 60 + 60, "end_min": 24 * 60 + 150})
    assert (status, text) == (200, "saved")  # 1:00–2:30 AM, dropped on the evening's column
    page = request(port, "GET", "/plan")[2]
    assert 'data-end="1620"' in page  # the grid reaches 3 AM
    assert f'data-day="{day.isoformat()}" data-state="accepted" data-s="1500" data-e="1590"' in page
    status, loc, _ = request(port, "POST", "/plan/save", {"kind": "block", "id": "", "key": items[1].id,
                                                          "day": day.isoformat(), "start": "00:30", "end": "01:15"})
    assert (status, loc) == (303, "/plan")  # typed in the editor: also that night, not the morning before
    b = next(b for b in store.plan_blocks(("accepted",)) if b.key == items[1].id)
    assert b.start.astimezone(plan_service.TZ).date() == day + timedelta(days=1)


def test_exam_prep_card_add_change_hours_dismiss_and_settings(env):
    store, items, port = env
    exams = [Item(source="odyssey", source_key=k, type="exam", title=f"{c} Midterm", context=c,
                  due_date=NOW + timedelta(days=d)) for k, c, d in (("m", "MATH 137", 6), ("p", "PHYS 121", 8), ("c", "CS 135", 9))]
    store.sync_source("odyssey", exams)
    page = request(port, "GET", "/today")[2]
    assert "MATH 137 midterm" in page and "add a study goal?" in page and "Not this one" in page
    assert "MATH 137 midterm</b> in 6 days" in page  # the countdown
    assert "add a study goal?" in request(port, "GET", "/plan")[2]
    status, loc, _ = request(port, "POST", "/exam-prep/add", {"id": exams[0].id, "next": "/plan"})
    assert status == 303 and loc.startswith("/plan?msg=")
    status, _, _ = request(port, "POST", "/exam-prep/add", {"id": exams[1].id, "hours": "4.5", "next": "/today"})
    request(port, "POST", "/exam-prep/dismiss", {"id": exams[2].id, "next": "/today"})
    goals = {g["item_id"]: g for g in store.goals()}
    assert goals[exams[0].id]["hours"] == 8 and goals[exams[1].id]["hours"] == 4.5 and exams[2].id not in goals
    assert "add a study goal?" not in request(port, "GET", "/today")[2]
    assert request(port, "POST", "/exam-prep/add", {"id": "nope"})[0] == 400
    status, loc, _ = request(port, "POST", "/exam-prep/settings", {"window": "10", "quiz": "1", "midterm": "6", "final": "12"})
    assert (status, loc) == (303, "/settings?saved=1#exam-prep")
    assert request(port, "POST", "/exam-prep/settings", {"window": "0", "quiz": "1", "midterm": "6", "final": "12"})[0] == 400
    assert 'id="exam-prep"' in request(port, "GET", "/settings")[2]


def test_focus_on_an_own_event(env, monkeypatch):
    store, items, port = env
    from aggregator import focus, plan_service
    monkeypatch.setattr(focus, "open_url", lambda url: None)
    start = datetime.now(plan_service.TZ).replace(second=0, microsecond=0)
    eid = plan_service.add_event(store, "Read chapter 4", start, start + timedelta(minutes=45))
    page = request(port, "GET", f"/focus/start?block={eid}")[2]
    assert "Read chapter 4" in page and "your event" in page and "Session goal" in page
    status, loc, _ = request(port, "POST", "/focus/start", {"block": eid, "goal": "notes on 4.1-4.3"})
    sid = store.running_focus()["id"]
    assert loc == f"/focus/{sid}" and store.get_focus(sid)["planned_minutes"] == 45
    page = request(port, "GET", f"/focus/{sid}")[2]
    assert "Read chapter 4" in page and "Log this session" in page and "Did you finish" not in page
    request(port, "POST", f"/focus/{sid}/extend", {})
    later = datetime.now(timezone.utc) + timedelta(minutes=50)
    focus.finish(store, sid, "not_yet", later)  # ran past the event's end after +15
    ev = next(e for e in store.plan_events() if e["id"] == eid)
    assert ev["end"] == later and store.running_focus() is None  # stretched to when it really ended
    assert 'event-saved" name="block" id="dlg-focus"' in request(port, "GET", "/plan")[2]


def test_after_midnight_the_plan_still_shows_last_night_as_today(env, monkeypatch):
    store, items, port = env
    from aggregator import pages, plan_service
    plan_service.save_settings(store, "09:30", "01:40", 2, 7)
    fri = datetime.now(plan_service.TZ).date() + timedelta(days=7 - datetime.now(plan_service.TZ).weekday() + 4)
    one_am = datetime.combine(fri + timedelta(days=1), datetime.min.time(), plan_service.TZ) + timedelta(hours=1)
    monkeypatch.setattr(pages, "now_utc", lambda: one_am.astimezone(timezone.utc))
    bid = store.add_block(items[0].id, one_am + timedelta(minutes=5), one_am + timedelta(minutes=35), "accepted", "manual", True)
    page = pages.render_plan(store)
    first_day = page.split('class="pg-col', 1)[1]
    assert f'data-date="{fri.isoformat()}"' in first_day[:80]  # Friday is still the first column
    assert f'data-id="{bid}" data-day="{fri.isoformat()}"' in page and 'data-s="1505"' in page  # 1:05 AM, Friday night
    assert plan_service.day_start(store, one_am) == datetime.combine(fri, datetime.min.time(), plan_service.TZ) + timedelta(hours=9, minutes=30)


def test_test_checkin_dialog_and_routes(env):
    store, items, port = env
    start = NOW - timedelta(hours=4)
    t = Item(source="odyssey", source_key="m", type="exam", title="MATH 137 Midterm", context="MATH 137", due_date=start,
             extra={"end": (start + timedelta(minutes=110)).isoformat()})
    store.sync_source("odyssey", [t])
    page = request(port, "GET", "/today")[2]
    assert '<dialog id="debrief"' in page and "How did MATH 137 Midterm go?" in page
    status, loc, _ = request(port, "POST", "/debrief/later", {"id": t.id, "next": "/today"})
    assert (status, loc) == (303, "/today") and '<dialog id="debrief"' not in request(port, "GET", "/today")[2]
    status, loc, _ = request(port, "POST", "/debrief/save", {"id": t.id, "next": "/plan", "went": "fine",
                                                             "improve": "sleep more", "notes": "", "minutes": "100"})
    assert status == 303 and loc.startswith("/plan?msg=")
    assert store.debriefs()[0]["minutes"] == 100
    goals_page = request(port, "GET", "/goals")[2]
    assert "After your tests" in goals_page and "sleep more" in goals_page
    assert request(port, "POST", "/debrief/save", {"id": t.id, "minutes": "x", "next": "/today"})[1].startswith("/today?msg=Not")
    assert request(port, "POST", "/debrief/skip", {"id": "nope"})[0] == 400
