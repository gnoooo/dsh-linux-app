"""Tests for argument parsing and pass-through to `dsh web`."""

from __future__ import annotations

import unittest

from dsh_desktop.cli import _parse


class ForwardingTests(unittest.TestCase):
    """Unknown options must reach `dsh web`, so an embedding launcher can add
    launcher flags (e.g. ``--patch``) without this wrapper knowing them."""

    def test_known_options_are_consumed(self) -> None:
        args, forwarded = _parse(["--port", "8080", "--devtools"])
        self.assertEqual(args.port, 8080)
        self.assertTrue(args.devtools)
        self.assertEqual(forwarded, ())

    def test_unknown_option_is_forwarded(self) -> None:
        args, forwarded = _parse(["--patch", "/tmp/overlay.yml"])
        self.assertEqual(forwarded, ("--patch", "/tmp/overlay.yml"))

    def test_unknown_and_known_mix(self) -> None:
        args, forwarded = _parse(["--patch", "x.yml", "--port", "9"])
        self.assertEqual(args.port, 9)
        self.assertEqual(forwarded, ("--patch", "x.yml"))

    def test_double_dash_forwarding(self) -> None:
        args, forwarded = _parse(["--", "--patch", "x.yml"])
        self.assertEqual(forwarded, ("--patch", "x.yml"))

    def test_positional_after_double_dash(self) -> None:
        _, forwarded = _parse(["--port", "1", "--", "--host", "10.0.0.7"])
        self.assertEqual(forwarded, ("--host", "10.0.0.7"))


if __name__ == "__main__":
    unittest.main()
