"""Tests for configuration loading and overrides."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from dsh_desktop.config import ConfigError, Settings, apply_overrides, load_settings


class LoadSettingsTests(unittest.TestCase):
    def _load(self, toml_text: str, environ: dict[str, str] | None = None) -> Settings:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(toml_text)
            env = {"XDG_CONFIG_HOME": tmp}
            env.update(environ or {})
            return load_settings(path, env)

    def test_missing_file_yields_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = load_settings(Path(tmp) / "absent.toml", {"XDG_CONFIG_HOME": tmp})
        self.assertEqual(settings.port, 0)
        self.assertIsNone(settings.command)
        self.assertEqual(settings.extra_args, ())
        self.assertTrue(settings.startup_timeout > 0)

    def test_parses_every_supported_key(self) -> None:
        settings = self._load(
            """
            command = ["npx", "-y", "@deepseek-ai/dsh"]
            args = ["--verbose"]
            host = "127.0.0.1"
            port = 8080
            workspace = "/tmp"
            startup_timeout = 30
            devtools = true
            zoom = 1.25
            """
        )
        self.assertEqual(settings.command, ("npx", "-y", "@deepseek-ai/dsh"))
        self.assertEqual(settings.extra_args, ("--verbose",))
        self.assertEqual(settings.host, "127.0.0.1")
        self.assertEqual(settings.port, 8080)
        self.assertEqual(settings.workspace, Path("/tmp"))
        self.assertEqual(settings.startup_timeout, 30.0)
        self.assertTrue(settings.devtools)
        self.assertAlmostEqual(settings.zoom, 1.25)

    def test_command_accepts_a_shell_quoted_string(self) -> None:
        settings = self._load('command = "npx -y @deepseek-ai/dsh"')
        self.assertEqual(settings.command, ("npx", "-y", "@deepseek-ai/dsh"))

    def test_unknown_keys_are_ignored(self) -> None:
        settings = self._load("future_option = 42\nport = 1234\n")
        self.assertEqual(settings.port, 1234)

    def test_invalid_port_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            self._load("port = 70000")

    def test_non_integer_port_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            self._load('port = "not-a-number"')

    def test_empty_command_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            self._load("command = []")

    def test_malformed_toml_is_reported_with_path(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            self._load("port = [unclosed")
        self.assertIn("config.toml", str(caught.exception))

    def test_environment_overrides_file(self) -> None:
        settings = self._load(
            "port = 1111\ndevtools = false\n",
            {"DSH_DESKTOP_PORT": "2222", "DSH_DESKTOP_DEVTOOLS": "1"},
        )
        self.assertEqual(settings.port, 2222)
        self.assertTrue(settings.devtools)

    def test_environment_command_is_shlex_split(self) -> None:
        settings = load_settings(
            Path("/nonexistent/config.toml"),
            {"DSH_DESKTOP_COMMAND": "npx -y @deepseek-ai/dsh"},
        )
        self.assertEqual(settings.command, ("npx", "-y", "@deepseek-ai/dsh"))

    def test_empty_environment_value_is_ignored(self) -> None:
        settings = load_settings(
            Path("/nonexistent/config.toml"), {"DSH_DESKTOP_PORT": ""}
        )
        self.assertEqual(settings.port, 0)

    def test_bad_boolean_in_environment_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            load_settings(Path("/nonexistent/config.toml"), {"DSH_DESKTOP_DEVTOOLS": "maybe"})

    def test_config_path_from_environment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "custom.toml"
            path.write_text("port = 3333\n")
            settings = load_settings(None, {"DSH_DESKTOP_CONFIG": str(path)})
        self.assertEqual(settings.port, 3333)


class ApplyOverridesTests(unittest.TestCase):
    def test_none_values_are_ignored(self) -> None:
        base = Settings(port=1234, host="127.0.0.1")
        self.assertEqual(apply_overrides(base, port=None, host=None), base)

    def test_command_string_becomes_argv(self) -> None:
        settings = apply_overrides(Settings(), command="npx -y @deepseek-ai/dsh")
        self.assertEqual(settings.command, ("npx", "-y", "@deepseek-ai/dsh"))

    def test_workspace_is_expanded(self) -> None:
        settings = apply_overrides(Settings(), workspace="~")
        self.assertEqual(settings.workspace, Path.home())

    def test_validation_still_applies(self) -> None:
        with self.assertRaises(ConfigError):
            apply_overrides(Settings(), port=-1)

    def test_extra_args_override(self) -> None:
        settings = apply_overrides(Settings(), extra_args=("--verbose",))
        self.assertEqual(settings.extra_args, ("--verbose",))


if __name__ == "__main__":
    unittest.main()
