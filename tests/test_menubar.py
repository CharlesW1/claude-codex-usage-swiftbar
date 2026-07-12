import unittest
from datetime import datetime, timezone
from claude_usage import (
    menubar_rows, timer_color, next_check_label, filter_menubar_rows,
    Usage, CodexUsage, WHITE, GREEN, ORANGE, RED, GRAY,
)

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
# Claude 5h resets 10:30 (3h12m out); Codex 5h resets 09:00 (1h42m out).
CLAUDE = Usage(11.0, "2026-06-28T10:30:00+00:00", 43.0, "2026-07-04T12:00:00+00:00")
CODEX = CodexUsage(95.0, "2026-06-28T09:00:00+00:00", 28.0, "2026-07-04T12:00:00+00:00")


class TestTimerColor(unittest.TestCase):
    def test_imminent_is_white(self):
        self.assertEqual(timer_color(10 * 60), WHITE)      # 10m left (best)
    def test_soon_is_green(self):
        self.assertEqual(timer_color(30 * 60), GREEN)      # 30m left
    def test_mid_is_orange(self):
        self.assertEqual(timer_color(2 * 3600), ORANGE)    # 2h left
    def test_far_is_red(self):
        self.assertEqual(timer_color(4 * 3600), RED)       # 4h left

    # A weekly window must scale its tiers (fractions of the window), not
    # reuse the 5-hour absolutes — otherwise it sits red for ~7 days straight.
    def test_weekly_window_scales_tiers(self):
        wk = 7 * 86400
        self.assertEqual(timer_color(6 * 3600, wk), WHITE)    # 6h left ≈ 3.6%
        self.assertEqual(timer_color(1 * 86400, wk), GREEN)   # 1d ≈ 14%
        self.assertEqual(timer_color(3 * 86400, wk), ORANGE)  # 3d ≈ 43%
        self.assertEqual(timer_color(6 * 86400, wk), RED)     # 6d ≈ 86%

    def test_default_window_matches_legacy_5h_behavior(self):
        for secs in (10 * 60, 30 * 60, 2 * 3600, 4 * 3600):
            self.assertEqual(timer_color(secs), timer_color(secs, 18000))


class TestMenubarRows(unittest.TestCase):
    def test_value_and_timer_colored_independently(self):
        rows = menubar_rows(CLAUDE, CODEX, NOW)
        c, x = rows
        # Claude: 11% used -> white value (best); 3h12m left -> red timer
        self.assertEqual((c.label, c.value, c.value_color), ("C", "11%", WHITE))
        self.assertEqual((c.timer, c.timer_color), ("3h12m", RED))
        # Codex: 95% used -> red value; 1h42m left -> orange timer
        self.assertEqual((x.label, x.value, x.value_color), ("Cx", "95%", RED))
        self.assertEqual((x.timer, x.timer_color), ("1h42m", ORANGE))

    def test_missing_reset_has_empty_timer(self):
        claude = Usage(0.0, None, 43.0, "2026-07-04T12:00:00+00:00")
        c = menubar_rows(claude, CODEX, NOW)[0]
        self.assertEqual(c.value, "0%")
        self.assertEqual(c.timer, "")

    def test_missing_provider_is_dash(self):
        rows = menubar_rows(CLAUDE, None, NOW)
        self.assertEqual((rows[1].label, rows[1].value, rows[1].value_color),
                         ("Cx", "—", GRAY))

    def test_filter_can_show_only_claude(self):
        rows = filter_menubar_rows(menubar_rows(CLAUDE, CODEX, NOW), "claude")
        self.assertEqual([r.label for r in rows], ["C"])

    def test_filter_can_show_only_codex(self):
        rows = filter_menubar_rows(menubar_rows(CLAUDE, CODEX, NOW), "codex")
        self.assertEqual([r.label for r in rows], ["Cx"])

    def test_filter_defaults_to_both(self):
        rows = filter_menubar_rows(menubar_rows(CLAUDE, CODEX, NOW), "both")
        self.assertEqual([r.label for r in rows], ["C", "Cx"])

    def test_codex_weekly_row_uses_weekly_tiers(self):
        # Weekly-only Codex (2026 shape), resets in ~1d: green under weekly
        # tiers, would be red under the 5-hour absolutes.
        cx = CodexUsage(11.0, "2026-06-29T07:18:00+00:00", None, None,
                        primary_window_s=7 * 86400)
        x = menubar_rows(CLAUDE, cx, NOW)[1]
        self.assertEqual(x.timer_color, GREEN)


class TestNextCheckLabel(unittest.TestCase):
    def test_shows_local_clock_time_of_next_run(self):
        label = next_check_label(NOW, 300)
        self.assertRegex(label, r"↻ \d{1,2}:\d{2}:\d{2} (AM|PM)")


if __name__ == "__main__":
    unittest.main()
