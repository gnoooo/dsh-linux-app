"""The native window: a GTK3 window hosting one WebKit2 view.

Startup and failure states are drawn with plain GTK widgets rather than HTML.
That is not only simpler than a custom URI scheme — it is required for
authentication to work:

DSH sets its session cookie with ``SameSite=Strict`` and redirects the tokenised
URL to the clean root.  A navigation started *from* a ``dsh-desktop://`` page
counts as cross-site, so WebKit withholds that cookie on the redirect and the
GUI answers "authentication required".  Keeping the token URL as the view's
first-ever navigation (the equivalent of typing it in an address bar) avoids the
cross-site initiator entirely.

All GTK work happens on the main thread; backend callbacks are marshalled
through ``GLib.idle_add``.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from urllib.parse import urlsplit

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")

from gi.repository import Gdk, Gio, GLib, Gtk, WebKit2  # noqa: E402

from . import APP_TITLE  # noqa: E402
from .config import Settings  # noqa: E402
from .paths import cache_dir, data_dir, log_file  # noqa: E402
from .server import DshServer, LaunchCommand, LaunchError, resolve_command  # noqa: E402

_ZOOM_MIN = 0.5
_ZOOM_MAX = 3.0
_ZOOM_STEP = 0.1


def _origin(url: str) -> tuple[str, str | None, int | None] | None:
    """Return ``(scheme, host, port)`` for a URL, normalising default ports."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.hostname:
        return None
    port = parts.port
    if port is None:
        port = {"http": 80, "https": 443}.get(parts.scheme)
    return (parts.scheme, parts.hostname, port)


class MainWindow(Gtk.ApplicationWindow):
    """One desktop session: a WebKit view plus its ``dsh web`` child."""

    def __init__(self, application: Gtk.Application, settings: Settings) -> None:
        super().__init__(application=application, title=APP_TITLE)
        self.settings = settings
        self.set_default_size(1280, 860)

        self._server: DshServer | None = None
        self._origin: tuple[str, str | None, int | None] | None = None
        self._closing = False
        self._app_loaded = False
        self._zoom = settings.zoom

        self._command: LaunchCommand | None = None
        self._command_error: str | None = None
        try:
            self._command = resolve_command(settings.command)
        except LaunchError as exc:
            self._command_error = str(exc)

        self._set_icon()
        self._build_webview()
        self._build_stack()
        self.add(self._stack)

        self.connect("destroy", self._on_destroy)
        self.connect("key-press-event", self._on_key)

        if self._command_error is not None:
            self._show_error(self._command_error)
        else:
            self._show_loading()
            GLib.idle_add(self._start_backend)

    # -- construction ----------------------------------------------------

    def _set_icon(self) -> None:
        icon = Path(__file__).with_name("icon.svg")
        if not icon.exists():
            return
        try:
            self.set_icon_from_file(str(icon))
        except GLib.Error:
            pass

    def _build_webview(self) -> None:
        data = data_dir()
        cache = cache_dir()
        try:
            (data / "webkit").mkdir(parents=True, exist_ok=True)
            (cache / "webkit").mkdir(parents=True, exist_ok=True)
            manager = WebKit2.WebsiteDataManager(
                base_data_directory=str(data / "webkit"),
                base_cache_directory=str(cache / "webkit"),
            )
            context = WebKit2.WebContext(website_data_manager=manager)
        except (OSError, GLib.Error) as exc:
            # A read-only or sandboxed home must not prevent the app from
            # running; only the persistence of cookies and localStorage is lost.
            print(
                f"dsh-desktop: cannot use {data} for the WebKit profile ({exc}); "
                "continuing without persistent storage",
                file=sys.stderr,
            )
            context = WebKit2.WebContext()
        try:
            # Keep cookies and localStorage across launches so the GUI's own
            # preferences (theme, selected model) survive a restart.
            context.get_cookie_manager().set_persistent_storage(
                str(data / "cookies.sqlite"),
                WebKit2.CookiePersistentStorage.SQLITE,
            )
        except (GLib.Error, OSError) as exc:
            print(f"dsh-desktop: cookie persistence unavailable: {exc}", file=sys.stderr)
        self._context = context

        view = WebKit2.WebView(web_context=context)
        web_settings = view.get_settings()
        web_settings.set_enable_developer_extras(self.settings.devtools)
        web_settings.set_javascript_can_open_windows_automatically(False)
        web_settings.set_enable_html5_local_storage(True)
        web_settings.set_enable_webgl(True)
        view.set_zoom_level(self._zoom)

        view.connect("load-failed", self._on_load_failed)
        view.connect("create", self._on_create)
        view.connect("decide-policy", self._on_decide_policy)
        view.connect("permission-request", self._on_permission_request)
        view.connect("web-process-terminated", self._on_web_process_terminated)
        view.connect("notify::title", self._on_title_changed)
        view.connect("key-press-event", self._on_key)

        self._webview = view

    def _build_stack(self) -> None:
        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        stack.set_transition_duration(120)
        stack.add_named(self._build_loading_view(), "loading")
        stack.add_named(self._build_error_view(), "error")
        stack.add_named(self._webview, "web")
        # Gtk.Stack only switches to a child that is already visible, so the
        # children have to be shown here rather than by the window's show_all().
        stack.show_all()
        self._stack = stack
        self._switch_to("loading")

    def _switch_to(self, name: str) -> None:
        """Show one stack child, tolerating a not-yet-realized window."""
        child = self._stack.get_child_by_name(name)
        if child is None:
            return
        child.show()
        self._stack.set_visible_child(child)

    def _build_loading_view(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_start(48)
        box.set_margin_end(48)

        self._spinner = Gtk.Spinner()
        self._spinner.set_size_request(28, 28)
        self._spinner.start()
        box.pack_start(self._spinner, False, False, 0)

        self._loading_title = Gtk.Label()
        self._loading_title.set_markup("<span size='x-large' weight='bold'>Starting DeepSeek Harness…</span>")
        box.pack_start(self._loading_title, False, False, 0)

        self._loading_subtitle = Gtk.Label(
            label="Launching the local backend and waiting for it to report its address."
        )
        self._loading_subtitle.set_line_wrap(True)
        self._loading_subtitle.set_max_width_chars(64)
        self._loading_subtitle.set_justify(Gtk.Justification.CENTER)
        self._loading_subtitle.get_style_context().add_class("dim-label")
        box.pack_start(self._loading_subtitle, False, False, 0)

        self._loading_command = Gtk.Label(label=self._command_line())
        self._loading_command.set_selectable(True)
        self._loading_command.set_line_wrap(True)
        self._loading_command.set_max_width_chars(64)
        self._loading_command.get_style_context().add_class("monospace")
        self._loading_command.get_style_context().add_class("dim-label")
        box.pack_start(self._loading_command, False, False, 0)

        restart = Gtk.Button(label="Restart backend")
        restart.set_halign(Gtk.Align.CENTER)
        restart.connect("clicked", lambda *_: self._restart_backend())
        box.pack_start(restart, False, False, 6)

        return box

    def _build_error_view(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_start(48)
        box.set_margin_end(48)

        title = Gtk.Label()
        title.set_markup("<span size='x-large' weight='bold'>DeepSeek Harness did not start</span>")
        box.pack_start(title, False, False, 0)

        # Not selectable: a selectable label takes focus and paints its text
        # with the selection colour, which is unreadable here.  The command and
        # the log below stay selectable, which is what users actually copy.
        self._error_message = Gtk.Label(label="")
        self._error_message.set_line_wrap(True)
        self._error_message.set_max_width_chars(72)
        self._error_message.set_justify(Gtk.Justification.CENTER)
        box.pack_start(self._error_message, False, False, 0)

        self._error_command = Gtk.Label(label="")
        self._error_command.set_selectable(True)
        self._error_command.set_line_wrap(True)
        self._error_command.set_max_width_chars(72)
        self._error_command.get_style_context().add_class("monospace")
        self._error_command.get_style_context().add_class("dim-label")
        # no_show_all keeps a later show_all() from revealing an empty block.
        self._error_command.set_no_show_all(True)
        box.pack_start(self._error_command, False, False, 0)

        self._error_output = Gtk.Label(label="")
        self._error_output.set_selectable(True)
        self._error_output.set_line_wrap(True)
        self._error_output.set_xalign(0.0)
        self._error_output.get_style_context().add_class("monospace")
        self._error_output_scroller = Gtk.ScrolledWindow()
        self._error_output_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._error_output_scroller.set_max_content_height(260)
        self._error_output_scroller.set_propagate_natural_height(True)
        self._error_output_scroller.set_shadow_type(Gtk.ShadowType.IN)
        self._error_output_scroller.set_no_show_all(True)
        self._error_output_scroller.add(self._error_output)
        box.pack_start(self._error_output_scroller, True, True, 0)

        retry = Gtk.Button(label="Try again")
        retry.set_halign(Gtk.Align.CENTER)
        retry.connect("clicked", lambda *_: self._restart_backend())
        box.pack_start(retry, False, False, 6)

        return box

    # -- backend lifecycle ----------------------------------------------

    def _command_line(self) -> str:
        """The backend command line, for the status views."""
        if self._server is not None:
            return self._server.describe()
        if self._command is None:
            return ""
        return shlex.join(
            [*self._command.argv, "web", "--no-open", "--port", str(self.settings.port)]
        )

    def _make_server(self) -> DshServer:
        assert self._command is not None
        return DshServer(
            self._command,
            extra_args=self.settings.extra_args,
            host=self.settings.host,
            port=self.settings.port,
            workspace=self.settings.workspace,
            startup_timeout=self.settings.startup_timeout,
            log_path=log_file(),
            on_url=lambda url: GLib.idle_add(self._on_backend_url, url),
            on_exit=lambda code: GLib.idle_add(self._on_backend_exit, code),
            on_timeout=lambda: GLib.idle_add(self._on_backend_slow),
        )

    def _start_backend(self) -> bool:
        self._server = None
        if self._command_error is not None:
            self._show_error(self._command_error)
            return False
        try:
            server = self._make_server()
            server.start()
        except (LaunchError, OSError) as exc:
            self._server = None
            self._show_error(str(exc))
            return False
        self._server = server
        return False

    def _restart_backend(self) -> bool:
        if self._server is not None:
            self._server.stop()
            self._server = None
        self._origin = None
        self._app_loaded = False
        self._show_loading()
        return self._start_backend()

    def _on_backend_url(self, url: str) -> bool:
        self._origin = _origin(url)
        self.set_title(APP_TITLE)
        self._app_loaded = True
        self._switch_to("web")
        # First navigation of this view: no cross-site initiator, so the
        # SameSite=Strict session cookie survives the token redirect.
        self._webview.load_uri(url)
        return False

    def _on_backend_exit(self, code: int) -> bool:
        if self._closing:
            return False
        output = self._server.tail(80) if self._server is not None else ""
        if self._origin is None and code == 0:
            # DSH requires Node >= 24.9 and exits silently on an older runtime,
            # so "exit 0 before any URL" almost always means the wrong Node.
            node = self._server.node_version() if self._server is not None else None
            used = f" This session used Node.js {node}." if node else ""
            message = (
                "The backend exited before it reported its address. DeepSeek "
                "Harness needs Node.js 24.9 or newer, and an older Node exits "
                f"this way without printing anything.{used}"
            )
        else:
            message = f"The backend exited unexpectedly (code {code})."
        self._show_error(message, output)
        return False

    def _on_backend_slow(self) -> bool:
        if self._origin is not None:
            return False
        self._loading_title.set_markup("<span size='x-large' weight='bold'>Still starting…</span>")
        self._loading_subtitle.set_text(
            "This is taking longer than usual. On the first run npx may be "
            "downloading DeepSeek Harness, which can take a minute."
        )
        return False

    # -- status views ----------------------------------------------------

    def _show_loading(self) -> None:
        self._loading_title.set_markup("<span size='x-large' weight='bold'>Starting DeepSeek Harness…</span>")
        self._loading_subtitle.set_text(
            "Launching the local backend and waiting for it to report its address."
        )
        self._loading_command.set_text(self._command_line())
        self._spinner.start()
        self._switch_to("loading")

    def _show_error(self, message: str, output: str | None = None) -> None:
        self._spinner.stop()
        self._error_message.set_text(message)
        command = self._command_line()
        text = (output or "").strip()
        self._error_command.set_text(command)
        self._error_output.set_text(text)
        # Switch first: showing a container also shows its children, so hiding
        # the empty blocks has to happen afterwards.
        self._switch_to("error")
        self._error_command.set_visible(bool(command))
        # Both the scroller and its label are no_show_all, so each needs an
        # explicit show; revealing the container alone leaves the text hidden.
        self._error_output.set_visible(bool(text))
        self._error_output_scroller.set_visible(bool(text))

    # -- web view signals ------------------------------------------------

    def _is_internal(self, uri: str | None) -> bool:
        if not uri:
            return False
        if uri.startswith(("about:", "data:", "blob:")):
            return True
        if self._origin is None:
            return False
        return _origin(uri) == self._origin

    def _on_decide_policy(
        self,
        _view: WebKit2.WebView,
        decision: WebKit2.PolicyDecision,
        decision_type: WebKit2.PolicyDecisionType,
    ) -> bool:
        if decision_type not in (
            WebKit2.PolicyDecisionType.NAVIGATION_ACTION,
            WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION,
        ):
            return False
        action = decision.get_navigation_action()
        request = action.get_request() if action is not None else None
        uri = request.get_uri() if request is not None else None
        if uri is None:
            return False
        if self._is_internal(uri):
            if decision_type == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
                decision.ignore()
                self._webview.load_uri(uri)
                return True
            return False
        # Never navigate the app window away from the local GUI; hand links to
        # the user's real browser instead.
        decision.ignore()
        self._open_external(uri)
        return True

    def _on_create(
        self, _view: WebKit2.WebView, navigation_action: WebKit2.NavigationAction
    ) -> WebKit2.WebView | None:
        request = navigation_action.get_request()
        uri = request.get_uri() if request is not None else None
        if uri is not None:
            if self._is_internal(uri):
                self._webview.load_uri(uri)
            else:
                self._open_external(uri)
        return None

    def _on_permission_request(
        self, view: WebKit2.WebView, request: WebKit2.PermissionRequest
    ) -> bool:
        allowed = tuple(
            kind
            for kind in (
                getattr(WebKit2, "ClipboardPermissionRequest", None),
                getattr(WebKit2, "NotificationPermissionRequest", None),
            )
            if kind is not None
        )
        if isinstance(request, allowed) and self._is_internal(view.get_uri()):
            request.allow()
        else:
            request.deny()
        return True

    def _on_load_failed(
        self,
        _view: WebKit2.WebView,
        _load_event: WebKit2.LoadEvent,
        failing_uri: str,
        error: GLib.Error,
    ) -> bool:
        # A superseded load reports CANCELLED; that is normal and not an error.
        try:
            if error.matches(WebKit2.network_error_quark(), WebKit2.NetworkError.CANCELLED):
                return False
        except (AttributeError, TypeError):
            pass
        if self._is_internal(failing_uri):
            self._show_error(f"Could not load {failing_uri}: {error.message}")
            return True
        return False

    def _on_web_process_terminated(
        self, _view: WebKit2.WebView, reason: WebKit2.WebProcessTerminationReason
    ) -> None:
        self._show_error(f"The web content process stopped unexpectedly ({reason.value_nick}).")

    def _on_title_changed(self, view: WebKit2.WebView, _pspec: object) -> None:
        title = view.get_title()
        self.set_title(title if title else APP_TITLE)

    # -- window events ---------------------------------------------------

    def shutdown(self) -> None:
        """Stop the backend. Idempotent, and safe to call from any shutdown path."""
        self._closing = True
        if self._server is not None:
            self._server.stop()
            self._server = None

    def _on_destroy(self, *_args: object) -> None:
        self.shutdown()

    def _on_key(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        name = Gdk.keyval_name(event.keyval) or ""

        if name == "F11":
            if self.is_fullscreen():
                self.unfullscreen()
            else:
                self.fullscreen()
            return True
        if name == "F5" and not ctrl:
            self._reload()
            return True
        if name == "F12" or (ctrl and shift and name.lower() == "i"):
            self._open_inspector()
            return True
        if ctrl and name.lower() == "r":
            if shift:
                self._restart_backend()
            else:
                self._reload()
            return True
        if ctrl and name.lower() == "q":
            self.close()
            return True
        if ctrl and shift and name.lower() == "l":
            self._copy_url()
            return True
        if ctrl and name in {"plus", "equal", "KP_Add"}:
            self._set_zoom(self._zoom + _ZOOM_STEP)
            return True
        if ctrl and name in {"minus", "KP_Subtract"}:
            self._set_zoom(self._zoom - _ZOOM_STEP)
            return True
        if ctrl and name == "0":
            self._set_zoom(1.0)
            return True
        return False

    # -- small actions ---------------------------------------------------

    def _reload(self) -> None:
        current = self._webview.get_uri() or ""
        if self._app_loaded and current.startswith(("http://", "https://")):
            self._webview.reload()
        elif self._stack.get_visible_child_name() == "error":
            self._restart_backend()

    def _open_inspector(self) -> None:
        self._webview.get_settings().set_enable_developer_extras(True)
        inspector = self._webview.get_inspector()
        if inspector is not None:
            inspector.show()

    def _set_zoom(self, value: float) -> None:
        self._zoom = min(_ZOOM_MAX, max(_ZOOM_MIN, round(value, 2)))
        self._webview.set_zoom_level(self._zoom)

    def _copy_url(self) -> None:
        uri = self._webview.get_uri() or ""
        if not uri.startswith(("http://", "https://")):
            uri = self._server.url if self._server is not None else ""
        if not uri:
            return
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(uri, -1)
        clipboard.store()

    def _open_external(self, uri: str) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except GLib.Error as exc:
            print(f"dsh-desktop: could not open {uri}: {exc.message}", file=sys.stderr)


__all__ = ["MainWindow"]
