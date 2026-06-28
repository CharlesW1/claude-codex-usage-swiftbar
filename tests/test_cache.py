import os
import tempfile
import unittest
from datetime import datetime, timezone
import claude_usage
from claude_usage import Usage, UsageError, cache_save, cache_load, build_output

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
U = Usage(
    session_pct=46.0, session_resets_at="2026-06-28T10:30:00+00:00",
    weekly_pct=23.0, weekly_resets_at="2026-07-04T12:00:00+00:00",
)


class CacheTestBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig_cache = claude_usage.CACHE_PATH
        claude_usage.CACHE_PATH = os.path.join(self._tmp, "last.json")
        self._orig_token = claude_usage.read_token
        self._orig_fetch = claude_usage.fetch_usage

    def tearDown(self):
        claude_usage.CACHE_PATH = self._orig_cache
        claude_usage.read_token = self._orig_token
        claude_usage.fetch_usage = self._orig_fetch


class TestCacheRoundTrip(CacheTestBase):
    def test_save_then_load(self):
        self.assertIsNone(cache_load())
        cache_save(U)
        loaded = cache_load()
        self.assertEqual(loaded, U)


class TestBuildOutputFallback(CacheTestBase):
    def test_429_falls_back_to_cached_value(self):
        cache_save(U)
        claude_usage.read_token = lambda: "tok"
        def boom(t):
            raise UsageError("bad_response", "HTTP 429")
        claude_usage.fetch_usage = boom
        out = build_output(NOW)
        # shows the cached percentage, not the scary "?"
        self.assertIn("46%", out.splitlines()[0])
        self.assertIn("last reading", out)

    def test_no_cache_429_shows_error(self):
        claude_usage.read_token = lambda: "tok"
        def boom(t):
            raise UsageError("bad_response", "HTTP 429")
        claude_usage.fetch_usage = boom
        out = build_output(NOW)
        self.assertTrue(out.splitlines()[0].startswith("?"))

    def test_auth_error_never_uses_cache(self):
        cache_save(U)
        claude_usage.read_token = lambda: "tok"
        def boom(t):
            raise UsageError("auth")
        claude_usage.fetch_usage = boom
        out = build_output(NOW)
        self.assertIn("Open Claude Code to refresh", out)


if __name__ == "__main__":
    unittest.main()
