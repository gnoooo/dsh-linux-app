"""User configuration: a small TOML file plus environment overrides.

Precedence (lowest to highest): built-in defaults, ``config.toml``,
``DSH_DESKTOP_*`` environment variables, command-line flags.  The command line
layer is applied by :mod:`dsh_desktop.cli` via :func:`apply_overrides`.
"""

from __future__ import annotations

import os
import shlex
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from .paths import config_file


class ConfigError(Exception):
    """Raised when the configuration file or an override is unusable."""


@dataclass(frozen=True)
class Settings:
    """Effective runtime settings for one desktop session."""

    #: Explicit launcher command, e.g. ``("npx", "-y", "@deepseek-ai/dsh")``.
    #: ``None`` means "auto-detect", see :func:`dsh_desktop.server.resolve_command`.
    command: tuple[str, ...] | None = None
    #: Extra arguments appended to ``dsh web --no-open --port <port>``.
    extra_args: tuple[str, ...] = ()
    #: Bind address; ``None`` keeps the DSH default (loopback).
    host: str | None = None
    #: Listener port; ``0`` asks the OS for a free one.
    port: int = 0
    #: Working directory for the backend (the agent's default workspace).
    workspace: Path | None = None
    #: Seconds to wait before warning that startup is unusually slow.
    startup_timeout: float = 180.0
    #: Enable WebKit's developer extras and the F12 inspector.
    devtools: bool = False
    #: Initial page zoom factor.
    zoom: float = 1.0

    @property
    def launch_argv(self) -> tuple[str, ...] | None:
        """The configured command as an argv tuple, if any."""
        return self.command


_TRUE = frozenset({"1", "true", "yes", "on", "enabled"})
_FALSE = frozenset({"0", "false", "no", "off", "disabled"})


def _as_bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE:
            return True
        if lowered in _FALSE:
            return False
    raise ConfigError(f"{where}: expected a boolean, got {value!r}")


def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{where}: expected an integer, got {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip(), 10)
        except ValueError:
            pass
    raise ConfigError(f"{where}: expected an integer, got {value!r}")


def _as_float(value: Any, where: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{where}: expected a number, got {value!r}")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise ConfigError(f"{where}: expected a number, got {value!r}")


def _as_argv(value: Any, where: str) -> tuple[str, ...]:
    """Accept a TOML array of strings or a shell-quoted string."""
    if isinstance(value, str):
        try:
            parts = shlex.split(value)
        except ValueError as exc:
            raise ConfigError(f"{where}: {exc}") from exc
        if not parts:
            raise ConfigError(f"{where}: command is empty")
        return tuple(parts)
    if isinstance(value, Sequence):
        parts = []
        for item in value:
            if not isinstance(item, str):
                raise ConfigError(f"{where}: every element must be a string, got {item!r}")
            parts.append(item)
        if not parts:
            raise ConfigError(f"{where}: command is empty")
        return tuple(parts)
    raise ConfigError(f"{where}: expected a string or an array of strings, got {value!r}")


def _as_path(value: Any, where: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: expected a non-empty path, got {value!r}")
    return Path(value).expanduser().resolve()


def _validate(settings: Settings, where: str) -> Settings:
    if not 0 <= settings.port <= 65535:
        raise ConfigError(f"{where}: port must be between 0 and 65535, got {settings.port}")
    if settings.startup_timeout <= 0:
        raise ConfigError(
            f"{where}: startup_timeout must be positive, got {settings.startup_timeout}"
        )
    if not 0.25 <= settings.zoom <= 5.0:
        raise ConfigError(f"{where}: zoom must be between 0.25 and 5.0, got {settings.zoom}")
    if settings.host is not None and not settings.host.strip():
        raise ConfigError(f"{where}: host must not be empty")
    return settings


def _from_toml(data: Mapping[str, Any], source: str) -> Settings:
    settings = Settings()
    for key, value in data.items():
        where = f"{source}: {key}"
        if key == "command":
            settings = replace(settings, command=_as_argv(value, where))
        elif key in {"args", "extra_args"}:
            settings = replace(settings, extra_args=_as_argv(value, where))
        elif key == "host":
            if not isinstance(value, str):
                raise ConfigError(f"{where}: expected a string, got {value!r}")
            settings = replace(settings, host=value)
        elif key == "port":
            settings = replace(settings, port=_as_int(value, where))
        elif key == "workspace":
            settings = replace(settings, workspace=_as_path(value, where))
        elif key in {"startup_timeout", "startup_timeout_seconds"}:
            settings = replace(settings, startup_timeout=_as_float(value, where))
        elif key in {"devtools", "developer_extras"}:
            settings = replace(settings, devtools=_as_bool(value, where))
        elif key == "zoom":
            settings = replace(settings, zoom=_as_float(value, where))
        # Unknown keys are ignored so a newer config never blocks an older build.
    return settings


_ENV_KEYS = {
    "DSH_DESKTOP_COMMAND": "command",
    "DSH_DESKTOP_ARGS": "args",
    "DSH_DESKTOP_HOST": "host",
    "DSH_DESKTOP_PORT": "port",
    "DSH_DESKTOP_WORKSPACE": "workspace",
    "DSH_DESKTOP_STARTUP_TIMEOUT": "startup_timeout",
    "DSH_DESKTOP_DEVTOOLS": "devtools",
    "DSH_DESKTOP_ZOOM": "zoom",
}


def _from_environ(environ: Mapping[str, str], base: Settings) -> Settings:
    overrides: dict[str, Any] = {}
    for variable, key in _ENV_KEYS.items():
        raw = environ.get(variable)
        if raw is None or raw == "":
            continue
        overrides[key] = raw
    if not overrides:
        return base
    return _from_toml(overrides, "environment")


def load_settings(
    path: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> Settings:
    """Load settings from ``path`` (default: the XDG config file) and the env.

    A missing file is not an error; a malformed file is.
    """
    environ = os.environ if environ is None else environ
    explicit = environ.get("DSH_DESKTOP_CONFIG", "")
    if path is None and explicit:
        path = Path(explicit).expanduser()
    if path is None:
        path = config_file(environ)

    settings = Settings()
    if path.exists():
        try:
            with path.open("rb") as handle:
                data = tomllib.load(handle)
        except OSError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: expected a table at the top level")
        settings = _from_toml(data, str(path))

    settings = _from_environ(environ, settings)
    return _validate(settings, str(path))


def apply_overrides(settings: Settings, **overrides: Any) -> Settings:
    """Return ``settings`` with the non-``None`` keyword overrides applied."""
    effective = {key: value for key, value in overrides.items() if value is not None}
    if not effective:
        return settings
    if "command" in effective and isinstance(effective["command"], str):
        effective["command"] = _as_argv(effective["command"], "--dsh")
    if "workspace" in effective and isinstance(effective["workspace"], str):
        effective["workspace"] = _as_path(effective["workspace"], "--workspace")
    return _validate(replace(settings, **effective), "command line")


__all__ = ["ConfigError", "Settings", "apply_overrides", "load_settings"]
