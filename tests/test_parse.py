import unittest
from datetime import datetime, timezone
from claude_usage import parse_usage, time_until, Usage, UsageError

GOOD = {
    "five_hour": {"utilization": 46.0, "resets_at": "2026-06-28T10:30:00.673002+00:00"},
    "seven_day": {"utilization": 23.0, "resets_at": "2026-07-04T12:00:00+00:00"},
}


class TestParseUsage(unittest.TestCase):
    def test_parses_good_payload(self):
        u = parse_usage(GOOD)
        self.assertEqual(u.session_pct, 46.0)
        self.assertEqual(u.weekly_pct, 23.0)
        self.assertEqual(u.session_resets_at, "2026-06-28T10:30:00.673002+00:00")

    def test_missing_key_raises_bad_response(self):
        with self.assertRaises(UsageError) as ctx:
            parse_usage({"five_hour": {"utilization": 1.0}})  # no resets_at / seven_day
        self.assertEqual(ctx.exception.kind, "bad_response")


class TestTimeMath(unittest.TestCase):
    def test_time_until_seconds(self):
        now = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
        secs = time_until("2026-06-28T10:30:00+00:00", now)
        self.assertEqual(int(secs), 3 * 3600 + 12 * 60)

    def test_time_until_accepts_z_suffix(self):
        now = datetime(2026, 6, 28, 10, 0, 0, tzinfo=timezone.utc)
        secs = time_until("2026-06-28T10:30:00Z", now)
        self.assertEqual(int(secs), 30 * 60)


if __name__ == "__main__":
    unittest.main()
