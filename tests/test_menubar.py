import unittest
from datetime import datetime, timezone
from claude_usage import (
    menubar_lines, next_check_label, Usage, CodexUsage, GREEN, RED, GRAY,
)

CLAUDE = Usage(11.0, "2026-06-28T10:30:00+00:00", 43.0, "2026-07-04T12:00:00+00:00")
CODEX = CodexUsage(95.0, "2026-06-28T09:00:00+00:00", 28.0, "2026-07-04T12:00:00+00:00")


class TestMenubarLines(unittest.TestCase):
    def test_two_lines_labeled_and_colored(self):
        lines = menubar_lines(CLAUDE, CODEX)
        self.assertEqual(lines[0], ("C 11%", GREEN))
        self.assertEqual(lines[1], ("Cx 95%", RED))

    def test_missing_provider_shows_dash_gray(self):
        lines = menubar_lines(CLAUDE, None)
        self.assertEqual(lines[0], ("C 11%", GREEN))
        self.assertEqual(lines[1], ("Cx —", GRAY))

    def test_both_missing(self):
        lines = menubar_lines(None, None)
        self.assertEqual(lines[0][0], "C —")
        self.assertEqual(lines[1][0], "Cx —")


class TestNextCheckLabel(unittest.TestCase):
    def test_shows_local_clock_time_of_next_run(self):
        now = datetime(2026, 6, 28, 10, 19, 0, tzinfo=timezone.utc)
        label = next_check_label(now, 300)
        # 5 minutes after now, formatted as H:MM (local tz); just assert shape
        self.assertRegex(label, r"↻ \d{1,2}:\d{2}")


if __name__ == "__main__":
    unittest.main()
