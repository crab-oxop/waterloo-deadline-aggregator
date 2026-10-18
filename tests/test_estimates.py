from aggregator import estimates
from aggregator.models import Item


def done(key, context="CS 135", kind="assignment", est=None, actual=None):
    return Item(source="d2l", source_key=key, type=kind, title=key, context=context, status="done",
                estimated_duration=est, actual_duration=actual)


def test_defaults_and_formatting():
    cal = estimates.calibrate([])
    assert estimates.estimate_minutes(done("a"), cal) == 120
    assert estimates.estimate_minutes(done("q", kind="quiz"), cal) == 30
    assert estimates.estimate_minutes(done("x", est=45), cal) == 45
    assert [estimates.fmt_minutes(m) for m in (None, 30, 60, 90)] == ["", "30m", "1h", "1h 30m"]


def test_needs_three_samples_before_calibrating():
    two = [done(f"d{i}", est=60, actual=120) for i in range(2)]
    assert estimates.calibrate(two).ratio_for(done("new")) == (1.0, None)


def test_course_ratio_beats_type_beats_overall():
    history = (
        [done(f"cs{i}", est=60, actual=90) for i in range(3)]                        # CS 135: 1.5×
        + [done(f"m{i}", context="MATH 137", est=60, actual=30) for i in range(2)]   # too few for MATH
        + [done(f"q{i}", context=None, kind="quiz", est=30, actual=60) for i in range(3)]  # quizzes: 2×
        + [done("skip", est=60)]                                                     # no actual → ignored
    )
    cal = estimates.calibrate(history)
    assert cal.ratio_for(done("n1")) == (1.5, "CS 135")
    assert estimates.estimate_minutes(done("n1"), cal) == 180  # 120 default × 1.5
    assert cal.ratio_for(done("n2", context="MATH 137", kind="quiz")) == (2.0, "quiz items")
    ratio, basis = cal.ratio_for(done("n3", context="MATH 137"))
    assert basis == "assignment items" and round(ratio, 3) == 1.1  # (3×90 + 2×30) / (5×60)
    ratio, basis = cal.ratio_for(done("n4", context="MATH 137", kind="exam"))
    assert basis == "all items" and 1 < ratio < 2  # no exam history → everything pooled


def test_ratio_is_clamped():
    cal = estimates.calibrate([done(f"w{i}", est=10, actual=10_000) for i in range(3)])
    assert cal.ratio_for(done("n"))[0] == estimates.RATIO_BOUNDS[1]
