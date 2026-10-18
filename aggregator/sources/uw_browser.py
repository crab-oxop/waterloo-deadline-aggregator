"""One browser profile for everything behind Waterloo sign-in (Crowdmark via LEARN, Quest).
Sharing it means one Waterloo login + Duo covers every source in the same session.
Chromium encrypts the profile's cookies with Windows DPAPI; it lives in the data dir (paths.py), outside OneDrive."""
from __future__ import annotations

from ..paths import DATA_DIR

PROFILE_DIR = DATA_DIR / "uw_profile"


def launch(p, headless: bool):
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return p.chromium.launch_persistent_context(str(PROFILE_DIR), headless=headless)


def first_page(ctx):
    return ctx.pages[0] if ctx.pages else ctx.new_page()
