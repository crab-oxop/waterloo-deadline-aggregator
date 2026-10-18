"""App log at <data dir>/app.log — the desktop app has no console, so this is where sign-in steps,
sync results and errors go. Rotates at ~1 MB. Never contains links, tokens or passwords."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from .paths import DATA_DIR

LOG_PATH = DATA_DIR / "app.log"


def setup(console: bool = False) -> None:
    root = logging.getLogger("aggregator")
    if any(isinstance(h, RotatingFileHandler) for h in root.handlers):
        return
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)
    if console:
        root.addHandler(logging.StreamHandler())
    root.setLevel(logging.INFO)
