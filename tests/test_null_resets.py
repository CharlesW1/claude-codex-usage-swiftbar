import unittest
from datetime import datetime, timezone
from claude_usage import parse_usage, render_usage, Usage

NOW = datetime(2026, 6, 30, 17, 0, 0, tzinfo=timezone.utc)

# What the endpoint returns when there is no active session window.
NULL_RESETS = {
    "five_hour": {"utilization": 0.0, "resets_at": None},
    "seven_day": {"utilization": 34.0, "resets_at": "2026-07-04T12:00:00+00:00"},
}


class TestNullResets(unittest.TestCase):
    def test_parse_keeps_none_not_string(self):
        u = parse_usage(NULL_RESETS)
        self.assertIsNone(u.session_resets_at)
        self.assertEqual(u.session_pct, 0.0)

    def test_render_does_not_crash_on_null_reset(self):
        u = parse_usage(NULL_RESETS)
        out = render_usage(u, NOW)  # must not raise
        self.assertEqual(out.splitlines()[0].split(" | ")[0], "0%")
        self.assertIn("Session (5h)  0%", out)
        self.assertIn("Weekly  34%  ·  resets Sat 4 Jul", out)

    def test_literal_none_string_is_treated_as_missing(self):
        # a stale cache file may hold the old "None" string
        u = Usage(0.0, "None", 34.0, "2026-07-04T12:00:00+00:00")
        out = render_usage(u, NOW)  # must not raise
        self.assertEqual(out.splitlines()[0].split(" | ")[0], "0%")


if __name__ == "__main__":
    unittest.main()
