"""Smoke tests for manage.sh.

These run the real script as a subprocess: the point is that the project manager
a maintainer types by hand is the one that gets exercised.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import tomllib
import unittest

ROOT = pathlib.Path(__file__).parents[1]
MANAGE = ROOT / "manage.sh"
VERSION_FILES = (
    ROOT / "pyproject.toml",
    ROOT / "uv.lock",
    ROOT / "packaging/arch/PKGBUILD",
    ROOT / "packaging/arch/.SRCINFO",
)


def run(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [str(MANAGE), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        env=env,
    )


def project_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        return str(tomllib.load(handle)["project"]["version"])


class ScriptShapeTests(unittest.TestCase):
    def test_shell_syntax(self) -> None:
        result = subprocess.run(["bash", "-n", str(MANAGE)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_is_executable(self) -> None:
        self.assertTrue(os.access(MANAGE, os.X_OK), "manage.sh must be executable")

    def test_help_lists_every_command(self) -> None:
        result = run("help")
        self.assertEqual(result.returncode, 0)
        for command in ("info", "bump", "check", "test", "build", "clean", "codes"):
            self.assertIn(command, result.stdout)

    def test_codes_lists_exit_codes(self) -> None:
        result = run("codes")
        self.assertEqual(result.returncode, 0)
        for code in ("64", "65", "66", "69", "70", "78"):
            self.assertIn(code, result.stdout)

    def test_unknown_command_exits_usage(self) -> None:
        self.assertEqual(run("not-a-command").returncode, 64)

    def test_unknown_clean_target_exits_usage(self) -> None:
        self.assertEqual(run("clean", "nope").returncode, 64)

    def test_build_rejects_unknown_target(self) -> None:
        self.assertEqual(run("build", "nope").returncode, 64)

    def test_check_rejects_bad_tag(self) -> None:
        self.assertEqual(run("check", "--tag", "0.1.0").returncode, 64)


class BumpTests(unittest.TestCase):
    def test_dry_run_writes_nothing(self) -> None:
        before = {path: path.read_bytes() for path in VERSION_FILES}
        result = run("bump", "patch", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        major, minor, patch = (int(part) for part in project_version().split(".")[:3])
        self.assertIn(f"{major}.{minor}.{patch + 1}", result.stdout)
        self.assertIn("dry-run", result.stdout)
        self.assertEqual({path: path.read_bytes() for path in VERSION_FILES}, before)

    def test_rejects_non_canonical_version(self) -> None:
        self.assertEqual(run("bump", "1.2.3.4", "--dry-run").returncode, 65)

    def test_rejects_version_equal_to_current(self) -> None:
        self.assertEqual(run("bump", project_version(), "--dry-run").returncode, 64)

    def test_rejects_two_arguments(self) -> None:
        self.assertEqual(run("bump", "patch", "1.0.0", "--dry-run").returncode, 64)


class CleanTests(unittest.TestCase):
    def test_dry_run_removes_nothing(self) -> None:
        marker = ROOT / "src/dsh_desktop/__pycache__"
        existed = marker.exists()
        result = run("clean", "cache", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dry-run", result.stdout)
        self.assertEqual(marker.exists(), existed)

    def test_refuses_without_yes_when_not_a_tty(self) -> None:
        # stdin is /dev/null here, so the confirmation prompt cannot be answered.
        result = run("clean", "cache")
        self.assertIn(result.returncode, (0, 64))
        if result.returncode == 64:
            self.assertIn("--yes", result.stderr)


@unittest.skipUnless(shutil.which("uv") and shutil.which("git"), "needs uv and git")
class InfoTests(unittest.TestCase):
    def test_info_reports_versions_and_runtime(self) -> None:
        result = run("info")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(project_version(), result.stdout)
        self.assertIn("Runtime", result.stdout)
        self.assertIn("Git", result.stdout)


if __name__ == "__main__":
    unittest.main()
