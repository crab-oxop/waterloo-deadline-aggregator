"""The dashboard's pages (v2 "Professional"). Pure functions: store in, HTML out.
The server lives in dashboard.py; the look (tokens, shell, pieces) in ui.py."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

from . import calendar_view, estimates, priority, relevance, reminders
from .models import NON_WORK_TYPES, Item
from .store import Store
from .ui import (COMPONENT_LABEL, SOURCE_LABEL, TYPE_LABEL, clock, colour_class, course_chip, esc, full_due, icon, local,
                 meta_html, now_utc, shell, short_date, tick_form, title_html, type_class)

STALE_AFTER = timedelta(days=1)
# Sources captured by hand on purpose (Quest: once per term) get a much longer leash.
CAPTURED = ("quest", "odyssey", "web")  # read during sign-in, not by the background sync
STALE_AFTER_BY_SOURCE = {"quest": timedelta(days=130), "odyssey": timedelta(days=3), "web": timedelta(days=3)}
STATUS_LABELS = {"not_started": "Not started", "in_progress": "In progress", "done": "Done"}
SORTS = {"priority": "Priority", "due": "Due date"}
FILTER_KEYS = ("course", "source", "type", "tab", "sort", "show", "q", "hidden")
TABS = {"all": ("All", None), "work": ("Assignments", {"assignment", "manual"}),
        "tests": ("Quizzes & exams", {"quiz", "exam"}), "events": ("Events", {"meeting", "announcement", "message"})}
CELL_LIMIT = 4
PRIO_LABEL = {"high": "High", "med": "Med", "low": "Low", "done": "Done"}

# Set by the server: how many connections need the user (shown as a badge on "Connections").
connection_attention = None
# Set by the server: the live-update token pages compare against /state.
state_token = None


# --- shared helpers ----------------------------------------------------------------

def is_overdue(it: Item, now: datetime) -> bool:
    return it.due_date is not None and it.due_date < now and it.status != "done" and it.type != "meeting"


def health(store: Store) -> tuple[list[str], datetime | None, list[str]]:
    """(problems worth flagging, most recent successful sync, names of sources that are fresh)."""
    now = now_utc()
    problems, latest, fresh = [], None, []
    for h in store.source_health():
        name = SOURCE_LABEL.get(h["source"], h["source"])
        last_ok = datetime.fromisoformat(h["last_success"]) if h["last_success"] else None
        limit = STALE_AFTER_BY_SOURCE.get(h["source"], STALE_AFTER)
        if h["last_error"]:
            problems.append(f"{name} sync failed: {h['last_error']}")
        elif last_ok is None or now - last_ok > limit:
            when = short_date(last_ok) if last_ok else "ever"
            problems.append(f"{name} hasn't updated since {when} — re-check its login or feed.")
        elif h["source"] not in CAPTURED:
            fresh.append(name.replace("Google Calendar", "Calendar"))
        if last_ok and h["source"] not in CAPTURED and (latest is None or last_ok > latest):
            latest = last_ok
    return problems, latest, fresh


def sync_state(store: Store) -> tuple[str, str, bool]:
    problems, latest, fresh = health(store)
    if problems:
        n = len(problems)
        return (f"{n} source{'s' if n > 1 else ''} need{'s' if n == 1 else ''} attention", "Open Connections", False)
    if latest:
        return f"Synced {clock(latest)}", " · ".join(fresh) or "All sources", True
    return "Not synced yet", "Open Connections", False


def flags_html(store: Store) -> str:
    return "".join(f'<div class="flag" role="status">{esc(p)}</div>' for p in health(store)[0])


def day_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = local(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def nav_counts(store: Store) -> dict[str, int]:
    now = now_utc()
    items = store.list_items()
    start, end = day_bounds(now)
    today = [i for i in items if i.status != "done" and
             (is_overdue(i, now) or (i.due_date and start <= i.due_date < end))]
    rems = [i for i in store.list_items(include_past=True) if i.type == "reminder" and i.status != "done"]
    return {"/today": len(today), "/reminders": len(rems),
            "/": sum(1 for i in items if i.status != "done" and i.type != "reminder")}


def page(store: Store, title: str, body: str, active: str, head_extra: str = "", heading: str = "",
         subtitle: str = "", query: str = "") -> str:
    from .sources.quest import term_label

    counts = nav_counts(store)
    if connection_attention:
        counts["/connections"] = connection_attention()
    body += debrief_dialog(store, active)
    return shell(title, body, active, counts, head_extra, heading=heading, subtitle=subtitle,
                 sync=sync_state(store), term=term_label(date.today()), query=query,
                 state=state_token() if state_token else "")


def rel_class(it: Item, now: datetime) -> str:
    if is_overdue(it, now):
        return " late"
    return " soon" if it.due_date and it.status != "done" and it.due_date - now < timedelta(days=2) else ""


def rel_text(it: Item, now: datetime) -> str:
    if it.due_date is None:
        return ""
    if it.extra.get("completed_at") and it.status == "done":
        return "submitted"
    return reminders.relative(it.due_date, now)


def when_html(it: Item, now: datetime) -> str:
    if it.due_date is None:
        return '<div class="when"><b>No date</b></div>'
    late = " late" if is_overdue(it, now) else ""
    return (f'<div class="when{late}"><b>{esc(short_date(it.due_date))}</b>'
            f'<span>{esc(clock(it.due_date))} · {esc(rel_text(it, now))}</span></div>')


def prio_html(it: Item) -> str:
    lvl = priority.level(it.priority_score, it.status)
    return (f'<span class="pbar p-{lvl}" title="Score {it.priority_score:.2f}"><i><b></b><b></b><b></b></i>'
            f'{PRIO_LABEL[lvl]}</span>')


def estimate_text(it: Item, cal: estimates.Calibration) -> tuple[str, str]:
    if it.status == "done":
        return estimates.fmt_minutes(it.actual_duration) or "—", "Actual time"
    if it.type in NON_WORK_TYPES:
        return "—", ""
    ratio, basis = cal.ratio_for(it)
    hint = f"Your {basis} take {ratio:.2f}× your estimates" if basis else "Default estimate"
    return "~" + estimates.fmt_minutes(estimates.estimate_minutes(it, cal)), hint


def simple_row(it: Item, now: datetime, next_path: str) -> str:
    """Compact row: tick · title/meta · when · priority. Used on Today."""
    return (f'<div class="row{" done" if it.status == "done" else ""}">{tick_form(it, next_path)}'
            f'<div style="min-width:0">{title_html(it)}{meta_html(it)}</div>{when_html(it, now)}'
            f'<div class="prio-cell">{prio_html(it)}</div></div>')


def _status_select(it: Item, next_path: str) -> str:
    opts = "".join(f'<option value="{s}"{" selected" if s == it.status else ""}>{l}</option>'
                   for s, l in STATUS_LABELS.items())
    return (f'<form method="post" action="/status" style="margin:0"><input type="hidden" name="id" value="{it.id}">'
            f'<input type="hidden" name="next" value="{esc(next_path)}">'
            f'<select name="status" onchange="this.form.submit()" aria-label="Status of {esc(it.title)}">{opts}</select>'
            '<noscript><button class="btn sm">Set</button></noscript></form>')


# --- Deadlines -----------------------------------------------------------------------

def _eye(it: Item, next_path: str) -> str:
    """Per-row Hide / Show: pins the item out of (or into) the academic view."""
    showing = relevance.shown(it)
    label = "Hide — not academic" if showing else "Always show"
    return (f'<form method="post" action="/relevance" style="margin:0"><input type="hidden" name="id" value="{it.id}">'
            f'<input type="hidden" name="choice" value="{"hide" if showing else "show"}">'
            f'<input type="hidden" name="next" value="{esc(next_path)}">'
            f'<button class="eyebtn" title="{label}" aria-label="{label}: {esc(it.title)}">{icon("eyeoff" if showing else "eye")}</button></form>')


def _why(it: Item) -> str:
    label = relevance.reason_label(it)
    return f'<span class="why">{esc(label)}</span>' if label and not relevance.shown(it) else ""


def _link(q: dict, **changes) -> str:
    params = {k: v for k, v in q.items() if k in FILTER_KEYS and v}
    for k, v in changes.items():
        if v:
            params[k] = v
        else:
            params.pop(k, None)
    return "/?" + urlencode(params) if params else "/"


def _bucket(it: Item, now: datetime) -> str:
    if it.status == "done":
        return "Completed"
    if it.due_date is None:
        return "No date"
    if is_overdue(it, now):
        return "Overdue"
    today = local(now).date()
    end_week = today + timedelta(days=6 - today.weekday())
    d = local(it.due_date).date()
    if d <= end_week:
        return "This week"
    return "Next week" if d <= end_week + timedelta(days=7) else "Later"


BUCKETS = ("Overdue", "This week", "Next week", "Later", "No date", "Completed")


def render(store: Store, query: dict[str, str] | None = None, notice: str = "") -> str:
    q = query or {}
    now = now_utc()
    show_hidden = q.get("hidden") == "1"
    everything = store.list_items(include_hidden=True)
    hidden_count = sum(1 for i in everything if not relevance.shown(i) and i.status != "done" and i.type != "reminder")
    all_items = everything if show_hidden else [i for i in everything if relevance.shown(i)]
    cal = estimates.calibrate(store.done_items())
    course, source, kind = q.get("course", ""), q.get("source", ""), q.get("type", "")
    tab = q.get("tab") if q.get("tab") in TABS else "all"
    sort = q.get("sort") if q.get("sort") in SORTS else "priority"
    show = q.get("show") if q.get("show") in ("all", "open") else "all"
    needle = q.get("q", "").strip().lower()

    def base(i: Item) -> bool:  # reminders have their own page; they only appear here when asked for by type
        return ((not course or (i.context or "") == course) and (not source or i.source == source)
                and (i.type == kind if kind else i.type != "reminder") and (show == "all" or i.status != "done")
                and (not needle or needle in i.title.lower() or needle in (i.context or "").lower()))

    pool = [i for i in all_items if base(i)]
    tab_counts = {k: sum(1 for i in pool if i.status != "done" and (types is None or i.type in types))
                  for k, (_, types) in TABS.items()}
    types = TABS[tab][1]
    items = [i for i in pool if types is None or i.type in types]
    if sort == "due":
        items.sort(key=lambda i: (i.due_date is None, i.due_date or now))

    tabs = "".join(f'<a href="{esc(_link(q, tab=None if k == "all" else k))}"{" aria-current=\"true\"" if k == tab else ""}>'
                   f'{esc(label)} <span>{tab_counts[k]}</span></a>' for k, (label, _) in TABS.items())
    courses = sorted({i.context for i in all_items if i.context})
    sources = sorted({i.source for i in all_items})
    keep = "".join(f'<input type="hidden" name="{k}" value="{esc(q[k])}">' for k in ("tab", "sort", "show", "q", "type") if q.get(k))

    def fsel(name, label, choices, current):
        opts = "".join(f'<option value="{esc(v)}"{" selected" if v == current else ""}>{esc(t)}</option>' for v, t in choices)
        return f'<select class="fsel" name="{name}" aria-label="{esc(label)}" onchange="this.form.submit()">{opts}</select>'

    toolbar = (f'<div class="toolbar"><div class="tabs" role="group" aria-label="Type">{tabs}</div>'
               '<form method="get" action="/" class="toolbar">'
               + fsel("course", "Course", [("", "All courses")] + [(c, c) for c in courses], course)
               + fsel("source", "Source", [("", "All sources")] + [(s, SOURCE_LABEL.get(s, s)) for s in sources], source)
               + keep + '<noscript><button class="btn sm">Apply</button></noscript></form>'
               '<div style="margin-left:auto;display:flex;gap:8px;align-items:center">'
               '<span style="color:var(--text3);font-size:13px">Sort</span><div class="tabs" role="group" aria-label="Sort">'
               + "".join(f'<a href="{esc(_link(q, sort=None if k == "priority" else k))}"{" aria-current=\"true\"" if k == sort else ""}>{l}</a>'
                         for k, l in SORTS.items())
               + '</div><div class="tabs" role="group" aria-label="Show">'
               + "".join(f'<a href="{esc(_link(q, show=None if k == "all" else k))}"{" aria-current=\"true\"" if k == show else ""}>{l}</a>'
                         for k, l in (("all", "All"), ("open", "Open")))
               + '</div>' + (f'<a class="btn sm" href="/">Clear</a>' if any(q.get(k) for k in FILTER_KEYS) else "")
               + '</div></div>')

    next_path = _link(q)
    grouped: dict[str, list[Item]] = defaultdict(list)
    for i in items:
        grouped[_bucket(i, now)].append(i)
    rows = []
    for b in BUCKETS:
        if not grouped.get(b):
            continue
        rows.append(f'<tr class="grp"><td colspan="9">{b} <span class="mono" style="font-weight:400">· {len(grouped[b])}</span></td></tr>')
        for it in grouped[b]:
            est, hint = estimate_text(it, cal)
            weight = f"{it.grade_weight:g}%" if it.grade_weight is not None else (
                f"{float(it.extra['points']):g} pts" if it.extra.get("points") else "—")
            src = SOURCE_LABEL.get(it.source, it.source)
            due = (f'<span class="mono">{esc(short_date(it.due_date))}</span>'
                   f'<span class="rel{rel_class(it, now)}">{esc(clock(it.due_date))} · {esc(rel_text(it, now))}</span>'
                   if it.due_date else '<span style="color:var(--text3)">No date</span>')
            rows.append(
                f'<tr class="{"done" if it.status == "done" else ""}{" muted" if not relevance.shown(it) else ""}">'
                f'<td style="width:40px;padding-right:0">{tick_form(it, next_path)}</td>'
                f'<td class="tcell">{title_html(it, "ttl")}<div class="meta" style="font-size:12px;color:var(--text3)">'
                f'{esc(TYPE_LABEL.get(it.type, it.type))}'
                f'{esc(" · " + it.extra["location"]) if it.extra.get("room") or it.extra.get("seat") else ""}'
                f'{_why(it)}</div></td>'
                f'<td>{course_chip(it.context) or "<span style=color:var(--text3)>—</span>"}</td>'
                f'<td class="hide-sm hide-lt" style="color:var(--text2)">{esc(src)}</td><td>{due}</td>'
                f'<td class="num mono hide-md">{esc(weight)}</td><td class="num mono hide-md" title="{esc(hint)}">{esc(est)}</td>'
                f'<td class="hide-sm">{prio_html(it)}</td><td class="hide-sm hide-lt"><div class="rowact">'
                f'{_status_select(it, next_path)}{_eye(it, next_path)}</div></td></tr>')
    table = ('<section class="card"><div class="tblwrap"><table class="tbl"><thead><tr><th></th><th>Title</th><th>Course</th>'
             '<th class="hide-sm hide-lt">Source</th><th>Due</th><th class="num hide-md">Weight</th><th class="num hide-md">Est.</th>'
             '<th class="hide-sm">Priority</th><th class="hide-sm hide-lt">Status</th></tr></thead><tbody>'
             + ("".join(rows) or f'<tr><td colspan="9" class="empty">{"Nothing matches." if q else "Nothing yet."}</td></tr>')
             + '</tbody></table></div></section>')
    open_items = [i for i in items if i.status != "done"]
    total = sum(estimates.estimate_minutes(i, cal) for i in open_items if i.type not in NON_WORK_TYPES)
    sub = f"{len(open_items)} open · ~{estimates.fmt_minutes(total) or '0m'} of work"
    if needle:
        sub += f" · matching “{q.get('q', '').strip()}”"
    if show_hidden:
        hint = (f'<div class="hidnote">{icon("eye")}Showing everything, including {hidden_count} non-academic '
                f'item{"s" if hidden_count != 1 else ""} (dimmed). <a href="{esc(_link(q, hidden=None))}">Academic only</a></div>')
    elif hidden_count:
        hint = (f'<div class="hidnote">{icon("eyeoff")}{hidden_count} non-academic item{"s" if hidden_count != 1 else ""} '
                f'hidden — residence, clubs, optional sessions. <a href="{esc(_link(q, hidden="1"))}">Show</a></div>')
    else:
        hint = ""
    body = flags_html(store) + notice + toolbar + hint + table
    return page(store, "Deadlines — Waterloo Deadlines", body, "/", heading="Deadlines", subtitle=sub,
                query=q.get("q", ""))


# --- Today ---------------------------------------------------------------------------

HOUR_PX = 56


def _timeline(store: Store, items: list[Item], local_now: datetime) -> tuple[str, str]:
    """(timeline html, end-of-day list html). Classes as coloured blocks, timed items as markers, a 'now' line."""
    today = local_now.date()
    classes = [c for c in store.classes() if c.meets_on(today)]
    timed = [i for i in items if clock(i.due_date) != "end of day"]
    eod = [i for i in items if clock(i.due_date) == "end of day"]
    hours = [c.start.hour for c in classes] + [local(i.due_date).hour for i in timed]
    ends = [c.end.hour + (1 if c.end.minute else 0) for c in classes] + [local(i.due_date).hour + 1 for i in timed]
    first, last = min([8] + hours), max([18] + ends)
    first = min(first, local_now.hour) if first <= local_now.hour < last + 1 else first

    def y(h: int, m: int = 0) -> float:
        return (h - first + m / 60) * HOUR_PX

    parts = []
    for h in range(first, last + 1):
        label = f"{(h - 1) % 12 + 1} {'AM' if h % 24 < 12 else 'PM'}"  # h can pass 24 (after midnight)
        parts.append(f'<div class="hour mono" style="top:{y(h):.0f}px">{label}</div><div class="hline" style="top:{y(h):.0f}px"></div>')
    now_t = local_now.time()
    for c in classes:
        top, height = y(c.start.hour, c.start.minute), y(c.end.hour, c.end.minute) - y(c.start.hour, c.start.minute)
        state = " past" if c.end <= now_t else ""
        kind = COMPONENT_LABEL.get(c.component, c.component)
        span = f'{c.start.strftime("%I:%M").lstrip("0")} – {c.end.strftime("%I:%M %p").lstrip("0")}'
        where = f" · {c.location}" if c.location else ""
        parts.append(f'<div class="blk {colour_class(c.course)}{state}" style="top:{top + 1:.0f}px;height:{max(height - 2, 22):.0f}px" '
                     f'title="{esc(c.title)}"><b>{esc(c.course)} · {esc(kind)}</b><span class="mono">{esc(span + where)}</span></div>')
    for i in timed:
        l = local(i.due_date)
        done = " done" if i.status == "done" else ""
        parts.append(f'<div class="tdue{done}" style="top:{y(l.hour, l.minute):.0f}px"><i></i>'
                     f'<span class="mono">{esc(clock(i.due_date))}</span> {esc(i.title)}</div>')
    if first <= local_now.hour < last + 1:
        parts.append(f'<div class="now" style="top:{y(local_now.hour, local_now.minute):.0f}px">'
                     f'<span>{local_now:%H:%M}</span></div>')
    height = y(last) + 4
    tl = f'<div class="tl" style="height:{height:.0f}px">{"".join(parts)}</div>'
    eod_html = "".join(f'<div class="it"><span class="tdot {type_class(i.type)}"></span>{course_chip(i.context)}'
                       f'<span class="t">{esc(i.title)}</span><span class="tm">by end of day</span></div>' for i in eod)
    return tl, eod_html


def _up_next(items: list[Item], start: datetime) -> str:
    tomorrow, horizon = start + timedelta(days=1), start + timedelta(days=7)
    ahead = sorted((i for i in items if i.due_date and tomorrow <= i.due_date < horizon and i.status != "done"),
                   key=lambda i: i.due_date)
    if not ahead:
        return '<div class="empty">Nothing in the next week.</div>'
    by_day: dict[date, list[Item]] = defaultdict(list)
    for i in ahead:
        by_day[local(i.due_date).date()].append(i)
    out = []
    for d, bucket in by_day.items():
        label = f"Tomorrow · {d:%a %b} {d.day}" if d == tomorrow.date() else f"{d:%a %b} {d.day}"
        out.append(f'<div class="dayg"><span>{label}</span><span>{len(bucket)} item{"s" if len(bucket) != 1 else ""}</span></div>')
        for i in bucket:
            tag, attrs = ("a", f' href="{esc(i.source_link)}" target="_blank" rel="noopener"') if i.source_link else ("div", "")
            lead = course_chip(i.context) or f'<span class="tdot {type_class(i.type)}"></span>'
            tm = "End of day" if clock(i.due_date) == "end of day" else clock(i.due_date)
            out.append(f'<{tag} class="it"{attrs}>{lead}<span class="t">{esc(i.title)}</span>'
                       f'<span class="tm mono">{esc(tm)}</span></{tag}>')
    return "".join(out) + '<div style="height:8px"></div>'


def _attention(store: Store) -> str:
    from .secrets import get_secret
    items = [esc(p) for p in health(store)[0]]
    if not get_secret("outlook_ics_url"):
        items.append("Outlook calendar isn't connected")
    if not store.classes():
        items.append("Quest schedule not captured — your classes will appear on the timeline")
    if not items:
        return ""
    rows = "".join(f'<div class="it"><span class="tdot" style="background:var(--warn)"></span>'
                   f'<span class="t" style="font-weight:400;color:var(--text2);white-space:normal">{i}</span></div>' for i in items)
    return (f'<section class="card"><div class="ch"><h2>Needs attention</h2><div class="r">'
            f'<a class="btn sm" href="/connections">Open Connections</a></div></div>{rows}</section>')


def render_today(store: Store, notice: str = "") -> str:
    now = now_utc()
    local_now = local(now)
    start, end = day_bounds(now)
    items = store.list_items()
    cal = estimates.calibrate(store.done_items())

    overdue = [i for i in items if is_overdue(i, now)]
    overdue_ids = {i.id for i in overdue}
    today = sorted((i for i in items if i.due_date and start <= i.due_date < end and i.id not in overdue_ids),
                   key=lambda i: (i.status == "done", i.due_date))
    todo = [i for i in overdue + today if i.status != "done"]
    week = [i for i in items if i.due_date and end <= i.due_date < start + timedelta(days=8) and i.status != "done"]
    soon = sum(1 for i in week if i.due_date < start + timedelta(days=4) and i.type not in NON_WORK_TYPES)
    work = sum(estimates.estimate_minutes(i, cal) for i in todo + week if i.type not in NON_WORK_TYPES)
    today_work = sum(estimates.estimate_minutes(i, cal) for i in todo if i.type not in NON_WORK_TYPES)

    kinds = sorted({TYPE_LABEL.get(i.type, i.type).lower() for i in todo})
    stats = (
        '<div class="stats">'
        f'<div class="stat"><div class="l">Due today</div><div class="v">{len(todo)}<small>{esc(", ".join(kinds) or "all clear")}</small></div></div>'
        f'<div class="stat"><div class="l">Next 7 days</div><div class="v">{len(week)}'
        f'<small class="{"up" if soon else ""}">{soon} deadline{"s" if soon != 1 else ""} in the next 3 days</small></div></div>'
        f'<div class="stat"><div class="l">Overdue</div><div class="v">{len(overdue)}'
        f'<small class="{"bad" if overdue else ""}">{"needs a look" if overdue else "all clear"}</small></div></div>'
        f'<div class="stat"><div class="l">Planned work · 7 days</div><div class="v mono">~{esc(estimates.fmt_minutes(work) or "0m")}</div></div>'
        '</div>')
    tl, eod = _timeline(store, today, local_now)
    if not store.classes():  # no timetable yet: an empty 10-hour grid is just noise
        timed = [i for i in today if clock(i.due_date) != "end of day"]
        tl = ('<div class="it" style="color:var(--text3)"><span class="t" style="font-weight:400;white-space:normal">'
              'Your classes appear here as a timeline once Quest is connected.</span>'
              '<a class="btn sm" href="/connections">Connect Quest</a></div>'
              + "".join(f'<div class="it"><span class="tdot {type_class(i.type)}"></span>'
                        f'<span class="tm mono">{esc(clock(i.due_date))}</span><span class="t">{esc(i.title)}</span></div>'
                        for i in timed))
    n_classes = sum(1 for c in store.classes() if c.meets_on(local_now.date()))
    sched_sub = (f"{n_classes} class{'es' if n_classes != 1 else ''} · {len(today)} item{'s' if len(today) != 1 else ''}"
                 if store.classes() else "Connect Quest to see your classes")
    schedule = (f'<section class="card"><div class="ch"><h2>Schedule</h2><span class="s">{esc(sched_sub)}</span>'
                '<div class="r"><a class="link" href="/calendar">Calendar</a></div></div>'
                + tl + (f'<div class="dayg" style="border-top:1px solid var(--line2)"><span>Due by end of day</span></div>{eod}' if eod else "")
                + '</section>')
    overdue_card = (f'<section class="card"><div class="ch hot"><h2>Overdue</h2><span class="s">{len(overdue)}</span></div>'
                    + "".join(simple_row(i, now, "/today") for i in overdue) + "</section>") if overdue else ""
    today_card = (f'<section class="card"><div class="ch"><h2>Due today</h2>'
                  f'<span class="s">{"~" + estimates.fmt_minutes(today_work) + " of work" if today_work else ""}</span></div>'
                  + ("".join(simple_row(i, now, "/today") for i in today) or '<div class="empty">Nothing due today.</div>')
                  + "</section>")
    parts = [f"{len(todo)} to do today" if todo else "Nothing due today"]
    body = (f'{notice}{focus_banner(store)}{exam_prep_cards(store, "/today")}{free_now_card(store, "/today")}{stats}{exam_countdown(store)}<div class="grid2"><div class="col">{schedule}{work_plan_card(store, now)}{overdue_card}{today_card}</div>'
            f'<div class="col"><section class="card"><div class="ch"><h2>Up next</h2><span class="s">next 7 days</span>'
            f'<div class="r"><a class="link" href="/?sort=due&amp;show=open">All deadlines</a></div></div>'
            f'{_up_next(items, start)}</section>{_attention(store)}</div></div>')
    subtitle = f"{local_now:%A, %B} {local_now.day} · {' · '.join(parts)}"
    return page(store, "Today — Waterloo Deadlines", body, "/today", heading="Today", subtitle=subtitle)


# --- Work plan (study blocks) -----------------------------------------------------------

def fmt_short(minutes: int) -> str:
    """'about 45 min' / 'about 2.5 h' — rounded, it's an estimate."""
    if minutes < 60:
        return f"about {max(5, round(minutes / 5) * 5)} min"
    return f"about {round(minutes / 30) / 2:g} h"


def _span(b) -> str:
    a, z = local(b.start), local(b.end)
    return f"{a:%I:%M}".lstrip("0") + "–" + f"{z:%I:%M %p}".lstrip("0")


def _block_actions(b, next_path: str, compact: bool = False) -> str:
    def form(action, ic, label, cls=""):
        return (f'<form method="post" action="{action}"><input type="hidden" name="id" value="{b.id}">'
                f'<input type="hidden" name="next" value="{esc(next_path)}">'
                f'<button class="icobtn{cls}" title="{label}" aria-label="{label}">{icon(ic)}</button></form>')
    if b.state == "accepted":
        return form("/plan/dismiss", "x", "Remove this block")
    return form("/plan/accept", "check", "Accept this block", " ok") + form("/plan/dismiss", "x", "Dismiss")


def _drag_attrs(key: str, title: str) -> str:
    """Makes an element a drag source for the plan grid (drop = a 30-minute block you can resize)."""
    return (f' data-drag="{esc(key)}" data-title="{esc(title)}" tabindex="0" role="button" '
            f'aria-label="{esc(title)}: drag onto the calendar, or press Enter to pick a time"')


def _shortfalls_html(result, items: dict, drag: bool = False) -> str:
    rows = []
    for sf in sorted(result.shortfalls, key=lambda x: x.due):
        it = items.get(sf.key)
        chip = course_chip(it.context) if it else ""
        attrs = _drag_attrs(sf.key, sf.title) if drag else ""
        grip = f'<span class="grip" aria-hidden="true">{icon("plus")}</span>' if drag else '<span class="tdot"></span>'
        rows.append(f'<div class="short{" dsrc" if drag else ""}"{attrs}>{grip}<div>{chip} <b>{esc(sf.title)}</b> — not enough free '
                    f'time before it\'s due ({esc(short_date(sf.due))}, {esc(clock(sf.due))}), '
                    f'<b>{fmt_short(sf.short)} short</b>.'
                    + (' <span class="dhint">Drag onto the calendar to make time.</span>' if drag else "")
                    + '</div></div>')
    for c in result.conflicts:
        it = items.get(c.block.key)
        rows.append(f'<div class="short"><span class="tdot" style="background:var(--warn)"></span><div>Accepted block '
                    f'{esc(short_date(c.block.start))} {esc(_span(c.block))} for <b>{esc(it.title if it else "an item")}</b> '
                    f'now overlaps <b>{esc(c.with_)}</b>. It hasn\'t been moved — remove it or keep it.</div></div>')
    return "".join(rows)


def _plan_state(store: Store):
    from . import plan_service
    result = plan_service.replan(store)
    items = plan_service.subjects(store)  # items and goals
    blocks = [b for b in store.plan_blocks(("suggested", "accepted")) if b.key in items]
    return result, items, blocks


def block_label(subject) -> str:
    """'Study: …' for exam/quiz prep and goals, the plain title for deliverables."""
    from . import plan_service
    title = subject.title
    return f"Study: {title}" if plan_service.is_study(subject) and not title.lower().startswith("study") else title


def plan_day_button(store: Store, next_path: str, size: str = "sm") -> str:
    """"Plan my day" — or, once a day plan exists, "Undo day plan"."""
    from . import plan_service
    if plan_service.day_plan_active(store):
        return (f'<form method="post" action="/plan/day/undo" style="margin:0"><input type="hidden" name="next" value="{esc(next_path)}">'
                f'<button class="btn {size}" title="Remove what Plan my day added">Undo day plan</button></form>')
    return (f'<form method="post" action="/plan/day" style="margin:0"><input type="hidden" name="next" value="{esc(next_path)}">'
            f'<button class="btn {size} pri" aria-label="Plan my day" '
            f'title="Fill the rest of today with work and study, around your classes">'
            f'{icon("plan")}Plan my day</button></form>')


def free_now_card(store: Store, next_path: str) -> str:
    """The planner's nudge: what to work on right now (or the block that's on), with one button."""
    from . import plan_service
    tip = plan_service.free_now(store)
    if not tip:
        return ""
    it = tip["item"]
    label = block_label(it)  # the course chip beside it already names the course
    if tip["kind"] == "block":
        b = tip["block"]
        when = f"until {clock(b.end)}" if tip["live"] else f"at {clock(b.start)}"
        head = ("Work block on now" if tip["live"] else "Coming up") if b.state == "accepted" else \
            ("Suggested right now" if tip["live"] else "Suggested next")
        action = ("" if b.state == "accepted" else
                  f'<form method="post" action="/plan/accept" style="margin:0"><input type="hidden" name="id" value="{b.id}">'
                  f'<input type="hidden" name="next" value="{esc(next_path)}">'
                  f'<button class="btn pri">{icon("check")}Start it</button></form>')
        text = f'<b>{esc(label)}</b> <span class="mono">{esc(_span(b))}</span> · {esc(when)}'
    else:
        head = f"You're free until {clock(tip['until'])}"
        action = (f'<form method="post" action="/plan/start" style="margin:0"><input type="hidden" name="id" value="{it.id}">'
                  f'<input type="hidden" name="next" value="{esc(next_path)}">'
                  f'<button class="btn pri">{icon("check")}Start now · {esc(estimates.fmt_minutes(tip["minutes"]))}</button></form>')
        due = f"due {short_date(it.due_date)}, {clock(it.due_date)}" if it.due_date else ""
        text = f'Good time for <b>{esc(label)}</b>{" · " + esc(due) if due else ""}'
    return (f'<section class="card nudge" aria-live="polite"><div class="nudge-in">'
            f'<div class="nudge-ic">{icon("plan")}</div><div class="nudge-tx"><div class="nudge-h">{esc(head)}</div>'
            f'<div class="nudge-b">{course_chip(it.context)} {text}</div></div>{action}</div></section>')



def work_plan_card(store: Store, now: datetime) -> str:
    """Today's suggested/accepted blocks, plus anything that won't fit — on the Today tab."""
    from . import plan_service
    result, items, blocks = _plan_state(store)
    start = plan_service.day_start(store, now)  # the planning day: 1 AM still counts as last night
    end = start + timedelta(days=1)
    today = [b for b in blocks if start <= b.start < end and b.end > now]
    sug = [b for b in today if b.state == "suggested"]
    mins = sum(b.minutes for b in today)
    head = (f'<div class="ch"><h2>Work plan</h2><span class="s">{"~" + estimates.fmt_minutes(mins) + " today" if mins else ""}</span>'
            '<div class="r">'
            + (f'<form method="post" action="/plan/accept-day" style="margin:0"><input type="hidden" name="next" value="/today">'
               f'<button class="btn sm">{icon("check")}Accept all for today</button></form>' if sug else "")
            + plan_day_button(store, "/today")
            + '<a class="link" href="/plan">Week</a></div></div>')
    rows = "".join(
        f'<div class="wb{" sug" if b.state == "suggested" else ""}"><span class="when mono">{esc(_span(b))}</span>'
        f'{course_chip(items[b.key].context)}<span class="t">{esc(block_label(items[b.key]))}</span>'
        f'<span class="badge {"b-ok" if b.state == "accepted" else "b-mute"} hide-sm">{"Accepted" if b.state == "accepted" else "Suggested"}</span>'
        f'<div class="acts">{_block_actions(b, "/today")}</div></div>' for b in today)
    if not rows:
        rows = '<div class="empty">No work blocks left today.</div>'
    return f'<section class="card">{head}{_shortfalls_html(result, items)}{rows}</section>'


ZOOM_LEVELS = (0.4, 0.55, 0.8, 1.1, 1.5, 2.0)  # px per minute on the plan grid (0.8 = 48 px per hour)


# Each day on the grid runs until 3 AM the next morning, so late nights can be planned on the
# evening they belong to. Anything between midnight and 3 AM shows under the previous day.
LATE_END = 27 * 60


def _wall_min(dt: datetime, d: date) -> int:
    """Minutes from midnight of `d` to `dt`, on the wall clock (so DST days line up with the grid)."""
    wall = local(dt).replace(tzinfo=None)
    return int((wall - datetime.combine(d, datetime.min.time())).total_seconds() // 60)


def _hm(m: int) -> str:
    h, mm = divmod(m % (24 * 60), 60)
    return f"{(h - 1) % 12 + 1}:{mm:02d} {'AM' if h < 12 else 'PM'}"


# The grid is laid out in *minutes* (CSS variables --s/--e per entry, --g0/--g1 for the range) and
# CSS turns minutes into pixels with one variable, --px — so zooming is just changing --px.
PLAN_JS = """<script>
(function(){
  var grid=document.getElementById('pgrid'); if(!grid) return;
  var ZOOMS=%(zooms)s, SNAP=5, MINLEN=15, KEY='plan-zoom', SKEY='plan-scroll';
  var dlg=document.getElementById('pdlg'), f=dlg&&dlg.querySelector('form');
  function F(n){return f.elements.namedItem(n);}  // not f.title / f.id: those are the form's own attributes
  function G0(){return +grid.dataset.start;} function G1(){return +grid.dataset.end;}
  function px(){return parseFloat(getComputedStyle(grid).getPropertyValue('--px'))||0.8;}
  function hm(m){m=Math.round(m);m=((m%%1440)+1440)%%1440;var h=Math.floor(m/60),x=m%%60;return (((h+11)%%12)+1)+':'+(x<10?'0':'')+x+(h<12?' AM':' PM');}
  function t24(m){m=((m%%1440)+1440)%%1440;var h=Math.floor(m/60),x=m%%60;return (h<10?'0':'')+h+':'+(x<10?'0':'')+x;}
  function snap(m){return Math.round(m/SNAP)*SNAP;}
  function setSE(el,s,e){el.style.setProperty('--s',s);el.style.setProperty('--e',e);
    var t=el.querySelector('.tm'); if(t) t.textContent=hm(snap(s))+' – '+hm(snap(e));}
  function busy(on){if(on)document.body.dataset.busy='1';else delete document.body.dataset.busy;}

  // --- zoom: buttons, Ctrl+wheel (anchored at the pointer), remembered between visits ---
  var zlvl=document.getElementById('zlvl');
  function zoomTo(z, anchorY){ z=Math.max(ZOOMS[0],Math.min(ZOOMS[ZOOMS.length-1],z));
    var old=px(), head=grid.querySelector('.pg-head').offsetHeight;
    var y=(anchorY==null?grid.clientHeight/2:anchorY)-head, m=(grid.scrollTop+y)/old;
    grid.classList.add('instant'); grid.style.setProperty('--px',z); grid.scrollTop=m*z-y;
    requestAnimationFrame(function(){requestAnimationFrame(function(){grid.classList.remove('instant');});});
    if(zlvl) zlvl.textContent=Math.round(z/0.8*100)+'%%'; try{localStorage.setItem(KEY,z);}catch(e){} }
  function zstep(dir,anchorY){var c=px(),i=0;for(var k=0;k<ZOOMS.length;k++){if(Math.abs(ZOOMS[k]-c)<1e-6)i=k;}
    var j=Math.max(0,Math.min(ZOOMS.length-1,(ZOOMS.indexOf(c)<0?ZOOMS.findIndex(function(v){return v>c;}):i)+dir)); zoomTo(ZOOMS[j],anchorY);}
  var zi=document.getElementById('zin'), zo=document.getElementById('zout'), zf=document.getElementById('zfit');
  if(zi) zi.addEventListener('click',function(){zstep(1);}); if(zo) zo.addEventListener('click',function(){zstep(-1);});
  if(zf) zf.addEventListener('click',function(){var h=grid.clientHeight-grid.querySelector('.pg-head').offsetHeight-8;
    zoomTo(h/Math.max(60,G1()-G0())); grid.scrollTop=0;});
  grid.addEventListener('wheel',function(ev){ if(!ev.ctrlKey&&!ev.metaKey) return; ev.preventDefault();
    var r=grid.getBoundingClientRect(); zstep(ev.deltaY<0?1:-1, ev.clientY-r.top); },{passive:false});
  try{var z=parseFloat(localStorage.getItem(KEY)); if(z) grid.style.setProperty('--px',z);}catch(e){}
  if(zlvl) zlvl.textContent=Math.round(px()/0.8*100)+'%%';

  // --- scroll: keep the grid where you left it (edits, live refreshes, reloads) ---
  function saveScroll(){try{sessionStorage.setItem(SKEY,JSON.stringify([grid.scrollTop,grid.scrollLeft]));}catch(e){}}
  (function(){var v=null;try{v=JSON.parse(sessionStorage.getItem(SKEY));}catch(e){}
    if(v){grid.scrollTop=v[0];grid.scrollLeft=v[1];}
    else { var c=grid.querySelector('.pg-col.today'), n=c&&c.dataset.now?+c.dataset.now:G0();
      grid.scrollTop=Math.max(0,(n-60-G0())*px()); } })();
  grid.addEventListener('scroll',function(){ if(!grid.__t) grid.__t=setTimeout(function(){grid.__t=0;saveScroll();},150); });
  window.addEventListener('beforeunload',saveScroll);

  // --- after a change: swap in the re-planned grid in place, and let blocks glide to their new spots ---
  function refresh(){ busy(true);
    return fetch(location.pathname,{cache:'no-store'}).then(function(r){return r.text();}).then(function(html){
      var doc=new DOMParser().parseFromString(html,'text/html'), ng=doc.getElementById('pgrid');
      if(!ng){location.reload();return;}
      var before={}; grid.querySelectorAll('.pgi[data-id]').forEach(function(el){before[el.dataset.id]=el.getBoundingClientRect();});
      var st=grid.scrollTop, sl=grid.scrollLeft;
      grid.dataset.start=ng.dataset.start; grid.dataset.end=ng.dataset.end;
      grid.style.setProperty('--g0',ng.style.getPropertyValue('--g0')); grid.style.setProperty('--g1',ng.style.getPropertyValue('--g1'));
      grid.style.setProperty('--days',ng.style.getPropertyValue('--days'));
      document.getElementById('pg-head').innerHTML=doc.getElementById('pg-head').innerHTML;
      document.getElementById('pg-body').innerHTML=doc.getElementById('pg-body').innerHTML;
      ['plan-warn','plan-accept','plan-nudge','plan-day','plan-tray'].forEach(function(id){var a=document.getElementById(id),b=doc.getElementById(id);if(a&&b)a.innerHTML=b.innerHTML;});
      var sub=document.querySelector('.top .dt'), nsub=doc.querySelector('.top .dt'); if(sub&&nsub) sub.textContent=nsub.textContent;
      grid.scrollTop=st; grid.scrollLeft=sl;
      grid.querySelectorAll('.pgi[data-id]').forEach(function(el){ var o=before[el.dataset.id], n=el.getBoundingClientRect();
        if(o){var dx=o.left-n.left, dy=o.top-n.top; if(Math.abs(dx)<1&&Math.abs(dy)<1) return;
          el.style.transition='none'; el.style.transform='translate('+dx+'px,'+dy+'px)'; el.getBoundingClientRect();
          el.style.transition=''; el.style.transform='';}
        else { el.classList.add('fresh'); requestAnimationFrame(function(){requestAnimationFrame(function(){el.classList.remove('fresh');});}); } });
    }).catch(function(){location.reload();}).then(function(){busy(false);}); }
  window.__softRefresh=refresh;  // the live-update poll uses this instead of reloading the page
  function send(data){busy(true);
    return fetch('/plan/edit',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:new URLSearchParams(data)})
      .then(function(r){return r.text().then(function(t){if(!r.ok)alert(t||'Could not save that change.');return refresh();});})
      .catch(function(){location.reload();});}

  // --- the editor dialog ---
  function openDlg(o){ if(!dlg) return;
    f.reset(); delete f.dataset.sent;
    F('kind').value=o.kind; F('id').value=o.id||''; F('key').value=o.key||''; F('day').value=o.day; F('start').value=t24(o.s); F('end').value=t24(o.e);
    F('title').value=o.kind==='event'?(o.title||''):'';
    var fb=document.getElementById('dlg-focus'); if(fb) fb.value=o.id||'';
    dlg.querySelector('.dt').textContent=o.heading;
    dlg.querySelector('.ttl-row').hidden=(o.kind==='block');
    dlg.querySelector('.itm').textContent=o.kind==='block'?o.title:'';
    var tag=o.kind+(o.id?'-'+(o.state||'saved'):'-new');
    dlg.querySelectorAll('[data-for]').forEach(function(b){b.hidden=b.dataset.for.split(' ').indexOf(tag)<0;});
    saveScroll(); busy(true); dlg.showModal(); (o.kind==='event'?F('title'):F('start')).focus({preventScroll:true}); }
  if(dlg){ dlg.addEventListener('close',function(){busy(false);});
    dlg.querySelectorAll('[data-close]').forEach(function(b){b.addEventListener('click',function(){dlg.close();});});
    f.addEventListener('submit',saveScroll); }
  var addBtn=document.getElementById('addev');
  if(addBtn) addBtn.addEventListener('click',function(){var c=grid.querySelector('.pg-col.today')||grid.querySelector('.pg-col');
    var s=Math.max(G0(),Math.min(G1()-60,+(c.dataset.now||G0()))); s=Math.ceil(s/15)*15;
    openDlg({kind:'event',day:c.dataset.date,s:s,e:s+60,heading:'New event'});});
  function info(el){return {kind:el.dataset.kind,id:el.dataset.id,state:el.dataset.state,day:el.closest('.pg-col').dataset.date,
    s:+el.dataset.s,e:+el.dataset.e,title:el.dataset.title,heading:el.dataset.kind==='block'?'Work block':'Your event'};}

  // --- drag: resize (edges), move (body, across days), draw (empty space). Follows the pointer
  //     smoothly; snaps to 5 minutes when you let go. ---
  var drag=null, frame=0, lastEv=null, cols=[], pend=null;
  function colAtX(x){for(var i=0;i<cols.length;i++){if(x>=cols[i].l&&x<cols[i].r)return cols[i].el;}return null;}
  grid.addEventListener('pointerdown',function(ev){
    if(ev.button!==0||ev.target.closest('button,a,form'))return;
    var el=ev.target.closest('.pb'), col=ev.target.closest('.pg-col'); if(!col) return;
    cols=[].map.call(grid.querySelectorAll('.pg-col'),function(c){var r=c.getBoundingClientRect();return {el:c,l:r.left,r:r.right};});
    var P=px(), rect=col.getBoundingClientRect(), m=snap(G0()+(ev.clientY-rect.top)/P);
    if(el){ var h=ev.target.closest('.hdl');
      drag={el:el,mode:h?h.dataset.edge:'move',P:P,y0:ev.clientY,x0:ev.clientX,s0:+el.dataset.s,e0:+el.dataset.e,s:+el.dataset.s,e:+el.dataset.e,col:col,moved:false};
    } else if(!ev.target.closest('.pe')) {
      var g=document.createElement('div'); g.className='pgi pb own ghost'; g.innerHTML='<div class="pbi"><span class="tm"></span></div>'; col.appendChild(g);
      drag={el:g,mode:'draw',P:P,y0:ev.clientY,x0:ev.clientX,s0:m,e0:m,s:m,e:m+30,col:col,moved:false}; setSE(g,m,m+30);
    } else return;
    ev.preventDefault(); grid.setPointerCapture(ev.pointerId); busy(true);
  });
  function step(){ frame=0; var ev=lastEv; if(!drag||!ev) return;
    var d=drag, dy=(ev.clientY-d.y0)/d.P, g0=G0(), g1=G1();
    if(!d.moved&&(Math.abs(ev.clientY-d.y0)>3||Math.abs(ev.clientX-d.x0)>3)){
      d.moved=true; d.el.classList.add('dragging'); if(d.mode!=='draw'){d.el.style.setProperty('--lane',0);d.el.style.setProperty('--lanes',1);}}
    if(!d.moved) return;
    if(d.mode==='top'){d.s=Math.min(Math.max(g0,d.s0+dy),d.e0-MINLEN);d.e=d.e0;}
    else if(d.mode==='bottom'){d.e=Math.max(Math.min(g1,d.e0+dy),d.s0+MINLEN);d.s=d.s0;}
    else if(d.mode==='draw'){var a=d.s0,b=d.s0+dy;d.s=Math.max(g0,Math.min(a,b));d.e=Math.min(g1,Math.max(a,b,d.s+MINLEN));}
    else { var len=d.e0-d.s0; d.s=Math.min(Math.max(g0,d.s0+dy),g1-len); d.e=d.s+len;
      var c=colAtX(ev.clientX); if(c&&c!==d.col){c.appendChild(d.el);d.col=c;} }
    setSE(d.el,d.s,d.e);
  }
  grid.addEventListener('pointermove',function(ev){ if(!drag) return; lastEv=ev; if(!frame) frame=requestAnimationFrame(step); });
  function end(ev){ if(!drag) return; if(frame){cancelAnimationFrame(frame);frame=0;} lastEv=ev; step(); var d=drag; drag=null;
    d.el.classList.remove('dragging');
    var s=snap(d.s), e=snap(d.e); if(e-s<MINLEN) e=s+MINLEN;
    if(d.mode==='draw'){ if(!d.moved){s=d.s0;e=d.s0+60;} d.el.remove();
      openDlg({kind:'event',day:d.col.dataset.date,s:s,e:e,heading:'New event'}); return; }
    if(!d.moved){ busy(false); var el=d.el;
      if(pend&&pend.el===el){ clearTimeout(pend.t); pend=null;  // second click: a double-click
        location.href='/focus/start?block='+encodeURIComponent(el.dataset.id);  // blocks and own events
        return; }
      if(pend) clearTimeout(pend.t);
      pend={el:el,t:setTimeout(function(){pend=null;openDlg(info(el));},280)};  // single click: the editor
      return; }
    if(d.col.dataset.now&&e<=+d.col.dataset.now){ busy(false); refresh(); return; }  // entirely in the past: snap back
    setSE(d.el,s,e);  // settles onto the 5-minute grid with a short transition
    if(s===d.s0&&e===d.e0&&d.col.dataset.date===d.el.dataset.day){ busy(false); refresh(); return; }
    send({kind:d.el.dataset.kind,id:d.el.dataset.id,day:d.col.dataset.date,start_min:s,end_min:e});
  }
  // --- drag in from outside the grid ("Won't fit" rows, goal chips): a 30-minute block where you
  //     drop it — resize it afterwards. Click / Enter instead opens the editor to type a time. ---
  var ext=null, extFrame=0, extEv=null;
  function extStep(){ extFrame=0; var ev=extEv; if(!ext||!ev) return;
    ext.float.style.transform='translate('+(ev.clientX+12)+'px,'+(ev.clientY+8)+'px)';
    if(!ext.moved&&(Math.abs(ev.clientX-ext.x0)>4||Math.abs(ev.clientY-ext.y0)>4)){ext.moved=true;ext.float.hidden=false;busy(true);
      cols=[].map.call(grid.querySelectorAll('.pg-col'),function(c){var r=c.getBoundingClientRect();return {el:c,l:r.left,r:r.right};});}
    if(!ext.moved) return;
    var gr=grid.getBoundingClientRect(), edge=40;  // scroll the grid when dragging near its edge
    if(ev.clientY<gr.top+edge+30) grid.scrollTop-=12; else if(ev.clientY>gr.bottom-edge) grid.scrollTop+=12;
    if(ev.clientX<gr.left+edge+52) grid.scrollLeft-=12; else if(ev.clientX>gr.right-edge) grid.scrollLeft+=12;
    cols=[].map.call(grid.querySelectorAll('.pg-col'),function(c){var r=c.getBoundingClientRect();return {el:c,l:r.left,r:r.right};});
    var c=(ev.clientY>gr.top&&ev.clientY<gr.bottom)?colAtX(ev.clientX):null;
    if(!c){ if(ext.ghost){ext.ghost.remove();ext.ghost=null;} ext.col=null; return; }
    var P=px(), m=snap(G0()+(ev.clientY-c.getBoundingClientRect().top)/P-15); m=Math.max(G0(),Math.min(G1()-30,m));
    if(!ext.ghost){ext.ghost=document.createElement('div');ext.ghost.className='pgi pb acc ghost dragging';
      ext.ghost.innerHTML='<div class="pbi"><span class="tm"></span><span class="lb"></span></div>';}
    if(ext.ghost.parentNode!==c) c.appendChild(ext.ghost);
    var past=c.dataset.now&&m+30<=+c.dataset.now; ext.past=!!past; ext.ghost.classList.toggle('past',!!past);
    ext.ghost.querySelector('.lb').textContent=past?'Already passed — drop later':ext.title; setSE(ext.ghost,m,m+30); ext.col=c; ext.m=m;
    if(!extFrame&&ext) extFrame=requestAnimationFrame(extStep);  // keep auto-scrolling while held near an edge
  }
  document.addEventListener('pointerdown',function(ev){ var src=ev.target.closest&&ev.target.closest('[data-drag]');
    if(!src||ev.button!==0||ev.target.closest('a,button,form')) return;
    var fl=document.createElement('div'); fl.className='dfloat'; fl.textContent=src.dataset.title; fl.hidden=true; document.body.appendChild(fl);
    ext={src:src,key:src.dataset.drag,title:src.dataset.title,x0:ev.clientX,y0:ev.clientY,float:fl,moved:false,ghost:null,col:null};
    src.setPointerCapture(ev.pointerId); ev.preventDefault();
  });
  document.addEventListener('pointermove',function(ev){ if(!ext) return; extEv=ev; if(!extFrame) extFrame=requestAnimationFrame(extStep); });
  function extEnd(ev,cancel){ if(!ext) return;
    if(!cancel&&ev){ if(extFrame){cancelAnimationFrame(extFrame);extFrame=0;} extEv=ev; extStep(); }  // drop where you let go, even after a fast drag
    var e=ext; ext=null; if(extFrame){cancelAnimationFrame(extFrame);extFrame=0;}
    e.float.remove();
    if(!e.moved&&!cancel){ busy(false); pickTime(e); return; }
    if(cancel||!e.col||e.past){ if(e.ghost) e.ghost.remove(); busy(false); return; }
    e.ghost.classList.remove('dragging');
    fetch('/plan/add-block',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},
      body:new URLSearchParams({key:e.key,day:e.col.dataset.date,start_min:e.m,end_min:e.m+30})})
      .then(function(r){return r.text().then(function(t){if(!r.ok){alert(t||'Could not add that.');} return refresh();});})
      .catch(function(){location.reload();});
  }
  document.addEventListener('pointerup',function(ev){extEnd(ev,false);});
  document.addEventListener('pointercancel',function(ev){extEnd(ev,true);});
  document.addEventListener('keydown',function(ev){var src=ev.target.closest&&ev.target.closest('[data-drag]');
    if(src&&(ev.key==='Enter'||ev.key===' ')){ev.preventDefault();pickTime({key:src.dataset.drag,title:src.dataset.title});}});
  function pickTime(e){ var c=grid.querySelector('.pg-col.today')||grid.querySelector('.pg-col');
    var s=Math.max(G0(),Math.min(G1()-30,+(c.dataset.now||G0()))); s=Math.ceil(s/15)*15;
    openDlg({kind:'block',key:e.key,day:c.dataset.date,s:s,e:s+30,title:e.title,heading:'Make time for'}); }

  (function(){ var m=/[?&]edit=([0-9a-f]{32})/.exec(location.search); if(!m) return;
    var el=grid.querySelector('.pb[data-id="'+m[1]+'"]'); if(el){ el.scrollIntoView({block:'center'}); openDlg(info(el)); } })();
  grid.addEventListener('pointerup',end);
  grid.addEventListener('pointercancel',function(){if(drag){drag=null;refresh();}});
  grid.addEventListener('keydown',function(ev){var el=ev.target.closest&&ev.target.closest('.pb');
    if(el&&(ev.key==='Enter'||ev.key===' ')){ev.preventDefault();openDlg(info(el));}});
})();
</script>""" % {"zooms": list(ZOOM_LEVELS)}


def _plan_dialog() -> str:
    return (
        '<dialog id="pdlg" class="pdlg" aria-labelledby="pdlg-h"><form method="post" action="/plan/save">'
        '<h2 id="pdlg-h" class="dt">Edit</h2><div class="itm"></div>'
        '<input type="hidden" name="kind"><input type="hidden" name="id"><input type="hidden" name="key">'
        '<label class="ttl-row">Name<input name="title" maxlength="120" autocomplete="off" placeholder="e.g. Gym, work shift"></label>'
        '<label>Day<input type="date" name="day" required></label>'
        '<div class="two"><label>Start<input type="time" name="start" step="300" required></label>'
        '<label>End<input type="time" name="end" step="300" required></label></div>'
        '<p class="hint">An end before the start means after midnight.</p>'
        '<div class="acts">'
        '<button class="btn" formaction="/plan/remove" formnovalidate data-for="block-suggested block-accepted event-saved">Remove</button>'
        '<button class="btn" formaction="/plan/accept" formnovalidate data-for="block-suggested">Accept</button>'
        '<button class="btn" formaction="/focus/start" formmethod="get" formnovalidate data-for="block-suggested block-accepted event-saved" '
        'name="block" id="dlg-focus">Focus</button>'
        '<span style="flex:1"></span>'
        '<button type="button" class="btn" data-close>Cancel</button>'
        '<button class="btn pri">Save</button></div>'
        '<input type="hidden" name="next" value="/plan"></form></dialog>')


def _lanes(day_entries: list) -> list[tuple[int, int, int, int, str]]:
    """Side by side, not on top of each other: overlapping entries share the column width.
    Returns (start, end, lane, lanes in its cluster, html)."""
    out, cluster, cluster_end = [], [], None

    def flush():
        lane_ends: list[int] = []
        placed = []
        for m0, m1, h in cluster:
            lane = next((i for i, e in enumerate(lane_ends) if e <= m0), None)
            if lane is None:
                lane_ends.append(m1)
                lane = len(lane_ends) - 1
            else:
                lane_ends[lane] = m1
            placed.append((m0, m1, lane, h))
        out.extend((m0, m1, lane, len(lane_ends), h) for m0, m1, lane, h in placed)

    for m0, m1, h in sorted(day_entries, key=lambda e: (e[0], -e[1])):
        if cluster and m0 >= cluster_end:
            flush()
            cluster, cluster_end = [], None
        cluster.append((m0, m1, h))
        cluster_end = m1 if cluster_end is None else max(cluster_end, m1)
    if cluster:
        flush()
    return out


def render_plan(store: Store, notice: str = "") -> str:
    from . import plan_service
    now = now_utc()
    s = plan_service.load_settings(store)
    result, items, blocks = _plan_state(store)
    busy = [b for b in plan_service.busy_from(store, store.list_items(include_past=True), now, s)]
    own = store.plan_events()
    own_labels = {(e["start"], e["end"], e["title"]) for e in own}
    busy = [b for b in busy if (b.start, b.end, b.label) not in own_labels]  # own events drawn separately
    today = s.day_of(now)  # until you wake up it's still the day before: 1 AM Saturday is Friday night
    days = [today + timedelta(days=n) for n in range(s.days)]
    clash_ids = {c.block.id for c in result.conflicts}

    # Grid range: awake hours, stretched to fit anything scheduled outside them.
    wake_m = s.wake.hour * 60 + s.wake.minute
    sleep_m = s.sleep.hour * 60 + s.sleep.minute + (24 * 60 if s.overnight else 0)
    entries: list[tuple[date, int, int, str]] = []  # (column day, start min, end min, html)

    def column_for(start: datetime, end: datetime):
        d = local(start).date()
        m0, m1 = _wall_min(start, d), _wall_min(end, d)
        late = m0 < LATE_END - 24 * 60 or (s.overnight and m0 + 24 * 60 <= sleep_m + 120)
        if late and m0 < wake_m and (d - timedelta(days=1)) in days:
            d, m0, m1 = d - timedelta(days=1), m0 + 24 * 60, m1 + 24 * 60  # after-midnight: previous evening
        return (d, m0, m1) if d in days else None

    for bz in busy:
        c = column_for(bz.start, bz.end)
        if c:
            entries.append((*c, f'<div class="pgi pe busy" style="{{pos}}"><div class="pbi"><span class="tm">{esc(_span(bz))}</span>'
                                f'<span class="lb">{esc(bz.label)}</span></div></div>'))
    for b in blocks:
        if b.end <= now:
            continue
        c = column_for(b.start, b.end)
        if not c:
            continue
        it = items[b.key]
        base = block_label(it)
        label = base if not it.context or it.context in it.title else f"{it.context} · {base}"
        cls = ("acc" if b.state == "accepted" else "sug") + (" clash" if b.id in clash_ids else "")
        cls += " study" if base.startswith("Study") else ""
        entries.append((*c, (
            f'<div class="pgi pb {cls}" style="{{pos}}" tabindex="0" role="button" data-kind="block" data-id="{b.id}" '
            f'data-day="{c[0].isoformat()}" data-state="{b.state}" data-s="{c[1]}" data-e="{c[2]}" data-title="{esc(label)}" '
            f'aria-label="{"Accepted" if b.state == "accepted" else "Suggested"} work block: {esc(label)}, {esc(_span(b))}. Enter to edit">'
            '<i class="hdl" data-edge="top"></i><div class="pbi">'
            f'<span class="tm">{esc(_span(b))}</span><span class="lb">{esc(label)}</span>'
            f'<span class="st">{"accepted" if b.state == "accepted" else "suggested"}</span>'
            f'<span class="row2">{_block_actions(b, "/plan")}</span>'
            '</div><i class="hdl" data-edge="bottom"></i></div>')))
    for e in own:
        if e["end"] <= now:
            continue
        c = column_for(e["start"], e["end"])
        if not c:
            continue
        entries.append((*c, (
            f'<div class="pgi pb own" style="{{pos}}" tabindex="0" role="button" data-kind="event" data-id="{e["id"]}" '
            f'data-day="{c[0].isoformat()}" data-s="{c[1]}" data-e="{c[2]}" data-title="{esc(e["title"])}" '
            f'aria-label="Your event: {esc(e["title"])}. Enter to edit, double-click to focus">'
            '<i class="hdl" data-edge="top"></i><div class="pbi">'
            f'<span class="tm">{esc(_hm(c[1]))} – {esc(_hm(c[2]))}</span><span class="lb">{esc(e["title"])}</span>'
            '</div><i class="hdl" data-edge="bottom"></i></div>')))

    g0 = min([wake_m] + [m0 for _, m0, _, _ in entries]) // 60 * 60
    g1 = -(-max([sleep_m, LATE_END] + [m1 for _, _, m1, _ in entries]) // 60) * 60

    def pos(m0: int, m1: int, lane: int, n: int) -> str:
        return f"--s:{max(m0, g0)};--e:{min(m1, g1)};--lane:{lane};--lanes:{n}"

    hours = "".join(f'<div class="pg-hr" style="--s:{m}"><span>{esc(_hm(m).replace(":00", ""))}</span></div>'
                    for m in range(g0, g1 + 1, 60))
    heads, cols = ['<div class="pg-corner"></div>'], []
    for d in days:
        planned = sum(b.minutes for b in blocks if b.end > now and (c := column_for(b.start, b.end)) and c[0] == d)
        heads.append(f'<div class="pg-dh{" today" if d == today else ""}"><b>{d:%a} {d.day}</b>'
                     f'<span>{esc(estimates.fmt_minutes(planned) or "")}</span></div>')
        shade = ""
        if wake_m > g0:
            shade += f'<div class="pg-sleep" style="--s:{g0};--e:{wake_m}"></div>'
        if sleep_m < g1:
            shade += f'<div class="pg-sleep" style="--s:{sleep_m};--e:{g1}"></div>'
        now_m = _wall_min(now, d) if d == today else None
        now_line = f'<div class="pg-now" style="--s:{now_m}"></div>' if now_m is not None and g0 <= now_m <= g1 else ""
        body = "".join(h.replace("{pos}", pos(m0, m1, lane, n))
                       for m0, m1, lane, n, h in _lanes([(m0, m1, h) for dd, m0, m1, h in entries if dd == d]))
        cols.append(f'<div class="pg-col{" today" if d == today else ""}" data-date="{d.isoformat()}"'
                    f'{f" data-now={now_m}" if now_m is not None else ""}>{shade}{body}{now_line}</div>')

    grid = (f'<div class="pgrid" id="pgrid" data-start="{g0}" data-end="{g1}" '
            f'style="--days:{len(days)};--g0:{g0};--g1:{g1}" tabindex="-1">'
            f'<div class="pg-head" id="pg-head">{"".join(heads)}</div>'
            f'<div class="pg-body" id="pg-body"><div class="pg-times">{hours}</div>{"".join(cols)}</div></div>')

    warn = _shortfalls_html(result, items, drag=True)
    goals_now = [g for g in store.goals(active_only=True)]
    tray = ('<div class="tray"><span class="tray-l">Add time — drag onto the calendar:</span>'
            + "".join(f'<span class="dchip dsrc"{_drag_attrs(g["id"], g["title"])}>{course_chip(g["course"])}'
                      f'<span>Study: {esc(g["title"])}</span></span>' for g in goals_now)
            + '<a class="link" href="/goals">Goals</a></div>') if goals_now else ""
    warn_card = (f'<section class="card"><div class="ch hot"><h2>Won\'t fit</h2>'
                 f'<span class="s">{len(result.shortfalls)} item{"s" if len(result.shortfalls) != 1 else ""}'
                 f'{f" · {len(result.conflicts)} conflict" + ("s" if len(result.conflicts) != 1 else "") if result.conflicts else ""}</span></div>'
                 f'{warn}</section>') if warn else ""
    sug_today = any(b.state == "suggested" and s.day_of(b.start) == today and b.end > now for b in blocks)
    accept_all = ('<form method="post" action="/plan/accept-day" style="margin:0"><input type="hidden" name="next" value="/plan">'
                  f'<button class="btn sm">{icon("check")}Accept all for today</button></form>' if sug_today else "")
    zoom = ('<div class="zoom" role="group" aria-label="Zoom">'
            '<button type="button" class="btn sm" id="zout" title="Zoom out (Ctrl + scroll)" aria-label="Zoom out">−</button>'
            '<span id="zlvl" class="mono">100%</span>'
            '<button type="button" class="btn sm" id="zin" title="Zoom in (Ctrl + scroll)" aria-label="Zoom in">+</button>'
            '<button type="button" class="btn sm" id="zfit" title="Fit the whole day">Fit</button></div>')
    bar = ('<div class="toolbar"><span style="color:var(--text2);font-size:13px">'
           f'Awake {s.wake:%H:%M}–{s.sleep:%H:%M} · blocks up to {estimates.fmt_minutes(int(s.max_block.total_seconds() // 60))} · '
           f'drag edges to resize, drag blocks to move, drag empty space to add an event, Ctrl + scroll to zoom · '
           f'<a class="link" href="/settings#planner">Sleep &amp; planner settings</a></span>'
           f'<div style="margin-left:auto;display:flex;gap:8px;align-items:center">{zoom}'
           f'<button type="button" class="btn sm" id="addev">{icon("plus")}Add event</button>'
           f'<span id="plan-accept">{accept_all}</span>'
           f'<span id="plan-day">{plan_day_button(store, "/plan")}</span></div></div>')
    total = sum(b.minutes for b in blocks if b.end > now)
    accepted = sum(b.minutes for b in blocks if b.end > now and b.state == "accepted")
    body = (f'{notice}{focus_banner(store)}{exam_prep_cards(store, "/plan")}<div id="plan-nudge">{free_now_card(store, "/plan")}</div><div id="plan-warn">{warn_card}</div>'
            f'<div id="plan-tray">{tray}</div>{bar}<section class="card pgwrap">{grid}</section>'
            f'{_plan_dialog()}{PLAN_JS}')
    sub = (f"~{estimates.fmt_minutes(total) or '0m'} of work planned over {s.days} days · "
           f"{estimates.fmt_minutes(accepted) or 'none'} accepted · suggestions only until you accept or edit them")
    return page(store, "Work plan — Waterloo Deadlines", body, "/plan", heading="Work plan", subtitle=sub)


def planner_settings_card(store: Store) -> str:
    from . import plan_service
    s = plan_service.load_settings(store)
    return ('<section class="card" id="planner"><div class="ch"><h2>Sleep &amp; work planner</h2>'
            '<span class="s">blocks start 30 min after you wake and end 30 min before bed · Proactive starts work earlier and aims to finish a day ahead</span></div>'
            '<form method="post" action="/plan/settings" class="planset">'
            f'<label>Wake up<input type="time" name="wake" value="{s.wake:%H:%M}" required></label>'
            f'<label>Go to sleep<input type="time" name="sleep" value="{s.sleep:%H:%M}" required></label>'
            f'<label>Longest block (hours)<input type="number" name="max_block" min="0.5" max="8" step="0.25" '
            f'value="{s.max_block.total_seconds() / 3600:g}" required style="width:110px"></label>'
            f'<label>Days to plan ahead<input type="number" name="days" min="1" max="14" step="1" value="{s.days}" '
            'required style="width:110px"></label>'
            f'<label>Max work per day (hours)<input type="number" name="max_day" min="1" max="16" step="0.5" '
            f'value="{s.max_per_day.total_seconds() / 3600:g}" required style="width:110px"></label>'
            '<label>Planning style<select name="style">'
            + "".join(f'<option value="{k}"{" selected" if k == plan_service.style_of(store) else ""}>{esc(v)}</option>'
                      for k, v in plan_service.STYLE_LABELS.items())
            + '</select></label>'
            '<button class="btn pri">Save</button></form></section>')



# --- Goals -------------------------------------------------------------------------------

def _progress_bar(done: int, planned: int, target: int) -> str:
    target = max(target, 1)
    d = min(100, done * 100 / target)
    pl = min(100 - d, planned * 100 / target)
    return (f'<div class="gbar" role="img" aria-label="{done} of {target} minutes done, {planned} planned">'
            f'<i style="width:{d:.1f}%"></i><b style="width:{pl:.1f}%"></b></div>')


def render_goals(store: Store, notice: str = "") -> str:
    from . import exam_prep, plan_service
    exam_prep.follow_exams(store)  # out-of-date goals go before the list is drawn
    goals = store.goals()
    courses = sorted({i.context for i in store.list_items() if i.context and any(ch.isdigit() for ch in i.context)})
    rows = []
    for g in goals:
        prog = plan_service.goal_progress(store, g)
        when = "this week" if g["kind"] == "weekly" else f"by {g['until']:%a %b} {g['until'].day}" if g["until"] else ""
        target = f"{g['hours']:g} h {'/ week' if g['kind'] == 'weekly' else 'total'}"
        state = "" if g["active"] else '<span class="badge b-mute">Paused</span>'
        rows.append(
            f'<div class="goal{"" if g["active"] else " paused"}"><div class="gmain">'
            f'<div class="gtop">{course_chip(g["course"])}<b>{esc(g["title"])}</b>{state}</div>'
            f'<div class="gmeta">{esc(target)} · {int(g["session_min"])} min sessions · '
            f'{esc(estimates.fmt_minutes(prog["done"]) or "0m")} done, {esc(estimates.fmt_minutes(prog["planned"]) or "0m")} planned {esc(when)}</div>'
            f'{_progress_bar(prog["done"], prog["planned"], prog["target"])}</div>'
            '<div class="gacts">'
            f'<form method="post" action="/goals/toggle" style="margin:0"><input type="hidden" name="id" value="{g["id"]}">'
            f'<button class="btn sm">{"Pause" if g["active"] else "Resume"}</button></form>'
            f'<form method="post" action="/goals/delete" style="margin:0"><input type="hidden" name="id" value="{g["id"]}">'
            f'<button class="btn sm" title="Delete goal">{icon("x")}</button></form></div></div>')
    listing = ('<section class="card"><div class="ch"><h2>Your goals</h2>'
               f'<span class="s">{sum(1 for g in goals if g["active"])} active</span></div>'
               + ("".join(rows) or '<div class="empty">No goals yet — add one below, or start from a suggestion.</div>')
               + '</section>')

    sugg = plan_service.suggested_goals(store)
    sugg_rows = "".join(
        f'<form method="post" action="/goals/add" class="gsug"><input type="hidden" name="title" value="{esc(x["title"])}">'
        f'<input type="hidden" name="course" value="{esc(x["item"].context or "")}"><input type="hidden" name="kind" value="by_date">'
        f'<input type="hidden" name="hours" value="{x["hours"]:g}"><input type="hidden" name="until" value="{local(x["item"].due_date).date().isoformat()}">'
        f'<input type="hidden" name="session" value="{60 if x["hours"] >= 4 else 30}"><input type="hidden" name="item_id" value="{x["item"].id}">'
        f'{course_chip(x["item"].context)}<span class="t">{esc(x["title"])}</span>'
        f'<span class="tm mono">{esc(short_date(x["item"].due_date))}</span>'
        f'<button class="btn sm">{icon("plus")}{x["hours"]:g} h goal</button></form>' for x in sugg[:8])
    suggestions = ('<section class="card"><div class="ch"><h2>Suggested from your tests</h2>'
                   '<span class="s">quizzes and exams in the next 3 weeks</span></div>'
                   + (sugg_rows or '<div class="empty">Nothing coming up without a goal.</div>') + '</section>')

    course_opts = "".join(f'<option value="{esc(c)}">{esc(c)}</option>' for c in courses)
    form = ('<section class="card" id="add"><div class="ch"><h2>New goal</h2>'
            '<span class="s">study time the planner fits in after deadline work</span></div>'
            '<form method="post" action="/goals/add" class="planset">'
            '<label style="flex:1;min-width:220px">Goal<input name="title" required maxlength="120" '
            'placeholder="e.g. Review MATH 137 lectures, Practice CS 135 problems"></label>'
            f'<label>Course<select name="course"><option value="">(none)</option>{course_opts}</select></label>'
            '<label>Type<select name="kind" id="gkind"><option value="weekly">Hours per week</option>'
            '<option value="by_date">Hours before a date</option></select></label>'
            '<label>Hours<input type="number" name="hours" min="0.25" max="80" step="0.25" value="3" required style="width:90px"></label>'
            '<label>By date<input type="date" name="until" id="guntil"></label>'
            '<label>Session<select name="session"><option value="30">30 min</option><option value="45" selected>45 min</option>'
            '<option value="60">1 h</option><option value="90">1.5 h</option><option value="120">2 h</option></select></label>'
            '<button class="btn pri">Add goal</button></form>'
            # picking a date switches to "before a date"; switching to weekly clears the date
            '<script>(function(){var k=document.getElementById("gkind"),u=document.getElementById("guntil");'
            'if(!k||!u)return;u.addEventListener("change",function(){if(u.value)k.value="by_date";});'
            'k.addEventListener("change",function(){u.required=k.value==="by_date";if(k.value==="weekly")u.value="";});})();</script>'
            '</section>')
    body = f'{notice}{listing}<div class="grid2"><div class="col">{form}</div><div class="col">{suggestions}</div></div>{debrief_list(store)}'
    return page(store, "Goals — Waterloo Deadlines", body, "/goals", heading="Goals",
                subtitle="Study time beyond deadlines · sessions are fitted into free time on the Work plan, one per goal per day")



# --- Test check-ins ---------------------------------------------------------------------------

def debrief_dialog(store: Store, next_path: str) -> str:
    """After a test, the next page opened asks how it went. If a test is still to come, the page
    reloads itself once it's over — on the next tick, or as soon as the window is looked at again
    (opening the laptop) — so the question is waiting."""
    from . import debrief
    if store.running_focus():
        return ""
    now = now_utc()
    pend = debrief.pending(store, now)
    nxt = debrief.next_end(store, now)
    watch = ""
    if nxt and not pend:
        watch = ("<script>(function(){var at=%d;function chk(){if(Date.now()>=at)location.reload();}"
                 "setInterval(chk,60000);document.addEventListener('visibilitychange',function(){if(!document.hidden)chk();});"
                 "window.addEventListener('focus',chk);})();</script>" % int(nxt.timestamp() * 1000))
    if not pend:
        return watch
    i = pend[0]
    name = i.title  # the full name: "MATH 137 Quiz 3", not just "quiz"
    mins = debrief.scheduled_minutes(i)
    hid = f'<input type="hidden" name="id" value="{i.id}"><input type="hidden" name="next" value="{esc(next_path)}">'
    more = f'<span class="hint">{len(pend) - 1} more after this one</span>' if len(pend) > 1 else ""
    return (
        '<dialog id="debrief" class="pdlg dbf" aria-labelledby="dbf-h"><form method="post" action="/debrief/save">'
        f'{hid}<h2 id="dbf-h">How did {esc(name)} go?</h2>'
        f'<div class="itm">{esc(short_date(i.due_date))}, {esc(clock(i.due_date))}{" · " + esc(i.extra.get("location", "")) if i.extra.get("location") else ""}</div>'
        '<label>How did it go?<textarea name="went" rows="3" maxlength="2000" autofocus></textarea></label>'
        '<label>What could be improved for next time?<textarea name="improve" rows="3" maxlength="2000"></textarea></label>'
        '<label>Other notes<textarea name="notes" rows="2" maxlength="2000"></textarea></label>'
        f'<label>How long did it take? (minutes)<input type="number" name="minutes" min="1" max="720" step="1" '
        f'value="{mins or ""}" style="width:110px"></label>'
        f'<div class="acts">{more}<span style="flex:1"></span>'
        '<button class="btn" formaction="/debrief/skip" formnovalidate>Skip</button>'
        '<button class="btn" formaction="/debrief/later" formnovalidate>Later</button>'
        '<button class="btn pri">Save</button></div></form></dialog>'
        "<script>(function(){var d=document.getElementById('debrief');if(d&&d.showModal)d.showModal();})();</script>")


def debrief_list(store: Store) -> str:
    """Goals page: what the user wrote after his tests, newest first."""
    rows = []
    for d in store.debriefs()[:12]:
        i = store.get_item(d["item_id"])
        if i is None:
            continue
        name = i.title
        parts = [(lbl, d[k]) for lbl, k in (("How it went", "went"), ("Improve", "improve"), ("Notes", "notes")) if d[k]]
        took = f' · took {estimates.fmt_minutes(d["minutes"])}' if d["minutes"] else ""
        rows.append(f'<div class="goal"><div class="gmain"><div class="gtop">{course_chip(i.context)}<b>{esc(name)}</b>'
                    f'<span class="gmeta" style="margin:0">{esc(short_date(i.due_date)) if i.due_date else ""}{esc(took)}</span></div>'
                    + "".join(f'<div class="gmeta"><b>{esc(lbl)}:</b> {esc(v)}</div>' for lbl, v in parts) + '</div></div>')
    if not rows:
        return ""
    return ('<section class="card"><div class="ch"><h2>After your tests</h2><span class="s">your check-ins</span></div>'
            + "".join(rows) + '</section>')


# --- Midterm prep ---------------------------------------------------------------------------

def exam_prep_cards(store: Store, next_path: str) -> str:
    """"MATH 137 midterm, Tue Oct 20: add a study goal? 8h over 10 days" — one card per exam in
    the look-ahead window without a goal. Suggest only: nothing is created until Add."""
    from . import debrief, exam_prep
    out = []
    for x in exam_prep.suggestions(store)[:3]:
        i, h = x["item"], x["hours"]
        span = "today" if x["days"] <= 0 else f"over {x['days']} day{'s' if x['days'] != 1 else ''}"
        hid = f'<input type="hidden" name="id" value="{i.id}"><input type="hidden" name="next" value="{esc(next_path)}">'
        out.append(
            f'<section class="card nudge prep"><div class="nudge-in"><div class="nudge-ic">{icon("target")}</div>'
            f'<div class="nudge-tx"><div class="nudge-h">{esc(exam_prep.exam_name(i))}, {esc(short_date(i.due_date))}: add a study goal?</div>'
            f'<div class="nudge-b"><b>{h:g}h</b> {esc(span)} · fitted into free time before the exam</div>'
            + (f'<div class="nudge-b">Last time you wrote: <i>{esc(tip)}</i></div>' if (tip := debrief.last_improvement(store, i.context)) else "")
            + '</div>'
            f'<form method="post" action="/exam-prep/add" style="margin:0">{hid}<button class="btn pri">{icon("plus")}Add</button></form>'
            f'<details class="prep-h"><summary class="btn">Change hours</summary>'
            f'<form method="post" action="/exam-prep/add" class="prep-f">{hid}'
            f'<input type="number" name="hours" min="0.5" max="80" step="0.5" value="{h:g}" aria-label="Study hours"> h'
            '<button class="btn pri sm">Add</button></form></details>'
            f'<form method="post" action="/exam-prep/dismiss" style="margin:0">{hid}<button class="btn">Not this one</button></form>'
            '</div></section>')
    return "".join(out)


def exam_countdown(store: Store) -> str:
    """Today: "MATH 137 midterm in 9 days · 3h of 8h studied" for the next scheduled exam."""
    from . import exam_prep
    c = exam_prep.countdown(store)
    if c is None:
        return ""
    studied = (f' · {estimates.fmt_minutes(c["studied"]) or "0h"} of {estimates.fmt_minutes(c["target"])} studied'
               if c["target"] else "")
    return (f'<div class="exam-cd">{icon("target")}<span><b>{esc(c["name"])}</b> {esc(c["when"])}{esc(studied)}</span>'
            f'<span class="mono">{esc(short_date(c["item"].due_date))}, {esc(clock(c["item"].due_date))}</span></div>')


def exam_prep_settings_card(store: Store) -> str:
    from . import exam_prep
    s = exam_prep.load_settings(store)

    def num(name, label, val, lo, hi, step):
        return (f'<label>{esc(label)}<input type="number" name="{name}" value="{val:g}" min="{lo}" max="{hi}" '
                f'step="{step}" style="width:90px"></label>')
    return ('<section class="card" id="exam-prep"><div class="ch"><h2>Midterm prep</h2>'
            '<span class="s">study goals offered for upcoming exams</span></div>'
            '<form method="post" action="/exam-prep/settings" class="planset">'
            + num("window", "Offer a goal this many days ahead", s.window, 1, 60, 1)
            + num("quiz", "Quiz (h)", s.quiz, 0.5, 80, 0.5)
            + num("midterm", "Midterm (h)", s.midterm, 0.5, 80, 0.5)
            + num("final", "Final (h)", s.final, 0.5, 80, 0.5)
            + '<button class="btn pri">Save</button></form></section>')

# --- Focus sessions ------------------------------------------------------------------------

def _focus_subject(store: Store, sess_or_block_key: str):
    from . import focus
    return focus.subject_for(store, sess_or_block_key)


def focus_banner(store: Store) -> str:
    """While a session runs (or was left running when the app closed): where it is, and how to get back."""
    from . import focus
    s = store.running_focus()
    if not s:
        return ""
    subj = _focus_subject(store, s["item_id"])
    st = focus.timer(s)
    state = ("on a break" if st.on_break else f"{focus.fmt_clock(st.remaining)} left" if not st.over
             else f"+{focus.fmt_clock(st.over_by)} over")
    title = block_label(subj) if subj else "a removed item"
    return (f'<section class="card nudge"><div class="nudge-in"><div class="nudge-ic">{icon("plan")}</div>'
            f'<div class="nudge-tx"><div class="nudge-h">Focus session running</div>'
            f'<div class="nudge-b"><b>{esc(title)}</b> · {esc(state)}</div></div>'
            f'<form method="post" action="/focus/{s["id"]}/open" style="margin:0"><button class="btn pri">Open</button></form>'
            f'<form method="post" action="/focus/{s["id"]}/finish" style="margin:0"><input type="hidden" name="outcome" value="abandoned">'
            '<button class="btn">End</button></form></div></section>')


def render_focus_start(store: Store, block_id: str, notice: str = "") -> str:
    from . import focus
    block = focus.slot(store, block_id) if block_id else None
    running = store.running_focus()
    if block is None or block.state == "dismissed":
        body = (f'{notice}<section class="card"><div class="empty">That block is no longer on the plan. '
                '<a class="link" href="/plan">Back to the work plan</a></div></section>')
        return page(store, "Focus — Waterloo Deadlines", body, "/plan", heading="Focus session")
    subj = _focus_subject(store, block.key)
    if running and running["block_id"] != block.id:
        other = _focus_subject(store, running["item_id"])
        body = (f'{notice}<section class="card" style="max-width:560px"><div class="ch"><h2>A session is already running</h2></div>'
                f'<div style="padding:16px 18px;display:flex;flex-direction:column;gap:12px">'
                f'<div><b>{esc(block_label(other) if other else "A removed item")}</b> — '
                f'{esc(focus.fmt_clock(focus.timer(running).worked))} focused so far.</div>'
                '<div style="display:flex;gap:8px;flex-wrap:wrap">'
                f'<form method="post" action="/focus/{running["id"]}/open" style="margin:0"><button class="btn pri">Go to it</button></form>'
                f'<form method="post" action="/focus/switch" style="margin:0"><input type="hidden" name="old" value="{running["id"]}">'
                f'<input type="hidden" name="block" value="{block.id}"><button class="btn">End it and start this one</button></form>'
                '<a class="btn" href="/plan">Back</a></div></div></section>')
        return page(store, "Focus — Waterloo Deadlines", body, "/plan", heading="Focus session")
    if running and running["block_id"] == block.id:
        return render_focus_session(store, running["id"])
    fs = focus.load_settings(store)
    has_link = bool(getattr(subj, "source_link", None))
    label = block_label(subj) if subj else "This item was removed"
    due = getattr(subj, "due_date", None)
    facts = " · ".join(x for x in (
        getattr(subj, "context", None) or "",
        f"due {short_date(due)}, {clock(due)}" if due else ("your event" if getattr(subj, "type", "") == "event" else "no due date"),
        f"{estimates.fmt_minutes(block.minutes)} planned ({_span(block)}, {short_date(block.start)})") if x)

    def toggle(name, text, on, disabled=False, note=""):
        return (f'<label class="ftog{" off" if disabled else ""}"><input type="checkbox" name="{name}" value="1"'
                f'{" checked" if on and not disabled else ""}{" disabled" if disabled else ""}> {esc(text)}'
                f'{f" <span>{esc(note)}</span>" if note else ""}</label>')

    body = (
        f'{notice}<section class="card fstart"><form method="post" action="/focus/start" class="fform">'
        f'<input type="hidden" name="block" value="{block.id}">'
        f'<div class="fhead">{course_chip(getattr(subj, "context", None))}<h2>{esc(label)}</h2></div>'
        f'<div class="fmeta">{esc(facts)}</div>'
        f'<div class="fdl">{esc(focus.deadline_line(store, subj))}</div>'
        '<label class="fgoal">Session goal<input name="goal" maxlength="200" autofocus autocomplete="off" '
        'placeholder="By the end I\'ll have…"></label>'
        '<label class="fphone"><input type="checkbox" name="phone" value="1"> Phone out of reach?</label>'
        '<div class="ftogs">'
        + toggle("open_assignment", "Open the assignment", fs.open_assignment, not has_link,
                 "" if has_link else "no link for this one")
        + toggle("open_music", "Open music", fs.open_music)
        + toggle("open_claude", "Open Claude as a tutor", fs.open_claude)
        + '</div><div class="facts">'
        f'<button class="btn pri big">{icon("check")}Start</button>'
        f'<a class="btn" href="/plan?edit={block.id}">Edit times</a><a class="btn" href="/plan">Back</a>'
        '</div></form></section>')
    return page(store, "Focus — Waterloo Deadlines", body, "/plan", heading="Focus session",
                subtitle="Enter starts · the timer runs for the whole block · take breaks when you need them")


def _others_due(store: Store, exclude: str, now: datetime) -> list[str]:
    soon = [i for i in store.list_items() if i.id != exclude and i.status != "done" and i.due_date
            and now < i.due_date < now + timedelta(days=4) and i.type not in NON_WORK_TYPES]
    soon.sort(key=lambda i: i.due_date)
    return [f"{(i.context + ' ') if i.context else ''}{i.title} — due {short_date(i.due_date)}, {clock(i.due_date)}"
            for i in soon[:5]]


def focus_claude_text(store: Store, s: dict) -> str:
    from . import focus
    subj = _focus_subject(store, s["item_id"])
    if subj is None:
        return ""
    est = ""
    if getattr(subj, "type", "") not in ("goal", "event"):
        est = estimates.fmt_minutes(estimates.estimate_minutes(subj, estimates.calibrate(store.done_items())))
    return focus.claude_message(focus.load_settings(store).template, subj, s["goal"],
                                _others_due(store, s["item_id"], now_utc()), est)


def render_focus_session(store: Store, sid: str, compact: bool = False) -> str:
    """The running session: one big countdown for the whole block. Works as a page in the main
    window (or a browser tab under `serve`) and in the small always-on-top focus window."""
    from . import focus
    from .ui import CSS, FONTS, PAGE_JS, THEME_BOOT
    s = store.get_focus(sid)
    if s is None:
        return page(store, "Focus — Waterloo Deadlines", '<section class="card"><div class="empty">No such session.</div></section>',
                    "/plan", heading="Focus session")
    subj = _focus_subject(store, s["item_id"])
    st = focus.timer(s)
    fs = focus.load_settings(store)
    label = block_label(subj) if subj else "A removed item"
    weekly_goal = getattr(subj, "type", "") == "event" or (getattr(subj, "type", "") == "goal" and any(
        g["id"] == s["item_id"] and g["kind"] == "weekly" for g in store.goals()))  # nothing to mark done
    claude_text = focus_claude_text(store, s)
    finish = ('<div class="fqs" id="fq" hidden><div class="fq-h">Did you finish the item?</div><div class="fq-b">'
              f'<form method="post" action="/focus/{sid}/finish"><input type="hidden" name="outcome" value="done">'
              f'<input type="hidden" name="win" value="{1 if compact else 0}"><button class="btn pri">Done</button></form>'
              f'<form method="post" action="/focus/{sid}/finish"><input type="hidden" name="outcome" value="not_yet">'
              f'<input type="hidden" name="win" value="{1 if compact else 0}"><button class="btn">Not yet</button></form>'
              '<button type="button" class="btn" id="fq-cancel">Keep going</button></div></div>') if not weekly_goal else (
              '<div class="fqs" id="fq" hidden><div class="fq-b">'
              f'<form method="post" action="/focus/{sid}/finish"><input type="hidden" name="outcome" value="not_yet">'
              f'<input type="hidden" name="win" value="{1 if compact else 0}"><button class="btn pri">Log this session</button></form>'
              '<button type="button" class="btn" id="fq-cancel">Keep going</button></div></div>')
    ended = bool(s["ended_at"])
    body = (
        f'<div class="fsess{" compact" if compact else ""}" id="fsess" data-sid="{sid}" data-chime="{1 if fs.chime else 0}">'
        f'<div class="fs-top">{course_chip(getattr(subj, "context", None))}<div class="fs-title">{esc(label)}</div></div>'
        + (f'<div class="fs-goal">By the end I\'ll have: <b>{esc(s["goal"])}</b></div>' if s["goal"] else "")
        + f'<div class="fs-tw" id="ftw"><div class="fs-timer{" warn" if st.warning else ""}{" over" if st.over else ""}" id="ftimer" role="timer" aria-live="off">'
          f'{"+" if st.over else ""}{focus.fmt_clock(st.remaining)}</div></div>'
        f'<div class="fs-sub" id="fsub">{"On a break" if st.on_break else ("Over time" if st.over else "left in this block")}</div>'
        f'<div class="fs-dl">{esc(focus.deadline_line(store, subj))}</div>'
        + ('<div class="fs-actions">'
           f'<form method="post" action="/focus/{sid}/{"resume" if st.on_break else "break"}" class="fs-f" id="fbreakf">'
           f'<input type="hidden" name="win" value="{1 if compact else 0}"><button class="btn big" id="fbreak">{"Resume" if st.on_break else "Break"}</button></form>'
           f'<form method="post" action="/focus/{sid}/extend" class="fs-f"><input type="hidden" name="win" value="{1 if compact else 0}"><button class="btn big">+15 min</button></form>'
           '<button type="button" class="btn big pri" id="ffinish">Finish</button></div>' if not ended else
           '<div class="fs-sub">This session has ended.</div>')
        + finish
        + (f'<details class="fs-claude"><summary>Claude message</summary><textarea readonly rows="8" id="fclaude">{esc(claude_text)}</textarea>'
           '<button type="button" class="btn sm" id="fcopy">Copy</button></details>' if claude_text else "")
        + '</div>')
    script = """<script>
(function(){
  var box=document.getElementById('fsess'); if(!box) return;
  var sid=box.dataset.sid, el=document.getElementById('ftimer'), sub=document.getElementById('fsub');
  var base=null, at=0, chimed=false; try{chimed=sessionStorage.getItem('chimed-'+sid)==='1'}catch(e){}
  function fmt(s){s=Math.abs(Math.round(s));var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;
    return (h?h+':'+(m<10?'0':'')+m:m)+':'+(x<10?'0':'')+x;}
  function chime(){ if(chimed||box.dataset.chime!=='1') return; chimed=true; try{sessionStorage.setItem('chimed-'+sid,'1')}catch(e){}
    try{var c=new (window.AudioContext||window.webkitAudioContext)(),o=c.createOscillator(),g=c.createGain();
      o.type='sine';o.frequency.value=880;g.gain.setValueAtTime(0.0001,c.currentTime);
      g.gain.exponentialRampToValueAtTime(0.25,c.currentTime+0.02);g.gain.exponentialRampToValueAtTime(0.0001,c.currentTime+0.9);
      o.connect(g);g.connect(c.destination);o.start();o.stop(c.currentTime+1);}catch(e){} }
  function draw(){ if(!base) return; var dt=(Date.now()-at)/1000;
    if(base.ended){return;}
    if(base.on_break){ el.className='fs-timer brk'; el.textContent=fmt(base.remaining);
      sub.textContent='On a break · '+fmt(base.break_for+dt); document.title='Break — '+fmt(base.break_for+dt); return; }
    var r=base.remaining-dt, over=r<=0;
    el.className='fs-timer'+(over?' over':(r<=600?' warn':'')); el.textContent=(over?'+':'')+fmt(r);
    sub.textContent=over?'Over time — finish when you\\'re ready, or add 15 min':'left in this block';
    document.title=(over?'+':'')+fmt(r)+' — Focus';
    if(over) chime();
  }
  function load(){ fetch('/focus/'+sid+'/state',{cache:'no-store'}).then(function(r){return r.json();})
    .then(function(j){base=j;at=Date.now();draw();}).catch(function(){}); }
  // The countdown fills its space: as large as fits the width and the height left over, so a
  // bigger window means a bigger timer. Re-fitted on resize and when the digits change (1:00:00 → 59:59).
  var tw=document.getElementById('ftw'), fitLen=-1;
  function fit(){ if(!tw) return; el.style.fontSize='100px';
    var w=el.scrollWidth||1, h=el.offsetHeight||1, W=tw.clientWidth*0.96, H=tw.clientHeight*0.92;
    el.style.fontSize=Math.max(32,Math.floor(Math.min(W/w,H/h)*100))+'px'; fitLen=el.textContent.length; }
  var _draw=draw; draw=function(){ _draw(); if(el.textContent.length!==fitLen) fit(); };
  if(window.ResizeObserver&&tw) new ResizeObserver(fit).observe(tw); else window.addEventListener('resize',fit);
  fit();
  load(); setInterval(draw,250); setInterval(load,15000);
  document.addEventListener('visibilitychange',function(){if(!document.hidden)load();});
  var fin=document.getElementById('ffinish'), fq=document.getElementById('fq');
  if(fin&&fq){ fin.addEventListener('click',function(){fq.hidden=false;fin.hidden=true;});
    document.getElementById('fq-cancel').addEventListener('click',function(){fq.hidden=true;fin.hidden=false;}); }
  var cp=document.getElementById('fcopy'); if(cp) cp.addEventListener('click',function(){
    var t=document.getElementById('fclaude'); t.select();
    (navigator.clipboard?navigator.clipboard.writeText(t.value):Promise.reject()).catch(function(){document.execCommand('copy');});
    cp.textContent='Copied';});
})();
</script>"""
    if compact:  # the small always-on-top window: no navigation, just the session
        return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                f'<title>Focus — {esc(label)}</title>{THEME_BOOT}{FONTS}<style>{CSS}</style></head>'
                f'<body class="focus-win">{body}{script}{PAGE_JS}</body></html>')
    return page(store, "Focus — Waterloo Deadlines", body + script, "/plan", heading="Focus session",
                subtitle="One countdown for the whole block · breaks don't count as work")


def focus_settings_card(store: Store) -> str:
    from . import focus
    fs = focus.load_settings(store)

    def cb(name, text, on):
        return f'<label class="inline"><input type="checkbox" name="{name}" value="1"{" checked" if on else ""}> {esc(text)}</label>'
    return ('<section class="card" id="focus"><div class="ch"><h2>Focus sessions</h2>'
            '<span class="s">double-click a block on the Work plan to start one</span></div>'
            '<form method="post" action="/focus/settings" class="planset" style="align-items:flex-start">'
            f'<label style="flex:1;min-width:260px">Music link (opens in your browser — instrumental works best)'
            f'<input name="music_url" value="{esc(fs.music_url)}" maxlength="500"></label>'
            '<div style="display:flex;flex-direction:column;gap:6px">'
            + cb("open_assignment", "Open the assignment", fs.open_assignment)
            + cb("open_music", "Open music", fs.open_music)
            + cb("open_claude", "Open Claude", fs.open_claude)
            + cb("chime", "Chime when the block's time is up", fs.chime)
            + '</div>'
            '<label style="flex-basis:100%">Claude message (placeholders: {title} {course} {type} {due} {weight} '
            '{estimate} {link} {goal} {others})'
            f'<textarea name="template" rows="9" maxlength="4000">{esc(fs.template)}</textarea></label>'
            '<button class="btn pri">Save focus settings</button></form></section>')

# --- Reminders -----------------------------------------------------------------------

def _reminder(it: Item, now: datetime) -> str:
    parts = []
    if it.due_date:
        parts.append(short_date(it.due_date))
        if clock(it.due_date) != "end of day":
            parts.append(clock(it.due_date))
        if it.status != "done":
            parts.append(reminders.relative(it.due_date, now))
    link = (f' · <a href="{esc(it.source_link)}" target="_blank" rel="noopener">open link</a>' if it.source_link else "")
    meta = f'<div class="meta">{esc(" · ".join(parts))}{link}</div>' if parts or link else ""
    note = it.extra.get("note", "")
    return (f'<div class="rem{" done" if it.status == "done" else ""}">{tick_form(it, "/reminders")}'
            f'<div class="body"><div class="title">{esc(it.title)} {course_chip(it.context)}</div>{meta}'
            f'{f"<div class=note>{esc(note)}</div>" if note else ""}</div></div>')


def render_reminders(store: Store, notice: str = "") -> str:
    now = now_utc()
    items = [i for i in store.list_items(include_past=True) if i.type == "reminder"]
    groups, done = reminders.group(items, now)
    main_groups, side = [], []
    for name, bucket in groups.items():
        if not bucket:
            continue
        hot = " hot" if name in ("Overdue", "Today") else ""
        block = (f'<div class="rgrp{hot}">{esc(name)}<span class="n">{len(bucket)}</span></div>'
                 + "".join(_reminder(i, now) for i in bucket))
        (side if name in reminders.UNDATED else main_groups).append(block)
    if not main_groups and not side:
        main_groups.append('<div class="empty">No open reminders. Add one above.</div>')
    if done:
        main_groups.append(f'<details class="done-list"><summary>Done · {len(done)}</summary>'
                           + "".join(_reminder(i, now) for i in done[:30]) + "</details>")
    contexts = sorted({i.context for i in store.list_items() if i.context})
    options = "".join(f'<option value="{esc(c)}">' for c in contexts)
    form = (
        '<form method="post" action="/reminders/add" id="add">'
        '<div class="quick"><input class="big" type="text" name="title" required maxlength="200" '
        'placeholder="Remind me to…" aria-label="Reminder">'
        '<input type="date" name="date" aria-label="Date"><input type="time" name="time" aria-label="Time">'
        f'<button class="btn pri">{icon("plus")}Add</button></div>'
        '<details class="more"><summary>More: tag, note, keep in mind</summary><div class="fields">'
        '<input type="text" name="context" list="ctx" maxlength="40" placeholder="Tag, e.g. CS 135" aria-label="Tag">'
        f'<datalist id="ctx">{options}</datalist>'
        '<label class="inline"><input type="checkbox" name="ongoing" value="1"> Keep in mind (ongoing habit)</label>'
        '<textarea name="note" rows="2" maxlength="1000" placeholder="Note" aria-label="Note"></textarea></div></details>'
        '</form><script>if(location.hash==="#add"){var f=document.querySelector("#add input.big");if(f)f.focus()}</script>')
    body = (f'{notice}{form}<div class="grid2"><div class="col" style="gap:0">{"".join(main_groups)}</div>'
            f'<div class="col" style="gap:0">{"".join(side)}</div></div>')
    open_n = sum(len(b) for b in groups.values())
    return page(store, "Reminders — Waterloo Deadlines", body, "/reminders", heading="Reminders",
                subtitle=f"{open_n} open · no date → “No set date”; a date without a time means by end of that day")


def add_reminder(store: Store, form: dict[str, str]) -> str:
    title = form.get("title", "").strip()
    if not title:
        raise ValueError("give the reminder a title")
    if len(title) > 200 or len(form.get("note", "")) > 1000 or len(form.get("context", "")) > 40:
        raise ValueError("too long")
    ongoing = form.get("ongoing") == "1"
    due = None if ongoing else reminders.make_due(form.get("date", "").strip(), form.get("time", "").strip())
    note = form.get("note", "").strip()
    extra = {**({"note": note} if note else {}), **({"ongoing": True} if ongoing else {})}
    for it in store.list_items(include_past=True, include_hidden=True):
        if (it.source == "manual" and it.status != "done" and it.title.lower() == title.lower()
                and it.due_date == due):
            return it.id  # already there — a repeated click, not a second reminder
    return store.upsert_manual(Item(
        source="manual", source_key=reminders.new_key(title), type="reminder", title=title,
        context=form.get("context", "").strip() or None, due_date=due, extra=extra))


# --- Calendar ------------------------------------------------------------------------

def _cal_event(it: Item, now: datetime) -> str:
    c = clock(it.due_date)
    tm = "" if c == "end of day" else c.replace(":00", "").replace(" AM", "a").replace(" PM", "p")
    label = f"{it.context} · {it.title}" if it.context and it.type != "reminder" else it.title
    tip = f"{label} — {full_due(it.due_date)}" + (" (done)" if it.status == "done" else "")
    state = " done" if it.status == "done" else (" past" if it.due_date < now else "")
    inner = (f'<span class="tm">{esc(tm)}</span>' if tm else "") + f'<span class="et">{esc(label)}</span>'
    cls = f"ev {type_class(it.type)}{state}"
    if it.source_link:
        return f'<a class="{cls}" href="{esc(it.source_link)}" target="_blank" rel="noopener" title="{esc(tip)}">{inner}</a>'
    return f'<span class="{cls}" title="{esc(tip)}">{inner}</span>'


def render_calendar(store: Store, month: str | None = None) -> str:
    now = now_utc()
    today = calendar_view.local_today()
    year, mon = calendar_view.parse_month(month, today)
    days = calendar_view.by_day(store.list_items(include_past=True))
    cells = [f'<div class="dow">{d}</div>' for d in ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")]
    for week in calendar_view.weeks(year, mon):
        for d in week:
            items = days.get(d, [])
            cls = ["day"] + (["other"] if d.month != mon else []) + (["today"] if d == today else []) \
                + ([] if items else ["blank"])
            shown = "".join(_cal_event(i, now) for i in items[:CELL_LIMIT])
            if len(items) > CELL_LIMIT:
                shown += (f'<details><summary>+{len(items) - CELL_LIMIT} more</summary>'
                          + "".join(_cal_event(i, now) for i in items[CELL_LIMIT:]) + "</details>")
            cells.append(f'<div class="{" ".join(cls)}"><div class="dn"><span class="wd">{d:%a %b}</span><b>{d.day}</b></div>'
                         f'{shown}</div>')
    py, pm = calendar_view.shift(year, mon, -1)
    ny, nm = calendar_view.shift(year, mon, 1)
    this_month = (year, mon) == (today.year, today.month)
    legend = ('<div class="legend"><span><span class="tdot t-work"></span>Assignment</span>'
              '<span><span class="tdot t-test"></span>Quiz / exam</span><span><span class="tdot t-meet"></span>Event</span>'
              '<span><span class="tdot t-rem"></span>Reminder</span></div>')
    head = (f'<div class="ch"><h2>{date(year, mon, 1):%B %Y}</h2><div class="r">{legend}'
            + ('' if this_month else '<a class="btn sm" href="/calendar">Today</a>')
            + f'<a class="btn sm" href="/calendar?m={py}-{pm:02d}" aria-label="Previous month">{icon("chev_l")}</a>'
            f'<a class="btn sm" href="/calendar?m={ny}-{nm:02d}" aria-label="Next month">{icon("chev_r")}</a></div></div>')
    body = f'<section class="card">{head}<div class="cal">{"".join(cells)}</div></section>'
    return page(store, f"{date(year, mon, 1):%B %Y} — Waterloo Deadlines", body, "/calendar",
                heading="Calendar", subtitle=f"{date(year, mon, 1):%B %Y}")


# --- Done prompt ---------------------------------------------------------------------

def _fmt_num(v: float | None) -> str:
    return "" if v is None else f"{v:g}"


def render_done_prompt(store: Store, item: Item, next_path: str, notice: str = "") -> str:
    cal = estimates.calibrate(store.done_items())
    est = estimates.estimate_minutes(item, cal)
    prefill = _fmt_num(round(item.actual_duration / 60, 2)) if item.actual_duration else ""
    body = (
        f'<div class="center col">{notice}<section class="card"><div class="ch"><h2>Nice — {esc(item.title)} is done</h2></div>'
        '<div style="padding:18px;display:flex;flex-direction:column;gap:14px">'
        f'<div>How long did it actually take? The estimate was <span class="mono">~{esc(estimates.fmt_minutes(est))}</span>. '
        'Your answer tunes future estimates for this course.</div>'
        '<form method="post" action="/duration" class="inline" style="gap:10px;flex-wrap:wrap">'
        f'<input type="hidden" name="id" value="{item.id}"><input type="hidden" name="next" value="{esc(next_path)}">'
        f'<label class="inline">Hours <input type="number" name="hours" min="0.05" max="200" step="0.05" value="{prefill}" '
        'autofocus required aria-label="Hours it took" style="width:110px"></label>'
        f'<button class="btn pri">Save</button><a class="btn" href="{esc(next_path)}">Skip</a></form></div></section></div>')
    return page(store, "How long did it take? — Waterloo Deadlines", body, "", heading="Completed",
                subtitle="Log your time")


# --- Courses & weights ---------------------------------------------------------------

def render_settings(store: Store, notice: str = "") -> str:
    importance = store.context_importance()
    by_ctx: dict[str, list[Item]] = defaultdict(list)
    for it in sorted(store.list_items(), key=lambda i: (i.due_date is None, i.due_date)):
        by_ctx[it.context or "(no course)"].append(it)
    for ctx in importance:  # keep courses with a saved setting even if nothing is due right now
        by_ctx.setdefault(ctx, [])

    sections = []
    for ctx in sorted(by_ctx, key=lambda c: (c == "(no course)", c)):
        imp = ""
        if ctx != "(no course)":
            imp = (f'<label class="inline" style="font-size:13px;color:var(--text2)">Importance '
                   f'<input type="number" name="imp:{esc(ctx)}" aria-label="Importance for {esc(ctx)}" min="0.1" max="10" '
                   f'step="0.1" value="{esc(_fmt_num(importance.get(ctx)))}" placeholder="1" style="width:80px"></label>')
        rows = "".join(
            f'<div class="set-row"><div class="due mono" style="color:var(--text3);font-size:12px">'
            f'{esc(short_date(it.due_date)) if it.due_date else "No date"}</div>'
            f'<div class="title">{esc(it.title)}</div>'
            f'<label class="inline"><input type="number" name="w:{it.id}" min="0" max="100" step="0.1" '
            f'value="{esc(_fmt_num(it.grade_weight))}" placeholder="1" aria-label="Weight for {esc(it.title)}">%</label>'
            f'<label class="inline"><input type="number" name="est:{it.id}" min="0.05" max="200" step="0.05" '
            f'value="{esc(_fmt_num(round(it.estimated_duration / 60, 2)) if it.estimated_duration else "")}" '
            f'placeholder="{esc(_fmt_num(round(estimates.base_minutes(it) / 60, 2)))}" '
            f'aria-label="Estimated hours for {esc(it.title)}">h</label></div>' for it in by_ctx[ctx])
        head = ('<div class="set-row headrow"><span class="due">Due</span><span>Item</span><span>Grade weight</span>'
                '<span>Your estimate</span></div>')
        label = course_chip(ctx) if ctx != "(no course)" else '<h2>No course</h2>'
        sections.append(f'<section class="card"><div class="ch">{label}<div class="r">{imp}</div></div>'
                        f'{head + rows if rows else "<div class=empty>Nothing due right now.</div>"}</section>')
    body = (
        f'{notice}{planner_settings_card(store)}{exam_prep_settings_card(store)}{focus_settings_card(store)}<form method="post" action="/settings" class="col">{"".join(sections)}'
        '<div class="savebar"><button class="btn pri">Save changes</button>'
        '<span style="color:var(--text3)">Priority = weight × importance × how soon it\'s due.</span></div></form>')
    return page(store, "Courses & weights — Waterloo Deadlines", body, "/settings", heading="Courses & weights",
                subtitle="Grade weights from each syllabus (blank counts as 1%) · importance: 1 = normal · estimates in hours")


def parse_opt_float(raw: str) -> float | None:
    raw = raw.strip()
    if raw == "":
        return None
    val = float(raw)
    if not math.isfinite(val):
        raise ValueError("must be a number")
    return val


def apply_settings(store: Store, form: dict[str, str], item_id_re) -> list[str]:
    """Apply a settings form. Returns human-readable errors (fields with errors are skipped)."""
    errors = []
    known = {i.id: i for i in store.list_items(include_inactive=True, include_past=True, include_hidden=True)}
    current_imp = store.context_importance()
    for key, raw in form.items():
        try:
            if key.startswith("est:"):
                item_id = key[4:]
                if item_id not in known:
                    continue
                hours = parse_opt_float(raw)
                minutes = None if hours is None else round(hours * 60)  # store rejects <= 0
                if minutes != known[item_id].estimated_duration:
                    store.set_estimated_duration(item_id, minutes)
            elif key.startswith("w:"):
                item_id = key[2:]
                if not item_id_re.match(item_id) or item_id not in known:
                    continue
                val = parse_opt_float(raw)
                if val != known[item_id].grade_weight:
                    store.set_grade_weight(item_id, val)
            elif key.startswith("imp:"):
                ctx = key[4:]
                val = parse_opt_float(raw)
                if val != current_imp.get(ctx):
                    store.set_context_importance(ctx, val)
        except ValueError as e:
            errors.append(f"{raw!r}: {e}")
    return errors


# --- Connections ---------------------------------------------------------------------

STATE_BADGE = {"ok": ("b-ok", "Connected"), "needs_login": ("b-warn", "Needs sign-in"), "missing": ("b-warn", "Not set up"),
               "optional": ("b-mute", "Not set up"), "error": ("b-bad", "Couldn't check")}
TILES = {"crowdmark": ("k2", "C"), "quest": ("k5", "Q"), "odyssey": ("k4", "E"), "web": ("k3", "W"), "d2l": ("k6", "L"), "gcal": ("k1", "G"), "outlook": ("k3", "O")}
def _poll(token: str) -> str:
    """Reload when the Connections state changes — checked every 2 s, and never while you're typing
    (a timed page refresh used to wipe whatever was in the search box)."""
    return ""  # superseded by the live-update poll every page has (ui.PAGE_JS → /state)
    t = json.dumps(token).replace("</", "<\\/")
    return ("<script>(function(){var t=" + t + ";setInterval(function(){var a=document.activeElement;"
            "if(a&&/^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName))return;"
            "fetch('/connections/state',{cache:'no-store'}).then(function(r){return r.text()})"
            ".then(function(s){if(s!==t)location.reload()}).catch(function(){})},2000)})();</script>")


def _badge(check, running: bool) -> str:
    if running and check.action == "waterloo":
        return '<span class="badge b-acc">Signing in…</span>'
    cls, text = STATE_BADGE.get(check.state, ("b-mute", check.state))
    return f'<span class="badge {cls}">{text}</span>'


def _link_form(kind: str, placeholder: str, skip: bool = False) -> str:
    skip_btn = (f'<button class="btn" formaction="/connect/skip" name="kind" value="{kind}" formnovalidate>'
                "Don't use this</button>") if skip else ""
    # type=password keeps the private link off the screen; pasting (Ctrl+V) works normally here.
    return (f'<form class="linkform" method="post" action="/connect/link"><input type="hidden" name="kind" value="{kind}">'
            f'<input type="password" name="url" placeholder="{esc(placeholder)}" aria-label="{esc(placeholder)}" '
            f'autocomplete="off" spellcheck="false" required><button class="btn pri">Save link</button>{skip_btn}</form>')


WATERLOO = ("crowdmark", "quest", "odyssey", "web")  # everything behind the one Waterloo sign-in


def _steps(mgr, need: set[str]) -> str:
    phase = getattr(mgr, "phase", "") or "signin"
    order = ["signin"] + [k for k in WATERLOO if k in need]
    labels = {"signin": "Sign in + Duo", "crowdmark": "Crowdmark", "quest": "Quest schedule", "odyssey": "Odyssey exams", "web": "Course pages"}
    cur = order.index(phase) if phase in order else 0
    out = ['<span class="ok">✓ Browser opened</span>']
    for n, key in enumerate(order):
        cls, mark = ("ok", "✓") if n < cur else ("cur", "●") if n == cur else ("", "○")
        out.append(f'<span class="{cls}">{mark} {labels[key]}</span>')
    return '<div class="steps">' + "".join(out) + "</div>"


def render_connections(store: Store, mgr, notice: str = "") -> str:
    from .paths import legacy_note

    if getattr(mgr, "google_result", ""):
        good = mgr.google_result.startswith("Google Calendar reconnected")
        notice = f'<div class="flag{" good" if good else ""}" role="status">{esc(mgr.google_result)}</div>' + notice
    moved = legacy_note()
    if moved:
        notice = f'<div class="flag good" role="status">{esc(moved)}</div>' + notice
    checks = {c.key: c for c in (mgr.checks or [])}
    if not checks:
        body = (notice + '<section class="card"><div class="prog" style="border-top:0"><span class="spin"></span>'
                'Checking your connections…</div></section>')
        return page(store, "Connections — Waterloo Deadlines", body, "/connections", _poll(mgr.state_token()),
                    heading="Connections", subtitle="Sign in once — everything else syncs on its own")

    running = mgr.signin_running
    need = {k for k in WATERLOO if k in checks and checks[k].action == "waterloo"}
    if running:
        action = '<form method="post" action="/connect/cancel"><button class="btn">Cancel</button></form>'
        extra = _steps(mgr, (need or set(WATERLOO)) | {"odyssey", "web"})
        prog = ('<div class="prog"><span class="spin"></span>A browser window is open — sign in with your Waterloo '
                'account and approve Duo. This page updates by itself.</div>')
    else:
        label = "Sign in to Waterloo" if need else "Sign in again"
        action = (f'<form method="post" action="/connect/waterloo"><button class="btn{" pri" if need else ""}">'
                  f'{icon("shield")}{label}</button></form>')
        extra, prog = "", ""
    results = "".join(f'<div class="flag{" good" if r.startswith(("connected", "saved")) else ""}">'
                      f'{esc(k.capitalize())}: {esc(r)}</div>' for k, r in mgr.signin_results.items())
    hero = (f'<section class="card"><div class="hero"><div class="ic">{icon("shield")}</div><div style="flex:1;min-width:240px">'
            '<h3>Waterloo sign-in</h3><p>One sign-in and one Duo approval connect Crowdmark, read your Quest schedule, Odyssey exam times and course pages. '
            f'Your password never touches this app.</p>{extra}</div>{action}</div>{prog}'
            + (f'<div class="results">{results}</div>' if results and not running else "") + '</section>')

    names = {"crowdmark": ("Crowdmark", "Assessments & submissions · via Waterloo sign-in"),
             "quest": ("Quest", "Class schedule · via Waterloo sign-in"),
             "odyssey": ("Odyssey", "Quizzes, midterms & finals · rooms and seats · via Waterloo sign-in"),
             "web": ("Course pages", "CS 135 assignments page · via Waterloo sign-in"),
             "d2l": ("LEARN", "Course calendar feed"),
             "gcal": ("Google Calendar", "Pushes deadlines to your “Waterloo Deadlines” calendar"),
             "outlook": ("Outlook calendar", "Published calendar link · email needs Waterloo IT approval")}
    rows = []
    for key in (*WATERLOO, "d2l", "gcal", "outlook"):
        c = checks.get(key)
        if not c:
            continue
        tile_cls, mono = TILES[key]
        name, desc = names[key]
        detail = f"{desc} · {c.detail}" if c.detail not in ("connected", "signed in") else desc
        if c.state == "ok":
            act = ""
        elif c.action == "waterloo":
            act = "" if running else (f'<form method="post" action="/connect/waterloo"><button class="btn sm">Sign in</button></form>')
        elif c.action == "google":
            act = ('<span class="badge b-acc">Waiting for Google…</span>' if getattr(mgr, "google_running", False) else
                   '<form method="post" action="/connect/google"><button class="btn sm pri">Reconnect Google</button></form>')
        elif c.action in ("paste_d2l", "paste_outlook"):
            act = f'<a class="btn sm" href="#{key}-link">Paste link</a>'
        else:
            act = ""
        rows.append(f'<div class="conn"><div class="tile {tile_cls}">{mono}</div><div><b>{esc(name)}</b>'
                    f'<span class="d">{esc(detail)}</span></div>{_badge(c, running)}<div>{act}</div></div>')
    checked = f"checked {clock(mgr.checked_at)}" if mgr.checked_at else ""
    sources = (f'<section class="card"><div class="ch"><h2>Sources</h2><span class="s">{len(rows)} connections · {esc(checked)}</span>'
               f'<div class="r"><form method="post" action="/connect/recheck"><button class="btn sm">{icon("refresh")}Check again</button>'
               f'</form></div></div>{"".join(rows)}</section>')

    forms = []
    d2l = checks.get("d2l")
    if d2l and d2l.state != "ok":
        forms.append('<section class="card" id="d2l-link"><div class="ch"><h2>LEARN calendar link</h2></div>'
                     + _link_form("d2l", "Paste your LEARN calendar feed link (kept hidden)")
                     + '<div class="help">In LEARN: any course → Calendar → Settings → tick “Enable Calendar Feeds” → Save. '
                       'Then Subscribe → All Courses → copy the link.</div></section>')
    ol = checks.get("outlook")
    from .login import _prefs
    if ol and ol.state != "ok" and not _prefs().get("outlook_skipped"):
        forms.append('<section class="card" id="outlook-link"><div class="ch"><h2>Outlook calendar link</h2></div>'
                     + _link_form("outlook", "Paste the published ICS link (kept hidden)", skip=True)
                     + '<div class="help">Outlook on the web → Settings → Calendar → Shared calendars → Publish a calendar '
                       '(Calendar · Can view all details) → copy the <strong>ICS</strong> link.</div></section>')
    all_good = not mgr.needs_attention()
    checking = ('<section class="card"><div class="prog" style="border-top:0"><span class="spin"></span>'
                'Checking your connections again…</div></section>') if mgr.checking and not running else ""
    body = (f'<div class="col" style="max-width:980px">{notice}{checking}{hero}{sources}{"".join(forms)}'
            f'<div><a class="btn{" pri" if all_good else ""}" href="/today">Continue to Today</a></div></div>')
    busy = running or mgr.checking
    return page(store, "Connections — Waterloo Deadlines", body, "/connections", _poll(mgr.state_token()) if busy else "",
                heading="Connections",
                subtitle="Everything is connected" if all_good else "Sign in once — everything else syncs on its own")


def render_starting(store: Store, mgr=None) -> str:
    body = ('<section class="card"><div class="prog" style="border-top:0"><span class="spin"></span>'
            'Checking your connections…</div></section>')
    poll = _poll(mgr.state_token()) if mgr else '<meta http-equiv="refresh" content="1">'
    return page(store, "Starting — Waterloo Deadlines", body, "", poll,
                heading="Waterloo Deadlines", subtitle="Starting up…")
