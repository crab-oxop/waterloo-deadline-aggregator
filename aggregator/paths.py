"""Where runtime state lives: the database, logs, the Waterloo browser profile, token cache,
saved page captures and prefs. Deliberately OUTSIDE the project folder, which sits in OneDrive —
sessions and tokens must never leave this machine (SPEC § Security).

    Windows:  %LOCALAPPDATA%\\WaterlooDeadlines
              (Microsoft Store Python: %LOCALAPPDATA%\\Packages\\<python package>\\LocalCache\\Local\\
              WaterlooDeadlines — see _package_local_dir)
    other:    ~/.local/share/waterloo-deadlines
    override: the WATERLOO_DEADLINES_DATA environment variable (tests point it at a temp dir)

Things that ship with the code (icons) live in assets/, not here."""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

ENV_VAR = "WATERLOO_DEADLINES_DATA"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LEGACY_DATA_DIR = PROJECT_ROOT / "data"  # where everything lived before (inside OneDrive)
MIGRATED_MARKER = ".migrated_from"

log = logging.getLogger(__name__)


def _package_family() -> str | None:
    """The MSIX package family when Python itself is packaged (Microsoft Store Python), else None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    try:
        fn = ctypes.windll.kernel32.GetCurrentPackageFamilyName
    except (AttributeError, OSError):
        return None
    length = wintypes.UINT(0)
    if fn(ctypes.byref(length), None) != 122:  # ERROR_INSUFFICIENT_BUFFER → packaged; 15700 → not
        return None
    buf = ctypes.create_unicode_buffer(length.value)
    return buf.value if fn(ctypes.byref(length), buf) == 0 else None


def _package_local_dir(local: Path) -> Path | None:
    """Store Python's writes under %LOCALAPPDATA% are silently redirected into its package's
    LocalCache — but the browsers we launch (Chromium, WebView2) aren't packaged and would see the
    un-redirected path: two different folders, and a sign-in that seems to vanish. Naming the
    redirected location explicitly gives every process the same real folder."""
    family = _package_family()
    return local / "Packages" / family / "LocalCache" / "Local" if family else None


def default_data_dir() -> Path:
    override = os.environ.get(ENV_VAR)
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return (_package_local_dir(local) or local) / "WaterlooDeadlines"
    return Path.home() / ".local" / "share" / "waterloo-deadlines"


DATA_DIR = default_data_dir()


def _is_empty(d: Path) -> bool:
    return not d.exists() or not any(d.iterdir())


def migrate_legacy(legacy: Path = LEGACY_DATA_DIR, target: Path | None = None) -> list[str]:
    """One-time move of the old project data/ folder into the data dir. Runs only when the old
    folder has something in it and the new one is empty. Each entry is moved (not copied), so the
    sessions leave OneDrive; the old folder itself is left in place for the user to delete. Returns
    the names moved (empty list when there was nothing to do)."""
    target = target or DATA_DIR
    if legacy.resolve() == target.resolve() or _is_empty(legacy) or not _is_empty(target):
        return []
    busy = _in_use(legacy)
    if busy:  # all or nothing: never leave half the data behind (a copy may have it open)
        log.warning("migration skipped: %s in use in %s — is another copy of the app running?",
                    ", ".join(busy), legacy)
        return []
    target.mkdir(parents=True, exist_ok=True)
    moved, failed = [], []
    for entry in sorted(legacy.iterdir()):
        try:
            try:
                entry.rename(target / entry.name)  # same drive: an atomic rename, never a partial copy
            except OSError as e:
                if getattr(e, "winerror", None) != 17 and e.errno != 18:  # 17/EXDEV: another drive
                    raise
                shutil.move(str(entry), str(target / entry.name))
            moved.append(entry.name)
        except OSError as e:  # e.g. a file OneDrive is holding open; leave it, say so
            failed.append(entry.name)
            log.warning("migration: couldn't move %s (%s)", entry.name, e)
    (target / MIGRATED_MARKER).write_text(f"{legacy}\n", encoding="utf-8")
    log.info("migration: moved %d item(s) from %s to %s%s", len(moved), legacy, target,
             f"; left behind: {', '.join(failed)}" if failed else "")
    return moved


def _in_use(folder: Path) -> list[str]:
    """Entries that can't be moved right now. Windows refuses to rename a file that's open, or a
    folder with an open file inside, so a rename there-and-back is a safe, instant probe."""
    busy = []
    for entry in folder.iterdir():
        probe = entry.with_name(entry.name + ".moving")
        try:
            entry.rename(probe)
            probe.rename(entry)
        except OSError:
            busy.append(entry.name)
    return busy


def legacy_note(target: Path | None = None) -> str:
    """One line for the Connections page while the old folder still exists after a migration."""
    target = target or DATA_DIR
    marker = target / MIGRATED_MARKER
    if not marker.exists():
        return ""
    old = Path(marker.read_text(encoding="utf-8").strip())
    if not old.exists():
        return ""
    left = [p.name for p in old.iterdir()] if old.is_dir() else []
    if left:
        return (f"Your data moved to {target}, but {len(left)} item(s) couldn't be moved from the old folder "
                f"{old} ({', '.join(left[:4])}). Close the app and delete that folder once you've checked it.")
    return f"Your data now lives in {target}. The old folder {old} is no longer used and can be deleted."
