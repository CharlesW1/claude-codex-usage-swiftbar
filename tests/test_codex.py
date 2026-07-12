import unittest
from datetime import datetime, timezone
from claude_usage import (parse_codex, window_label, CodexUsage, UsageError,
                          time_until)

# Real shape from chatgpt.com/backend-api/wham/usage
SAMPLE = {
    "plan_type": "team",
    "rate_limit": {
        "allowed": True,
        "limit_reached": False,
        "primary_window": {
            "used_percent": 50,
            "limit_window_seconds": 18000,
            "reset_after_seconds": 7001,
            "reset_at": 1783374804,
        },
        "secondary_window": {
            "used_percent": 28,
            "limit_window_seconds": 604800,
            "reset_after_seconds": 549345,
            "reset_at": 1783917148,
        },
    },
}


class TestParseCodex(unittest.TestCase):
    def test_extracts_used_percent(self):
        c = parse_codex(SAMPLE)
        self.assertIsInstance(c, CodexUsage)
        self.assertEqual(c.primary_pct, 50.0)
        self.assertEqual(c.secondary_pct, 28.0)

    def test_converts_reset_at_epoch_to_iso(self):
        c = parse_codex(SAMPLE)
        # 1783374804 is a real UTC instant; time_until must parse it back
        now = datetime.fromtimestamp(1783374804 - 3600, tz=timezone.utc)
        self.assertAlmostEqual(time_until(c.primary_resets_at, now), 3600, delta=1)

    def test_missing_reset_is_none(self):
        data = {"rate_limit": {
            "primary_window": {"used_percent": 0, "reset_at": None},
            "secondary_window": {"used_percent": 5, "reset_at": 0},
        }}
        c = parse_codex(data)
        self.assertIsNone(c.primary_resets_at)
        self.assertIsNone(c.secondary_resets_at)
        self.assertEqual(c.primary_pct, 0.0)

    def test_null_secondary_window_means_absent(self):
        # Codex returns a present-but-null window when that tier has no limit
        # (mid-2026 they dropped the 5h window). Absent = None, not fake 0%.
        data = {"rate_limit": {
            "primary_window": {"used_percent": 6, "reset_at": 1784489356,
                               "limit_window_seconds": 604800},
            "secondary_window": None,
        }}
        c = parse_codex(data)
        self.assertEqual(c.primary_pct, 6.0)
        self.assertEqual(c.primary_window_s, 604800)
        self.assertIsNone(c.secondary_pct)
        self.assertIsNone(c.secondary_resets_at)

    def test_both_windows_null_means_both_absent(self):
        c = parse_codex({"rate_limit":
                         {"primary_window": None, "secondary_window": None}})
        self.assertIsNone(c.primary_pct)
        self.assertIsNone(c.secondary_pct)
        self.assertIsNone(c.primary_resets_at)
        self.assertIsNone(c.secondary_resets_at)

    def test_window_durations_captured_from_payload(self):
        c = parse_codex(SAMPLE)  # 18000s primary, 604800s secondary
        self.assertEqual(c.primary_window_s, 18000)
        self.assertEqual(c.secondary_window_s, 604800)

    def test_bad_payload_raises(self):
        with self.assertRaises(UsageError) as ctx:
            parse_codex({"nope": 1})
        self.assertEqual(ctx.exception.kind, "bad_response")


class TestWindowLabel(unittest.TestCase):
    def test_known_durations(self):
        self.assertEqual(window_label(18000, "x"), "5-hour")
        self.assertEqual(window_label(604800, "x"), "Weekly")
        self.assertEqual(window_label(86400, "x"), "Daily")

    def test_unknown_duration_uses_legacy_default(self):
        # Old caches / payloads without limit_window_seconds keep the
        # historical labels so nothing changes for them.
        self.assertEqual(window_label(None, "5-hour"), "5-hour")
        self.assertEqual(window_label(0, "Weekly"), "Weekly")


if __name__ == "__main__":
    unittest.main()
