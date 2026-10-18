"""D2L/LEARN connector via the Calendar feed (LEARN → Calendar → Settings → Enable
Calendar Feeds → Subscribe → All Courses). Read-only; no scraping or SSO needed."""
from __future__ import annotations

import re
from datetime import datetime

from ..models import Item
from ..secrets import get_secret
from .ics import FeedError, fetch_feed  # noqa: F401  (re-exported for callers/tests)
from .ics import parse_feed as _parse_ics

SOURCE = "d2l"
SECRET_NAME = "d2l_feed_url"
LEARN_LINK_RE = re.compile(r"https://learn\.uwaterloo\.ca/\S+")


def parse_feed(data: bytes, now: datetime | None = None) -> list[Item]:
    return _parse_ics(data, SOURCE, LEARN_LINK_RE, now)


def is_configured() -> bool:
    return bool(get_secret(SECRET_NAME))


def collect(url_or_path: str | None = None) -> list[Item]:
    src = url_or_path or get_secret(SECRET_NAME)
    if not src:
        raise FeedError("no D2L feed configured — run: python -m aggregator setup-d2l")
    return parse_feed(fetch_feed(src))
