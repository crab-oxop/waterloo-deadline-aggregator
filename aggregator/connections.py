"""Connection state for the in-app Connections page: checks run in the background (the
Crowdmark probe takes a few seconds), and the Waterloo sign-in runs in its own thread so the
app stays responsive while the user signs in + approves Duo in the browser window it opens."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import login
from .secrets import redact_url, set_secret
from .store import Store

log = logging.getLogger(__name__)


def looks_like_link(url: str) -> bool:
    parts = urlsplit(url.strip())
    return parts.scheme.lower() in ("http", "https", "webcal") and bool(parts.netloc)


class ConnectionManager:
    def __init__(self, store: Store, lock=None, after_signin=None):
        self.store = store
        self.lock = lock or threading.RLock()
        self.after_signin = after_signin  # e.g. kick off a sync so new Crowdmark items appear
        self.checks: list[login.Check] | None = None
        self.checked_at: datetime | None = None
        self.checking = False
        self.signin_running = False
        self.signin_results: dict[str, str] = {}
        self.phase = ""  # signin | quest — drives the progress steps on the page
        self.google_running = False
        self.google_result = ""
        self._cancel = threading.Event()
        self._guard = threading.Lock()

    # --- checks -------------------------------------------------------------------
    def _claim_check(self) -> bool:
        with self._guard:
            # While the sign-in window is open it holds the browser profile; a check then would
            # fail for the wrong reason. The sign-in re-checks when it finishes.
            if self.checking or self.signin_running:
                return False
            self.checking = True
            return True

    def _run_check(self) -> None:
        try:
            with self.lock:
                checks = login.check_all(self.store)
            self.checks, self.checked_at = checks, datetime.now(timezone.utc)
            log.info("connections: %s", {c.key: c.state for c in checks})
        except Exception as e:  # never let a check crash the app
            log.warning("connection check failed: %s", e)
        finally:
            self.checking = False

    def refresh(self) -> None:
        """Re-check every connection (blocking)."""
        if self._claim_check():
            self._run_check()

    def refresh_async(self) -> bool:
        """Start a check; `checking` is already True when this returns, so the page shows it at once."""
        if not self._claim_check():
            return False
        threading.Thread(target=self._run_check, daemon=True).start()
        return True

    def state_token(self) -> str:
        """Changes whenever anything the Connections page shows changes — the page polls this."""
        checked = self.checked_at.isoformat() if self.checked_at else "-"
        return (f"{checked}|{self.checking}|{self.signin_running}|{self.phase}|{sorted(self.signin_results.items())}"
                f"|{self.google_running}|{self.google_result}")

    def needs_attention(self) -> bool:
        prefs = login._prefs()
        return any(c.action and not (c.key == "outlook" and prefs.get("outlook_skipped"))
                   for c in (self.checks or []))

    # --- Waterloo sign-in (Crowdmark + Quest, one Duo) ----------------------------
    def start_waterloo_signin(self) -> bool:
        with self._guard:
            if self.signin_running:
                return False
            self.signin_running = True
            self.signin_results = {}
            self.phase = "signin"
            self._cancel.clear()
        need = {c.key for c in (self.checks or []) if c.action == "waterloo"} or {"crowdmark", "quest", "odyssey"}

        def run():
            try:
                self.signin_results = login.waterloo_sign_in(
                    self.store, "crowdmark" in need, "quest" in need, lock=self.lock,
                    cancelled=self._cancel.is_set, progress=lambda p: setattr(self, "phase", p),
                    need_odyssey=True, need_web=True)  # free ride: refresh exam times + seats on every sign-in
            except Exception as e:
                self.signin_results = {"sign-in": f"didn't finish ({type(e).__name__}: {e})"}
            finally:
                self.signin_running = False
                self.refresh()
                if self.after_signin:
                    try:
                        self.after_signin()
                    except Exception as e:
                        log.warning("post-sign-in sync failed: %s", e)

        threading.Thread(target=run, daemon=True).start()
        return True

    def start_google(self, after=None) -> bool:
        """Re-run Google's sign-in (in the user's browser) when the Calendar token expired."""
        with self._guard:
            if self.google_running:
                return False
            self.google_running = True
            self.google_result = ""

        def run():
            from . import gcal
            try:
                gcal.reauthorize()
                self.google_result = "Google Calendar reconnected."
                if after:
                    after()  # push everything that was waiting
            except Exception as e:
                self.google_result = f"Google sign-in didn't finish ({type(e).__name__}: {e})"
                log.warning("google reconnect failed: %s", e)
            finally:
                self.google_running = False
                self.refresh()

        threading.Thread(target=run, daemon=True).start()
        return True

    def cancel_signin(self) -> None:
        """Stop waiting; the sign-in window closes within a second."""
        self._cancel.set()

    # --- pasted links -------------------------------------------------------------
    def save_link(self, kind: str, url: str) -> str:
        """Validate and store a LEARN / Outlook calendar link. Returns a message for the page."""
        from .sources import d2l_ics, ics, outlook

        url = url.strip()
        if not looks_like_link(url):
            raise ValueError("that doesn't look like a link — copy the whole https://… address")
        if kind == "d2l":
            n = len(d2l_ics.parse_feed(d2l_ics.fetch_feed(url)))
            set_secret(d2l_ics.SECRET_NAME, url)
            msg = f"LEARN calendar saved ({redact_url(url)}) — {n} events."
        elif kind == "outlook":
            n = len(ics.parse_feed(ics.fetch_feed(url), outlook.SOURCE))
            set_secret(outlook.ICS_SECRET, url)
            prefs = login._prefs()
            prefs.pop("outlook_skipped", None)
            login._save_prefs(prefs)
            msg = f"Outlook calendar saved ({redact_url(url)}) — {n} events in the next 6 months."
        else:
            raise ValueError("unknown link type")
        self.refresh_async()
        return msg

    def skip(self, kind: str) -> None:
        if kind == "outlook":
            prefs = login._prefs()
            prefs["outlook_skipped"] = True
            login._save_prefs(prefs)
