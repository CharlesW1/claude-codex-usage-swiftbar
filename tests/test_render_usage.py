import unittest
from datetime import datetime, timezone
from claude_usage import render_usage, Usage

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
U = Usage(
    session_pct=46.0, session_resets_at="2026-06-28T10:30:00+00:00",
    weekly_pct=23.0, weekly_resets_at="2026-07-04T12:00:00+00:00",
)


class TestRenderUsage(unittest.TestCase):
    def test_menu_bar_line(self):
        first = render_usage(U, NOW).splitlines()[0]
        self.assertEqual(first, "46% · 3h12m | sfimage=gauge.medium color=#34c759")

    def test_has_session_and_weekly_lines(self):
        out = render_usage(U, NOW)
        self.assertIn("Session (5h)  46%  ·  resets in 3h 12m | color=#34c759", out)
        self.assertIn("Weekly  23%  ·  resets Sat 4 Jul | color=#34c759", out)

    def test_has_refresh_and_hint(self):
        out = render_usage(U, NOW)
        self.assertIn("Refresh | refresh=true", out)
        self.assertIn("Run /usage in Claude Code for full details", out)

    def test_high_session_is_red_in_bar(self):
        u = Usage(95.0, U.session_resets_at, 23.0, U.weekly_resets_at)
        self.assertIn("color=#ff3b30", render_usage(u, NOW).splitlines()[0])


if __name__ == "__main__":
    unittest.main()
