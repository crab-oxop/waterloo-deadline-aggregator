from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from aggregator import gcal
from aggregator.sources import odyssey
from aggregator.sources.ics import FeedError
from aggregator.store import Store

TZ = ZoneInfo("America/Toronto")
HTML = """<html><body><h1>Assessment Schedule (userid)</h1>
<p>Assigned seat information will typically be available 3 business days prior to the examination.</p>
<table><tr><th>Exam</th><th>Duration</th><th>When</th><th>Room</th><th>Seat</th><th>Sequence</th></tr>
<tr><td>MATH 137 Quiz 2</td><td>50 minutes</td><td>2026-09-29 16:30–17:20</td><td>HALL 100</td><td>C-07</td><td></td></tr>
<tr><td>MATH 135 Midterm</td><td>1 hour 50 minutes (110 minutes)</td><td>2026-10-27 18:30–20:20</td><td>TBA</td><td></td><td></td></tr>
<tr><td>CS 135 Final</td><td>2 hours 30 minutes (150 minutes)</td><td>TBA</td><td>TBA</td><td></td><td></td></tr>
</table></body></html>"""


def test_parse_reads_times_rooms_and_seats():
    quiz, mid, final = odyssey.parse(HTML)
    assert (quiz.type, quiz.context, quiz.title) == ("quiz", "MATH 137", "MATH 137 Quiz 2")
    assert quiz.due_date == datetime(2026, 9, 29, 16, 30, tzinfo=TZ)
    assert quiz.extra["end"] == "2026-09-29T17:20:00-04:00"
    assert quiz.extra["location"] == "HALL 100 · Seat C-07"
    assert mid.type == "exam" and "room" not in mid.extra  # TBA room isn't shown as a room
    assert final.due_date is None and final.context == "CS 135"  # not scheduled yet


def test_parse_refuses_a_signed_out_page():
    with pytest.raises(FeedError):
        odyssey.parse("<html><body><a href='/mellon/login'>Log in</a></body></html>")


def test_rescrape_fills_in_a_final_once_scheduled():
    store = Store(":memory:")
    store.sync_source("odyssey", odyssey.parse(HTML))
    later = HTML.replace("<td>TBA</td><td>TBA</td>", "<td>2026-12-14 09:00–11:30</td><td>PAC 1</td>")
    store.sync_source("odyssey", odyssey.parse(later))
    final = next(i for i in store.list_items() if i.title == "CS 135 Final")
    assert final.due_date == datetime(2026, 12, 14, 9, 0, tzinfo=TZ)
    assert len([i for i in store.list_items(include_past=True) if i.source == "odyssey"]) == 3  # same items, updated


def test_calendar_event_spans_the_sitting():
    quiz = odyssey.parse(HTML)[0]
    body = gcal.event_body(quiz)
    assert body["end"]["dateTime"] == "2026-09-29T17:20:00-04:00"


def test_on_schedule():
    assert odyssey.on_schedule("https://odyssey.uwaterloo.ca/teaching/schedule")
    assert not odyssey.on_schedule("https://adfs.uwaterloo.ca/adfs/ls/?SAMLRequest=x")


def test_stale_after_three_days():
    now = datetime(2026, 9, 24, 12, tzinfo=TZ)
    assert odyssey.is_stale(None, now)
    assert not odyssey.is_stale("2026-09-22T12:00:00-04:00", now)
    assert odyssey.is_stale("2026-09-20T12:00:00-04:00", now)
