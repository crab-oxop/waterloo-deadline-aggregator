"""Runs every configured connector into the store, then mirrors the result to Google
Calendar. Failures are recorded (and flagged on the dashboard) instead of raised.

Speed: sources are fetched in parallel and *without* the store lock — only the quick database
writes take it — so the dashboard stays responsive while a slow source (Crowdmark launches a
browser) is still working. A source that just failed (e.g. Crowdmark after its ~8 h session
ends) isn't retried on every cycle; a fresh sign-in triggers a sync that retries it at once."""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone

from . import gcal
from .sources import crowdmark, d2l_ics, outlook
from .store import Store

log = logging.getLogger(__name__)

# source name → module exposing is_configured() and collect(). Unconfigured sources are
# skipped silently — only a configured source that breaks gets flagged.
CONNECTORS = {d2l_ics.SOURCE: d2l_ics, outlook.SOURCE: outlook, crowdmark.SOURCE: crowdmark}
# After a failure, wait this long before trying a source again (unless forced by a sign-in).
RETRY_AFTER = {crowdmark.SOURCE: timedelta(hours=1)}


def _recently_failed(store: Store, source: str, now: datetime) -> bool:
    wait = RETRY_AFTER.get(source)
    if not wait:
        return False
    h = next((h for h in store.source_health() if h["source"] == source), None)
    if not h or not h["last_error"] or not h["last_attempt"]:
        return False
    return now - datetime.fromisoformat(h["last_attempt"]) < wait


def _record(store: Store, name: str, e: Exception, results: dict) -> None:
    msg = f"{type(e).__name__}: {e}"
    store.record_failure(name, msg)
    results[name] = msg
    log.warning("sync of %s failed: %s", name, msg)


def sync_all(store: Store, calendar: bool = True, lock=None, force: bool = False) -> dict[str, dict | str]:
    lock = lock or nullcontext()
    now = datetime.now(timezone.utc)
    results: dict[str, dict | str] = {}
    with lock:
        todo = {s: c for s, c in CONNECTORS.items()
                if c.is_configured() and (force or not _recently_failed(store, s, now))}

    def fetch(connector):  # network only — no store access, so no lock
        try:
            return connector.collect(), None
        except Exception as e:  # one broken source must not take down the others
            return None, e

    with ThreadPoolExecutor(max_workers=max(1, len(todo))) as pool:
        fetched = dict(zip(todo, pool.map(fetch, todo.values())))
    with lock:
        for source, (items, err) in fetched.items():
            if err is not None:
                _record(store, source, err, results)
                continue
            try:
                results[source] = store.sync_source(source, items)
            except Exception as e:
                _record(store, source, e, results)
    if calendar and gcal.is_configured():  # network calls happen outside the lock
        try:
            r = gcal.sync_calendar(store, lock)
            with lock:
                results["gcal"] = store.mark_ok("gcal", r)
        except Exception as e:
            with lock:
                _record(store, "gcal", e, results)
    log.info("sync: %s", results)
    return results


class Syncer:
    """One sync at a time; extra requests while one runs are coalesced into a single follow-up."""

    def __init__(self, store: Store, lock=None, calendar: bool = True):
        self.store, self.lock, self.calendar = store, lock, calendar
        self._guard = threading.Lock()
        self.running = False
        self._again = False
        self._force = False

    def run(self, force: bool = False) -> None:
        with self._guard:
            if self.running:
                self._again, self._force = True, self._force or force
                return
            self.running = True
        try:
            while True:
                sync_all(self.store, self.calendar, self.lock, force)
                with self._guard:
                    if not self._again:
                        self.running = False
                        return
                    force, self._again, self._force = self._force, False, False
        except BaseException:
            with self._guard:
                self.running = False
            raise

    def run_async(self, force: bool = False) -> None:
        threading.Thread(target=self.run, args=(force,), daemon=True).start()
