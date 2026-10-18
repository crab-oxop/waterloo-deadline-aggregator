"""One-way push into a dedicated "Waterloo Deadlines" Google Calendar (SPEC.md § Google Calendar Sync).
The dashboard is the source of truth: we only ever write events we own, never read others back."""
from __future__ import annotations

import hashlib
import json
import threading
from contextlib import nullcontext
from datetime import datetime, timezone
from typing import Protocol
from zoneinfo import ZoneInfo

from . import priority
from .models import Item
from .secrets import get_secret, set_secret
from .store import Store

CALENDAR_NAME = "Waterloo Deadlines"
SCOPES = ["https://www.googleapis.com/auth/calendar.app.created"]  # only calendars this app creates
TOKEN_SECRET = "gcal_token"  # authorized-user JSON: refresh token + client id/secret
CALENDAR_ID_SECRET = "gcal_calendar_id"
TZ = "America/Toronto"

GCAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS gcal_events (
    item_id   TEXT PRIMARY KEY,
    body_hash TEXT NOT NULL,
    synced_at TEXT NOT NULL
);
"""


class CalendarClient(Protocol):
    def upsert_event(self, event_id: str, body: dict) -> None: ...
    def delete_event(self, event_id: str) -> None: ...


def event_body(item: Item) -> dict:
    # Deadlines are points in time; a zero-length event sits at the due time.
    start = {"dateTime": item.due_date.isoformat(), "timeZone": TZ}
    prefix = f"[{item.context}] " if item.context else ""
    lines = [f"Type: {item.type}", f"Status: {item.status.replace('_', ' ')}"]
    if item.grade_weight is not None:
        lines.append(f"Weight: {item.grade_weight:g}%")
    if item.source_link:
        lines.append(item.source_link)
    body = {
        "summary": f"{prefix}{item.title}",
        "description": "\n".join(lines),
        "start": start,
        "end": {"dateTime": item.extra["end"], "timeZone": TZ} if item.extra.get("end") else start,
        "status": "confirmed",
        "transparency": "transparent",  # deadlines shouldn't block free/busy
        "reminders": {"useDefault": False, "overrides": []},  # v1 is pull, not push
    }
    if item.source_link:
        body["source"] = {"title": item.source.upper(), "url": item.source_link}
    return body


WORK_PREFIX = "Work: "
STUDY_PREFIX = "Study: "  # exam/quiz prep and goal sessions


def work_body(block, item: Item) -> dict:
    """An accepted study block — titled "Work: …" so it's easy to tell apart from deadlines."""
    ctx = f"[{item.context}] " if item.context else ""
    lines = [f"Planned block for: {item.title}",
             f"Due: {item.due_date.astimezone(ZoneInfo(TZ)).strftime('%a %b %d, %I:%M %p') if item.due_date else 'no date'}",
             "Accepted in Waterloo Deadlines (Plan tab)."]
    if item.source_link:
        lines.append(item.source_link)
    return {
        "summary": f"{STUDY_PREFIX if getattr(item, 'type', '') in ('quiz', 'exam', 'goal') else WORK_PREFIX}{ctx}{item.title}",
        "description": "\n".join(lines),
        "start": {"dateTime": block.start.isoformat(), "timeZone": TZ},
        "end": {"dateTime": block.end.isoformat(), "timeZone": TZ},
        "status": "confirmed",
        "transparency": "opaque",  # unlike deadlines, a work block is time you've set aside
        "colorId": "9",
        "reminders": {"useDefault": False, "overrides": []},
    }


def own_event_body(ev: dict) -> dict:
    """An event the user added on the Work plan (gym, a shift, …)."""
    return {
        "summary": ev["title"],
        "description": "Added in Waterloo Deadlines (Work plan).",
        "start": {"dateTime": ev["start"].isoformat(), "timeZone": TZ},
        "end": {"dateTime": ev["end"].isoformat(), "timeZone": TZ},
        "status": "confirmed",
        "transparency": "opaque",
        "reminders": {"useDefault": False, "overrides": []},
    }


def _hash(body: dict) -> str:
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _plan_changes(store: Store, now: datetime) -> tuple[list, list, int]:
    """(upserts [(id, body, hash)], deletes [id], unchanged count). Database only — no network."""
    store.db.executescript(GCAL_SCHEMA)
    pushed = {r["item_id"]: r["body_hash"] for r in store.db.execute("SELECT * FROM gcal_events")}

    wanted: dict[str, dict] = {}
    for it in store.list_items(include_past=True):  # inactive items drop out → deleted below
        if it.due_date is None or it.status == "done":  # done → dropped from the calendar
            continue
        if priority.is_past(it.due_date, now) and it.id not in pushed:
            continue  # never backfill stale history; already-pushed past events are left alone
        wanted[it.id] = event_body(it)

    # Accepted study blocks ride the same one-way mirror, keyed by their own ids. Blocks of items
    # that are done drop out first, so their events are deleted below.
    from . import plan_service
    plan_service.cleanup(store, now=now)
    for block, item in plan_service.accepted_events(store):
        if block.end < now - priority.PAST_GRACE and block.id not in pushed:
            continue
        wanted[block.id] = work_body(block, item)
    for ev in store.plan_events():  # The user's own events from the Work plan
        if ev["end"] < now - priority.PAST_GRACE and ev["id"] not in pushed:
            continue
        wanted[ev["id"]] = own_event_body(ev)

    upserts, unchanged = [], 0
    for event_id, body in wanted.items():
        h = _hash(body)
        if pushed.get(event_id) == h:
            unchanged += 1
        else:
            upserts.append((event_id, body, h))
    return upserts, sorted(set(pushed) - set(wanted)), unchanged


def push(store: Store, client: CalendarClient, now: datetime | None = None, lock=None) -> dict:
    """Make the calendar mirror the dashboard's live items. Event IDs are the item IDs
    (hex ⊂ Google's base32hex alphabet), so re-pushing updates instead of duplicating.

    `lock` (the dashboard's store lock) is held only while reading and recording — never during
    the calls to Google, which take a second or more and would freeze every page meanwhile."""
    now = now or datetime.now(timezone.utc)
    lock = lock or nullcontext()
    with lock:
        upserts, deletes, unchanged = _plan_changes(store, now)
    stamp = now.isoformat()
    done_up, done_del = [], []
    try:
        for event_id, body, h in upserts:
            client.upsert_event(event_id, body)
            done_up.append((event_id, h))
        for event_id in deletes:  # removed from source → remove from calendar
            client.delete_event(event_id)
            done_del.append(event_id)
    finally:  # record whatever reached Google, even if a later call failed
        with lock, store.db:
            store.db.executemany(
                "INSERT INTO gcal_events VALUES (?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET "
                "body_hash = excluded.body_hash, synced_at = excluded.synced_at",
                [(i, h, stamp) for i, h in done_up])
            store.db.executemany("DELETE FROM gcal_events WHERE item_id = ?", [(i,) for i in done_del])
    return {"created_or_updated": len(done_up), "unchanged": unchanged, "deleted": len(done_del)}


# --- Google API binding ------------------------------------------------------

def is_configured() -> bool:
    return bool(get_secret(TOKEN_SECRET))


def authorize(client_secrets_path: str) -> None:
    """Run the OAuth consent flow in the browser; the resulting token goes to the OS keychain.
    The credentials.json file can be deleted afterwards."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    with open(client_secrets_path, encoding="utf-8") as f:
        client_config = json.load(f)
    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True)
    set_secret(TOKEN_SECRET, creds.to_json())


def reauthorize() -> None:
    """Sign in to Google again (the user approves in his browser) using the client id/secret already
    stored with the old token — no credentials.json needed. The new token replaces the old one."""
    global _client
    from google_auth_oauthlib.flow import InstalledAppFlow

    old = json.loads(get_secret(TOKEN_SECRET) or "{}")
    if not old.get("client_id") or not old.get("client_secret"):
        raise RuntimeError("no stored Google client — run: python -m aggregator setup-gcal <credentials.json>")
    config = {"installed": {"client_id": old["client_id"], "client_secret": old["client_secret"],
                            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                            "token_uri": old.get("token_uri") or "https://oauth2.googleapis.com/token",
                            "redirect_uris": ["http://localhost"]}}
    flow = InstalledAppFlow.from_client_config(config, SCOPES)
    creds = flow.run_local_server(port=0, open_browser=True, timeout_seconds=300)
    set_secret(TOKEN_SECRET, creds.to_json())
    _client = None  # rebuild with the new token on the next push


class GoogleCalendarClient:
    def __init__(self):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        token = get_secret(TOKEN_SECRET)
        if not token:
            raise RuntimeError("Google Calendar not connected — run: python -m aggregator setup-gcal <credentials.json>")
        creds = Credentials.from_authorized_user_info(json.loads(token), SCOPES)
        if not creds.valid:
            creds.refresh(Request())
            set_secret(TOKEN_SECRET, creds.to_json())
        self.svc = build("calendar", "v3", credentials=creds, cache_discovery=False)
        self.recreated = False
        self.calendar_id = self._calendar_id()

    def _calendar_id(self) -> str:
        from googleapiclient.errors import HttpError

        cal_id = get_secret(CALENDAR_ID_SECRET)
        if cal_id:
            try:
                self.svc.calendars().get(calendarId=cal_id).execute()
                return cal_id
            except HttpError as e:
                if e.resp.status not in (403, 404):
                    raise
                # Calendar was deleted by hand — recreate it and resync everything.
        cal = self.svc.calendars().insert(body={"summary": CALENDAR_NAME, "timeZone": TZ}).execute()
        set_secret(CALENDAR_ID_SECRET, cal["id"])
        self.recreated = True
        return cal["id"]

    def upsert_event(self, event_id: str, body: dict) -> None:
        from googleapiclient.errors import HttpError

        try:  # update also revives a previously-deleted (cancelled) event with this id
            self.svc.events().update(calendarId=self.calendar_id, eventId=event_id, body=body).execute()
        except HttpError as e:
            if e.resp.status != 404:
                raise
            self.svc.events().insert(calendarId=self.calendar_id, body={**body, "id": event_id}).execute()

    def delete_event(self, event_id: str) -> None:
        from googleapiclient.errors import HttpError

        try:
            self.svc.events().delete(calendarId=self.calendar_id, eventId=event_id).execute()
        except HttpError as e:
            if e.resp.status not in (404, 410):
                raise


_client: "GoogleCalendarClient | None" = None
_push_lock = threading.Lock()  # one push at a time: the Google client isn't thread-safe


def sync_calendar(store: Store, lock=None) -> dict | str:
    """Push to Google. The client is built once and reused (building it costs ~2 s of network)."""
    global _client
    if not is_configured():
        return "not configured"
    with _push_lock:
        try:
            if _client is None:
                _client = GoogleCalendarClient()
                if _client.recreated:
                    with lock or nullcontext():
                        store.db.executescript(GCAL_SCHEMA)
                        with store.db:
                            store.db.execute("DELETE FROM gcal_events")  # new calendar is empty: push everything
            return push(store, _client, lock=lock)
        except Exception:
            _client = None  # start fresh next time (expired auth, calendar deleted, network…)
            raise
