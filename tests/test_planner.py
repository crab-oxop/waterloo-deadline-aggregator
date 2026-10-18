from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from aggregator import planner
from aggregator.planner import Block, Busy, Settings, Task

TZ = ZoneInfo("America/Toronto")


def at(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=TZ)


def task(key, due, minutes, priority=1.0, title=None):
    return Task(key, title or key, due, minutes, priority)


def total(blocks, key):
    return sum(b.minutes for b in blocks if b.key == key)


NOW = at(2026, 10, 5, 9)  # Monday 9:00


def test_splits_long_tasks_into_blocks_within_limits_and_finishes_early():
    r = planner.plan([task("a", at(2026, 10, 8, 21), 300)], [], NOW)
    assert total(r.blocks, "a") == 300 and not r.shortfalls
    assert all(30 <= b.minutes <= 120 for b in r.blocks)
    assert all(b.end <= at(2026, 10, 8, 9) for b in r.blocks)  # ≥12 h before 9 PM Thursday
    assert all(time(9) <= b.start.astimezone(TZ).time() and b.end.astimezone(TZ).time() <= time(22) for b in r.blocks)
    starts = [b.start for b in r.blocks]
    assert starts == sorted(starts) and len(set(starts)) == len(starts)


def test_nothing_fits_reports_how_short():
    busy = [Busy(at(2026, 10, 5, 0), at(2026, 10, 12, 23, 59), "Busy all week")]
    r = planner.plan([task("a", at(2026, 10, 7, 21), 180, title="A03")], busy, NOW)
    assert r.blocks == []
    [s] = r.shortfalls
    assert (s.title, s.needed, s.short) == ("A03", 180, 180)


def test_partial_fit_shortfall_is_the_remainder():
    busy = [Busy(at(2026, 10, 5, 10), at(2026, 10, 5, 22), "Out")]  # only 9:00–9:45 free today (15-min buffer)
    r = planner.plan([task("a", at(2026, 10, 5, 23), 120)], busy, NOW)
    assert total(r.blocks, "a") == 45
    assert r.shortfalls[0].short == 75


def test_class_overlapping_an_outlook_event_blocks_the_union_plus_buffers():
    busy = [Busy(at(2026, 10, 5, 10, 30), at(2026, 10, 5, 11, 20), "CS 135 LEC"),
            Busy(at(2026, 10, 5, 11), at(2026, 10, 5, 12), "Office hours (Outlook)")]
    s = Settings(days=1)
    r = planner.plan([task("a", at(2026, 10, 5, 23, 59), 240)], busy, NOW, s)
    for b in r.blocks:
        assert not (b.start < at(2026, 10, 5, 12, 15) and b.end > at(2026, 10, 5, 10, 15)), b
    assert r.blocks[0].start == at(2026, 10, 5, 9) and r.blocks[0].end == at(2026, 10, 5, 10, 15)


def test_item_due_in_two_hours_uses_the_time_it_has():
    now = at(2026, 10, 5, 14, 3)
    r = planner.plan([task("q", now + timedelta(hours=2), 60)], [], now)
    [b] = r.blocks
    assert b.start == at(2026, 10, 5, 14, 5) and b.minutes == 60 and b.end <= now + timedelta(hours=2)
    assert not r.shortfalls


def test_overdue_items_are_skipped():
    r = planner.plan([task("late", NOW - timedelta(hours=1), 60)], [], NOW)
    assert r.blocks == [] and r.shortfalls == []


def test_priority_first_then_earliest_deadline():
    s = Settings(days=1, wake=time(8, 30), sleep=time(11, 30))  # 2 h of room today
    hi = task("hi", at(2026, 10, 5, 23, 30), 120, priority=5)
    lo = task("lo", at(2026, 10, 5, 23), 60, priority=0.5)
    r = planner.plan([lo, hi], [], NOW, s)
    assert total(r.blocks, "hi") == 120 and total(r.blocks, "lo") == 0
    assert [x.key for x in r.shortfalls] == ["lo"]  # due today, didn't fit
    tie_a, tie_b = task("b", at(2026, 10, 7, 21), 60), task("a", at(2026, 10, 6, 21), 60)
    r = planner.plan([tie_a, tie_b], [], NOW, Settings(days=1))
    assert r.blocks[0].key == "a"  # same priority: earlier deadline goes first


def test_work_due_after_the_horizon_is_not_a_shortfall():
    s = Settings(days=2, sleep=time(10, 30))
    r = planner.plan([task("big", at(2026, 10, 7, 21), 600)], [], NOW, s)  # due past a 2-day plan
    assert total(r.blocks, "big") == 120 and r.shortfalls == []  # the visible hours are used, the rest waits


def test_dst_end_on_nov_1_uses_the_right_offsets():
    now = at(2026, 10, 31, 21)  # Sat 9 PM EDT (UTC-4); clocks fall back at 2 AM Sunday
    r = planner.plan([task("a", at(2026, 11, 2, 23), 240)], [], now, Settings(days=3))
    sunday = [b for b in r.blocks if b.start.astimezone(TZ).date() == at(2026, 11, 1, 0).date()]
    assert sunday and sunday[0].start.astimezone(timezone.utc).hour == 14  # 9:00 EST = 14:00 UTC
    assert r.blocks[0].start.astimezone(timezone.utc) == datetime(2026, 11, 1, 1, 0, tzinfo=timezone.utc)  # Sat 9 PM EDT
    windows = planner.working_windows(now, Settings(days=3))
    assert windows[1][1] - windows[1][0] == timedelta(hours=13)  # Sunday still 09:00–22:00 local


def test_replanning_keeps_accepted_blocks_and_flags_conflicts():
    t = task("a", at(2026, 10, 8, 21), 180)
    first = planner.plan([t], [], NOW)
    accepted = [Block(b.key, b.start, b.end, "accepted") for b in first.blocks[:1]]
    again = planner.plan([t], [], NOW, accepted=accepted)
    assert total(again.blocks, "a") == 180 - accepted[0].minutes  # accepted time counts toward the task
    assert all(not (b.start < accepted[0].end and accepted[0].start < b.end) for b in again.blocks)
    clash = Busy(accepted[0].start, accepted[0].start + timedelta(minutes=30), "New Outlook event")
    third = planner.plan([t], [clash], NOW, accepted=accepted)
    assert [c.with_ for c in third.conflicts] == ["New Outlook event"]  # flagged, not moved or dropped
    assert total(third.blocks, "a") == 180 - accepted[0].minutes


def test_dismissed_slots_are_not_suggested_again():
    t = task("a", at(2026, 10, 8, 21), 60)
    first = planner.plan([t], [], NOW).blocks[0]
    again = planner.plan([t], [], NOW, dismissed=[Block("a", first.start, first.end, "dismissed")])
    assert again.blocks[0].start >= first.end


def test_block_ids_are_stable_and_calendar_safe():
    b = Block("0123abcd", at(2026, 10, 5, 9), at(2026, 10, 5, 10))
    assert b.id == Block("0123abcd", at(2026, 10, 5, 9), at(2026, 10, 5, 10)).id
    assert len(b.id) == 32 and set(b.id) <= set("0123456789abcdef")


def test_far_future_work_waits_for_its_lead_window():
    quiz = task("dec", at(2026, 12, 7, 17, 30), 30)  # 30-min quiz prep in December
    r = planner.plan([quiz], [], NOW)
    assert r.blocks == [] and r.shortfalls == []  # nothing crammed into this week
    soon = task("a", at(2026, 10, 9, 21), 120)  # 2 h → ~3 days of lead
    r = planner.plan([soon], [], NOW)
    assert r.blocks and all(b.start >= at(2026, 10, 9, 21) - timedelta(days=3) for b in r.blocks)


def test_tight_lead_window_falls_back_to_earlier_free_time():
    due = at(2026, 10, 8, 21)
    busy = [Busy(due - timedelta(days=3), due, "Away")]  # the whole lead window is booked
    r = planner.plan([task("a", due, 120)], busy, NOW)
    assert total(r.blocks, "a") == 120 and not r.shortfalls
    assert all(b.end <= due - timedelta(days=3) for b in r.blocks)


def test_never_a_block_shorter_than_the_minimum():
    # Fragmented days (the real-data case: 75 + 40 min fit, leaving a 5-minute sliver).
    for gap in range(30, 130, 5):
        for minutes in (20, 45, 115, 120, 200):
            busy = [Busy(at(2026, 10, 5, 9) + timedelta(minutes=gap + 30 * k + 15 * k * k),
                         at(2026, 10, 5, 9) + timedelta(minutes=gap + 30 * k + 15 * k * k + 20), "x")
                    for k in range(8)]
            r = planner.plan([task("a", at(2026, 10, 7, 21), minutes)], busy, NOW)
            assert all(b.minutes >= min(30, minutes) for b in r.blocks), (gap, minutes, r.blocks)


def test_bedtime_after_midnight_extends_the_evening():
    s = Settings(days=2, wake=time(10), sleep=time(1, 30))  # up 10:00, bed 1:30 AM
    windows = planner.working_windows(at(2026, 10, 5, 9), s)
    assert windows[0] == (at(2026, 10, 5, 10, 30), at(2026, 10, 6, 1, 0))
    late = planner.working_windows(at(2026, 10, 6, 0, 20), s)  # 12:20 AM: still "Monday night"
    assert late[0] == (at(2026, 10, 6, 0, 20), at(2026, 10, 6, 1, 0))


def test_nothing_is_suggested_while_asleep():
    s = Settings(wake=time(7), sleep=time(23))
    r = planner.plan([task("a", at(2026, 10, 9, 21), 600)], [], NOW, s)
    for b in r.blocks:
        t0, t1 = b.start.astimezone(TZ).time(), b.end.astimezone(TZ).time()
        assert time(7, 30) <= t0 and t1 <= time(22, 30), b


def test_edited_block_keeps_its_id():
    b = Block("k", at(2026, 10, 5, 9), at(2026, 10, 5, 10), "accepted", bid="abc123")
    assert b.id == "abc123"


def test_rest_of_an_edited_task_is_spaced_away_from_the_edit():
    t = task("a", at(2026, 10, 8, 21), 180)
    mine = Block("a", at(2026, 10, 5, 9), at(2026, 10, 5, 10), "accepted", edited=True)
    r = planner.plan([t], [], NOW, accepted=[mine])
    assert total(r.blocks, "a") == 120
    assert all(b.start >= at(2026, 10, 5, 11) or b.end <= at(2026, 10, 5, 8) for b in r.blocks)
    other = task("b", at(2026, 10, 5, 23), 60)  # other tasks may still use that hour (after a short break)
    r = planner.plan([other], [], NOW, accepted=[mine])
    assert r.blocks[0].start == at(2026, 10, 5, 10, 15)
