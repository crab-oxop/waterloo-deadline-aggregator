"""Desktop app: the same dashboard in a native window (Edge WebView2 via pywebview), with the
server and syncing running in the background. Closing the window quits everything."""
from __future__ import annotations

import socket
import threading

from . import dashboard
from .paths import DATA_DIR
from .store import Store

TITLE = "Waterloo Deadlines"


def _port(preferred: int = 8765) -> int:
    """The usual port if free, else any free one (e.g. a terminal dashboard is already running)."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def run(store: Store | None = None, calendar: bool = True, check_connections: bool = True) -> None:
    import webview

    from .shortcut import ICON, set_process_identity

    set_process_identity()  # before any window: the taskbar shows "Waterloo Deadlines", not pythonw
    store = store or Store()
    port = _port()
    server = dashboard.start(store, port=port, calendar=calendar, check_connections=check_connections)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True  # LEARN/Crowdmark links open in your browser
    main = webview.create_window(TITLE, f"http://127.0.0.1:{port}/start", width=1280, height=860,
                                 min_size=(420, 600), background_color="#F7F8FA")
    focus_win: list = []

    def open_focus(path: str) -> None:
        """A small window that stays on top, so the countdown is visible while working elsewhere."""
        url = f"http://127.0.0.1:{port}{path}"
        if focus_win:
            try:
                focus_win[0].load_url(url)
                focus_win[0].restore()
                return
            except Exception:
                focus_win.clear()
        w = webview.create_window("Focus — Waterloo Deadlines", url, width=420, height=600, min_size=(320, 420),
                                  on_top=True, background_color="#F7F8FA")
        w.events.closed += lambda: focus_win.clear()
        focus_win.append(w)

    def close_focus() -> None:
        while focus_win:
            focus_win.pop().destroy()

    dashboard.focus_window_open = open_focus
    dashboard.focus_window_close = close_focus
    dashboard.main_window_load = lambda path: main.load_url(f"http://127.0.0.1:{port}{path}")
    # A persistent profile keeps the Light/Dark/Auto choice between launches.
    webview.start(private_mode=False, storage_path=str(DATA_DIR / "webview"),
                  icon=str(ICON) if ICON.exists() else None)  # window + taskbar icon
    server.shutdown()
