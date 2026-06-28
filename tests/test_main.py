import os
import tempfile
import unittest
from datetime import datetime, timezone
import claude_usage
from claude_usage import build_output, UsageError

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)


class TestBuildOutput(unittest.TestCase):
    def setUp(self):
        self._orig_token = claude_usage.read_token
        self._orig_fetch = claude_usage.fetch_usage
        # Redirect the cache off the real ~/.cache so tests stay hermetic.
        self._tmp = tempfile.mkdtemp()
        self._orig_cache = claude_usage.CACHE_PATH
        claude_usage.CACHE_PATH = os.path.join(self._tmp, "last.json")

    def tearDown(self):
        claude_usage.read_token = self._orig_token
        claude_usage.fetch_usage = self._orig_fetch
        claude_usage.CACHE_PATH = self._orig_cache

    def test_success_path(self):
        claude_usage.read_token = lambda: "tok"
        claude_usage.fetch_usage = lambda t: {
            "five_hour": {"utilization": 46.0, "resets_at": "2026-06-28T10:30:00+00:00"},
            "seven_day": {"utilization": 23.0, "resets_at": "2026-07-04T12:00:00+00:00"},
        }
        out = build_output(NOW)
        self.assertEqual(out.splitlines()[0], "46% · 3h12m | sfimage=gauge.medium color=#34c759")

    def test_no_token_renders_error(self):
        def boom():
            raise UsageError("no_token")
        claude_usage.read_token = boom
        claude_usage.fetch_usage = lambda t: {}
        out = build_output(NOW)
        self.assertIn("Keychain access denied", out)

    def test_auth_error_renders_error(self):
        claude_usage.read_token = lambda: "tok"
        def boom(t):
            raise UsageError("auth")
        claude_usage.fetch_usage = boom
        out = build_output(NOW)
        self.assertIn("Open Claude Code to refresh", out)


if __name__ == "__main__":
    unittest.main()
