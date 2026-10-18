from datetime import datetime, timezone

import pytest

from aggregator.mail_filters import CourseMentionFilter, Mail
from aggregator.sources import ics, outlook

NOW = datetime(2026, 9, 22, 12, tzinfo=timezone.utc)

OUTLOOK_ICS = b"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:Microsoft Exchange Server 2010
BEGIN:VEVENT
UID:040000008200E00074C5B7101A82E0080000000011
SUMMARY:CS 135 Tutorial
DTSTART;TZID=America/Toronto:20260911T143000
DTEND;TZID=America/Toronto:20260911T152000
RRULE:FREQ=WEEKLY;UNTIL=20261204T235959Z
LOCATION:ABC 1100
END:VEVENT
BEGIN:VEVENT
UID:040000008200E00074C5B7101A82E0080000000011
RECURRENCE-ID;TZID=America/Toronto:20260925T143000
SUMMARY:CS 135 Tutorial (moved)
DTSTART;TZID=America/Toronto:20260925T160000
DTEND;TZID=America/Toronto:20260925T165000
END:VEVENT
BEGIN:VEVENT
UID:one-off-1
SUMMARY:Club general meeting
DTSTART:20260924T230000Z
END:VEVENT
BEGIN:VEVENT
UID:ancient
SUMMARY:Orientation
DTSTART:20250901T140000Z
END:VEVENT
END:VCALENDAR
"""


def test_ics_expands_recurrences_within_window():
    items = ics.parse_feed(OUTLOOK_ICS, "outlook", now=NOW)
    titles = [i.title for i in items]
    assert "Orientation" not in titles  # outside the window
    assert "Club general meeting" in titles
    tutorials = [i for i in items if i.title.startswith("CS 135 Tutorial")]
    # Window starts Sep 8; Sep 11 .. Dec 4 weekly = 13 occurrences, one moved.
    assert len(tutorials) == 13
    assert len({i.id for i in tutorials}) == 13  # every occurrence has its own identity
    moved = next(i for i in tutorials if "moved" in i.title)
    assert (moved.due_date.hour, moved.due_date.minute) == (16, 0)
    assert all(i.context == "CS 135" and i.type == "meeting" for i in tutorials)
    one_off = next(i for i in items if i.title == "Club general meeting")
    assert one_off.source_key == "one-off-1"  # non-recurring keeps a bare UID


def test_ics_identities_stable_across_rescrapes():
    a = {i.id for i in ics.parse_feed(OUTLOOK_ICS, "outlook", now=NOW)}
    b = {i.id for i in ics.parse_feed(OUTLOOK_ICS, "outlook", now=NOW)}
    assert a == b


@pytest.mark.parametrize("subject,preview,expected", [
    ("CS135 A2 regrade", "", "CS 135"),
    ("Question", "about math 137 midterm", "MATH 137"),
    ("Re: PHYS-121 quiz", "", "PHYS 121"),
    ("PHYS 1210 seminar", "", None),     # different course number
    ("Club social", "pizza at 6", None),
    ("ENGL 119 then CS 135", "", "ENGL 119"),  # first course in subject wins
])
def test_course_mention_filter(subject, preview, expected):
    msg = Mail(subject=subject, preview=preview, sender="x@uwaterloo.ca", received=NOW)
    assert CourseMentionFilter().match(msg) == expected


def graph_message(**kw):
    base = {"id": "AAMk-volatile", "internetMessageId": "<abc@uwaterloo.ca>", "subject": "CS 135 A3 released",
            "bodyPreview": "Assignment 3 is out", "receivedDateTime": "2026-09-21T15:00:00Z",
            "from": {"emailAddress": {"name": "CS 135 Staff", "address": "cs135@uwaterloo.ca"}},
            "webLink": "https://outlook.office365.com/owa/?ItemID=x", "flag": {"flagStatus": "notFlagged"}}
    return {**base, **kw}


def test_message_to_item():
    it = outlook.message_to_item(graph_message())
    assert (it.source, it.type, it.context, it.due_date) == ("outlook", "message", "CS 135", None)
    assert it.source_key == "mail:<abc@uwaterloo.ca>"  # stable across folder moves
    assert it.extra["from"] == "CS 135 Staff"
    assert outlook.message_to_item(graph_message(subject="Lunch?", bodyPreview="")) is None

    flagged = outlook.message_to_item(graph_message(flag={
        "flagStatus": "flagged", "dueDateTime": {"dateTime": "2026-09-30T03:59:00.0000000", "timeZone": "UTC"}}))
    assert flagged.due_date == datetime(2026, 9, 30, 3, 59, tzinfo=timezone.utc)


def test_event_to_item():
    ev = {"id": "e1", "iCalUId": "UID-1", "subject": "MATH 137 office hours", "type": "occurrence",
          "start": {"dateTime": "2026-09-24T18:00:00.0000000", "timeZone": "UTC"}, "isAllDay": False,
          "location": {"displayName": "ABC 3300"}, "webLink": "https://outlook.office365.com/x"}
    it = outlook.event_to_item(ev)
    assert it.context == "MATH 137" and it.type == "meeting"
    assert it.source_key == "UID-1@2026-09-24T18:00:00+00:00"
    assert outlook.event_to_item({**ev, "isCancelled": True}) is None
    single = outlook.event_to_item({**ev, "type": "singleInstance"})
    assert single.source_key == "UID-1"


def test_collect_prefers_graph(monkeypatch):
    calls = []
    monkeypatch.setattr(outlook.graph, "is_signed_in", lambda: True)
    monkeypatch.setattr(outlook.graph, "get_all", lambda path, *a, **k: calls.append(path) or [])
    assert outlook.collect() == []
    assert calls == ["/me/calendarView", "/me/mailFolders/inbox/messages"]


def test_collect_falls_back_to_ics(monkeypatch):
    monkeypatch.setattr(outlook.graph, "is_signed_in", lambda: False)
    monkeypatch.setattr(outlook, "get_secret", lambda name: "https://example/cal.ics")
    monkeypatch.setattr(outlook, "fetch_feed", lambda url: OUTLOOK_ICS)
    assert all(i.source == "outlook" for i in outlook.collect())  # which events depends on today's window
    monkeypatch.setattr(outlook, "get_secret", lambda name: None)
    with pytest.raises(ics.FeedError):
        outlook.collect()
