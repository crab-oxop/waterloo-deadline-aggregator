"""Crowdmark connector. Crowdmark has no public API or calendar feed, so (per SPEC) we use
browser automation — but only to hold the login: the shared Waterloo browser profile signs in
via "Sign in with LEARN" (Waterloo SSO + Duo, done by the user), and each sync calls the same
read-only JSON endpoints the Crowdmark student page itself uses. Nothing is ever submitted.

Crowdmark's session cookie lasts ~8 hours, so after a night away a fresh sign-in is needed;
the startup login check (login.py) handles that."""
from __future__ import annotations

from datetime import datetime

from ..models import Item
from . import uw_browser
from .ics import FeedError, find_course, guess_type

SOURCE = "crowdmark"
BASE = "https://app.crowdmark.com"
SIGN_IN = f"{BASE}/sign-in/waterloo"
CONFIGURED_MARKER = uw_browser.PROFILE_DIR / ".crowdmark"
API_HEADERS = {"Accept": "application/vnd.api+json"}


def is_configured() -> bool:
    return CONFIGURED_MARKER.exists()


def start_sign_in(page) -> None:
    """Open Crowdmark's Waterloo sign-in and click 'Sign in with LEARN' (doesn't wait for the result)."""
    page.goto(SIGN_IN, wait_until="domcontentloaded")
    page.get_by_role("button", name="Sign in with LEARN").or_(
        page.get_by_role("link", name="Sign in with LEARN")).first.click(timeout=15000)


def sign_in(page, timeout_ms: int, cancelled=lambda: False) -> None:
    """Drive the page to Crowdmark via 'Sign in with LEARN'; waits until the student area loads.
    With a live Waterloo session this is instant; otherwise the user completes SSO + Duo in the window."""
    import time

    start_sign_in(page)
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if cancelled():
            raise TimeoutError("cancelled")
        if page.url.startswith(f"{BASE}/student"):
            return
        page.wait_for_timeout(500)  # not time.sleep: sync Playwright only updates page.url during its own calls
    raise TimeoutError("Crowdmark sign-in wasn't finished in time")


def _get(ctx, path: str) -> dict | None:
    resp = ctx.request.get(f"{BASE}{path}", headers=API_HEADERS)
    if resp.status in (401, 403):
        return None
    if not resp.ok:
        raise FeedError(f"Crowdmark {path.split('?')[0]} returned {resp.status}")
    return resp.json()


def fetch(ctx) -> tuple[dict, dict]:
    assignments = _get(ctx, "/api/v2/student/assignments?include[]=exam-master")
    courses = _get(ctx, "/api/v2/student/courses")
    if assignments is None or courses is None:
        raise PermissionError("not signed in")
    return assignments, courses


def mark_connected(ctx) -> int:
    """Confirm the session works and remember that Crowdmark is set up. Returns assessment count."""
    assignments, _ = fetch(ctx)
    CONFIGURED_MARKER.parent.mkdir(parents=True, exist_ok=True)
    CONFIGURED_MARKER.write_text("signed in via LEARN\n")
    return len(assignments.get("data", []))


def session_ok() -> bool:
    """Quick, invisible check: is the saved Crowdmark session still accepted?"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = uw_browser.launch(p, headless=True)
        try:
            fetch(ctx)
            return True
        except (PermissionError, FeedError):
            return False
        finally:
            ctx.close()


def collect() -> list[Item]:
    if not is_configured():
        raise FeedError("Crowdmark not connected — restart the dashboard to sign in, "
                        "or run: python -m aggregator login")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        ctx = uw_browser.launch(p, headless=True)
        try:
            try:
                assignments, courses = fetch(ctx)
            except PermissionError:
                # No silent re-login: Waterloo's SSO cookies don't outlive the sign-in window, so
                # it could only ever time out (it used to stall every sync for 30 s).
                raise FeedError("Crowdmark signs you out after ~8 hours; reconnecting needs Duo. "
                                "Your Crowdmark items are kept meanwhile. "
                                "Sign in again on the Connections page.") from None
        finally:
            ctx.close()
    return parse(assignments, courses)


def parse(assignments: dict, courses: dict) -> list[Item]:
    course_names = {c["id"]: c["attributes"].get("name", "") for c in courses.get("data", [])}
    masters = {m["id"]: m for m in assignments.get("included", []) if m.get("type") == "exam-masters"}
    items = []
    for a in assignments.get("data", []):
        attrs = a.get("attributes", {})
        master_ref = ((a.get("relationships") or {}).get("exam-master") or {}).get("data") or {}
        master = masters.get(master_ref.get("id"), {})
        m_attrs = master.get("attributes", {})
        course_id = (((master.get("relationships") or {}).get("course") or {}).get("data") or {}).get("id")
        title = (m_attrs.get("title") or "Crowdmark assessment").strip()
        due_raw = attrs.get("due") or m_attrs.get("due")
        extra = {"points": m_attrs.get("total-points"),
                 "late_penalty": f"{attrs.get('penalty-value')} per {attrs.get('penalty-period')}"
                 if attrs.get("penalty-value") else None}
        if attrs.get("submitted-at"):
            extra["completed_at"] = attrs["submitted-at"]  # store marks it done
        if attrs.get("marks-sent-at"):
            extra["graded_at"] = attrs["marks-sent-at"]
        items.append(Item(
            source=SOURCE, source_key=a["id"], type=guess_type(title, default="assignment"), title=title,
            context=find_course(course_names.get(course_id, "")) or course_names.get(course_id) or None,
            due_date=datetime.fromisoformat(due_raw.replace("Z", "+00:00")) if due_raw else None,
            source_link=f"{BASE}/student/assessments/{master['id']}" if master.get("id") else f"{BASE}/student",
            extra={k: v for k, v in extra.items() if v},
        ))
    return items
