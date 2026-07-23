"""
run.py — Flux Audio entry point.

Starts Flask in a background thread and opens a PyWebView window.
Exposes a Python API to JavaScript for native OS interactions
(e.g. folder picker) that the browser alone cannot perform.

Usage:
    python run.py
"""

import signal
import subprocess
import sys
import threading
import webview
from app import create_app
from config import Config

app = create_app()


class FluxAPI:
    """
    Python functions exposed to the frontend via window.pywebview.api.*
    Called from JavaScript as async functions — always return JSON-safe values.
    """

    def pick_folder(self):
        """
        Open a native macOS folder picker dialog.
        Returns the selected folder path as a string, or None if cancelled.
        Called from JS: const path = await window.pywebview.api.pick_folder()
        """
        result = webview.windows[0].create_file_dialog(webview.FileDialog.FOLDER)
        if result:
            return result[0]
        return None

    def get_library_root(self):
        """Return the configured library root path (for display purposes only)."""
        return str(Config.LIBRARY_ROOT)

    def open_in_browser(self, url):
        """
        Open a URL in the system's default browser (macOS: `open`).
        Used by the debug panel pop-out since PyWebView blocks window.open().
        Called from JS: await window.pywebview.api.open_in_browser(url)
        """
        try:
            subprocess.Popen(['open', url])
            return True
        except Exception as e:
            return str(e)


def start_flask():
    """
    Run Flask in a background thread so PyWebView owns the main thread.

    threaded=True (2026-07-19): the dev server otherwise handles ONE request
    at a time — a single slow request (a Batch Import "Review" scan hitting
    a slow NAS read, for example) blocks the ENTIRE app, including unrelated
    UI actions and even the debug panel's own polling. That's what made a
    stuck scan look like "the whole app died" rather than "one request is
    slow" — and why "New Scan" never helped: the retry just queued up behind
    the same stuck worker. config.py already sets check_same_thread=False on
    the SQLite connection specifically to support this.
    """
    app.run(
        host         = Config.HOST,
        port         = Config.PORT,
        debug        = False,       # must be False under PyWebView
        use_reloader = False,
        threaded     = True,
    )


if __name__ == "__main__":
    # Ctrl-C should kill the process even while PyWebView owns the main thread
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))

    # Flask runs in a daemon thread — dies when the window closes
    flask_thread = threading.Thread(target=start_flask, daemon=True)
    flask_thread.start()

    # PyWebView must own the main thread on macOS
    webview.create_window(
        title    = "Flux Audio",
        url      = f"http://{Config.HOST}:{Config.PORT}",
        js_api   = FluxAPI(),
        width    = 1440,
        height   = 900,
        min_size = (960, 640),
    )
    webview.start()
