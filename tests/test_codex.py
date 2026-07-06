import unittest
from datetime import datetime, timezone
from claude_usage import parse_codex, CodexUsage, UsageError, time_until

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

    def test_bad_payload_raises(self):
        with self.assertRaises(UsageError) as ctx:
            parse_codex({"nope": 1})
        self.assertEqual(ctx.exception.kind, "bad_response")


if __name__ == "__main__":
    unittest.main()
