from datetime import date, datetime, timezone

from aggregator import calendar_view
from aggregator.models import Item

T = calendar_view.LOCAL_TZ


def test_parse_month_and_shift():
    today = date(2026, 9, 24)
    assert calendar_view.parse_month("2026-10", today) == (2026, 10)
    for bad in (None, "", "2026-13", "26-10", "2026-1", "<script>", "1999-01"):
        assert calendar_view.parse_month(bad, today) == (2026, 9)
    assert calendar_view.shift(2026, 12, 1) == (2027, 1)
    assert calendar_view.shift(2026, 1, -1) == (2025, 12)


def test_weeks_start_sunday_and_cover_month():
    weeks = calendar_view.weeks(2026, 9)
    assert weeks[0][0] == date(2026, 8, 30) and weeks[0][0].weekday() == 6  # Sunday
    days = [d for w in weeks for d in w]
    assert date(2026, 9, 1) in days and date(2026, 9, 30) in days
    assert all(len(w) == 7 for w in weeks)


def test_by_day_uses_toronto_date():
    # 03:59 UTC on Sep 26 is still 11:59 PM Sep 25 in Toronto — must land on the 25th.
    late = Item(source="d2l", source_key="a", type="assignment", title="ENGL A1",
                due_date=datetime(2026, 9, 26, 3, 59, tzinfo=timezone.utc))
    early = Item(source="d2l", source_key="b", type="meeting", title="Contest",
                 due_date=datetime(2026, 9, 25, 18, 0, tzinfo=T))
    undated = Item(source="manual", source_key="c", type="reminder", title="Habit")
    days = calendar_view.by_day([late, early, undated])
    assert [i.title for i in days[date(2026, 9, 25)]] == ["Contest", "ENGL A1"]
    assert sum(len(v) for v in days.values()) == 2  # undated items aren't on the calendar
