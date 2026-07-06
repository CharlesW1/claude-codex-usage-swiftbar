import unittest
from claude_usage import (
    severity_color, format_duration, format_countdown, GREEN, ORANGE, RED,
)


class TestSeverityColor(unittest.TestCase):
    def test_below_70_is_green(self):
        self.assertEqual(severity_color(0), GREEN)
        self.assertEqual(severity_color(69.9), GREEN)

    def test_70_to_90_is_orange(self):
        self.assertEqual(severity_color(70), ORANGE)
        self.assertEqual(severity_color(90), ORANGE)

    def test_above_90_is_red(self):
        self.assertEqual(severity_color(90.1), RED)
        self.assertEqual(severity_color(100), RED)


class TestFormatDuration(unittest.TestCase):
    def test_hours_and_minutes_compact(self):
        self.assertEqual(format_duration(3 * 3600 + 12 * 60, compact=True), "3h12m")

    def test_hours_and_minutes_spaced(self):
        self.assertEqual(format_duration(3 * 3600 + 12 * 60, compact=False), "3h 12m")

    def test_minutes_only(self):
        self.assertEqual(format_duration(12 * 60 + 59, compact=True), "12m")

    def test_zero_or_negative_is_now(self):
        self.assertEqual(format_duration(0, compact=True), "now")
        self.assertEqual(format_duration(-5, compact=False), "now")


class TestFormatCountdown(unittest.TestCase):
    def test_days_and_hours(self):
        self.assertEqual(format_countdown(2 * 86400 + 1 * 3600 + 59 * 60), "2d 1h")

    def test_hours_and_minutes(self):
        self.assertEqual(format_countdown(1 * 3600 + 35 * 60 + 40), "1h 35m")

    def test_minutes_and_seconds(self):
        self.assertEqual(format_countdown(35 * 60 + 8), "35m 8s")

    def test_seconds_only(self):
        self.assertEqual(format_countdown(8), "8s")

    def test_negative_clamps_to_zero(self):
        self.assertEqual(format_countdown(-10), "0s")


if __name__ == "__main__":
    unittest.main()
