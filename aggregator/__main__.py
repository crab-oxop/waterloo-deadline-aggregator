"""CLI: python -m aggregator {serve,login,sync,list,setup-*}"""
from __future__ import annotations

import argparse
import getpass
import sys

from . import paths
from .sources import d2l_ics
from .secrets import redact_url, set_secret
from .store import Store
from .sync import sync_all


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="aggregator")
    p.add_argument("--db", default=None, help="SQLite path (default: <data dir>/aggregator.db)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup-d2l", help="store the LEARN calendar feed URL in the OS keychain")
    g = sub.add_parser("setup-gcal", help="connect Google Calendar (opens a browser for consent)")
    g.add_argument("client_secrets", help="path to the OAuth desktop-client credentials.json")
    sub.add_parser("login", help="check every connection and sign in to whatever needs it")
    sub.add_parser("setup-quest", help="read your class schedule from Quest (opens a browser; SSO + Duo)")
    sub.add_parser("setup-crowdmark", help="sign in to Crowdmark once (opens a browser for Waterloo SSO + Duo)")
    sub.add_parser("setup-outlook-ics", help="store a published Outlook calendar (ICS) link in the OS keychain")
    o = sub.add_parser("setup-outlook", help="sign in to Outlook via Microsoft Graph (mail + calendar)")
    o.add_argument("--client-id", required=True, help="Application (client) ID of the Entra app registration")
    o.add_argument("--tenant", default="organizations", help="tenant ID/domain (default: organizations)")
    sub.add_parser("sync", help="pull every configured source once, then push to Google Calendar")
    sub.add_parser("list", help="print items by priority")
    s = sub.add_parser("serve", help="run the dashboard (syncs on start + every 10 min)")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--no-refresh", action="store_true")
    s.add_argument("--no-calendar", action="store_true", help="don't push to Google Calendar")
    s.add_argument("--no-login", action="store_true", help="skip the startup connection check")
    s.add_argument("--no-browser", action="store_true", help="don't open the browser automatically")
    a = sub.add_parser("app", help="open the dashboard as a desktop app window")
    a.add_argument("--no-calendar", action="store_true", help="don't push to Google Calendar")
    a.add_argument("--no-login", action="store_true", help="skip the startup connection check")
    sub.add_parser("install-shortcut", help="add Waterloo Deadlines to the Desktop and Start menu")
    args = p.parse_args(argv)
    # Before anything writes to the data dir (a log file would make it look non-empty): move the
    # old in-project data/ (inside OneDrive) to the local data dir, once.
    moved = paths.migrate_legacy()
    if moved and args.cmd not in ("serve", "app"):
        print(f"Moved {len(moved)} item(s) from {paths.LEGACY_DATA_DIR} to {paths.DATA_DIR}; "
              "the old folder can be deleted.")

    if args.cmd == "setup-d2l":
        # getpass so the token-bearing URL never lands in shell history or the screen
        url = getpass.getpass("Paste your LEARN calendar feed URL (hidden): ").strip()
        items = d2l_ics.parse_feed(d2l_ics.fetch_feed(url))  # validate before saving
        set_secret(d2l_ics.SECRET_NAME, url)
        print(f"Saved {redact_url(url)} to the OS keychain — feed has {len(items)} events.")
        return 0

    if args.cmd == "setup-gcal":
        from . import gcal
        gcal.authorize(args.client_secrets)
        print(f"Connected. Token saved to the OS keychain; you can delete {args.client_secrets}.")
        print("Run `python -m aggregator sync` to create the calendar and push deadlines.")
        return 0

    if args.cmd in ("setup-quest", "setup-crowdmark"):
        # Both ride the shared Waterloo browser session (one Duo); `login` does both at once.
        from .login import waterloo_sign_in
        store = Store(args.db) if args.db else Store()
        results = waterloo_sign_in(store, need_crowdmark=args.cmd == "setup-crowdmark",
                                   need_quest=args.cmd == "setup-quest")
        for key, result in results.items():
            print(f"{key.capitalize()}: {result}")
        return 0 if all(r.startswith(("connected", "saved")) for r in results.values()) else 1

    if args.cmd == "login":
        from .login import run_startup
        store = Store(args.db) if args.db else Store()
        run_startup(store, interactive=True, include_skipped=True)
        return 0

    if args.cmd == "setup-outlook-ics":
        from .sources import ics, outlook
        url = getpass.getpass("Paste your published Outlook calendar ICS link (hidden): ").strip()
        items = ics.parse_feed(ics.fetch_feed(url), outlook.SOURCE)  # validate before saving
        set_secret(outlook.ICS_SECRET, url)
        print(f"Saved {redact_url(url)} to the OS keychain — {len(items)} events in the next 6 months.")
        return 0

    if args.cmd == "setup-outlook":
        from . import graph
        try:
            who = graph.login(args.client_id, args.tenant)
        except graph.GraphError as e:
            print(f"Outlook sign-in didn't complete: {e}")
            return 1
        print(f"Signed in as {who}. Token cache is encrypted in the data dir.")
        print("Outlook now syncs via Graph (calendar + course emails); the ICS link, if any, is no longer used.")
        return 0

    store = Store(args.db) if args.db else Store()
    if args.cmd == "sync":
        results = sync_all(store, force=True)  # a manual sync tries every source, even ones backing off
        for source, r in results.items():
            print(f"{source}: {r}")
        return 0 if all(isinstance(r, dict) for r in results.values()) else 1
    if args.cmd == "list":
        for it in store.list_items():
            due = it.due_date.strftime("%Y-%m-%d %H:%M") if it.due_date else "—"
            print(f"{it.priority_score:8.2f}  {due:16}  {it.context or '':9}  {it.type:10}  {it.title}")
        return 0
    if args.cmd in ("serve", "app"):
        import logging

        from . import applog
        applog.setup()
        if moved:
            logging.getLogger("aggregator.paths").info(
                "migration: moved %s from %s to %s", ", ".join(moved), paths.LEGACY_DATA_DIR, paths.DATA_DIR)
    if args.cmd == "serve":
        from .dashboard import serve
        # Sign-in now happens in the app itself (Connections page), which opens first if needed.
        serve(store, port=args.port, refresh=not args.no_refresh, calendar=not args.no_calendar,
              check_connections=not args.no_login, open_browser=not args.no_browser)
        return 0
    if args.cmd == "app":
        from .app import run
        run(store, calendar=not args.no_calendar, check_connections=not args.no_login)
        return 0
    if args.cmd == "install-shortcut":
        from .shortcut import install
        for path in install():
            print(f"Created {path}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
