"""Startup connection check: see what's signed in, and ask to sign in to whatever needs it.

Crowdmark and Quest both sit behind Waterloo sign-in, so they share one visible browser
session — one Waterloo login + Duo covers both. Optional sources the user declines (e.g. the
Outlook calendar link) are remembered so startup doesn't nag every time."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import date
from typing import Callable

from . import gcal
from .paths import DATA_DIR
from .secrets import get_secret, redact_url, set_secret
from .store import EmptySync, Store

PREFS = DATA_DIR / "login_prefs.json"
WATERLOO_TIMEOUT_MS = 10 * 60 * 1000  # time for the user to finish SSO + Duo
QUEST_AFTER_SIGNIN_S = 5 * 60


@dataclass
class Check:
    key: str
    name: str
    state: str          # ok | needs_login | missing | optional | error
    detail: str
    action: str = ""    # waterloo | paste_d2l | paste_outlook | "" (nothing startup can do)


def _prefs() -> dict:
    try:
        return json.loads(PREFS.read_text())
    except (OSError, ValueError):
        return {}


def _save_prefs(p: dict) -> None:
    PREFS.parent.mkdir(parents=True, exist_ok=True)
    PREFS.write_text(json.dumps(p, indent=2))


def _gcal_check(store: Store) -> Check:
    if not gcal.is_configured():
        return Check("gcal", "Google Calendar", "optional",
                     "not connected — run: python -m aggregator setup-gcal <credentials.json>")
    last = next((h for h in store.source_health() if h["source"] == "gcal"), {})
    err = last.get("last_error") or ""
    if err and (last.get("last_success") or "") < (last.get("last_attempt") or ""):
        if "invalid_grant" in err or "expired" in err.lower() or "revoked" in err.lower():
            return Check("gcal", "Google Calendar", "needs_login",
                         "Google sign-in expired — nothing has reached your calendar since", "google")
        return Check("gcal", "Google Calendar", "error", f"last push failed: {err[:120]}", "google")
    return Check("gcal", "Google Calendar", "ok", "connected")


def check_all(store: Store, today: date | None = None, crowdmark_probe: Callable[[], bool] | None = None) -> list[Check]:
    from .sources import course_pages, crowdmark, odyssey, quest
    from . import graph

    today = today or date.today()
    out = [
        Check("d2l", "LEARN calendar", "ok", "connected") if get_secret("d2l_feed_url")
        else Check("d2l", "LEARN calendar", "missing", "feed link not saved", "paste_d2l"),
        _gcal_check(store),
    ]
    if not crowdmark.is_configured():
        out.append(Check("crowdmark", "Crowdmark", "needs_login", "not connected yet", "waterloo"))
    else:
        try:
            last = next((h for h in store.source_health() if h["source"] == crowdmark.SOURCE), {})
            known_out = crowdmark_probe is None and bool(last.get("last_error")) and \
                (last.get("last_success") or "") < (last.get("last_attempt") or "")
            ok = False if known_out else (crowdmark_probe or crowdmark.session_ok)()
            out.append(Check("crowdmark", "Crowdmark", "ok", "signed in") if ok
                       else Check("crowdmark", "Crowdmark", "needs_login", "signed out (sessions last ~8 h)", "waterloo"))
        except Exception as e:  # e.g. Playwright's browser missing
            out.append(Check("crowdmark", "Crowdmark", "error", f"couldn't check ({type(e).__name__}) — sign in to reconnect",
                             "waterloo"))
    term = quest.term_label(today)
    out.append(Check("quest", "Quest schedule", "ok", f"{term} captured") if store.classes_term() == term
               else Check("quest", "Quest schedule", "needs_login", f"not captured for {term}", "waterloo"))
    if not odyssey.is_configured():
        out.append(Check("odyssey", "Odyssey exams", "needs_login", "not connected yet", "waterloo"))
    else:
        last = next((h for h in store.source_health() if h["source"] == odyssey.SOURCE), {})
        if odyssey.is_stale(last.get("last_success")):
            out.append(Check("odyssey", "Odyssey exams", "needs_login",
                             "exam times & seats over 3 days old — sign in to refresh", "waterloo"))
        else:
            out.append(Check("odyssey", "Odyssey exams", "ok", "connected"))
    last = next((h for h in store.source_health() if h["source"] == course_pages.SOURCE), {})
    if not course_pages.is_configured():
        out.append(Check("web", "Course pages", "needs_login", "not read yet", "waterloo"))
    elif course_pages.is_stale(last.get("last_success")):
        out.append(Check("web", "Course pages", "needs_login", "over 3 days old — sign in to refresh", "waterloo"))
    else:
        out.append(Check("web", "Course pages", "ok", "connected"))
    if graph.is_signed_in() or get_secret("outlook_ics_url"):
        out.append(Check("outlook", "Outlook calendar", "ok", "connected"))
    else:
        out.append(Check("outlook", "Outlook calendar", "optional", "not set up", "paste_outlook"))
    return out


def print_checks(checks: list[Check]) -> None:
    mark = {"ok": "✓", "needs_login": "!", "missing": "!", "optional": "·", "error": "?"}
    print("Checking connections…")
    for c in checks:
        print(f"  {mark[c.state]} {c.name:<18} {c.detail}")


def waterloo_sign_in(store: Store, need_crowdmark: bool, need_quest: bool, today: date | None = None,
                     lock=None, cancelled: Callable[[], bool] = lambda: False,
                     progress: Callable[[str], None] = lambda phase: None,
                     need_odyssey: bool = False, need_web: bool = False) -> dict[str, str]:
    """One visible browser: the user signs in once (SSO + Duo); Crowdmark, Quest and Odyssey ride that session.
    `lock` guards the store when this runs beside the web server."""
    import logging
    import time
    from contextlib import nullcontext

    def _pump(pages, ms):
        """Wait via Playwright, not time.sleep: sync Playwright only processes browser events
        (navigations, new tabs, page.url) while one of its calls runs. With time.sleep the
        pages' URLs stay frozen at the login page forever."""
        for pg in pages:
            try:
                pg.wait_for_timeout(ms)
                return
            except Exception:
                continue
        time.sleep(ms / 1000)

    from playwright.sync_api import sync_playwright

    from .sources import course_pages, crowdmark, odyssey, quest, uw_browser

    log = logging.getLogger("aggregator.signin").info
    today = today or date.today()
    results: dict[str, str] = {}
    pending = {k for k, need in (("crowdmark", need_crowdmark), ("quest", need_quest),
                                 ("odyssey", need_odyssey), ("web", need_web)) if need}
    log(f"sign-in started for {sorted(pending)}")
    with sync_playwright() as p:
        ctx = uw_browser.launch(p, headless=False)
        page = uw_browser.first_page(ctx)
        page.bring_to_front()
        print("A browser window is open — sign in with your Waterloo account and approve Duo. "
              "Everything else happens automatically.", flush=True)
        progress("signin")
        # Watch every tab and react to what's on screen, rather than waiting for steps in a fixed
        # order — so it can't get stuck if the user goes to Quest himself or a step takes a detour.
        watcher = quest.Watcher(quest.term_label(today), log)
        quest_opened = odyssey_opened = False
        odyssey_empty_ticks = 0
        web_todo = list(course_pages.PAGES)  # course pages still to read, in order
        web_found: list = []
        web_opened: str | None = None
        web_empty_ticks = 0
        try:
            try:
                if "crowdmark" in pending:
                    crowdmark.start_sign_in(page)
                elif "quest" not in pending and "odyssey" in pending:
                    page.goto(odyssey.LOGIN_URL, wait_until="domcontentloaded")
                    odyssey_opened = True
                elif "quest" not in pending and web_todo:
                    page.goto(web_todo[0].url, wait_until="domcontentloaded")
                    web_opened = web_todo[0].key
                else:
                    page.goto(quest.SCHEDULE_URL, wait_until="domcontentloaded")
                    quest_opened = True
            except Exception as e:
                log(f"opening the first page: {type(e).__name__}: {e}")
            deadline = time.monotonic() + WATERLOO_TIMEOUT_MS / 1000
            while pending and time.monotonic() < deadline:
                if cancelled():
                    for k in pending:
                        results[k] = "not connected (cancelled)"
                    break
                pages = [pg for pg in ctx.pages if not pg.is_closed()]
                if not pages:
                    for k in pending:
                        results[k] = "not connected (the sign-in window was closed)"
                    break
                urls = [pg.url for pg in pages]
                if "crowdmark" in pending and any(u.startswith(crowdmark.BASE + "/student") for u in urls):
                    try:
                        results["crowdmark"] = f"connected — {crowdmark.mark_connected(ctx)} assessments visible"
                        pending.discard("crowdmark")
                        log("crowdmark: connected")
                        progress("quest")
                    except Exception as e:  # page loaded before the session cookie landed; try next tick
                        log(f"crowdmark: not ready yet ({type(e).__name__})")
                if "quest" in pending:
                    qpage = next((pg for pg in pages if "quest.pecs.uwaterloo.ca" in pg.url), None)
                    if qpage is None and "crowdmark" not in pending and not quest_opened:
                        pages[-1].goto(quest.SCHEDULE_URL, wait_until="domcontentloaded")  # Waterloo session is live
                        quest_opened = True
                        progress("quest")
                    elif qpage is not None:
                        try:
                            html = watcher.step(qpage)
                        except quest.Unreadable as e:
                            results["quest"] = f"not captured ({e})"
                            pending.discard("quest")
                            html = None
                        if html:
                            with lock or nullcontext():
                                meetings = quest.save(store, html, today)
                            results["quest"] = (f"saved {len(meetings)} weekly class times" if meetings else
                                                f"reached the schedule but couldn't read it (saved to {quest.LAST_HTML})")
                            pending.discard("quest")
                            log(f"quest: {results['quest']}")
                if "odyssey" in pending and not pending & {"crowdmark", "quest"}:
                    opage = next((pg for pg in pages if odyssey.on_schedule(pg.url)), None)
                    if opage is not None:
                        try:
                            html = opage.content()
                            found = odyssey.parse(html)
                            if not found and odyssey_empty_ticks < 5:  # table not drawn yet? look again
                                odyssey_empty_ticks += 1
                                raise ValueError("no rows yet")
                            odyssey.save_capture(html)
                            with lock or nullcontext():
                                store.sync_source(odyssey.SOURCE, found)
                            odyssey.mark_configured()
                            dated = sum(1 for it in found if it.due_date)
                            results["odyssey"] = f"connected — {len(found)} assessments ({dated} scheduled)"
                            pending.discard("odyssey")
                            log(f"odyssey: {results['odyssey']}")
                        except EmptySync as e:  # an empty page must never wipe the exam schedule
                            results["odyssey"] = f"reached Odyssey but it listed no assessments — {e} (page saved)"
                            pending.discard("odyssey")
                            log(f"odyssey: {results['odyssey']}")
                        except Exception as e:  # page still loading; look again next tick
                            log(f"odyssey: not ready yet ({type(e).__name__})")
                    elif not odyssey_opened:
                        progress("odyssey")
                        pages[-1].goto(odyssey.LOGIN_URL, wait_until="domcontentloaded")  # session is live
                        odyssey_opened = True
                if "web" in pending and not pending & {"crowdmark", "quest", "odyssey"}:
                    target = web_todo[0] if web_todo else None
                    wpage = next((pg for pg in pages if target and course_pages.on_page(pg.url, target)), None)
                    if target is None:
                        pending.discard("web")
                    elif wpage is not None:
                        try:
                            html = wpage.content()
                            found = course_pages.parse(html, target)
                        except Exception as e:  # still loading
                            log(f"course page: not ready yet ({type(e).__name__})")
                            found, html = None, ""
                        web_empty_ticks = web_empty_ticks + 1 if found == [] else 0
                        if found or web_empty_ticks >= 4:  # nothing after a few looks: take it as it is
                            saved = course_pages.save_capture(target, html)
                            web_found += found
                            log(f"course page {target.key}: {len(found)} items (page saved to {saved})")
                            web_todo.pop(0)
                            web_empty_ticks = 0
                            if not web_todo:
                                try:
                                    with lock or nullcontext():
                                        store.sync_source(course_pages.SOURCE, web_found)
                                except EmptySync as e:
                                    log(f"course pages: {e}")
                                course_pages.mark_configured()
                                dated = sum(1 for it in web_found if it.due_date)
                                results["web"] = (f"connected — {len(web_found)} items ({dated} with due dates)"
                                                  if web_found else "reached the page, but found no due dates "
                                                  f"(saved to {course_pages.SAVE_DIR})")
                                pending.discard("web")
                    elif web_opened != target.key:
                        progress("web")
                        pages[-1].goto(target.url, wait_until="domcontentloaded")  # session is live
                        web_opened = target.key
                _pump(pages, 1000)
            for k in pending:
                results.setdefault(k, "not connected (timed out waiting for sign-in)")
        except Exception as e:
            log(f"sign-in error: {type(e).__name__}: {e}")
            for k in pending:
                results.setdefault(k, f"not connected ({type(e).__name__}: {e})")
        finally:
            try:
                ctx.close()
            except Exception:
                pass
    log(f"sign-in finished: {results}")
    return results


def read_clipboard() -> str:
    """Text on the Windows clipboard (hidden-input prompts can't take Ctrl+V on Windows)."""
    try:
        import tkinter
        root = tkinter.Tk()
        root.withdraw()
        try:
            return root.clipboard_get()
        finally:
            root.destroy()
    except Exception:
        return ""


def _paste(what_link: str, validate: Callable[[str], int], secret_name: str, what: str,
           read: Callable[[str], str], clipboard: Callable[[], str]) -> str:
    """Returns 'saved' | 'skipped' | 'failed'. Copy the link, press Enter → read from the clipboard,
    so the link never shows on screen. Typing 'skip' skips."""
    from .connections import looks_like_link

    answer = read(f"Copy your {what_link}, then press Enter (or type 'skip'): ").strip()
    if answer.lower() == "skip":
        return "skipped"
    url = answer or clipboard().strip()
    if not looks_like_link(url):
        print("  No link found on the clipboard. Skipped for now — you'll be asked again next start.")
        return "failed"
    try:
        n = validate(url)
    except Exception as e:
        print(f"  That link didn't work ({e}). You'll be asked again next start.")
        return "failed"
    set_secret(secret_name, url)
    print(f"  Saved {redact_url(url)} — {n} {what}.")
    return "saved"


def _yes(ask: Callable[[str], str], prompt: str) -> bool:
    for _ in range(3):
        answer = ask(prompt).strip().lower()
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        prompt = "  Please press Enter for yes, or type n for no: "
    return False


def run_startup(store: Store, ask: Callable[[str], str] = input, secret_input: Callable[[str], str] = input,
                interactive: bool | None = None, include_skipped: bool = False,
                today: date | None = None, crowdmark_probe: Callable[[], bool] | None = None,
                clipboard: Callable[[], str] = read_clipboard) -> list[Check]:
    """Show connection status; if a human is at the terminal, offer to fix what's missing."""
    from .sources import d2l_ics, ics, outlook

    interactive = sys.stdin.isatty() if interactive is None else interactive
    checks = check_all(store, today, crowdmark_probe)
    print_checks(checks)
    prefs = _prefs()
    needs = {c.key: c for c in checks if c.action
             and not (c.key == "outlook" and prefs.get("outlook_skipped") and not include_skipped)}
    if not needs:
        print("Everything is connected.")
        return checks
    if not interactive:
        print("Run `python -m aggregator login` in a terminal to sign in to what's missing.")
        return checks

    if needs.keys() & {"crowdmark", "quest", "odyssey", "web"}:
        names = " + ".join(n for n, k in (("Crowdmark", "crowdmark"), ("Quest", "quest"), ("Odyssey", "odyssey"))
                           if k in needs)
        if _yes(ask, f"Sign in to Waterloo now? One Duo covers {names}. [Y/n] "):
            for key, result in waterloo_sign_in(store, "crowdmark" in needs, "quest" in needs, today,
                                                need_odyssey=True, need_web=True).items():
                print(f"  {key.capitalize()}: {result}")
    if "d2l" in needs:
        _paste("LEARN calendar feed link", lambda u: len(d2l_ics.parse_feed(d2l_ics.fetch_feed(u))),
               d2l_ics.SECRET_NAME, "events", secret_input, clipboard)
    if "outlook" in needs:
        result = _paste("published Outlook calendar ICS link",
                        lambda u: len(ics.parse_feed(ics.fetch_feed(u), outlook.SOURCE)), outlook.ICS_SECRET,
                        "events in the next 6 months", secret_input, clipboard)
        if result == "skipped":  # only an explicit skip is remembered
            prefs["outlook_skipped"] = True
            _save_prefs(prefs)
            print("  Skipped. Won't ask again — run `python -m aggregator login` to add it later.")
    return checks
