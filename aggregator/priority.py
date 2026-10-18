"""Rule-based priority: grade_weight × importance × urgency(due_date) (SPEC.md § Prioritization Engine)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_GRADE_WEIGHT = 1.0  # % — small but nonzero so ungraded items aren't invisible
DEFAULT_IMPORTANCE = 1.0
NO_DUE_DATE_URGENCY = 0.05  # roughly "due in 20 days"
MIN_HOURS = 1.0  # clamps urgency so overdue/imminent items don't blow up to infinity
# LEARN feeds carry years of stale events from reused course shells; past this they're history.
PAST_GRACE = timedelta(days=7)


def is_past(due: datetime | None, now: datetime | None = None) -> bool:
    return due is not None and due < (now or datetime.now(timezone.utc)) - PAST_GRACE


def urgency(due: datetime | None, now: datetime | None = None) -> float:
    """Inverse time remaining, in 1/days. Recently overdue items get max urgency;
    items overdue by more than PAST_GRACE get none."""
    if due is None:
        return NO_DUE_DATE_URGENCY
    now = now or datetime.now(timezone.utc)
    if is_past(due, now):
        return 0.0
    hours_left = max((due - now).total_seconds() / 3600, MIN_HOURS)
    return 24.0 / hours_left


HIGH_AT = 1.0   # e.g. a 10% assignment due in ~2 days
MEDIUM_AT = 0.1  # e.g. a default-weight item due within ~10 days


def level(score_value: float, status: str) -> str:
    """Human label for a score — raw numbers mean nothing at a glance."""
    if status == "done":
        return "done"
    return "high" if score_value >= HIGH_AT else "med" if score_value >= MEDIUM_AT else "low"


def score(grade_weight: float | None, importance: float | None, due: datetime | None,
          status: str, now: datetime | None = None, kind: str | None = None) -> float:
    if status == "done":
        return 0.0
    if kind == "meeting" and due is not None and due < (now or datetime.now(timezone.utc)):
        return 0.0  # a meeting that already happened is over, not overdue
    gw = grade_weight if grade_weight is not None else DEFAULT_GRADE_WEIGHT
    imp = importance if importance is not None else DEFAULT_IMPORTANCE
    return round(gw * imp * urgency(due, now), 4)
