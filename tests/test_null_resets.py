import unittest
from claude_usage import parse_usage, Usage

# What the Claude endpoint returns when there is no active session window.
NULL_RESETS = {
    "five_hour": {"utilization": 0.0, "resets_at": None},
    "seven_day": {"utilization": 34.0, "resets_at": "2026-07-04T12:00:00+00:00"},
}


class TestNullResets(unittest.TestCase):
    def test_parse_keeps_none_not_string(self):
        u = parse_usage(NULL_RESETS)
        self.assertIsNone(u.session_resets_at)
        self.assertEqual(u.session_pct, 0.0)
        self.assertIsInstance(u, Usage)


if __name__ == "__main__":
    unittest.main()
