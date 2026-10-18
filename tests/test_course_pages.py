from datetime import datetime
from zoneinfo import ZoneInfo

from aggregator import relevance
from aggregator.sources import course_pages
from aggregator.store import Store

TZ = ZoneInfo("America/Toronto")
NOW = datetime(2026, 9, 29, 12, tzinfo=TZ)
PAGE = course_pages.PAGES[0]

HTML = """<html><body><h1>Assignments</h1>
<table><tr><th>Assignment</th><th>Due</th><th>Submit</th></tr>
<tr><td><a href="a00/a00.html">A00</a></td><td>Tue, Sep 16 at 9:00 PM</td><td><a href="/submit/a00">Submit</a></td></tr>
<tr><td><a href="a01/a01.html">A01</a></td><td>Tuesday, September 30</td><td></td></tr>
<tr><td>A02</td><td>2026-10-07 21:00</td><td></td></tr>
<tr><td>A10</td><td>TBA</td><td></td></tr>
<tr><td colspan="3">Late policy: see the policies page</td></tr>
</table>
<table><tr><th>Resource</th><th>Link</th></tr><tr><td>DrRacket</td><td>here</td></tr></table>
</body></html>"""


def test_reads_the_due_table():
    items = {i.title: i for i in course_pages.parse(HTML, PAGE, NOW)}
    assert set(items) == {"A00", "A01", "A02", "A10"}  # the note row and the resources table are skipped
    assert items["A00"].due_date == datetime(2026, 9, 16, 21, 0, tzinfo=TZ)
    assert items["A01"].due_date == datetime(2026, 9, 30, 21, 0, tzinfo=TZ)  # date only → 9 PM
    assert items["A02"].due_date == datetime(2026, 10, 7, 21, 0, tzinfo=TZ)
    assert items["A10"].due_date is None
    assert items["A00"].context == "CS 135" and items["A00"].type == "assignment"
    assert items["A00"].source_link == "https://student.cs.uwaterloo.ca/~cs135/assign/a00/a00.html"  # not "Submit"
    assert items["A02"].source_link == PAGE.url


def test_parse_due_formats():
    p = lambda s: course_pages.parse_due(s, NOW)  # noqa: E731
    assert p("Oct 1, 9pm") == datetime(2026, 10, 1, 21, 0, tzinfo=TZ)
    assert p("Friday, October 3 at noon") == datetime(2026, 10, 3, 12, 0, tzinfo=TZ)
    assert p("Jan 6 at 11:59 PM") == datetime(2027, 1, 6, 23, 59, tzinfo=TZ)  # nearest year
    assert p("TBA") is None and p("") is None


def test_items_are_academic_and_update_in_place():
    store = Store(":memory:")
    store.sync_source("web", course_pages.parse(HTML, PAGE, NOW))
    assert all(relevance.shown(i) for i in store.list_items(include_past=True))
    store.sync_source("web", course_pages.parse(HTML.replace("TBA", "Dec 2 at 9:00 PM"), PAGE, NOW))
    a10 = next(i for i in store.list_items(include_past=True) if i.title == "A10")
    assert a10.due_date == datetime(2026, 12, 2, 21, 0, tzinfo=TZ)
    assert len(store.list_items(include_past=True)) == 4


def test_on_page_matches_after_sign_in_redirect():
    assert course_pages.on_page("https://student.cs.uwaterloo.ca:443/~cs135/assign/index.html", PAGE)
    assert course_pages.on_page("https://student.cs.uwaterloo.ca/~cs135/assign/", PAGE)
    assert not course_pages.on_page("https://adfs.uwaterloo.ca/adfs/ls/", PAGE)
