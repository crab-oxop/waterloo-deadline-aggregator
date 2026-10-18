"""Decides which emails become dashboard items. Deliberately pluggable: the current rule is a
placeholder until a smarter scanner exists. To swap it, implement `MailFilter` and point
`ACTIVE_FILTER` at the new one — connectors only ever call `ACTIVE_FILTER.match(msg)`."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# Courses to watch for in email. Example: the standard 1A Waterloo CS courses. Update each term.
COURSES = ("CS 135", "MATH 135", "MATH 137", "PHYS 121", "ENGL 119")


@dataclass(frozen=True)
class Mail:
    """Source-agnostic view of an email, so the same filter works for Outlook and Gmail."""
    subject: str
    preview: str
    sender: str
    received: datetime


class MailFilter(Protocol):
    def match(self, msg: Mail) -> str | None:
        """Return the context (e.g. course code) the email belongs to, or None to ignore it."""
        ...


class CourseMentionFilter:
    """Keep emails whose subject or preview names one of the user's courses (CS135, cs 135, CS-135...)."""

    def __init__(self, courses: tuple[str, ...] = COURSES):
        self.patterns = []
        for course in courses:
            subject, number = course.split()
            self.patterns.append(
                (course, re.compile(rf"\b{re.escape(subject)}\s?-?\s?{re.escape(number)}\b", re.I)))

    def match(self, msg: Mail) -> str | None:
        # Subject first: a course named in the subject is a stronger signal than one in the body.
        # Within a text, the earliest mention wins.
        for text in (msg.subject, msg.preview):
            hits = [(m.start(), course) for course, rx in self.patterns if (m := rx.search(text))]
            if hits:
                return min(hits)[1]
        return None


ACTIVE_FILTER: MailFilter = CourseMentionFilter()
