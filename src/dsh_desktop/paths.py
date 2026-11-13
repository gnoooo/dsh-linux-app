"""XDG base-directory resolution for the wrapper's own state.

Everything the wrapper persists (WebKit profile, cookies, config, logs) lives
under the standard XDG directories, so a sandbox or a test harness can redirect
all of it by exporting ``XDG_*`` variables.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from . import APP_NAME


def _base(environ: Mapping[str, str], variable: str, fallback: str) -> Path:
    """Resolve one XDG base directory, ignoring relative values.

    The XDG spec says relative paths must be ignored; doing so keeps state from
    landing in whatever directory the app happened to be launched from.
    """
    raw = environ.get(variable, "")
    if raw and os.path.isabs(raw):
        return Path(raw)
    return Path.home() / fallback


def data_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Persistent app data (WebKit profile, cookie database)."""
    return _base(environ or os.environ, "XDG_DATA_HOME", ".local/share") / APP_NAME


def cache_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Disposable caches (WebKit network cache)."""
    return _base(environ or os.environ, "XDG_CACHE_HOME", ".cache") / APP_NAME


def config_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Configuration directory."""
    return _base(environ or os.environ, "XDG_CONFIG_HOME", ".config") / APP_NAME


def state_dir(environ: Mapping[str, str] | None = None) -> Path:
    """Logs and other non-essential runtime state."""
    return _base(environ or os.environ, "XDG_STATE_HOME", ".local/state") / APP_NAME


def config_file(environ: Mapping[str, str] | None = None) -> Path:
    """Path of the optional TOML configuration file."""
    return config_dir(environ) / "config.toml"


def log_file(environ: Mapping[str, str] | None = None) -> Path:
    """Path of the backend log, rewritten on every launch."""
    return state_dir(environ) / "dsh-web.log"


def ensure_parent(path: Path) -> Path:
    """Create ``path``'s parent directory and return ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


__all__ = [
    "cache_dir",
    "config_dir",
    "config_file",
    "data_dir",
    "ensure_parent",
    "log_file",
    "state_dir",
]
