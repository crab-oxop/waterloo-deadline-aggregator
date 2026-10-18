"""Shared data model. Every source normalizes into `Item` (SPEC.md § Data Model)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime

SOURCES = {"d2l", "quest", "crowdmark", "odyssey", "outlook", "gmail", "slack", "discord", "teams", "web", "manual"}
TYPES = {"assignment", "quiz", "exam", "meeting", "message", "announcement", "manual", "reminder"}
# Types that aren't units of work: no "how long did it take?" prompt, no time estimate.
NON_WORK_TYPES = {"meeting", "message", "announcement", "reminder"}
STATUSES = {"not_started", "in_progress", "done"}


def make_item_id(source: str, source_key: str) -> str:
    """Stable identity from (source, source's own ID/URL) — never title or due date,
    since those change on rescrape. Also used as the Google Calendar event key."""
    if source not in SOURCES:
        raise ValueError(f"unknown source: {source}")
    if not source_key:
        raise ValueError("source_key must be non-empty")
    return hashlib.sha256(f"{source}\x1f{source_key}".encode()).hexdigest()[:32]


@dataclass
class Item:
    source: str
    source_key: str  # the source's own internal ID or URL
    type: str
    title: str
    context: str | None = None  # course code (CS 135) or group (a club or design team)
    due_date: datetime | None = None  # timezone-aware
    source_link: str | None = None
    # Fields below are user-owned: a rescrape never overwrites them.
    grade_weight: float | None = None  # % of final grade
    importance: float | None = None
    status: str = "not_started"
    estimated_duration: int | None = None  # minutes
    actual_duration: int | None = None  # minutes
    relevance: str | None = None  # 'show' / 'hide' — the user's override of the academic filter
    # Set by the store.
    priority_score: float = 0.0
    last_synced: datetime | None = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in TYPES:
            raise ValueError(f"unknown type: {self.type}")
        if self.status not in STATUSES:
            raise ValueError(f"unknown status: {self.status}")
        if self.due_date is not None and self.due_date.tzinfo is None:
            raise ValueError("due_date must be timezone-aware")

    @property
    def id(self) -> str:
        return make_item_id(self.source, self.source_key)
