"""Outlook connector with two backends, chosen automatically:

- Microsoft Graph (preferred): calendar + course-related mail. Needs Waterloo IT's admin
  approval for Mail.Read/Calendars.Read, so it may not be available yet.
- Published-calendar ICS link (fallback): calendar only, no approval needed.

Read-only either way: nothing is sent, moved, flagged, or marked read."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .. import graph
from ..mail_filters import ACTIVE_FILTER, Mail
from ..models import Item
from ..secrets import get_secret
from .ics import LOCAL_TZ, WINDOW_FUTURE, WINDOW_PAST, FeedError, fetch_feed, find_course, guess_type
from .ics import parse_feed as parse_ics

SOURCE = "outlook"
ICS_SECRET = "outlook_ics_url"
MAIL_LOOKBACK = timedelta(days=14)


def is_configured() -> bool:
    return graph.is_signed_in() or bool(get_secret(ICS_SECRET))


def collect() -> list[Item]:
    if graph.is_signed_in():
        return graph_calendar() + graph_mail()
    url = get_secret(ICS_SECRET)
    if not url:
        raise FeedError("Outlook not connected — run: python -m aggregator setup-outlook-ics")
    return parse_ics(fetch_feed(url), SOURCE)


# --- Graph backend ---------------------------------------------------------------

def _parse_graph_time(value: dict, all_day: bool) -> datetime:
    # We ask Graph for UTC (Prefer header); all-day events are dates in the user's zone.
    raw = value["dateTime"]
    if all_day:
        return datetime.combine(date.fromisoformat(raw[:10]), datetime.min.time().replace(hour=23, minute=59),
                                tzinfo=LOCAL_TZ)
    return datetime.fromisoformat(raw[:26]).replace(tzinfo=timezone.utc)


def event_to_item(ev: dict) -> Item | None:
    if ev.get("isCancelled"):
        return None
    start = _parse_graph_time(ev["start"], ev.get("isAllDay", False))
    key = ev.get("iCalUId") or ev["id"]
    if ev.get("type") in ("occurrence", "exception"):  # same scheme as the ICS backend
        key += "@" + start.astimezone(timezone.utc).isoformat()
    subject = (ev.get("subject") or "").strip() or "(untitled)"
    location = (ev.get("location") or {}).get("displayName", "")
    return Item(source=SOURCE, source_key=key, type=guess_type(subject), title=subject,
                context=find_course(location, subject, ev.get("bodyPreview", "")),
                due_date=start, source_link=ev.get("webLink"),
                extra={"location": location} if location else {})


def graph_calendar(now: datetime | None = None) -> list[Item]:
    now = now or datetime.now(timezone.utc)
    events = graph.get_all("/me/calendarView", {
        "startDateTime": (now - WINDOW_PAST).isoformat(),
        "endDateTime": (now + WINDOW_FUTURE).isoformat(),
        "$select": "id,iCalUId,subject,start,end,isAllDay,isCancelled,type,location,webLink,bodyPreview",
        "$top": "100",
    }, headers={"Prefer": 'outlook.timezone="UTC"'})
    return [it for ev in events if (it := event_to_item(ev))]


def message_to_item(msg: dict) -> Item | None:
    received = datetime.fromisoformat(msg["receivedDateTime"].replace("Z", "+00:00"))
    sender = ((msg.get("from") or {}).get("emailAddress") or {})
    context = ACTIVE_FILTER.match(Mail(
        subject=msg.get("subject") or "", preview=msg.get("bodyPreview") or "",
        sender=sender.get("address", ""), received=received))
    if context is None:
        return None
    flag = msg.get("flag") or {}
    due = None
    if flag.get("flagStatus") == "flagged" and flag.get("dueDateTime"):
        due = _parse_graph_time(flag["dueDateTime"], all_day=False)
    # internetMessageId survives folder moves; Graph's own id does not.
    key = "mail:" + (msg.get("internetMessageId") or msg["id"])
    return Item(source=SOURCE, source_key=key, type="message",
                title=(msg.get("subject") or "(no subject)").strip(), context=context,
                due_date=due, source_link=msg.get("webLink"),
                extra={"from": sender.get("name") or sender.get("address", ""), "received": received.isoformat()})


def graph_mail(now: datetime | None = None) -> list[Item]:
    now = now or datetime.now(timezone.utc)
    since = (now - MAIL_LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ")
    messages = graph.get_all("/me/mailFolders/inbox/messages", {
        "$filter": f"receivedDateTime ge {since}",
        "$orderby": "receivedDateTime desc",
        "$select": "id,internetMessageId,subject,bodyPreview,from,receivedDateTime,webLink,flag",
        "$top": "50",
    }, headers={"Prefer": 'outlook.timezone="UTC"'})
    return [it for m in messages if (it := message_to_item(m))]
