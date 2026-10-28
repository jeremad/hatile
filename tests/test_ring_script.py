"""Tests for the command line of scripts/ring.py."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import unittest
from pathlib import Path

# Load the script by path: scripts is not a package.
_PATH = Path(__file__).parents[1] / "scripts" / "ring.py"
_SPEC = importlib.util.spec_from_file_location("ring", _PATH)
ring = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ring)

ADDRESS = "AA:BB:CC:DD:EE:FF"


class ArgumentsTest(unittest.TestCase):
    def test_accepts_the_limits(self) -> None:
        args = ring.parse_args([ADDRESS, "--strength", "1", "--duration", "255"])
        self.assertEqual((args.strength, args.duration), (1, 255))

    def test_rejects_out_of_range_values(self) -> None:
        for option, value in (
            ("--strength", "0"),
            ("--strength", "4"),
            ("--duration", "0"),
            ("--duration", "256"),
            ("--duration", "ten"),
        ):
            with (
                self.subTest(option=option, value=value),
                contextlib.redirect_stderr(io.StringIO()),
                self.assertRaises(SystemExit) as cm,
            ):
                ring.parse_args([ADDRESS, option, value])
            self.assertEqual(cm.exception.code, 2)
