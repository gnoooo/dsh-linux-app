"""A lightweight native desktop window for the DeepSeek Harness web GUI.

The package is a thin wrapper: it launches ``dsh web`` on a private port,
reads the tokenised URL that command prints, and displays that URL in a
WebKitGTK view.  No part of the DSH frontend is reimplemented here.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _installed_version

APP_ID = "org.deepseek.DshDesktop"
APP_NAME = "dsh-desktop"
APP_TITLE = "DeepSeek Harness"


def _detect_version() -> str:
    """Return the installed distribution version, or a source-tree marker."""
    try:
        return _installed_version("dsh-desktop")
    except PackageNotFoundError:
        return "0.0.0+source"


__version__ = _detect_version()

__all__ = ["APP_ID", "APP_NAME", "APP_TITLE", "__version__"]
