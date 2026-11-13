"""Command-line entry point for ``dsh-desktop``."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import APP_NAME, __version__, distro
from .config import ConfigError, Settings, apply_overrides, load_settings
from .paths import cache_dir, config_file, data_dir, log_file, state_dir
from .server import (
    MIN_NODE,
    LaunchError,
    child_path,
    node_satisfies,
    probe_node_version,
    resolve_command,
)

def _missing_gtk_message(error: object) -> str:
    """Explain a failed GTK import using this distribution's package names."""
    return (
        f"{APP_NAME}: the GTK 3 + WebKitGTK 4.1 Python bindings are required.\n"
        f"{distro.install_hint()}\n"
        f"Details: {error}\n"
    )

_EPILOG = """\
Configuration file: ~/.config/dsh-desktop/config.toml
  command  = ["npx", "-y", "@deepseek-ai/dsh"]   # default: auto-detect
  args     = []                                  # extra args for `dsh web`
  port     = 0                                   # 0 = pick a free port
  workspace = "~"                                # agent working directory

Arguments this app does not recognise are passed to `dsh web`, so an extra
overlay or a future dsh flag works without this wrapper knowing it:
  dsh-desktop --patch ./extra.yml --dsh /path/to/dsh

Keyboard shortcuts:
  F5 / Ctrl+R        reload the interface
  Ctrl+Shift+R       restart the backend
  F12 / Ctrl+Shift+I developer tools
  F11                fullscreen
  Ctrl+Shift+L       copy the GUI URL
  Ctrl+plus/minus/0  zoom in / out / reset
  Ctrl+Q             quit
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Run DeepSeek Harness in a native desktop window.",
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version",
        "-V",
        action="version",
        version=f"{APP_NAME} {__version__}",
    )
    parser.add_argument(
        "--dsh",
        metavar="COMMAND",
        help="launcher command for dsh, e.g. 'npx -y @deepseek-ai/dsh' "
        "(default: auto-detect dsh, then npx)",
    )
    parser.add_argument("--host", metavar="ADDR", help="bind address for the backend")
    parser.add_argument(
        "--port",
        type=int,
        metavar="PORT",
        help="backend port (default: 0, let the OS pick a free one)",
    )
    parser.add_argument(
        "--workspace",
        metavar="DIR",
        help="working directory for the backend (default: your home directory)",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        metavar="SECONDS",
        help="warn that startup is slow after this long (default: 180)",
    )
    parser.add_argument(
        "--zoom",
        type=float,
        metavar="FACTOR",
        help="initial zoom factor (default: 1.0)",
    )
    parser.add_argument(
        "--devtools",
        action="store_true",
        default=None,
        help="enable the WebKit developer extras (also available with F12)",
    )
    parser.add_argument(
        "--config",
        metavar="FILE",
        help="read settings from FILE instead of ~/.config/dsh-desktop/config.toml",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="print diagnostics (toolkit, launcher, paths) and exit",
    )
    return parser


def _diagnostics(settings: Settings) -> int:
    """Print what the app would use, so a broken menu launch is debuggable."""
    print(f"{APP_NAME} {__version__}")
    print(f"python       {sys.version.split()[0]}  ({sys.executable})")

    hint = distro.detect()
    print(f"distro       {hint.label}")

    status = 0
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        gi.require_version("WebKit2", "4.1")
        from gi.repository import Gtk, WebKit2

        print(
            "gtk          "
            f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
        )
        print(
            "webkitgtk    "
            f"{WebKit2.get_major_version()}.{WebKit2.get_minor_version()}."
            f"{WebKit2.get_micro_version()}"
        )
    except (ImportError, ValueError) as exc:
        print(f"gtk          UNAVAILABLE: {exc}")
        for line in hint.render().splitlines():
            print(f"             {line}")
        status = 1

    config_path = config_file()
    state = "present" if config_path.exists() else "absent"
    print(f"config       {config_path} ({state})")
    print(f"data         {data_dir()}")
    print(f"cache        {cache_dir()}")
    print(f"state        {state_dir()}")
    print(f"log          {log_file()}")

    try:
        command = resolve_command(settings.command)
    except LaunchError as exc:
        print(f"launcher     UNAVAILABLE: {exc}")
        return 1
    print(f"launcher     {command.describe()}")
    print(f"origin       {command.origin}")
    if command.bin_dir:
        print(f"bin dir      {command.bin_dir}")

    backend_env = dict(os.environ)
    backend_env["PATH"] = child_path(command, backend_env)
    node = probe_node_version(backend_env)
    print(f"node         {node or 'NOT FOUND on the backend PATH'}")
    if not node_satisfies(node):
        print(
            "             WARNING: DeepSeek Harness requires Node.js "
            f">= {MIN_NODE[0]}.{MIN_NODE[1]}.0"
        )

    if command.origin == "npx":
        print("dsh version  skipped (npx would download the package)")
    else:
        try:
            result = subprocess.run(
                [*command.argv, "--version"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            reported = (result.stdout or result.stderr).strip().splitlines()
            print(f"dsh version  {reported[0] if reported else '(no output)'}")
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"dsh version  could not determine: {exc}")

    workspace = settings.workspace or Path.home()
    print(f"workspace    {workspace} ({'ok' if workspace.is_dir() else 'MISSING'})")
    print(f"port         {settings.port}{' (OS-assigned)' if settings.port == 0 else ''}")
    print(f"backend      {settings.launch_argv or 'auto-detect'}")
    return status


def _parse(argv: list[str] | None) -> tuple[argparse.Namespace, tuple[str, ...]]:
    """Parse this app's flags; everything else is forwarded to ``dsh web``.

    Unknown options are forwarded on purpose: an embedding launcher (for example
    a wrapper that boots dsh with an extra overlay) can pass ``--patch`` or a
    future dsh flag without this wrapper having to learn it.
    """
    args, unknown = _build_parser().parse_known_args(argv)
    # `parse_known_args` keeps unrecognised arguments in argv order, which
    # matters: `--patch` and its path must stay adjacent. The bare `--` that
    # argparse reports belongs to this parser, not to dsh, so it is dropped.
    return args, tuple(arg for arg in unknown if arg != "--")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the app (or print diagnostics)."""
    args, forwarded = _parse(argv)

    try:
        settings = load_settings(
            Path(args.config).expanduser() if args.config else None
        )
        settings = apply_overrides(
            settings,
            command=args.dsh,
            host=args.host,
            port=args.port,
            workspace=args.workspace,
            startup_timeout=args.startup_timeout,
            devtools=args.devtools,
            zoom=args.zoom,
            extra_args=forwarded or None,
        )
    except ConfigError as exc:
        print(f"{APP_NAME}: {exc}", file=sys.stderr)
        return 2

    if args.check:
        return _diagnostics(settings)

    try:
        from .app import run
    except (ImportError, ValueError) as exc:
        print(_missing_gtk_message(exc), file=sys.stderr)
        return 3

    return run(settings)


if __name__ == "__main__":
    raise SystemExit(main())
