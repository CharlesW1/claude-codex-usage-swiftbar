import os
import tempfile
import unittest
from datetime import datetime, timezone
import claude_usage
from claude_usage import build_output, UsageError

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)

CLAUDE_GOOD = {
    "five_hour": {"utilization": 46.0, "resets_at": "2026-06-28T10:30:00+00:00"},
    "seven_day": {"utilization": 23.0, "resets_at": "2026-07-04T12:00:00+00:00"},
}
CODEX_GOOD = {
    "rate_limit": {
        "primary_window": {"used_percent": 50, "reset_at": 1782629880},
        "secondary_window": {"used_percent": 28, "reset_at": 1783917148},
    },
}


class BuildBase(unittest.TestCase):
    def setUp(self):
        self._orig = {}
        for name in ("current_token", "fetch_usage", "read_codex_creds",
                     "fetch_codex", "render_menubar_image", "CACHE_PATH",
                     "CODEX_CACHE_PATH"):
            self._orig[name] = getattr(claude_usage, name)
        tmp = tempfile.mkdtemp()
        claude_usage.CACHE_PATH = os.path.join(tmp, "claude.json")
        claude_usage.CODEX_CACHE_PATH = os.path.join(tmp, "codex.json")
        # Force text fallback (no Swift) so the menu-bar line is deterministic.
        claude_usage.render_menubar_image = lambda lines: None

    def tearDown(self):
        for name, val in self._orig.items():
            setattr(claude_usage, name, val)


class TestBothProviders(BuildBase):
    def test_success_shows_both(self):
        claude_usage.current_token = lambda now_ms, force=False: "tok"
        claude_usage.fetch_usage = lambda t: CLAUDE_GOOD
        claude_usage.read_codex_creds = lambda: {"access_token": "x", "account_id": "a"}
        claude_usage.fetch_codex = lambda t, a: CODEX_GOOD
        out = build_output(NOW, interval_s=300)
        first = out.splitlines()[0]
        self.assertIn("C 46%", first)
        self.assertIn("Cx 50%", first)
        self.assertIn("↻", first)
        self.assertIn("Session (5h)  46%", out)
        self.assertIn("5-hour  50%", out)

    def test_claude_locked_codex_ok(self):
        def boom(now_ms, force=False):
            raise UsageError("no_token")
        claude_usage.current_token = boom
        claude_usage.fetch_usage = lambda t: CLAUDE_GOOD
        claude_usage.read_codex_creds = lambda: {"access_token": "x", "account_id": "a"}
        claude_usage.fetch_codex = lambda t, a: CODEX_GOOD
        out = build_output(NOW, interval_s=300)
        self.assertIn("C —", out.splitlines()[0])
        self.assertIn("Keychain locked", out)
        self.assertIn("5-hour  50%", out)

    def test_claude_auth_after_forced_refresh_still_fails(self):
        claude_usage.current_token = lambda now_ms, force=False: "tok"
        def boom(t):
            raise UsageError("auth")
        claude_usage.fetch_usage = boom
        claude_usage.read_codex_creds = lambda: {"access_token": "x", "account_id": "a"}
        claude_usage.fetch_codex = lambda t, a: CODEX_GOOD
        out = build_output(NOW, interval_s=300)
        self.assertIn("auth expired — open Claude Code", out)

    def test_codex_signed_out_note(self):
        claude_usage.current_token = lambda now_ms, force=False: "tok"
        claude_usage.fetch_usage = lambda t: CLAUDE_GOOD
        def boom():
            raise UsageError("no_token")
        claude_usage.read_codex_creds = boom
        out = build_output(NOW, interval_s=300)
        self.assertIn("Cx —", out.splitlines()[0])
        self.assertIn("codex login", out)


if __name__ == "__main__":
    unittest.main()
