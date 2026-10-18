"""Academic-relevance filter: keeps coursework, tests and academic admin (drop dates, course
selection, fees) on screen and tucks away the rest — residence events, club socials, optional
drop-in sessions — so they don't bury real deadlines. Nothing is deleted: hidden items stay one
click away ("Show hidden"), and the user's own choice per item (Always show / Hide) always wins.
Hidden items also stay off the Google Calendar."""
from __future__ import annotations

import re

from .models import Item

ACADEMIC_SOURCES = {"d2l", "crowdmark", "odyssey", "quest"}
COURSE_RE = re.compile(r"^[A-Z]{2,6}\s?\d{2,3}[A-Z]?$")
ACADEMIC_CONTEXTS = {"quest", "registrar", "finance", "math", "engineering", "science"}

# Clearly not coursework. Checked against the title and the context (e.g. context "Residence").
NOT_ACADEMIC = re.compile(
    r"\b(residence|roommate|res life|don\b|floor meeting|community meeting|cupcakes?|party|social|mixer|"
    r"bbq|barbecue|pizza|free food|games? night|movie night|karaoke|intramurals?|varsity|athletics|gym|"
    r"fitness|yoga|volunteer|fundraiser|club fair|clubs? day|frosh|orientation event|scavenger hunt|"
    r"giveaway|contest|raffle|trivia)\b", re.I)
# Academic but optional: drop-in / solution-walkthrough sessions that recur every week.
OPTIONAL = re.compile(
    r"\b(optional|drop-?in|practice .*solutions?|solutions? session|session: .*solutions?|"
    r"info(rmation)? session|help session)\b", re.I)
ACADEMIC_WORDS = re.compile(
    r"\b(assignment|quiz|midterm|exam|final|test|lab|lecture|tutorial|reading|problem set|homework|"
    r"project|report|essay|course|class|drop|enrol|enroll|withdraw|tuition|fees?|grade|term|"
    r"registrar|academic|syllabus|office hours|submission|due)\b", re.I)

REASONS = {"not_academic": "Not academic", "optional": "Optional session", "unlinked": "No course link"}


def _user_added(it: Item) -> bool:
    """Typed in by the user on the Reminders page (not pulled from an email scan)."""
    return it.source == "manual" and not it.extra.get("from")


def classify(it: Item) -> tuple[bool, str]:
    """(show?, reason). Reason is '' for shown items, else a key of REASONS — or 'hidden'/'shown'
    when the user set it himself."""
    override = it.relevance
    if override == "show":
        return True, "shown"
    if override == "hide":
        return False, "hidden"
    if _user_added(it):
        return True, ""
    text = f"{it.title} {it.context or ''}"
    if NOT_ACADEMIC.search(text):
        return False, "not_academic"
    if OPTIONAL.search(it.title):
        return False, "optional"
    if it.source in ACADEMIC_SOURCES:
        return True, ""
    ctx = (it.context or "").strip()
    if COURSE_RE.match(ctx) or ctx.lower() in ACADEMIC_CONTEXTS or ACADEMIC_WORDS.search(it.title):
        return True, ""
    return False, "unlinked"


def shown(it: Item) -> bool:
    return classify(it)[0]


def reason_label(it: Item) -> str:
    kind = classify(it)[1]
    return {"hidden": "Hidden by you", "shown": ""}.get(kind, REASONS.get(kind, ""))
