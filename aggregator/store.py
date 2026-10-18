"""SQLite store. Items are keyed by stable identity so rescrapes update in place."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from . import priority
from .models import STATUSES, Item
from .paths import DATA_DIR

DEFAULT_DB = DATA_DIR / "aggregator.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id                 TEXT PRIMARY KEY,
    source             TEXT NOT NULL,
    source_key         TEXT NOT NULL,
    type               TEXT NOT NULL,
    title              TEXT NOT NULL,
    context            TEXT,
    due_date           TEXT,
    source_link        TEXT,
    grade_weight       REAL,
    importance         REAL,
    status             TEXT NOT NULL DEFAULT 'not_started',
    estimated_duration INTEGER,
    actual_duration    INTEGER,
    priority_score     REAL NOT NULL DEFAULT 0,
    last_synced        TEXT,
    active             INTEGER NOT NULL DEFAULT 1,  -- 0 once the source stops reporting it
    extra              TEXT NOT NULL DEFAULT '{}',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    status_changed_at  TEXT,
    relevance          TEXT,  -- user override of the academic filter: show | hide
    UNIQUE (source, source_key)
);
-- Every status transition, kept as future training data for learned prioritization.
CREATE TABLE IF NOT EXISTS status_events (
    item_id     TEXT NOT NULL REFERENCES items(id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    at          TEXT NOT NULL
);
-- Per-course/context defaults; item-level values override these.
CREATE TABLE IF NOT EXISTS contexts (
    context    TEXT PRIMARY KEY,
    importance REAL
);
-- Weekly class timetable from Quest; replaced wholesale each capture (so drops disappear).
CREATE TABLE IF NOT EXISTS classes (
    term        TEXT NOT NULL,
    course      TEXT NOT NULL,
    title       TEXT,
    component   TEXT,
    section     TEXT,
    class_nbr   TEXT,
    days        TEXT NOT NULL,  -- JSON list, Monday = 0
    start_time  TEXT NOT NULL,
    end_time    TEXT NOT NULL,
    location    TEXT,
    instructor  TEXT,
    start_date  TEXT,
    end_date    TEXT,
    captured_at TEXT NOT NULL
);
-- Study-block planner (planner.py): suggested / accepted / dismissed work blocks. The id is
-- also the Google Calendar event id of an accepted block.
CREATE TABLE IF NOT EXISTS plan_blocks (
    id         TEXT PRIMARY KEY,
    item_id    TEXT NOT NULL,
    start      TEXT NOT NULL,
    "end"      TEXT NOT NULL,
    state      TEXT NOT NULL,  -- suggested | accepted | dismissed
    created_at TEXT NOT NULL
);
-- the user's own events on the work plan (gym, a shift, …): busy time for the planner, mirrored to
-- Google Calendar under their id.
CREATE TABLE IF NOT EXISTS plan_events (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL,
    start      TEXT NOT NULL,
    "end"      TEXT NOT NULL,
    created_at TEXT NOT NULL
);
-- Goals: study time the user wants to put in, beyond finishing deliverables. "weekly" = hours per
-- week; "by_date" = hours in total before `until` (e.g. preparing for a midterm, then item_id
-- links the exam and replaces its default study estimate).
CREATE TABLE IF NOT EXISTS goals (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    course      TEXT,
    kind        TEXT NOT NULL,
    hours       REAL NOT NULL,
    until       TEXT,
    session_min INTEGER NOT NULL DEFAULT 45,
    item_id     TEXT,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
-- Test check-ins (debrief.py): the user's own notes after a test. Never sent anywhere.
CREATE TABLE IF NOT EXISTS test_debriefs (
    item_id     TEXT PRIMARY KEY,
    went        TEXT NOT NULL DEFAULT '',
    improve     TEXT NOT NULL DEFAULT '',
    notes       TEXT NOT NULL DEFAULT '',
    minutes     INTEGER,
    skipped     INTEGER NOT NULL DEFAULT 0,
    answered_at TEXT NOT NULL
);
-- Focus sessions (focus.py): the timer is always computed from these timestamps.
CREATE TABLE IF NOT EXISTS focus_sessions (
    id               TEXT PRIMARY KEY,
    block_id         TEXT,
    item_id          TEXT,
    goal             TEXT NOT NULL DEFAULT '',
    planned_minutes  INTEGER NOT NULL,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    paused_seconds   INTEGER NOT NULL DEFAULT 0,
    break_started_at TEXT,
    break_count      INTEGER NOT NULL DEFAULT 0,
    extended_minutes INTEGER NOT NULL DEFAULT 0,
    outcome          TEXT  -- done | not_yet | abandoned (NULL while running)
);
-- Small app settings (planner sleep/wake times, …) as key → JSON value.
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
-- Sync health per source, drives "D2L hasn't updated in 3 days" flags.
CREATE TABLE IF NOT EXISTS source_health (
    source       TEXT PRIMARY KEY,
    last_attempt TEXT,
    last_success TEXT,
    last_error   TEXT
);
"""

# Columns a rescrape may overwrite. Everything else is user-owned.
SCRAPED_FIELDS = ("type", "title", "context", "due_date", "source_link", "extra")


class EmptySync(ValueError):
    """A source returned nothing while it still has live items — treated as a failed read."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: Path | str = DEFAULT_DB):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(items)")}
        if "relevance" not in cols:  # databases from before the academic filter
            with self.db:
                self.db.execute("ALTER TABLE items ADD COLUMN relevance TEXT")
        block_cols = {r["name"] for r in self.db.execute("PRAGMA table_info(plan_blocks)")}
        if "edited" not in block_cols:  # plan tables from before hand-editing
            with self.db:
                self.db.execute("ALTER TABLE plan_blocks ADD COLUMN edited INTEGER NOT NULL DEFAULT 0")
                # Blocks accepted before this column existed came from hand edits: keep them as such.
                self.db.execute("UPDATE plan_blocks SET edited = 1 WHERE state = 'accepted'")
        if "origin" not in block_cols:  # '' = planner / hand; 'day' = added by "Plan my day"
            with self.db:
                self.db.execute("ALTER TABLE plan_blocks ADD COLUMN origin TEXT NOT NULL DEFAULT ''")

    # --- ingest -----------------------------------------------------------

    def sync_source(self, source: str, items: list[Item], allow_empty: bool = False) -> dict:
        """Replace the scraped view of one source: upsert every item, deactivate
        any previously-seen item the source no longer reports.

        An empty result for a source that has items is refused (EmptySync): a page that didn't
        load or a feed that hiccuped must not "cancel" everything — that once wiped the whole
        Odyssey exam schedule. Pass allow_empty=True only where empty genuinely means empty."""
        if not items and not allow_empty:
            n = self.db.execute("SELECT COUNT(*) FROM items WHERE source = ? AND active = 1", (source,)).fetchone()[0]
            if n:
                raise EmptySync(f"{source} returned nothing — kept the {n} item{'s' if n != 1 else ''} you had")
        now = _iso(_now())
        seen: set[str] = set()
        inserted = updated = 0
        with self.db:
            for it in items:
                if it.source != source:
                    raise ValueError(f"item from {it.source} passed to sync of {source}")
                seen.add(it.id)
                scraped = {
                    "type": it.type, "title": it.title, "context": it.context,
                    "due_date": _iso(it.due_date), "source_link": it.source_link,
                    "extra": json.dumps(it.extra, sort_keys=True),
                }
                exists = self.db.execute("SELECT 1 FROM items WHERE id = ?", (it.id,)).fetchone()
                if exists:
                    sets = ", ".join(f"{k} = :{k}" for k in SCRAPED_FIELDS)
                    self.db.execute(
                        f"UPDATE items SET {sets}, last_synced = :now, active = 1, updated_at = :now "
                        "WHERE id = :id",
                        {**scraped, "now": now, "id": it.id},
                    )
                    updated += 1
                else:
                    self.db.execute(
                        "INSERT INTO items (id, source, source_key, type, title, context, due_date, "
                        "source_link, grade_weight, importance, status, estimated_duration, "
                        "actual_duration, last_synced, extra, created_at, updated_at) VALUES "
                        "(:id, :source, :source_key, :type, :title, :context, :due_date, :source_link, "
                        ":grade_weight, :importance, :status, :estimated_duration, :actual_duration, "
                        ":now, :extra, :now, :now)",
                        {**scraped, "id": it.id, "source": it.source, "source_key": it.source_key,
                         "grade_weight": it.grade_weight, "importance": it.importance,
                         "status": it.status, "estimated_duration": it.estimated_duration,
                         "actual_duration": it.actual_duration, "now": now},
                    )
                    inserted += 1
            # A source can say an item is finished (e.g. Crowdmark "submitted"); that's
            # authoritative, so move it to done once — the user can still reopen it by hand.
            for it in items:
                if not it.extra.get("completed_at"):
                    continue
                row = self.db.execute("SELECT status FROM items WHERE id = ?", (it.id,)).fetchone()
                ever_done = self.db.execute(
                    "SELECT 1 FROM status_events WHERE item_id = ? AND to_status = 'done'", (it.id,)).fetchone()
                if row["status"] != "done" and not ever_done:
                    self.db.execute("UPDATE items SET status = 'done', status_changed_at = ? WHERE id = ?",
                                    (now, it.id))
                    self.db.execute(
                        "INSERT INTO status_events (item_id, from_status, to_status, at) VALUES (?, ?, 'done', ?)",
                        (it.id, row["status"], now))
            stale = [r["id"] for r in self.db.execute(
                "SELECT id FROM items WHERE source = ? AND active = 1", (source,))
                if r["id"] not in seen]
            self.db.executemany(
                "UPDATE items SET active = 0, updated_at = ? WHERE id = ?", [(now, i) for i in stale])
            self.db.execute(
                "INSERT INTO source_health (source, last_attempt, last_success, last_error) "
                "VALUES (?, ?, ?, NULL) ON CONFLICT(source) DO UPDATE SET "
                "last_attempt = excluded.last_attempt, last_success = excluded.last_success, "
                "last_error = NULL", (source, now, now))
        self.recompute_priorities()
        return {"inserted": inserted, "updated": updated, "deactivated": len(stale)}

    def mark_ok(self, source: str, result=None):
        """Record a successful run for a non-ingest job (e.g. calendar push); passes result through."""
        now = _iso(_now())
        with self.db:
            self.db.execute(
                "INSERT INTO source_health (source, last_attempt, last_success, last_error) "
                "VALUES (?, ?, ?, NULL) ON CONFLICT(source) DO UPDATE SET "
                "last_attempt = excluded.last_attempt, last_success = excluded.last_success, "
                "last_error = NULL", (source, now, now))
        return result

    def upsert_manual(self, it: Item) -> str:
        """Add or update one hand-entered item (source 'manual'). Unlike sync_source this never
        deactivates other items — manual entries only go away when the user marks them done."""
        if it.source != "manual":
            raise ValueError("upsert_manual only takes source='manual' items")
        now = _iso(_now())
        fields = {"type": it.type, "title": it.title, "context": it.context,
                  "due_date": _iso(it.due_date), "source_link": it.source_link,
                  "extra": json.dumps(it.extra, sort_keys=True)}
        with self.db:
            if self.db.execute("SELECT 1 FROM items WHERE id = ?", (it.id,)).fetchone():
                sets = ", ".join(f"{k} = :{k}" for k in SCRAPED_FIELDS)
                self.db.execute(f"UPDATE items SET {sets}, active = 1, updated_at = :now WHERE id = :id",
                                {**fields, "now": now, "id": it.id})
            else:
                self.db.execute(
                    "INSERT INTO items (id, source, source_key, type, title, context, due_date, source_link, "
                    "grade_weight, importance, status, last_synced, extra, created_at, updated_at) VALUES "
                    "(:id, 'manual', :key, :type, :title, :context, :due_date, :source_link, :gw, :imp, "
                    ":status, :now, :extra, :now, :now)",
                    {**fields, "id": it.id, "key": it.source_key, "gw": it.grade_weight,
                     "imp": it.importance, "status": it.status, "now": now})
        self.recompute_priorities()
        return it.id

    def replace_classes(self, term: str, meetings: list) -> int:
        """Swap in a freshly captured timetable (quest.ClassMeeting objects)."""
        now = _iso(_now())
        with self.db:
            self.db.execute("DELETE FROM classes")
            self.db.executemany(
                "INSERT INTO classes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(term, m.course, m.title, m.component, m.section, m.class_nbr, json.dumps(m.days),
                  m.start.isoformat(), m.end.isoformat(), m.location, m.instructor,
                  m.start_date.isoformat() if m.start_date else None,
                  m.end_date.isoformat() if m.end_date else None, now) for m in meetings])
        self.mark_ok("quest")
        return len(meetings)

    def classes_term(self) -> str | None:
        row = self.db.execute("SELECT term FROM classes LIMIT 1").fetchone()
        return row["term"] if row else None

    def classes(self) -> list:
        from datetime import date, time
        from .sources.quest import ClassMeeting  # local: keeps the store free of bs4 at import
        return [ClassMeeting(
            course=r["course"], title=r["title"] or "", component=r["component"] or "", section=r["section"] or "",
            class_nbr=r["class_nbr"] or "", days=json.loads(r["days"]),
            start=time.fromisoformat(r["start_time"]), end=time.fromisoformat(r["end_time"]),
            location=r["location"] or "", instructor=r["instructor"] or "",
            start_date=date.fromisoformat(r["start_date"]) if r["start_date"] else None,
            end_date=date.fromisoformat(r["end_date"]) if r["end_date"] else None)
            for r in self.db.execute("SELECT * FROM classes ORDER BY start_time")]

    def record_failure(self, source: str, error: str) -> None:
        now = _iso(_now())
        with self.db:
            self.db.execute(
                "INSERT INTO source_health (source, last_attempt, last_error) VALUES (?, ?, ?) "
                "ON CONFLICT(source) DO UPDATE SET last_attempt = excluded.last_attempt, "
                "last_error = excluded.last_error", (source, now, error))

    # --- user edits -------------------------------------------------------

    def set_status(self, item_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        now = _iso(_now())
        with self.db:
            row = self.db.execute("SELECT status FROM items WHERE id = ?", (item_id,)).fetchone()
            if row is None:
                raise KeyError(item_id)
            if row["status"] == status:
                return
            self.db.execute(
                "UPDATE items SET status = ?, status_changed_at = ?, updated_at = ? WHERE id = ?",
                (status, now, now, item_id))
            self.db.execute(
                "INSERT INTO status_events (item_id, from_status, to_status, at) VALUES (?, ?, ?, ?)",
                (item_id, row["status"], status, now))
        self.recompute_priorities()

    def set_grade_weight(self, item_id: str, weight: float | None) -> None:
        if weight is not None and not 0 <= weight <= 100:
            raise ValueError("grade weight must be 0–100%")
        with self.db:
            cur = self.db.execute("UPDATE items SET grade_weight = ?, updated_at = ? WHERE id = ?",
                                  (weight, _iso(_now()), item_id))
        if cur.rowcount == 0:
            raise KeyError(item_id)
        self.recompute_priorities()

    def _set_duration(self, column: str, item_id: str, minutes: int | None) -> None:
        if minutes is not None and not 0 < minutes <= 60 * 200:
            raise ValueError("duration must be between 1 minute and 200 hours")
        with self.db:
            cur = self.db.execute(f"UPDATE items SET {column} = ?, updated_at = ? WHERE id = ?",
                                  (minutes, _iso(_now()), item_id))
        if cur.rowcount == 0:
            raise KeyError(item_id)

    def set_relevance(self, item_id: str, choice: str | None) -> None:
        """'show' / 'hide' pins an item in or out of the academic filter; None goes back to automatic."""
        if choice not in (None, "show", "hide"):
            raise ValueError("relevance must be show, hide or automatic")
        with self.db:
            cur = self.db.execute("UPDATE items SET relevance = ?, updated_at = ? WHERE id = ?",
                                  (choice, _iso(_now()), item_id))
        if cur.rowcount == 0:
            raise KeyError(item_id)

    def set_estimated_duration(self, item_id: str, minutes: int | None) -> None:
        self._set_duration("estimated_duration", item_id, minutes)

    def set_actual_duration(self, item_id: str, minutes: int | None) -> None:
        self._set_duration("actual_duration", item_id, minutes)

    def get_item(self, item_id: str) -> Item | None:
        row = self.db.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
        return self._row_to_item(row) if row else None

    def done_items(self) -> list[Item]:
        """Every completed item, including stale/inactive ones — calibration history."""
        return [self._row_to_item(r) for r in self.db.execute("SELECT * FROM items WHERE status = 'done'")]

    def set_context_importance(self, context: str, importance: float | None) -> None:
        if importance is not None and not 0 < importance <= 10:
            raise ValueError("importance must be in (0, 10]")
        with self.db:
            if importance is None:
                self.db.execute("DELETE FROM contexts WHERE context = ?", (context,))
            else:
                self.db.execute(
                    "INSERT INTO contexts (context, importance) VALUES (?, ?) "
                    "ON CONFLICT(context) DO UPDATE SET importance = excluded.importance",
                    (context, importance))
        self.recompute_priorities()

    def context_importance(self) -> dict[str, float]:
        return {r["context"]: r["importance"] for r in self.db.execute("SELECT * FROM contexts")}

    # --- read -------------------------------------------------------------

    def recompute_priorities(self, now: datetime | None = None) -> None:
        """Urgency is time-dependent, so scores are refreshed on every sync/read."""
        ctx_imp = {r["context"]: r["importance"] for r in self.db.execute("SELECT * FROM contexts")}
        rows = self.db.execute(
            "SELECT id, type, grade_weight, importance, due_date, status, context FROM items").fetchall()
        with self.db:
            self.db.executemany("UPDATE items SET priority_score = ? WHERE id = ?", [
                (priority.score(r["grade_weight"],
                                r["importance"] if r["importance"] is not None else ctx_imp.get(r["context"]),
                                _parse(r["due_date"]), r["status"], now, r["type"]), r["id"])
                for r in rows])

    def list_items(self, include_inactive: bool = False, include_past: bool = False,
                   include_hidden: bool = False) -> list[Item]:
        """Live items, best first. Non-academic items (relevance.py) are left out unless asked for."""
        from . import relevance

        self.recompute_priorities()
        sql = "SELECT * FROM items" + ("" if include_inactive else " WHERE active = 1")
        sql += " ORDER BY priority_score DESC, due_date IS NULL, due_date"
        items = [self._row_to_item(r) for r in self.db.execute(sql)]
        if not include_hidden:
            items = [i for i in items if relevance.shown(i)]
        return items if include_past else [i for i in items if not priority.is_past(i.due_date)]

    # --- planner blocks and settings --------------------------------------

    def plan_blocks(self, states: tuple[str, ...] = ("suggested", "accepted", "dismissed")) -> list:
        from .planner import Block
        marks = ",".join("?" * len(states))
        return [Block(r["item_id"], _parse(r["start"]), _parse(r["end"]), r["state"], r["id"], bool(r["edited"]))
                for r in self.db.execute(f'SELECT * FROM plan_blocks WHERE state IN ({marks}) ORDER BY start', states)]

    def get_block(self, block_id: str):
        from .planner import Block
        r = self.db.execute("SELECT * FROM plan_blocks WHERE id = ?", (block_id,)).fetchone()
        return Block(r["item_id"], _parse(r["start"]), _parse(r["end"]), r["state"], r["id"], bool(r["edited"])) if r else None

    def add_block(self, item_id: str, start: datetime, end: datetime, state: str = "accepted",
                  origin: str = "", edited: bool = False) -> str:
        """A block added directly ("Start now", "Plan my day", or placed by hand on the grid)."""
        from .planner import Block
        b = Block(item_id, start, end, state)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO plan_blocks (id, item_id, start, "end", state, created_at, origin, edited) '
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                            (b.id, item_id, _iso(start), _iso(end), state, _iso(_now()), origin, int(edited)))
        return b.id

    def set_block_origin(self, block_ids: list[str], origin: str) -> None:
        with self.db:
            self.db.executemany("UPDATE plan_blocks SET origin = ? WHERE id = ?", [(origin, i) for i in block_ids])

    def blocks_with_origin(self, origin: str) -> list:
        from .planner import Block
        return [Block(r["item_id"], _parse(r["start"]), _parse(r["end"]), r["state"], r["id"], bool(r["edited"]))
                for r in self.db.execute("SELECT * FROM plan_blocks WHERE origin = ? ORDER BY start", (origin,))]

    # --- goals --------------------------------------------------------------

    def goals(self, active_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM goals" + (" WHERE active = 1" if active_only else "") + " ORDER BY created_at"
        return [{**dict(r), "until": date.fromisoformat(r["until"]) if r["until"] else None, "active": bool(r["active"])}
                for r in self.db.execute(sql)]

    def save_goal(self, goal_id: str, title: str, course: str | None, kind: str, hours: float,
                  until, session_min: int, item_id: str | None = None) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO goals (id, title, course, kind, hours, until, session_min, item_id, active, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
                "course = excluded.course, kind = excluded.kind, hours = excluded.hours, until = excluded.until, "
                "session_min = excluded.session_min",
                (goal_id, title, course, kind, hours, until.isoformat() if until else None, session_min, item_id,
                 _iso(_now())))

    def set_goal_active(self, goal_id: str, active: bool) -> bool:
        with self.db:
            return self.db.execute("UPDATE goals SET active = ? WHERE id = ?", (int(active), goal_id)).rowcount > 0

    def delete_goal(self, goal_id: str) -> bool:
        with self.db:
            self.db.execute("DELETE FROM plan_blocks WHERE item_id = ? AND state != 'accepted'", (goal_id,))
            return self.db.execute("DELETE FROM goals WHERE id = ?", (goal_id,)).rowcount > 0

    def move_block(self, block_id: str, start: datetime, end: datetime) -> bool:
        """New times for a block the user resized or moved. It becomes his (accepted): the planner
        never moves it, and it keeps its id — so its calendar event is updated, not duplicated."""
        with self.db:
            return self.db.execute(
                'UPDATE plan_blocks SET start = ?, "end" = ?, state = \'accepted\', edited = 1 '
                'WHERE id = ? AND state != \'dismissed\'',
                (_iso(start), _iso(end), block_id)).rowcount > 0

    # --- focus sessions -----------------------------------------------------

    @staticmethod
    def _focus_row(r) -> dict:
        d = dict(r)
        for k in ("started_at", "ended_at", "break_started_at"):
            d[k] = _parse(d[k])
        return d

    def focus_sessions(self, item_id: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM focus_sessions", ()
        if item_id is not None:
            sql, args = sql + " WHERE item_id = ?", (item_id,)
        return [self._focus_row(r) for r in self.db.execute(sql + " ORDER BY started_at", args)]

    def get_focus(self, session_id: str) -> dict | None:
        r = self.db.execute("SELECT * FROM focus_sessions WHERE id = ?", (session_id,)).fetchone()
        return self._focus_row(r) if r else None

    # --- test check-ins -------------------------------------------------------

    def debrief_ids(self) -> set[str]:
        return {r["item_id"] for r in self.db.execute("SELECT item_id FROM test_debriefs")}

    def debriefs(self, include_skipped: bool = False) -> list[dict]:
        sql = "SELECT * FROM test_debriefs" + ("" if include_skipped else " WHERE skipped = 0") + " ORDER BY answered_at DESC"
        return [dict(r) for r in self.db.execute(sql)]

    def save_debrief(self, item_id: str, went: str, improve: str, notes: str, minutes: int | None,
                     skipped: bool = False) -> None:
        with self.db:
            self.db.execute(
                "INSERT INTO test_debriefs (item_id, went, improve, notes, minutes, skipped, answered_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET went = excluded.went, "
                "improve = excluded.improve, notes = excluded.notes, minutes = excluded.minutes, "
                "skipped = excluded.skipped, answered_at = excluded.answered_at",
                (item_id, went, improve, notes, minutes, int(skipped), _iso(_now())))

    def running_focus(self) -> dict | None:
        r = self.db.execute("SELECT * FROM focus_sessions WHERE ended_at IS NULL ORDER BY started_at DESC").fetchone()
        return self._focus_row(r) if r else None

    def add_focus(self, session_id: str, block_id: str, item_id: str, goal: str, planned_minutes: int,
                  started_at: datetime) -> None:
        with self.db:
            self.db.execute("INSERT INTO focus_sessions (id, block_id, item_id, goal, planned_minutes, started_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (session_id, block_id, item_id, goal, planned_minutes, _iso(started_at)))

    def update_focus(self, session_id: str, **fields) -> None:
        allowed = {"ended_at", "paused_seconds", "break_started_at", "break_count", "extended_minutes", "outcome"}
        if not fields or set(fields) - allowed:
            raise ValueError("bad focus update")
        vals = [(_iso(v) if isinstance(v, datetime) else v) for v in fields.values()]
        with self.db:
            self.db.execute(f"UPDATE focus_sessions SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?",
                            (*vals, session_id))

    def plan_events(self) -> list[dict]:
        return [{"id": r["id"], "title": r["title"], "start": _parse(r["start"]), "end": _parse(r["end"])}
                for r in self.db.execute('SELECT * FROM plan_events ORDER BY start')]

    def save_plan_event(self, event_id: str, title: str, start: datetime, end: datetime) -> None:
        with self.db:
            self.db.execute(
                'INSERT INTO plan_events (id, title, start, "end", created_at) VALUES (?, ?, ?, ?, ?) '
                'ON CONFLICT(id) DO UPDATE SET title = excluded.title, start = excluded.start, "end" = excluded."end"',
                (event_id, title, _iso(start), _iso(end), _iso(_now())))

    def delete_plan_event(self, event_id: str) -> bool:
        with self.db:
            return self.db.execute("DELETE FROM plan_events WHERE id = ?", (event_id,)).rowcount > 0

    def replace_suggestions(self, blocks: list) -> None:
        """Swap in a fresh set of suggested blocks; accepted and dismissed ones are untouched."""
        now = _iso(_now())
        with self.db:
            self.db.execute("DELETE FROM plan_blocks WHERE state = 'suggested'")
            self.db.executemany(
                'INSERT OR IGNORE INTO plan_blocks (id, item_id, start, "end", state, created_at) '
                "VALUES (?, ?, ?, ?, 'suggested', ?)",
                [(b.id, b.key, _iso(b.start), _iso(b.end), now) for b in blocks])

    def set_block_state(self, block_id: str, state: str, only_from: str | None = None) -> bool:
        if state not in ("suggested", "accepted", "dismissed"):
            raise ValueError("unknown block state")
        sql, args = "UPDATE plan_blocks SET state = ? WHERE id = ?", [state, block_id]
        if only_from:
            sql, args = sql + " AND state = ?", args + [only_from]
        with self.db:
            return self.db.execute(sql, args).rowcount > 0

    def delete_blocks(self, block_ids: list[str]) -> None:
        with self.db:
            self.db.executemany("DELETE FROM plan_blocks WHERE id = ?", [(i,) for i in block_ids])

    def get_setting(self, key: str, default=None):
        row = self.db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set_setting(self, key: str, value) -> None:
        with self.db:
            self.db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, json.dumps(value)))

    def version(self) -> str:
        """Changes whenever anything a page shows changes (items, sync results, timetable)."""
        a = self.db.execute("SELECT COUNT(*), MAX(updated_at), MAX(last_synced) FROM items").fetchone()
        b = self.db.execute("SELECT MAX(last_attempt) FROM source_health WHERE source != 'gcal'").fetchone()
        c = self.db.execute("SELECT MAX(captured_at) FROM classes").fetchone()
        return "|".join(str(x) for x in (*a, *b, *c))

    def source_health(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM source_health ORDER BY source")]

    @staticmethod
    def _row_to_item(r: sqlite3.Row) -> Item:
        return Item(
            source=r["source"], source_key=r["source_key"], type=r["type"], title=r["title"],
            context=r["context"], due_date=_parse(r["due_date"]), source_link=r["source_link"],
            grade_weight=r["grade_weight"], importance=r["importance"], status=r["status"],
            estimated_duration=r["estimated_duration"], actual_duration=r["actual_duration"],
            priority_score=r["priority_score"], last_synced=_parse(r["last_synced"]),
            relevance=r["relevance"] if "relevance" in r.keys() else None,
            extra=json.loads(r["extra"]),
        )
