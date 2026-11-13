"""Tests for distribution detection and package-install hints."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dsh_desktop.distro import detect, install_hint, parse_os_release


def _hint(os_release: str):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "os-release"
        path.write_text(os_release)
        return detect(path, environ={})


ARCH = """\
NAME="Arch Linux"
PRETTY_NAME="Arch Linux"
ID=arch
BUILD_ID=rolling
"""

FEDORA = """\
NAME="Fedora Linux"
PRETTY_NAME="Fedora Linux 43 (KDE Plasma Desktop Edition)"
ID=fedora
VERSION_ID=43
"""

DEBIAN = """\
PRETTY_NAME="Debian GNU/Linux 13 (trixie)"
ID=debian
"""

UBUNTU_DERIVED = """\
NAME="Linux Mint"
PRETTY_NAME="Linux Mint 22"
ID=linuxmint
ID_LIKE="ubuntu debian"
"""

UNKNOWN = """\
NAME="Some Other Linux"
ID=someother
"""


class ParseOsReleaseTests(unittest.TestCase):
    def test_strips_quotes_and_comments(self) -> None:
        values = parse_os_release('# comment\nID=arch\nNAME="Arch Linux"\n')
        self.assertEqual(values["ID"], "arch")
        self.assertEqual(values["NAME"], "Arch Linux")

    def test_single_quotes(self) -> None:
        self.assertEqual(parse_os_release("ID='fedora'\n")["ID"], "fedora")

    def test_ignores_lines_without_equals(self) -> None:
        self.assertEqual(parse_os_release("garbage\n\nID=x\n"), {"ID": "x"})

    def test_empty_value(self) -> None:
        self.assertEqual(parse_os_release("ID_LIKE=\n")["ID_LIKE"], "")


class DetectTests(unittest.TestCase):
    def test_arch_uses_pacman(self) -> None:
        hint = _hint(ARCH)
        self.assertEqual(hint.distro_id, "arch")
        self.assertEqual(hint.manager, "pacman")
        self.assertEqual(
            hint.command, "sudo pacman -S --needed python-gobject webkit2gtk-4.1"
        )
        self.assertTrue(hint.known)

    def test_fedora_uses_dnf(self) -> None:
        hint = _hint(FEDORA)
        self.assertEqual(hint.manager, "dnf")
        self.assertEqual(
            hint.command, "sudo dnf install python3-gobject webkit2gtk4.1"
        )

    def test_debian_uses_apt(self) -> None:
        hint = _hint(DEBIAN)
        self.assertEqual(
            hint.command, "sudo apt install python3-gi gir1.2-webkit2-4.1"
        )

    def test_id_like_is_used_for_derivatives(self) -> None:
        hint = _hint(UBUNTU_DERIVED)
        self.assertEqual(hint.distro_id, "linuxmint")
        self.assertEqual(hint.manager, "apt")

    def test_arch_derivatives_use_pacman(self) -> None:
        hint = _hint('ID=cachyos\nID_LIKE=arch\n')
        self.assertEqual(hint.manager, "pacman")

    def test_unknown_distribution_gets_a_generic_note(self) -> None:
        hint = _hint(UNKNOWN)
        self.assertFalse(hint.known)
        self.assertIsNone(hint.command)
        self.assertIn("WebKitGTK 4.1", hint.note or "")

    def test_missing_file_is_not_an_error(self) -> None:
        hint = detect(Path("/nonexistent/os-release"), environ={})
        self.assertEqual(hint.distro_id, "unknown")
        self.assertFalse(hint.known)

    def test_pretty_name_is_preferred(self) -> None:
        self.assertEqual(_hint(ARCH).distro_name, "Arch Linux")

    def test_label_does_not_repeat_the_id(self) -> None:
        self.assertEqual(_hint(ARCH).label, "Arch Linux")
        self.assertEqual(_hint(FEDORA).label, "Fedora Linux 43 (KDE Plasma Desktop Edition)")

    def test_label_adds_the_id_when_missing(self) -> None:
        hint = _hint('NAME="Weird"\nID=weirdos\n')
        self.assertEqual(hint.label, "Weird (weirdos)")

    def test_environment_override_selects_the_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fake-release"
            path.write_text(ARCH)
            hint = detect(environ={"DSH_DESKTOP_OS_RELEASE": str(path)})
        self.assertEqual(hint.distro_id, "arch")

    def test_environment_override_wins_over_explicit_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fake-release"
            path.write_text(ARCH)
            hint = detect(
                Path("/nonexistent/os-release"),
                environ={"DSH_DESKTOP_OS_RELEASE": str(path)},
            )
        self.assertEqual(hint.distro_id, "arch")


class RenderTests(unittest.TestCase):
    def test_render_includes_command_and_distro(self) -> None:
        text = _hint(ARCH).render()
        self.assertIn("Arch Linux", text)
        self.assertIn("pacman -S --needed python-gobject webkit2gtk-4.1", text)

    def test_render_of_unknown_distro_has_no_command(self) -> None:
        text = _hint(UNKNOWN).render()
        self.assertNotIn("sudo", text)
        self.assertIn("typelib", text)

    def test_install_hint_runs_on_this_host(self) -> None:
        # Smoke test: must never raise, whatever /etc/os-release contains.
        self.assertTrue(install_hint(environ={}))


if __name__ == "__main__":
    unittest.main()
