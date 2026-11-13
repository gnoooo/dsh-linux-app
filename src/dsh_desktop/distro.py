"""Distribution detection and package-install hints.

The app's real requirement is narrow and testable: PyGObject must be able to
``gi.require_version`` **GTK 3.0** and **WebKit2 4.1**.  Package *names* differ
across distributions, so this module only ever produces a *hint* — whether the
bindings are actually present is always decided by importing them.

``python -m dsh_desktop.distro`` prints the hint for the running system, which
lets ``scripts/install.sh`` give the right command before a virtualenv exists.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

OS_RELEASE = Path("/etc/os-release")

#: What each distribution family calls "PyGObject + GTK 3 + WebKitGTK 4.1".
#: Only families whose package names are known are listed; anything else gets
#: the generic hint rather than a confidently wrong command.
_FAMILIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "fedora": ("dnf", ("python3-gobject", "webkit2gtk4.1")),
    "rhel": ("dnf", ("python3-gobject", "webkit2gtk4.1")),
    "centos": ("dnf", ("python3-gobject", "webkit2gtk4.1")),
    "debian": ("apt", ("python3-gi", "gir1.2-webkit2-4.1")),
    "ubuntu": ("apt", ("python3-gi", "gir1.2-webkit2-4.1")),
    "linuxmint": ("apt", ("python3-gi", "gir1.2-webkit2-4.1")),
    "pop": ("apt", ("python3-gi", "gir1.2-webkit2-4.1")),
    "arch": ("pacman", ("python-gobject", "webkit2gtk-4.1")),
    "manjaro": ("pacman", ("python-gobject", "webkit2gtk-4.1")),
    "endeavouros": ("pacman", ("python-gobject", "webkit2gtk-4.1")),
    "cachyos": ("pacman", ("python-gobject", "webkit2gtk-4.1")),
    "artix": ("pacman", ("python-gobject", "webkit2gtk-4.1")),
    "opensuse-tumbleweed": ("zypper", ("python3-gobject", "typelib-1_0-WebKit2-4_1")),
    "opensuse-leap": ("zypper", ("python3-gobject", "typelib-1_0-WebKit2-4_1")),
    "opensuse": ("zypper", ("python3-gobject", "typelib-1_0-WebKit2-4_1")),
    "void": ("xbps", ("python3-gobject", "webkit2gtk41")),
}

#: How to phrase the install command for each package manager.
_MANAGER_COMMANDS: dict[str, str] = {
    "dnf": "sudo dnf install",
    "apt": "sudo apt install",
    "pacman": "sudo pacman -S --needed",
    "zypper": "sudo zypper install",
    "xbps": "sudo xbps-install -S",
}

_GENERIC_NOTE = (
    "Install PyGObject and the WebKitGTK 4.1 GObject-introspection typelib\n"
    "(the package providing WebKit2-4.1.typelib). Package names vary by\n"
    "distribution; see the README for a table of known names."
)


@dataclass(frozen=True)
class PackageHint:
    """What to install on this distribution to get the GTK bindings."""

    distro_id: str
    distro_name: str
    manager: str | None
    packages: tuple[str, ...]
    command: str | None
    note: str | None = None

    @property
    def known(self) -> bool:
        """Whether a distribution-specific command is available."""
        return self.command is not None

    @property
    def label(self) -> str:
        """Display name, with the ID appended only when it adds information."""
        if self.distro_id and self.distro_id not in self.distro_name.lower():
            return f"{self.distro_name} ({self.distro_id})"
        return self.distro_name

    def render(self) -> str:
        """A short, human-readable recommendation."""
        lines = [f"Detected {self.label}."]
        if self.command:
            lines.append(f"  {self.command}")
        if self.note:
            lines.extend(f"  {line}" for line in self.note.splitlines())
        return "\n".join(lines)


def parse_os_release(text: str) -> dict[str, str]:
    """Parse ``os-release`` content into a mapping.

    The format is shell-like but only ``KEY=value`` with optional single or
    double quotes is used in practice; unknown lines are ignored rather than
    raising, because this feeds a diagnostic, not a decision.
    """
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def _read_os_release(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def detect(os_release_path: Path | None = None, environ: Mapping[str, str] | None = None) -> PackageHint:
    """Describe the package-install hint for this system."""
    environ = os.environ if environ is None else environ
    # An explicit override makes the mapping testable on any machine.
    override = environ.get("DSH_DESKTOP_OS_RELEASE", "")
    path = Path(override) if override else (os_release_path or OS_RELEASE)
    values = parse_os_release(_read_os_release(path))

    distro_id = (values.get("ID") or "unknown").lower()
    distro_name = values.get("PRETTY_NAME") or values.get("NAME") or distro_id
    candidates = [distro_id]
    candidates.extend(part.lower() for part in (values.get("ID_LIKE") or "").split())

    for candidate in candidates:
        family = _FAMILIES.get(candidate)
        if family is None:
            continue
        manager, packages = family
        verb = _MANAGER_COMMANDS.get(manager)
        command = f"{verb} {' '.join(packages)}" if verb else None
        return PackageHint(distro_id, distro_name, manager, packages, command)

    return PackageHint(distro_id, distro_name, None, (), None, _GENERIC_NOTE)


def install_hint(**kwargs: object) -> str:
    """The hint for the running system, ready to print."""
    return detect(**kwargs).render()  # type: ignore[arg-type]


def main() -> int:
    print(install_hint())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
