import pytest
from datetime import date, time

from aggregator.sources import quest
from aggregator.store import Store


def course_block(header, status, rows):
    """Quest's real List View layout: heading, Status table, then the class table (no reliance on ids)."""
    trs = "".join(f"<tr>{''.join(f'<td>{c}</td>' for c in r)}</tr>" for r in rows)
    return (f'<table><tr><td class="PAGROUPDIVIDER">{header}</td></tr>'
            '<tr><td><table><tr><th>Status</th><th>Units</th><th>Grading</th><th>Grade</th><th>Deadlines</th></tr>'
            f'<tr><td>{status}</td><td>0.50</td><td>Numeric Grading Basis</td><td>&nbsp;</td><td>&nbsp;</td></tr>'
            '</table></td></tr>'
            '<tr><td><table><tr><th>Class Nbr</th><th>Section</th><th>Component</th><th>Days &amp; Times</th>'
            f'<th>Room</th><th>Instructor</th><th>Start/End Date</th></tr>{trs}</table></td></tr></table>')


FALL = "09/09/2026 - 12/08/2026"
HTML = "<html><body>" + course_block("CS 135 - Designing Functional Programs", "Enrolled", [
    ("1001", "002", "LEC", "TTh 1:00PM - 2:20PM", "ABC 1100", "Alex Rivera", FALL),
    ("1002", "201", "TUT", "F 9:30AM - 10:20AM", "ABC 2200", "&nbsp;", FALL),
    ("&nbsp;", "&nbsp;", "&nbsp;", "W 11:30AM - 12:20PM", "ABC 2200", "&nbsp;", FALL),  # 2nd TUT time
]) + course_block("MATH 137 - Calculus 1 for Honours Mathematics", "Enrolled", [
    ("2001", "005", "LEC", "MWF 3:30PM - 4:20PM", "XYZ 1300", "Jordan Lee", FALL),
    ("2002", "205", "TST", "M 5:30PM - 6:50PM", "TBA", "&nbsp;", "09/21/2026 - 09/21/2026"),
]) + course_block("SCI 100 - Online Foundations", "Enrolled", [
    ("3001", "091", "LEC", "TBA", "Online", "Casey Morgan", FALL),  # online: no meeting time
]) + course_block("HIST 101 - Introduction to History", "Dropped", [
    ("4001", "001", "LEC", "MW 1:00PM - 2:20PM", "XYZ 200", "Someone", FALL),
]) + "</body></html>"

def test_parse_schedule():
    ms = quest.parse_schedule(HTML)
    assert {m.course for m in ms} == {"CS 135", "MATH 137"}  # online TBA skipped, HIST dropped
    lec = next(m for m in ms if m.course == "CS 135" and m.component == "LEC")
    assert (lec.days, lec.start, lec.end, lec.location) == ([1, 3], time(13, 0), time(14, 20), "ABC 1100")
    assert (lec.start_date, lec.end_date, lec.section) == (date(2026, 9, 9), date(2026, 12, 8), "002")
    assert lec.title == "Designing Functional Programs" and lec.instructor == "Alex Rivera"
    tuts = [m for m in ms if m.course == "CS 135" and m.component == "TUT"]
    assert [(m.days, m.section, m.class_nbr) for m in tuts] == [([4], "201", "1002"), ([2], "201", "1002")]
    calc = next(m for m in ms if m.course == "MATH 137" and m.component == "LEC")
    assert calc.days == [0, 2, 4]


def test_meets_on_respects_days_and_dates():
    ms = quest.parse_schedule(HTML)
    quiz = next(m for m in ms if m.component == "TST")
    assert quiz.meets_on(date(2026, 9, 21)) and not quiz.meets_on(date(2026, 9, 28))  # one-off
    lec = next(m for m in ms if m.course == "CS 135" and m.component == "LEC")
    assert lec.meets_on(date(2026, 9, 24))          # Thursday
    assert not lec.meets_on(date(2026, 9, 25))      # Friday
    assert not lec.meets_on(date(2026, 12, 10))     # after classes end


def test_term_label():
    assert quest.term_label(date(2026, 9, 24)) == "Fall 2026"
    assert quest.term_label(date(2027, 1, 5)) == "Winter 2027"
    assert quest.term_label(date(2027, 6, 1)) == "Spring 2027"


def test_store_roundtrip_replaces_timetable():
    store = Store(":memory:")
    ms = quest.parse_schedule(HTML)
    assert store.replace_classes("Fall 2026", ms) == len(ms)
    back = store.classes()
    assert len(back) == len(ms) and back[0].start <= back[-1].start
    store.replace_classes("Fall 2026", ms[:1])  # re-capture after a drop
    assert len(store.classes()) == 1


class FakeFrame:
    def __init__(self, url, html=""):
        self.url, self.html = url, html
    def content(self): return self.html


class FakePage:
    url = "https://quest.pecs.uwaterloo.ca/psc/x"
    def __init__(self, frames): self.frames = frames


CAL = '<input id="DERIVED_REGFRM1_SSR_SCHED_FORMAT$258$"><label>Weekly Calendar View</label>'
TERM = '<input id="SSR_DUMMY_RECV1$sels$0$$0">'


def test_state_finds_schedule_inside_a_frame():
    inner = FakeFrame("https://quest.pecs.uwaterloo.ca/psc/SS/...SSR_SSENRL_LIST.GBL", CAL + HTML)
    page = FakePage([FakeFrame("https://quest.pecs.uwaterloo.ca/psp/SS/...", "<html></html>"), inner])
    assert quest._state(page) == ("schedule", inner)


def test_state_spots_weekly_calendar_view_and_term_picker():
    assert quest._state(FakePage([FakeFrame("https://quest.pecs.uwaterloo.ca/psc/x", CAL)]))[0] == "calendar_view"
    assert quest._state(FakePage([FakeFrame("https://quest.pecs.uwaterloo.ca/psc/x", TERM)]))[0] == "term"
    assert quest._state(FakePage([FakeFrame("https://quest.pecs.uwaterloo.ca/psp/home", "<p>hi</p>")]))[0] == "quest"
    assert quest._state(FakePage([FakeFrame("https://adfs.uwaterloo.ca/adfs/ls/")]))[0] == "other"


class NavigatingFrame(FakeFrame):
    def content(self): raise RuntimeError("page is navigating")


def test_state_survives_a_frame_that_is_navigating():
    page = FakePage([NavigatingFrame("https://quest.pecs.uwaterloo.ca/psc/x")])
    assert quest._state(page) == ("quest", None)


def test_watcher_reads_schedule_when_it_appears(monkeypatch, tmp_path):
    monkeypatch.setattr(quest, "LAST_HTML", tmp_path / "q.html")
    monkeypatch.setattr(quest._time, "sleep", lambda s: None)
    w = quest.Watcher("Fall 2026")
    login_page = FakePage([FakeFrame("https://adfs.uwaterloo.ca/adfs/ls/")])
    assert w.step(login_page) is None and w.on_quest_since is None  # login pages don't start the clock
    page = FakePage([FakeFrame("https://quest.pecs.uwaterloo.ca/psc/x", HTML)])
    assert w.step(page) == HTML


def test_watcher_gives_up_only_while_on_quest(monkeypatch, tmp_path):
    monkeypatch.setattr(quest, "LAST_HTML", tmp_path / "q.html")
    dumped = []
    monkeypatch.setattr(quest, "_dump", lambda page: dumped.append(page))
    w = quest.Watcher("Fall 2026")
    unreadable = FakePage([FakeFrame("https://quest.pecs.uwaterloo.ca/psc/x", "<p>home</p>")])
    w.last_nudge = float("inf")  # don't try to navigate the fake page
    assert w.step(unreadable) is None
    w.on_quest_since -= quest.UNREADABLE_AFTER_S + 1
    with pytest.raises(quest.Unreadable):
        w.step(unreadable)
    assert dumped == [unreadable]