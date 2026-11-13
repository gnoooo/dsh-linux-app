"""The ``Gtk.Application`` object and the process entry point.

Using a named application gives single-instance behaviour for free: a second
launch (double-clicking the menu entry again) activates the running window
instead of starting a second backend.
"""

from __future__ import annotations

import signal
import sys

import gi

gi.require_version("Gtk", "3.0")

from gi.repository import Gio, GLib, Gtk  # noqa: E402

from . import APP_ID, APP_NAME  # noqa: E402
from .config import Settings  # noqa: E402
from .window import MainWindow  # noqa: E402


class DshDesktopApplication(Gtk.Application):
    """Owns the (single) main window for this session."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self._settings = settings
        self._window: MainWindow | None = None

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        try:
            Gtk.Window.set_default_icon_name(APP_ID)
        except (AttributeError, TypeError):
            pass

    def do_activate(self) -> None:
        if self._window is None:
            window = MainWindow(self, self._settings)
            window.connect("destroy", self._on_window_destroy)
            self._window = window
        self._window.show_all()
        self._window.present()
        self._window.deiconify()

    def _on_window_destroy(self, _window: Gtk.Window) -> None:
        self._window = None
        self.quit()

    def do_shutdown(self) -> None:
        # A quit that bypasses window destruction (Ctrl+C, SIGTERM, session
        # logout) must still stop the backend child and its process group.
        if self._window is not None:
            self._window.shutdown()
            self._window = None
        Gtk.Application.do_shutdown(self)


def run(settings: Settings) -> int:
    """Run the desktop app until the last window closes."""
    application = DshDesktopApplication(settings)

    def quit_on_signal(*_args: object) -> bool:
        for window in application.get_windows():
            window.close()
        application.quit()
        return GLib.SOURCE_REMOVE

    # Make Ctrl+C in a terminal behave like closing the window, which also
    # tears down the backend process group.
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, quit_on_signal)

    argv = [sys.argv[0] if sys.argv else APP_NAME]
    return application.run(argv)


__all__ = ["DshDesktopApplication", "run"]
