"""Tests for launcher resolution and backend supervision (no GTK needed)."""

from __future__ import annotations

import os
import stat
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from dsh_desktop.server import (
    URL_LINE,
    DshServer,
    LaunchCommand,
    LaunchError,
    candidate_node_dirs,
    candidate_paths,
    child_path,
    node_satisfies,
    resolve_command,
)


def _write_exe(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class UrlLineTests(unittest.TestCase):
    def test_matches_tokenised_url(self) -> None:
        line = "dsh web: http://127.0.0.1:3080/?token=abc123"
        match = URL_LINE.match(line)
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("url"), "http://127.0.0.1:3080/?token=abc123")

    def test_ignores_other_startup_warnings(self) -> None:
        self.assertIsNone(URL_LINE.match("dsh web: listening on 10.0.0.7 over plain HTTP"))
        self.assertIsNone(URL_LINE.match("dsh web: opening the default browser"))

    def test_handles_https_and_public_urls(self) -> None:
        match = URL_LINE.match("dsh web: https://app.example/ui/?token=t")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group("url"), "https://app.example/ui/?token=t")


class CandidatePathTests(unittest.TestCase):
    def test_finds_nvm_install_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = _write_exe(home / ".nvm/versions/node/v18.2.0/bin/dsh")
            new = _write_exe(home / ".nvm/versions/node/v22.11.0/bin/dsh")
            found = candidate_paths(home)
            self.assertEqual(found[0], new.resolve())
            self.assertIn(old.resolve(), found)

    def test_skips_non_executable_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            path = home / ".nvm/versions/node/v20.0.0/bin/dsh"
            path.parent.mkdir(parents=True)
            path.write_text("not executable")
            path.chmod(0o644)
            self.assertNotIn(path, candidate_paths(home))

    def test_missing_home_yields_no_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(candidate_paths(Path(tmp)), [])


class ResolveCommandTests(unittest.TestCase):
    def test_explicit_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = _write_exe(Path(tmp) / "bin/dsh")
            command = resolve_command([str(exe)], environ={"PATH": ""})
            self.assertEqual(command.argv, (str(exe),))
            self.assertEqual(command.bin_dir, str(exe.parent))
            self.assertEqual(command.origin, "configured")

    def test_explicit_name_is_looked_up_on_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = _write_exe(Path(tmp) / "dsh")
            command = resolve_command(["dsh"], environ={"PATH": tmp})
            self.assertEqual(command.argv, (str(exe),))

    def test_explicit_missing_name_raises(self) -> None:
        with self.assertRaises(LaunchError):
            resolve_command(["definitely-not-a-real-binary"], environ={"PATH": ""})

    def test_explicit_non_executable_file_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dsh"
            path.write_text("nope")
            path.chmod(0o644)
            with self.assertRaises(LaunchError):
                resolve_command([str(path)], environ={"PATH": ""})

    def test_path_dsh_wins_over_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            _write_exe(home / ".nvm/versions/node/v20.0.0/bin/dsh")
            on_path = _write_exe(Path(tmp) / "bin/dsh")
            command = resolve_command(
                environ={"PATH": str(Path(tmp) / "bin")},
                home=home,
            )
            self.assertEqual(command.argv, (str(on_path),))
            self.assertEqual(command.origin, "PATH")

    def test_falls_back_to_npx(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            npx = _write_exe(Path(tmp) / "npx")
            command = resolve_command(environ={"PATH": tmp}, home=Path(tmp) / "home")
            self.assertEqual(command.argv, (str(npx), "-y", "@deepseek-ai/dsh"))
            self.assertEqual(command.origin, "npx")
            self.assertEqual(command.bin_dir, str(npx.parent))

    def test_raises_with_actionable_message_when_nothing_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(LaunchError) as caught:
                resolve_command(environ={"PATH": ""}, home=Path(tmp))
            self.assertIn("npm install -g @deepseek-ai/dsh", str(caught.exception))


_EMIT_URL = textwrap.dedent(
    """
    import sys, time
    print("booting", flush=True)
    print("dsh web: http://127.0.0.1:4567/?token=deadbeef", flush=True)
    time.sleep(30)
    """
).strip()

_EXIT_EARLY = textwrap.dedent(
    """
    import sys
    print("boom: cannot start", file=sys.stderr, flush=True)
    sys.exit(3)
    """
).strip()


class DshServerTests(unittest.TestCase):
    def _server(self, script: str, **kwargs) -> DshServer:
        command = LaunchCommand((sys.executable, "-c", script), None, "test")
        return DshServer(command, **kwargs)

    def test_captures_url_and_cleans_up(self) -> None:
        urls: list[str] = []
        server = self._server(_EMIT_URL, on_url=urls.append, startup_timeout=0)
        server.start()
        try:
            url = server.wait_for_url(timeout=20)
            self.assertEqual(url, "http://127.0.0.1:4567/?token=deadbeef")
            self.assertEqual(urls, [url])
            self.assertTrue(server.running)
            self.assertIn("booting", server.tail())
        finally:
            server.stop()
        self.assertFalse(server.running)
        self.assertEqual(server.url, "http://127.0.0.1:4567/?token=deadbeef")

    def test_reports_exit_code_and_stderr(self) -> None:
        codes: list[int] = []
        server = self._server(_EXIT_EARLY, on_exit=codes.append, startup_timeout=0)
        server.start()
        try:
            for _ in range(200):
                if codes:
                    break
                time.sleep(0.1)
        finally:
            server.stop()
        self.assertEqual(codes, [3])
        self.assertIn("boom: cannot start", server.tail())

    def test_no_exit_callback_when_stopped_deliberately(self) -> None:
        codes: list[int] = []
        server = self._server(_EMIT_URL, on_exit=codes.append, startup_timeout=0)
        server.start()
        server.wait_for_url(timeout=20)
        server.stop()
        time.sleep(0.3)
        self.assertEqual(codes, [])

    def test_extra_args_come_before_the_app_flags(self) -> None:
        """A launcher flag such as --patch must precede --no-open.

        ``dsh web ...`` is rewritten to ``dsh --profile web ...`` and launcher
        options are only parsed until the first unknown option; placing
        ``--patch`` after ``--no-open`` makes the web app reject it.
        """
        command = LaunchCommand(("dsh",), None, "test")
        server = DshServer(command, port=0, extra_args=("--patch", "/tmp/x.yml"))
        self.assertEqual(
            server.argv(),
            ["dsh", "web", "--patch", "/tmp/x.yml", "--no-open", "--port", "0"],
        )

    def test_host_stays_with_the_app_flags(self) -> None:
        command = LaunchCommand(("dsh",), None, "test")
        server = DshServer(command, port=1, host="127.0.0.1", extra_args=("--patch", "x"))
        self.assertEqual(
            server.argv(),
            ["dsh", "web", "--patch", "x", "--no-open", "--port", "1", "--host", "127.0.0.1"],
        )

    def test_argv_includes_host_when_set(self) -> None:
        command = LaunchCommand(("dsh",), None, "test")
        server = DshServer(command, port=8080, host="127.0.0.1")
        self.assertIn("--host", server.argv())
        self.assertEqual(server.argv()[-1], "127.0.0.1")

    def test_missing_workspace_raises(self) -> None:
        command = LaunchCommand(("dsh",), None, "test")
        server = DshServer(command, workspace=Path("/nonexistent/dsh-workspace"))
        with self.assertRaises(LaunchError):
            server.start()

    def test_writes_log_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log_path = Path(tmp) / "nested" / "dsh-web.log"
            server = self._server(_EMIT_URL, startup_timeout=0, log_path=log_path)
            server.start()
            try:
                server.wait_for_url(timeout=20)
            finally:
                server.stop()
            content = log_path.read_text()
            self.assertIn("dsh web: http://127.0.0.1:4567/?token=deadbeef", content)


class ChildPathTests(unittest.TestCase):
    """The backend's PATH must never leave DSH on an unsupported Node.

    A ``dsh`` shim installed by Node 20 is still a ``#!/usr/bin/env node``
    script.  Preferring the shim's own directory, or trusting whatever ``node``
    a desktop session inherits, silently selects an old Node — under which DSH
    exits with status 0 and prints nothing at all.
    """

    @staticmethod
    def _node(directory: Path, version: str) -> Path:
        return _write_exe(directory / "node", f"#!/bin/sh\necho {version}\n")

    def test_leaves_path_untouched_when_node_is_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            node_bin = Path(tmp) / "v24/bin"
            self._node(node_bin, "v24.9.0")
            dsh_dir = Path(tmp) / "v20/bin"
            _write_exe(dsh_dir / "dsh")
            command = LaunchCommand((str(dsh_dir / "dsh"),), str(dsh_dir), "PATH")
            environ = {"PATH": str(node_bin)}
            self.assertEqual(child_path(command, environ), str(node_bin))

    def test_adds_a_node_when_path_has_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            new = home / ".nvm/versions/node/v24.9.0/bin"
            self._node(new, "v24.9.0")
            old = home / ".nvm/versions/node/v20.20.2/bin"
            self._node(old, "v20.20.2")
            dsh_dir = Path(tmp) / "v20/bin"
            _write_exe(dsh_dir / "dsh")
            command = LaunchCommand((str(dsh_dir / "dsh"),), str(dsh_dir), "PATH")

            empty_path = Path(tmp) / "emptybin"
            empty_path.mkdir()
            resolved = child_path(command, {"PATH": str(empty_path)}, home=home)

            first = resolved.split(os.pathsep)[0]
            self.assertEqual(first, str(new), "the newest Node should win")
            self.assertTrue(resolved.endswith(str(empty_path)))

    def test_replaces_a_too_old_node_on_path(self) -> None:
        """The Fedora case: /usr/bin/node is v22, nvm has v24."""
        with tempfile.TemporaryDirectory() as tmp:
            system_bin = Path(tmp) / "usr/bin"
            self._node(system_bin, "v22.22.2")
            home = Path(tmp) / "home"
            newer = home / ".nvm/versions/node/v24.22.2/bin"
            self._node(newer, "v24.22.2")
            dsh_dir = Path(tmp) / "v20/bin"
            _write_exe(dsh_dir / "dsh")
            command = LaunchCommand((str(dsh_dir / "dsh"),), str(dsh_dir), "PATH")

            resolved = child_path(command, {"PATH": str(system_bin)}, home=home)
            self.assertEqual(resolved.split(os.pathsep)[0], str(newer))
            self.assertTrue(resolved.endswith(str(system_bin)))

    def test_falls_back_to_launcher_bin_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dsh_dir = Path(tmp) / "bin"
            self._node(dsh_dir, "v24.9.0")
            _write_exe(dsh_dir / "dsh")
            command = LaunchCommand((str(dsh_dir / "dsh"),), str(dsh_dir), "PATH")
            empty_path = Path(tmp) / "emptybin"
            empty_path.mkdir()
            home = Path(tmp) / "home"
            home.mkdir()
            resolved = child_path(command, {"PATH": str(empty_path)}, home=home)
            self.assertEqual(resolved.split(os.pathsep)[0], str(dsh_dir))

    def test_leaves_unsupported_path_when_nothing_better_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            system_bin = Path(tmp) / "usr/bin"
            self._node(system_bin, "v22.0.0")
            command = LaunchCommand(("dsh",), None, "PATH")
            home = Path(tmp) / "home"
            home.mkdir()
            self.assertEqual(
                child_path(command, {"PATH": str(system_bin)}, home=home),
                str(system_bin),
            )


class NodeVersionTests(unittest.TestCase):
    def test_supported_versions(self) -> None:
        self.assertTrue(node_satisfies("v24.9.0"))
        self.assertTrue(node_satisfies("v25.1.0"))
        self.assertTrue(node_satisfies("v24.22.2"))

    def test_unsupported_versions(self) -> None:
        self.assertFalse(node_satisfies("v20.20.2"))
        self.assertFalse(node_satisfies("v24.8.9"))
        self.assertFalse(node_satisfies("v22.0.0"))

    def test_unknown_version_is_not_flagged(self) -> None:
        self.assertTrue(node_satisfies(None))
        self.assertTrue(node_satisfies("garbage"))

    def test_candidate_node_dirs_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            old = home / ".nvm/versions/node/v18.0.0/bin"
            new = home / ".nvm/versions/node/v24.0.0/bin"
            _write_exe(old / "node")
            _write_exe(new / "node")
            self.assertEqual(candidate_node_dirs(home)[0], new)


class LaunchCommandDescribeTests(unittest.TestCase):
    def test_describe_quotes_spaces(self) -> None:
        command = LaunchCommand(("npx", "-y", "@deepseek-ai/dsh"), None, "npx")
        self.assertEqual(command.describe(), "npx -y @deepseek-ai/dsh")


if __name__ == "__main__":
    unittest.main()


class SymlinkTests(unittest.TestCase):
    """A package manager installs ``bin/dsh`` as a symlink into node_modules.

    The shim is what a normal launch runs, so discovery must report it rather
    than the file it points at.
    """

    def test_candidate_paths_keeps_the_shim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = _write_exe(home / "lib/node_modules/@deepseek-ai/dsh/lib/bin.js")
            shim = home / ".nvm/versions/node/v24.9.0/bin/dsh"
            shim.parent.mkdir(parents=True)
            shim.symlink_to(target)
            self.assertEqual(candidate_paths(home), [shim])

    def test_deduplicates_two_shims_for_one_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            target = _write_exe(home / "lib/bin.js")
            first = home / ".nvm/versions/node/v24.9.0/bin/dsh"
            first.parent.mkdir(parents=True)
            first.symlink_to(target)
            second = home / ".local/bin/dsh"
            second.parent.mkdir(parents=True)
            second.symlink_to(target)
            self.assertEqual(candidate_paths(home), [first])
