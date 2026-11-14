"""Locate, launch, and supervise the ``dsh web`` backend process.

The wrapper's entire coupling to DeepSeek Harness is here:

* find a launcher (``dsh`` on ``PATH``, a Node version-manager install, or
  ``npx @deepseek-ai/dsh`` as a last resort);
* run ``dsh web --no-open --port <port>`` as a child in its own process group;
* read the ``dsh web: <url>`` line from stdout — the tokenised URL that
  authenticates the GUI — and hand it to the window;
* stop the child (and its whole process group) when the window closes.

Nothing here imports GTK, so it is testable headlessly.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

NPX_PACKAGE = "@deepseek-ai/dsh"

#: The one line this wrapper depends on.  ``dsh web`` also prints
#: ``dsh web: listening on ...`` warnings, so a URL must follow the prefix.
URL_LINE = re.compile(r"^dsh web:\s+(?P<url>https?://\S+)\s*$")

#: How many backend output lines to keep for the failure screen.
LOG_TAIL = 400

#: ``dsh`` installs under ``$HOME`` that a desktop-launched process will not
#: find on ``PATH`` (no shell profile is sourced by a .desktop entry).
_HOME_GLOBS = (
    ".nvm/versions/node/*/bin/dsh",
    ".local/share/fnm/node-versions/*/installation/bin/dsh",
    ".volta/bin/dsh",
    ".local/bin/dsh",
)

#: Node runtimes under ``$HOME``, used only when ``node`` is not on ``PATH``.
#: This is deliberately independent of where ``dsh`` lives: the launcher script
#: is a ``#!/usr/bin/env node`` shim, so the Node that runs it decides whether
#: DSH starts at all.
_HOME_NODE_GLOBS = (
    ".nvm/versions/node/*/bin/node",
    ".local/share/fnm/node-versions/*/installation/bin/node",
    ".volta/bin/node",
)

#: Conventional system-wide locations, checked after ``PATH``.
_SYSTEM_PATHS = (
    "/usr/local/bin/dsh",
    "/usr/bin/dsh",
)

_VERSION_IN_PATH = re.compile(r"v(\d+)\.(\d+)\.(\d+)")


class LaunchError(Exception):
    """Raised when no usable launcher exists or the backend cannot start."""


@dataclass(frozen=True)
class LaunchCommand:
    """A resolved way to invoke the DeepSeek Harness CLI."""

    argv: tuple[str, ...]
    #: Directory holding the launcher's own Node runtime, used as a last-resort
    #: ``PATH`` addition when no ``node`` can be found otherwise.
    bin_dir: str | None
    #: Where the command came from, for diagnostics.
    origin: str

    def describe(self) -> str:
        """A shell-quoted, copy-pasteable rendering of the command."""
        return shlex.join(self.argv)


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _version_key(path: Path) -> tuple[int, int, int]:
    match = _VERSION_IN_PATH.search(str(path))
    if match is None:
        return (-1, -1, -1)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _dedupe_by_target(paths: list[Path]) -> list[Path]:
    """Drop duplicate installs while preserving the first discovered spelling."""
    seen: dict[Path, None] = {}
    ordered: list[Path] = []
    for path in paths:
        try:
            key = path.resolve()
        except OSError:
            key = path
        if key in seen:
            continue
        seen[key] = None
        ordered.append(path)
    return ordered


def candidate_paths(
    home: Path | None = None,
    *,
    system_paths: Sequence[str] = _SYSTEM_PATHS,
) -> list[Path]:
    """Return conventional ``dsh`` locations, newest Node installation first.

    ``system_paths`` is injectable so callers can decide whether conventional
    system-wide locations are considered; it defaults to :data:`_SYSTEM_PATHS`.
    """
    home = Path.home() if home is None else home
    found: list[Path] = []
    for pattern in _HOME_GLOBS:
        found.extend(home.glob(pattern))
    found.extend(Path(raw) for raw in system_paths)
    # De-duplicate by target but keep the discovered path: a package manager
    # usually installs `bin/dsh` as a symlink into node_modules, and running the
    # shim (rather than the file it points at) is what a normal launch does.
    unique = _dedupe_by_target(found)
    return sorted(
        (p for p in unique if _is_executable_file(p)),
        key=_version_key,
        reverse=True,
    )


def _which(name: str, environ: Mapping[str, str]) -> str | None:
    path = environ.get("PATH", os.defpath)
    return shutil.which(name, path=path)


def candidate_node_dirs(home: Path | None = None) -> list[Path]:
    """Return directories holding a ``node`` runtime, newest first."""
    home = Path.home() if home is None else home
    found: list[Path] = []
    for pattern in _HOME_NODE_GLOBS:
        found.extend(home.glob(pattern))
    return sorted(
        {p.parent for p in _dedupe_by_target(found) if _is_executable_file(p)},
        key=_version_key,
        reverse=True,
    )


def _node_version_at(node: Path) -> str | None:
    """Version reported by one specific ``node`` executable."""
    try:
        result = subprocess.run(
            [str(node), "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return (result.stdout or "").strip() or None


def child_path(
    command: LaunchCommand,
    environ: Mapping[str, str],
    home: Path | None = None,
) -> str:
    """Compute the ``PATH`` for the backend process.

    Three real situations have to work:

    * a terminal launch where the user's shell already picked a good Node — the
      inherited ``PATH`` must be left alone;
    * a .desktop launch with no Node at all — a Node version-manager directory
      has to be added;
    * a .desktop launch where the distribution's ``node`` (often older than
      DSH requires) shadows a newer one — the newer directory must win, or DSH
      exits silently with status 0.

    So the decision is made on the *version*, not on mere presence.
    """
    current = environ.get("PATH", os.defpath)
    on_path = shutil.which("node", path=current)
    if on_path is not None and node_satisfies(_node_version_at(Path(on_path))):
        return current

    candidates: list[Path] = list(candidate_node_dirs(home))
    if command.bin_dir:
        candidates.append(Path(command.bin_dir))
    for directory in candidates:
        node = directory / "node"
        if not _is_executable_file(node):
            continue
        try:
            if on_path is not None and node.resolve() == Path(on_path).resolve():
                continue
        except OSError:
            pass
        if node_satisfies(_node_version_at(node)):
            return str(directory) + os.pathsep + current
    return current


def probe_node_version(environ: Mapping[str, str]) -> str | None:
    """Report the ``node`` version the backend would run under."""
    try:
        result = subprocess.run(
            ["node", "--version"],
            env=dict(environ),
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    reported = (result.stdout or "").strip()
    return reported or None


#: Minimum Node.js version DeepSeek Harness declares in ``engines.node``.
MIN_NODE: tuple[int, int] = (24, 9)


def node_satisfies(reported: str | None, minimum: tuple[int, int] = MIN_NODE) -> bool:
    """Whether a ``vX.Y.Z`` string meets ``minimum``; unknown counts as fine."""
    if not reported:
        return True
    match = re.match(r"v?(\d+)\.(\d+)", reported.strip())
    if match is None:
        return True
    return (int(match.group(1)), int(match.group(2))) >= minimum


def resolve_command(
    explicit: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    home: Path | None = None,
    system_paths: Sequence[str] = _SYSTEM_PATHS,
) -> LaunchCommand:
    """Decide how to start DeepSeek Harness.

    Order: an explicitly configured command, ``dsh`` on ``PATH``, a
    version-manager install, a system location, then ``npx``.

    ``system_paths`` is passed through to :func:`candidate_paths`; tests use an
    empty sequence to ignore launchers installed on the host.
    """
    environ = os.environ if environ is None else environ

    if explicit:
        argv = tuple(explicit)
        executable = argv[0]
        resolved = executable
        if os.sep not in executable:
            located = _which(executable, environ)
            if located is None:
                raise LaunchError(
                    f"configured command {executable!r} was not found on PATH; "
                    "fix `command` in config.toml or pass --dsh"
                )
            resolved = located
        elif not _is_executable_file(Path(executable)):
            raise LaunchError(f"configured command {executable!r} is not an executable file")
        return LaunchCommand(
            argv=(resolved, *argv[1:]),
            bin_dir=str(Path(resolved).parent),
            origin="configured",
        )

    located = _which("dsh", environ)
    if located is not None:
        return LaunchCommand((located,), str(Path(located).parent), "PATH")

    candidates = candidate_paths(home, system_paths=system_paths)
    if candidates:
        selected = candidates[0]
        system = {Path(raw) for raw in system_paths}
        origin = "system" if selected in system else "version manager"
        return LaunchCommand((str(selected),), str(selected.parent), origin)

    npx = _which("npx", environ)
    if npx is not None:
        return LaunchCommand((npx, "-y", NPX_PACKAGE), str(Path(npx).parent), "npx")

    raise LaunchError(
        "could not find the DeepSeek Harness CLI. Install it with\n"
        "    npm install -g @deepseek-ai/dsh\n"
        "or make sure `dsh` or `npx` is on PATH, or set `command` in\n"
        "    ~/.config/dsh-desktop/config.toml\n"
        "to something like [\"npx\", \"-y\", \"@deepseek-ai/dsh\"]."
    )


class DshServer:
    """One ``dsh web`` child process and the output it produces."""

    def __init__(
        self,
        command: LaunchCommand,
        *,
        extra_args: Sequence[str] = (),
        host: str | None = None,
        port: int = 0,
        workspace: Path | None = None,
        startup_timeout: float = 180.0,
        log_path: Path | None = None,
        stop_timeout: float = 10.0,
        on_url: Callable[[str], None] | None = None,
        on_exit: Callable[[int], None] | None = None,
        on_timeout: Callable[[], None] | None = None,
    ) -> None:
        self.command = command
        self.extra_args = tuple(extra_args)
        self.host = host
        self.port = port
        self.workspace = workspace
        self.startup_timeout = startup_timeout
        self.log_path = log_path
        self.stop_timeout = stop_timeout
        self._on_url = on_url
        self._on_exit = on_exit
        self._on_timeout = on_timeout

        self._proc: subprocess.Popen[str] | None = None
        self._env: dict[str, str] | None = None
        self._threads: list[threading.Thread] = []
        self._lines: deque[str] = deque(maxlen=LOG_TAIL)
        self._lock = threading.Lock()
        self._log_handle = None
        self._stopping = False
        self._timer: threading.Timer | None = None
        self._url: str | None = None
        self._url_event = threading.Event()

    # -- introspection ---------------------------------------------------

    @property
    def url(self) -> str | None:
        """The tokenised GUI URL, once the backend has announced it."""
        return self._url

    @property
    def pid(self) -> int | None:
        """The backend process id, or ``None`` before start."""
        return None if self._proc is None else self._proc.pid

    @property
    def running(self) -> bool:
        """Whether the backend process is still alive."""
        return self._proc is not None and self._proc.poll() is None

    def argv(self) -> list[str]:
        """The full command line used to start the backend.

        Extra arguments go *before* the app's own flags. ``dsh`` rewrites
        ``dsh web ...`` to ``dsh --profile web ...`` and then parses launcher
        options until the first option it does not know; so a launcher flag such
        as ``--patch overlay.yml`` only works ahead of ``--no-open``. An app
        flag placed there instead is still forwarded to the web app.
        """
        argv = [*self.command.argv, "web", *self.extra_args, "--no-open", "--port", str(self.port)]
        if self.host:
            argv += ["--host", self.host]
        return argv

    def describe(self) -> str:
        """A shell-quoted rendering of :meth:`argv`."""
        return shlex.join(self.argv())

    def tail(self, lines: int = 60) -> str:
        """The last ``lines`` output lines, newest last."""
        with self._lock:
            return "\n".join(list(self._lines)[-lines:])

    def node_version(self) -> str | None:
        """The Node.js version this backend runs under, if determinable."""
        env = self._env if self._env is not None else os.environ
        return probe_node_version(env)

    def wait_for_url(self, timeout: float | None = None) -> str | None:
        """Block until the URL is announced (used by tests and ``--check``)."""
        self._url_event.wait(timeout)
        return self._url

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Spawn the backend and begin pumping its output."""
        if self._proc is not None:
            raise RuntimeError("DshServer.start() called twice")

        cwd = self.workspace or Path.home()
        if not cwd.is_dir():
            raise LaunchError(f"workspace {str(cwd)!r} is not a directory")

        env = os.environ.copy()
        env["PATH"] = child_path(self.command, env)
        self._env = env

        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._log_handle = self.log_path.open("w", encoding="utf-8", errors="replace")
                self._log_handle.write(f"# {self.describe()}\n# cwd={cwd}\n")
                self._log_handle.flush()
            except OSError:
                self._log_handle = None

        try:
            self._proc = subprocess.Popen(
                self.argv(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(cwd),
                env=env,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                close_fds=True,
                # Own process group: stopping the wrapper must also stop the
                # Node server and anything the agent spawned.
                start_new_session=True,
            )
        except OSError as exc:
            self._close_log()
            raise LaunchError(f"could not run {self.describe()}: {exc}") from exc

        assert self._proc.stdout is not None and self._proc.stderr is not None
        self._spawn(self._pump, self._proc.stdout, "out")
        self._spawn(self._pump, self._proc.stderr, "err")
        self._spawn(self._watch)

        if self.startup_timeout > 0:
            timer = threading.Timer(self.startup_timeout, self._fire_timeout)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def stop(self) -> None:
        """Stop the backend, escalating to ``SIGKILL`` if it ignores ``SIGTERM``."""
        with self._lock:
            if self._stopping:
                return
            self._stopping = True
        self._cancel_timer()

        proc = self._proc
        if proc is not None and proc.poll() is None:
            self._signal_group(proc, signal.SIGTERM)
            try:
                proc.wait(timeout=self.stop_timeout)
            except subprocess.TimeoutExpired:
                self._signal_group(proc, signal.SIGKILL)
                try:
                    proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    pass

        for thread in self._threads:
            thread.join(timeout=2.0)
        self._close_log()

    # -- internals -------------------------------------------------------

    def _spawn(self, target: Callable[..., None], *args: object) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        self._threads.append(thread)
        thread.start()

    def _pump(self, stream, channel: str) -> None:
        try:
            for raw in stream:
                text = raw.rstrip("\n")
                self._record(channel, text)
                if channel == "out":
                    self._maybe_url(text)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _record(self, channel: str, text: str) -> None:
        line = text if channel == "out" else f"[stderr] {text}"
        with self._lock:
            self._lines.append(line)
            handle = self._log_handle
            if handle is not None:
                try:
                    handle.write(line + "\n")
                    handle.flush()
                except OSError:
                    pass

    def _maybe_url(self, text: str) -> None:
        match = URL_LINE.match(text)
        if match is None:
            return
        with self._lock:
            if self._url is not None:
                return
            self._url = match.group("url")
        self._cancel_timer()
        self._url_event.set()
        if self._on_url is not None:
            self._on_url(self._url)

    def _watch(self) -> None:
        proc = self._proc
        if proc is None:
            return
        code = proc.wait()
        # Let the output pumps drain before reporting the exit, so a failure
        # screen shows the child's final words rather than an empty box.
        for thread in list(self._threads):
            if thread is not threading.current_thread():
                thread.join(timeout=1.0)
        self._cancel_timer()
        with self._lock:
            stopping = self._stopping
        self._close_log()
        if not stopping and self._on_exit is not None:
            self._on_exit(code)

    def _fire_timeout(self) -> None:
        if self._url is not None:
            return
        if self._on_timeout is not None:
            self._on_timeout()

    def _cancel_timer(self) -> None:
        timer = self._timer
        if timer is not None:
            timer.cancel()

    def _signal_group(self, proc: subprocess.Popen[str], sig: int) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.send_signal(sig)
            except (ProcessLookupError, OSError):
                pass

    def _close_log(self) -> None:
        with self._lock:
            handle = self._log_handle
            self._log_handle = None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


__all__ = [
    "DshServer",
    "LaunchCommand",
    "LaunchError",
    "NPX_PACKAGE",
    "URL_LINE",
    "candidate_node_dirs",
    "candidate_paths",
    "child_path",
    "probe_node_version",
    "resolve_command",
]
