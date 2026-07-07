import os
import tempfile
import unittest
from datetime import datetime, timezone
import claude_usage
from claude_usage import (
    Usage, CodexUsage, UsageError, cache_save, cache_load,
    codex_cache_save, codex_cache_load, build_output,
)

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
U = Usage(46.0, "2026-06-28T10:30:00+00:00", 23.0, "2026-07-04T12:00:00+00:00")
CX = CodexUsage(50.0, "2026-06-28T09:00:00+00:00", 28.0, "2026-07-04T12:00:00+00:00")


class CacheBase(unittest.TestCase):
    def setUp(self):
        self._orig = {}
        for name in ("current_token", "fetch_usage", "read_codex_creds",
                     "fetch_codex", "render_menubar_image", "CACHE_PATH",
                     "CODEX_CACHE_PATH", "BOOST_UNTIL_PATH"):
            self._orig[name] = getattr(claude_usage, name)
        tmp = tempfile.mkdtemp()
        claude_usage.CACHE_PATH = os.path.join(tmp, "claude.json")
        claude_usage.CODEX_CACHE_PATH = os.path.join(tmp, "codex.json")
        claude_usage.BOOST_UNTIL_PATH = os.path.join(tmp, "boost")
        claude_usage.render_menubar_image = lambda lines: None

    def tearDown(self):
        for name, val in self._orig.items():
            setattr(claude_usage, name, val)


class TestCacheRoundTrip(CacheBase):
    def test_claude_round_trip(self):
        self.assertIsNone(cache_load())
        cache_save(U)
        self.assertEqual(cache_load(), U)

    def test_codex_round_trip(self):
        self.assertIsNone(codex_cache_load())
        codex_cache_save(CX)
        self.assertEqual(codex_cache_load(), CX)

    def test_claude_round_trip_preserves_fable(self):
        u = Usage(46.0, "2026-06-28T10:30:00+00:00", 23.0,
                  "2026-07-04T12:00:00+00:00",
                  fable_pct=40.0, fable_resets_at="2026-07-11T12:00:00+00:00")
        cache_save(u)
        self.assertEqual(cache_load(), u)

    def test_old_cache_without_fable_keys_loads_as_none(self):
        import json
        with open(claude_usage.CACHE_PATH, "w") as f:
            json.dump({"session_pct": 46.0, "session_resets_at": None,
                       "weekly_pct": 23.0, "weekly_resets_at": None}, f)
        loaded = cache_load()
        self.assertEqual(loaded.session_pct, 46.0)
        self.assertIsNone(loaded.fable_pct)
        self.assertIsNone(loaded.fable_resets_at)


class TestTransientFallback(CacheBase):
    def test_claude_429_falls_back_to_cache_marked_stale(self):
        cache_save(U)
        claude_usage.current_token = lambda now_ms, force=False: "tok"
        def boom(t):
            raise UsageError("bad_response", "HTTP 429")
        claude_usage.fetch_usage = boom
        claude_usage.read_codex_creds = lambda: {"access_token": "x", "account_id": "a"}
        claude_usage.fetch_codex = lambda t, a: {
            "rate_limit": {"primary_window": {"used_percent": 50, "reset_at": 1782629880},
                           "secondary_window": {"used_percent": 28, "reset_at": 1783917148}}}
        out = build_output(NOW, interval_s=300)
        self.assertIn("5-hour  46%", out)   # served from cache
        self.assertIn("last reading", out)        # marked stale

    def test_codex_offline_falls_back_to_cache(self):
        codex_cache_save(CX)
        claude_usage.current_token = lambda now_ms, force=False: "tok"
        claude_usage.fetch_usage = lambda t: {
            "five_hour": {"utilization": 46.0, "resets_at": "2026-06-28T10:30:00+00:00"},
            "seven_day": {"utilization": 23.0, "resets_at": "2026-07-04T12:00:00+00:00"}}
        claude_usage.read_codex_creds = lambda: {"access_token": "x", "account_id": "a"}
        def boom(t, a):
            raise UsageError("offline", "down")
        claude_usage.fetch_codex = boom
        out = build_output(NOW, interval_s=300)
        self.assertIn("5-hour  50%", out)         # codex served from cache
        self.assertIn("last reading", out)


if __name__ == "__main__":
    unittest.main()
