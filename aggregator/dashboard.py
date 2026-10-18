"""Local web server for the dashboard (127.0.0.1 only). Pages live in pages.py, the look in ui.py."""
from __future__ import annotations

import json
import logging
import re
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

from . import gcal, pages
from .connections import ConnectionManager
from .models import NON_WORK_TYPES, STATUSES
from .pages import (add_reminder, apply_settings, parse_opt_float, render, render_calendar,  # noqa: F401 (re-exported)
                    render_connections, render_done_prompt, render_reminders, render_settings, render_starting,
                    render_today)
from .store import Store
from .sync import Syncer
from .ui import esc

log = logging.getLogger(__name__)

REFRESH_EVERY = timedelta(minutes=10)  # feeds are small; Crowdmark backs off on its own when signed out
ITEM_ID_RE = re.compile(r"^[0-9a-f]{32}$")
MAX_BODY = 64 * 1024
LIST_PAGES = ("/", "/today", "/reminders", "/plan", "/goals")  # where a status change may send you back to

# One SQLite connection is shared by request threads and the refresh loop; serialize access.
store_lock = threading.RLock()


def _safe_next(raw: str | None) -> str:
    """Only redirect back to our own list pages — never to an arbitrary URL."""
    parts = urlsplit(raw or "/")
    if parts.scheme or parts.netloc or parts.path not in LIST_PAGES:
        return "/"
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _flag(text: str, good: bool = False) -> str:
    return f'<div class="flag{" good" if good else ""}" role="status">{esc(text)}</div>'


# Set by app.py in the desktop app: open the always-on-top focus window at a path, close it, and
# point the main window somewhere. Under `serve` they stay None and the session is a normal page.
focus_window_open = None
focus_window_close = None
main_window_load = None
SID_RE = re.compile(r"^/focus/([0-9a-f]{32})(/[a-z]+)?$")

PUSH_DELAY = 1.5  # seconds: a burst of edits (dragging a few blocks) becomes one push
_push_state = {"timer": None, "running": False, "again": False}
_push_guard = threading.Lock()


def _push_calendar_async(store: Store, enabled: bool = True) -> None:
    """Mirror edits to Google Calendar in the background: debounced, one push at a time, and never
    holding the store lock during network calls — so pages stay instant while Google is slow."""
    if not enabled or not gcal.is_configured():
        return

    def run():
        with _push_guard:
            if _push_state["running"]:
                _push_state["again"] = True
                return
            _push_state["running"] = True
        try:
            while True:
                try:
                    result = gcal.sync_calendar(store, store_lock)
                    with store_lock:
                        store.mark_ok("gcal", result)
                except Exception as e:
                    with store_lock:
                        store.record_failure("gcal", f"{type(e).__name__}: {e}")
                    log.warning("calendar push failed: %s", e)
                with _push_guard:
                    if not _push_state["again"]:
                        _push_state["running"] = False
                        return
                    _push_state["again"] = False
        except BaseException:
            with _push_guard:
                _push_state["running"] = False
            raise

    with _push_guard:
        if _push_state["timer"] is not None:
            _push_state["timer"].cancel()
        timer = threading.Timer(PUSH_DELAY, run)
        timer.daemon = True
        _push_state["timer"] = timer
    timer.start()


def _open_focus_links(store: Store, sess: dict, toggles: dict) -> None:
    """On Start: the assignment, music and Claude (each only if its toggle is on). Links are handed
    to Windows; nothing is sent anywhere — Claude only receives the text if the user presses send."""
    from . import focus
    subj = pages._focus_subject(store, sess["item_id"])
    fs = focus.load_settings(store)
    try:
        link = getattr(subj, "source_link", None)
        if toggles.get("open_assignment") and link and link.lower().startswith(("http://", "https://")):
            focus.open_url(link)  # no link (manual items, goal blocks): skipped silently
        if toggles.get("open_music") and fs.music_url:
            focus.open_url(fs.music_url)
        if toggles.get("open_claude"):
            text = pages.focus_claude_text(store, sess)
            if text:
                focus.open_url(focus.claude_url(text, "claude" if focus.claude_app_installed() else "web"))
    except Exception as e:  # opening something must never stop the session from starting
        log.warning("focus: couldn't open a link: %s", e)


def make_handler(store: Store, port: int, calendar: bool = True, connections: ConnectionManager | None = None,
                 syncer: Syncer | None = None):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    conns = connections or ConnectionManager(store, store_lock)

    def state() -> str:
        with store_lock:
            version = store.version()
        return f"{version}|{conns.state_token()}"

    pages.state_token = state
    pages.connection_attention = lambda: sum(
        1 for c in (conns.checks or []) if c.action and c.state in ("needs_login", "missing"))

    class Handler(BaseHTTPRequestHandler):
        def _trusted(self) -> bool:
            # Host check blocks DNS rebinding; Origin check blocks other sites POSTing to localhost.
            if self.headers.get("Host") not in allowed_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or urlsplit(origin).netloc in allowed_hosts

        def _send(self, code: int, body: str = "", location: str | None = None):
            data = body.encode()
            self.send_response(code)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Frame-Options", "DENY")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, obj):
            data = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _text(self, code: int, text: str):
            data = text.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _form(self) -> dict[str, str] | None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                return None
            parsed = parse_qs(self.rfile.read(length).decode(), keep_blank_values=True)
            return {k: v[-1] for k, v in parsed.items()}

        def do_GET(self):
            if not self._trusted():
                return self.send_error(403)
            path = urlsplit(self.path)
            query = {k: v[-1] for k, v in parse_qs(path.query).items()}
            if path.path == "/start":  # where the app opens: straight to sign-in if anything needs it
                if conns.checks is None:
                    with store_lock:
                        return self._send(200, render_starting(store, conns))
                return self._send(303, location="/connections" if conns.needs_attention() else "/today")
            if path.path in ("/state", "/connections/state"):  # tiny poll target: pages reload when it changes
                data = state().encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path.path == "/connections":  # rendered outside the lock: sign-in threads use it
                notice = _flag(query["msg"], good=True) if query.get("msg") else ""
                with store_lock:
                    return self._send(200, render_connections(store, conns, notice))
            with store_lock:
                if path.path == "/":
                    return self._send(200, render(store, query))
                msg = _flag(query["msg"], good=True) if query.get("msg") else ""
                if path.path == "/today":
                    return self._send(200, render_today(store, msg))
                if path.path == "/plan":
                    notice = _flag("Planner saved.", good=True) if "saved" in query else msg
                    return self._send(200, pages.render_plan(store, notice))
                if path.path == "/focus/start":
                    return self._send(200, pages.render_focus_start(store, query.get("block", ""), msg))
                m = SID_RE.match(path.path)
                if m and m.group(2) == "/state":  # the timer's numbers, computed from stored timestamps
                    from . import focus
                    sess = store.get_focus(m.group(1))
                    if sess is None:
                        return self.send_error(404)
                    st = focus.timer(sess)
                    return self._json({"remaining": st.remaining, "worked": st.worked, "on_break": st.on_break,
                                       "break_for": st.break_for, "planned": st.planned, "over": st.over,
                                       "ended": bool(sess["ended_at"])})
                if m and not m.group(2):
                    return self._send(200, pages.render_focus_session(store, m.group(1), compact="win" in query))
                if path.path == "/goals":
                    notice = _flag(query["msg"], good=True) if query.get("msg") else ""
                    return self._send(200, pages.render_goals(store, notice))
                if path.path == "/calendar":
                    return self._send(200, render_calendar(store, query.get("m")))
                if path.path == "/reminders":
                    notice = _flag("Added.", good=True) if "added" in query else ""
                    return self._send(200, render_reminders(store, notice))
                if path.path == "/done":
                    item = store.get_item(query.get("id", "")) if ITEM_ID_RE.match(query.get("id", "")) else None
                    if item is None:
                        return self.send_error(404)
                    return self._send(200, render_done_prompt(store, item, _safe_next(query.get("next"))))
                if path.path == "/settings":
                    notice = _flag("Saved.", good=True) if "saved" in query else ""
                    return self._send(200, render_settings(store, notice))
            self.send_error(404)

        def do_POST(self):
            form = self._form()  # read the body first: replying unread makes Windows reset the connection
            if not self._trusted():
                return self.send_error(403)
            if form is None:
                return self.send_error(413)
            path = urlsplit(self.path).path
            if path == "/connect/waterloo":
                conns.start_waterloo_signin()
                return self._send(303, location="/connections")
            if path == "/connect/google":  # Google Calendar token expired: sign in again in the browser
                conns.start_google(after=lambda: _push_calendar_async(store, calendar))
                return self._send(303, location="/connections")
            if path == "/connect/cancel":
                conns.cancel_signin()
                return self._send(303, location="/connections")
            if path == "/connect/recheck":
                conns.refresh_async()
                if syncer:
                    syncer.run_async(force=True)  # and pull fresh data right away
                return self._send(303, location="/connections")
            if path == "/connect/skip":
                conns.skip(form.get("kind", ""))
                return self._send(303, location="/connections")
            if path == "/connect/link":
                try:
                    msg = conns.save_link(form.get("kind", ""), form.get("url", ""))
                except Exception as e:  # bad link, unreachable feed, expired token…
                    with store_lock:
                        return self._send(400, render_connections(store, conns, _flag(f"Not saved: {e}")))
                return self._send(303, location=f"/connections?{urlencode({'msg': msg})}")
            with store_lock:
                if path == "/status":
                    item_id, status = form.get("id", ""), form.get("status", "")
                    if not ITEM_ID_RE.match(item_id) or status not in STATUSES:
                        return self.send_error(400)
                    try:
                        store.set_status(item_id, status)
                    except KeyError:
                        return self.send_error(404)
                    _push_calendar_async(store, calendar)
                    nxt = _safe_next(form.get("next"))
                    kind = store.get_item(item_id).type
                    if status == "done" and kind not in NON_WORK_TYPES:  # ask how long it took
                        return self._send(303, location=f"/done?{urlencode({'id': item_id, 'next': nxt})}")
                    return self._send(303, location=nxt)
                if path in ("/plan/accept", "/plan/dismiss"):
                    from . import plan_service
                    block_id = form.get("id", "")
                    if not ITEM_ID_RE.match(block_id):
                        return self.send_error(400)
                    (plan_service.accept if path.endswith("accept") else plan_service.dismiss)(store, block_id)
                    _push_calendar_async(store, calendar)  # accepted blocks mirror to Google Calendar
                    return self._send(303, location=_safe_next(form.get("next")))
                if path == "/plan/edit":  # a drag on the grid (resize / move); answered in plain text
                    from datetime import date
                    from . import plan_service
                    kind, item_id = form.get("kind", ""), form.get("id", "")
                    try:
                        if not ITEM_ID_RE.match(item_id) or kind not in ("block", "event"):
                            raise ValueError("unknown block")
                        start, end = plan_service.local_span(date.fromisoformat(form.get("day", "")),
                                                             int(form.get("start_min", "")), int(form.get("end_min", "")))
                        ok = (plan_service.edit_block if kind == "block" else plan_service.edit_event)(store, item_id, start, end)
                        if not ok:
                            raise ValueError("that block no longer exists — the page will refresh")
                    except ValueError as e:
                        return self._text(400, str(e))
                    _push_calendar_async(store, calendar)
                    return self._text(200, "saved")
                if path == "/plan/add-block":  # an item or goal dragged onto the grid (plain-text answer)
                    from datetime import date
                    from . import plan_service
                    key = form.get("key", "")
                    try:
                        if not ITEM_ID_RE.match(key):
                            raise ValueError("unknown item")
                        start, end = plan_service.local_span(date.fromisoformat(form.get("day", "")),
                                                             int(form.get("start_min", "")), int(form.get("end_min", "")))
                        plan_service.add_block_for(store, key, start, end)
                    except ValueError as e:
                        return self._text(400, str(e))
                    _push_calendar_async(store, calendar)
                    return self._text(200, "saved")
                if path == "/plan/save":  # the editor dialog: new event, or exact times for a block/event
                    from . import plan_service
                    kind, item_id = form.get("kind", ""), form.get("id", "")
                    try:
                        start, end = plan_service.parse_times(form.get("day", ""), form.get("start", ""), form.get("end", ""))
                        if kind == "event" and not item_id:
                            plan_service.add_event(store, form.get("title", ""), start, end)
                        elif kind == "event" and ITEM_ID_RE.match(item_id):
                            if not plan_service.edit_event(store, item_id, start, end, form.get("title", "")):
                                raise ValueError("that event no longer exists")
                        elif kind == "block" and ITEM_ID_RE.match(item_id):
                            if not plan_service.edit_block(store, item_id, start, end):
                                raise ValueError("that block no longer exists")
                        elif kind == "block" and not item_id and ITEM_ID_RE.match(form.get("key", "")):
                            plan_service.add_block_for(store, form["key"], start, end)  # typed in from the editor
                        else:
                            raise ValueError("unknown block")
                    except ValueError as e:
                        return self._send(400, pages.render_plan(store, _flag(f"Not saved: {e}")))
                    _push_calendar_async(store, calendar)
                    return self._send(303, location="/plan")
                if path == "/plan/remove":
                    from . import plan_service
                    kind, item_id = form.get("kind", ""), form.get("id", "")
                    if not ITEM_ID_RE.match(item_id):
                        return self.send_error(400)
                    if kind == "event":
                        plan_service.delete_event(store, item_id)
                    else:
                        plan_service.dismiss(store, item_id)
                    _push_calendar_async(store, calendar)
                    return self._send(303, location="/plan")
                if path == "/plan/start":  # "Start now" from the Free-now card
                    from . import plan_service
                    item_id = form.get("id", "")
                    if not ITEM_ID_RE.match(item_id):
                        return self.send_error(400)
                    try:
                        plan_service.start_now(store, item_id)
                    except ValueError:  # the free moment passed meanwhile: just show the page again
                        return self._send(303, location=_safe_next(form.get("next")))
                    _push_calendar_async(store, calendar)
                    return self._send(303, location=_safe_next(form.get("next")))
                if path in ("/focus/start", "/focus/switch"):
                    from . import focus
                    if path == "/focus/switch":  # end the running session (time kept), then start this one
                        old_id, target = form.get("old", ""), form.get("block", "")
                        tb = store.get_block(target) if ITEM_ID_RE.match(target) else None
                        if tb is not None and tb.state == "suggested":  # keep it through the replan below
                            store.set_block_state(tb.id, "accepted")
                        if ITEM_ID_RE.match(old_id):
                            try:
                                focus.finish(store, old_id, "abandoned")
                            except ValueError:  # already ended (e.g. from the focus window)
                                pass
                        return self._send(303, location=f"/focus/start?block={form.get('block', '')}")
                    block_id = form.get("block", "")
                    if not ITEM_ID_RE.match(block_id):
                        return self.send_error(400)
                    try:
                        sid = focus.start(store, block_id, form.get("goal", ""))
                    except focus.SessionRunning:
                        return self._send(303, location=f"/focus/start?block={block_id}")
                    except ValueError as e:
                        return self._send(400, pages.render_focus_start(store, block_id, _flag(str(e))))
                    toggles = {k: form.get(k) == "1" for k in ("open_assignment", "open_music", "open_claude")}
                    try:  # remembered for next time (the assignment toggle only when it could apply)
                        keep = {k: v for k, v in toggles.items() if k != "open_assignment" or "open_assignment" in form}
                        focus.save_settings(store, **keep)
                    except ValueError:
                        pass
                    _push_calendar_async(store, calendar)  # the block is accepted now
                    sess = store.get_focus(sid)
                    _open_focus_links(store, sess, toggles)
                    if focus_window_open:
                        try:
                            focus_window_open(f"/focus/{sid}?win=1")
                            return self._send(303, location="/plan")
                        except Exception as e:  # fall back to the page in the main window
                            log.warning("focus window failed: %s", e)
                    return self._send(303, location=f"/focus/{sid}")
                m = SID_RE.match(path)
                if m and m.group(2) in ("/break", "/resume", "/extend", "/finish", "/open"):
                    from . import focus
                    sid, action = m.group(1), m.group(2)[1:]
                    if store.get_focus(sid) is None:
                        return self.send_error(404)
                    if action == "open":  # back to a running session (e.g. after reopening the app)
                        if focus_window_open:
                            try:
                                focus_window_open(f"/focus/{sid}?win=1")
                                return self._send(303, location="/plan")
                            except Exception as e:
                                log.warning("focus window failed: %s", e)
                        return self._send(303, location=f"/focus/{sid}")
                    if action != "finish":
                        {"break": focus.take_break, "resume": focus.resume, "extend": focus.extend}[action](store, sid)
                        if action == "extend":
                            _push_calendar_async(store, calendar)
                        return self._send(303, location=f"/focus/{sid}" + ("?win=1" if form.get("win") == "1" else ""))
                    try:
                        nxt = focus.finish(store, sid, form.get("outcome", ""))["next"]
                    except ValueError:
                        return self.send_error(400)
                    _push_calendar_async(store, calendar)
                    if form.get("win") == "1" and main_window_load:  # the small window: send the main one on, then close
                        try:
                            main_window_load(nxt)
                            if focus_window_close:
                                focus_window_close()
                            return self._send(200, "<!doctype html><title>Saved</title><p>Session saved.</p>")
                        except Exception as e:
                            log.warning("closing focus window failed: %s", e)
                    return self._send(303, location=nxt)
                if path in ("/debrief/save", "/debrief/later", "/debrief/skip"):
                    from . import debrief
                    nxt = _safe_next(form.get("next"))
                    item_id = form.get("id", "")
                    if not ITEM_ID_RE.match(item_id) or (item := store.get_item(item_id)) is None:
                        return self.send_error(400)
                    if path.endswith("later"):
                        debrief.later(store, item)
                    elif path.endswith("skip"):
                        debrief.skip(store, item)
                    else:
                        try:
                            debrief.save(store, item, form.get("went", ""), form.get("improve", ""),
                                         form.get("notes", ""), form.get("minutes", ""))
                        except ValueError as e:
                            return self._send(303, location=f"{nxt}?{urlencode({'msg': f'Not saved: {e}'})}")
                        return self._send(303, location=f"{nxt}?{urlencode({'msg': 'Saved your notes. They are on the Goals page.'})}")
                    return self._send(303, location=nxt)
                if path in ("/exam-prep/add", "/exam-prep/dismiss"):
                    from . import exam_prep, plan_service
                    nxt = _safe_next(form.get("next"))
                    item_id = form.get("id", "")
                    if not ITEM_ID_RE.match(item_id) or (item := store.get_item(item_id)) is None:
                        return self.send_error(400)
                    if path.endswith("dismiss"):
                        exam_prep.dismiss(store, item)
                        return self._send(303, location=nxt)
                    try:
                        exam_prep.add_goal(store, item, parse_opt_float(form.get("hours", "")))
                        plan_service.replan(store)
                    except ValueError as e:
                        return self._send(303, location=f"{nxt}?{urlencode({'msg': f'Goal not added: {e}'})}")
                    _push_calendar_async(store, calendar)
                    note = f"Added a study goal for {exam_prep.exam_name(item)}, planned into the days before the exam."
                    return self._send(303, location=f"{nxt}?{urlencode({'msg': note})}")
                if path == "/exam-prep/settings":
                    from . import exam_prep, plan_service
                    try:
                        exam_prep.save_settings(store, int(parse_opt_float(form.get("window", "")) or 0),
                                                *(parse_opt_float(form.get(k, "")) or 0 for k in ("quiz", "midterm", "final")))
                    except ValueError as e:
                        return self._send(400, render_settings(store, _flag(f"Midterm prep not saved: {e}")))
                    return self._send(303, location="/settings?saved=1#exam-prep")
                if path == "/focus/settings":
                    from . import focus
                    try:
                        focus.save_settings(store, music_url=form.get("music_url", ""),
                                            open_assignment=form.get("open_assignment") == "1",
                                            open_music=form.get("open_music") == "1",
                                            open_claude=form.get("open_claude") == "1",
                                            chime=form.get("chime") == "1",
                                            template=form.get("template", "") or focus.DEFAULT_TEMPLATE)
                    except ValueError as e:
                        return self._send(400, render_settings(store, _flag(f"Focus settings not saved: {e}")))
                    return self._send(303, location="/settings?saved=1#focus")
                if path == "/plan/day":  # "Plan my day"
                    from . import plan_service
                    r = plan_service.plan_day(store)
                    _push_calendar_async(store, calendar)
                    msg = (f"Planned the rest of today: {r['blocks']} block{'s' if r['blocks'] != 1 else ''}, "
                           f"{pages.estimates.fmt_minutes(r['minutes']) or '0m'}." if r["blocks"]
                           else "Nothing more fits today — you're free or already fully planned.")
                    nxt = _safe_next(form.get("next"))
                    return self._send(303, location=nxt + ("&" if "?" in nxt else "?") + urlencode({"msg": msg}))
                if path == "/plan/day/undo":
                    from . import plan_service
                    plan_service.undo_day(store)
                    _push_calendar_async(store, calendar)
                    return self._send(303, location=_safe_next(form.get("next")))
                if path == "/goals/add":
                    from datetime import date as _date
                    from . import plan_service
                    try:
                        until = _date.fromisoformat(form["until"]) if form.get("until") else None
                        kind = "by_date" if until else form.get("kind", "")  # picking a date means "by that date"
                        item_id = form.get("item_id") or None
                        if item_id and not ITEM_ID_RE.match(item_id):
                            raise ValueError("unknown item")
                        plan_service.add_goal(store, form.get("title", ""), form.get("course"), kind,
                                              parse_opt_float(form.get("hours", "")) or 0, until,
                                              int(parse_opt_float(form.get("session", "")) or 45), item_id)
                    except ValueError as e:
                        return self._send(400, pages.render_goals(store, _flag(f"Not added: {e}")))
                    return self._send(303, location="/goals?" + urlencode({"msg": "Goal added — it's in your work plan."}))
                if path in ("/goals/toggle", "/goals/delete"):
                    goal_id = form.get("id", "")
                    if not ITEM_ID_RE.match(goal_id):
                        return self.send_error(400)
                    if path.endswith("delete"):
                        store.delete_goal(goal_id)
                    else:
                        g = next((x for x in store.goals() if x["id"] == goal_id), None)
                        if g:
                            store.set_goal_active(goal_id, not g["active"])
                    _push_calendar_async(store, calendar)
                    return self._send(303, location="/goals")
                if path == "/plan/accept-day":
                    from . import plan_service
                    plan_service.accept_day(store, plan_service.day_start(store))
                    _push_calendar_async(store, calendar)
                    return self._send(303, location=_safe_next(form.get("next")))
                if path == "/plan/settings":
                    from . import plan_service
                    try:
                        plan_service.save_settings(store, form.get("wake", ""), form.get("sleep", ""),
                                                   parse_opt_float(form.get("max_block", "")) or 0,
                                                   int(parse_opt_float(form.get("days", "")) or 0),
                                                   form.get("style") or None,
                                                   parse_opt_float(form.get("max_day", "")))
                    except ValueError as e:
                        return self._send(400, render_settings(store, _flag(f"Planner not saved: {e}")))
                    return self._send(303, location="/plan?saved=1")
                if path == "/relevance":
                    item_id, choice = form.get("id", ""), form.get("choice", "")
                    if not ITEM_ID_RE.match(item_id) or choice not in ("show", "hide", "auto"):
                        return self.send_error(400)
                    try:
                        store.set_relevance(item_id, None if choice == "auto" else choice)
                    except KeyError:
                        return self.send_error(404)
                    _push_calendar_async(store, calendar)  # hidden items leave the calendar
                    return self._send(303, location=_safe_next(form.get("next")))
                if path == "/reminders/add":
                    try:
                        add_reminder(store, form)
                    except ValueError as e:
                        return self._send(400, render_reminders(store, _flag(f"Not added: {e}")))
                    _push_calendar_async(store, calendar)
                    return self._send(303, location="/reminders?added=1")
                if path == "/duration":
                    item_id, nxt = form.get("id", ""), _safe_next(form.get("next"))
                    if not ITEM_ID_RE.match(item_id) or (item := store.get_item(item_id)) is None:
                        return self.send_error(404)
                    try:
                        hours = parse_opt_float(form.get("hours", ""))
                        if hours is None:
                            raise ValueError("enter the hours it took, or Skip")
                        store.set_actual_duration(item_id, round(hours * 60))
                    except ValueError as e:
                        return self._send(400, render_done_prompt(store, item, nxt, _flag(f"Not saved: {e}")))
                    return self._send(303, location=nxt)
                if path == "/settings":
                    errors = apply_settings(store, form, ITEM_ID_RE)
                    _push_calendar_async(store, calendar)
                    if errors:
                        return self._send(400, render_settings(store, "".join(_flag(f"Not saved: {e}") for e in errors)))
                    return self._send(303, location="/settings?saved=1")
            self.send_error(404)

        def log_message(self, *args):  # keep the console quiet
            pass

    return Handler


def start(store: Store, host: str = "127.0.0.1", port: int = 8765, refresh: bool = True,
          calendar: bool = True, check_connections: bool = True) -> ThreadingHTTPServer:
    """Start the server + background work; returns the server (caller runs serve_forever or a window)."""
    syncer = Syncer(store, store_lock, calendar)
    conns = ConnectionManager(store, store_lock,
                              after_signin=(lambda: syncer.run(force=True)) if refresh else None)
    if check_connections:
        conns.refresh_async()
    else:
        conns.checks = []  # skip the check: /start goes straight to Today
    if refresh:
        stop = threading.Event()

        def loop():
            while True:  # sync on startup, then on a fixed interval — never continuous polling
                syncer.run()
                if stop.wait(REFRESH_EVERY.total_seconds()):
                    return

        threading.Thread(target=loop, daemon=True).start()
    return ThreadingHTTPServer((host, port), make_handler(store, port, calendar, conns, syncer if refresh else None))


def serve(store: Store, host: str = "127.0.0.1", port: int = 8765, refresh: bool = True,
          calendar: bool = True, check_connections: bool = True, open_browser: bool = True) -> None:
    server = start(store, host, port, refresh, calendar, check_connections)
    url = f"http://{host}:{port}/start"
    print(f"Dashboard at http://{host}:{port}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)  # opens on Connections first if anything needs signing in
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
