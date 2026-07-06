import unittest
from datetime import datetime, timezone
from claude_usage import render_dropdown, Usage, CodexUsage

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
CLAUDE = Usage(11.0, "2026-06-28T10:30:00+00:00", 43.0, "2026-07-04T12:00:00+00:00")
CODEX = CodexUsage(50.0, "2026-06-28T09:00:00+00:00", 28.0, "2026-07-04T12:00:00+00:00")


class TestRenderDropdown(unittest.TestCase):
    def test_both_providers_present(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300)
        self.assertIn("Claude | color=#8e8e93", out)
        # Claude 11% / 43% are the white "best" tier -> no color param (adaptive)
        self.assertIn("5-hour  11%  ·  resets in 3h 12m\n", out + "\n")
        self.assertIn("Weekly  43%  ·  resets in 6d 4h\n", out + "\n")
        self.assertIn("Codex | color=#8e8e93", out)
        self.assertIn("5-hour  50%  ·  resets in 1h 42m | color=#34c759", out)  # 50% green
        self.assertIn("Weekly  28%  ·  resets in 6d 4h\n", out + "\n")

    def test_next_check_and_refresh_present(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300)
        self.assertRegex(out, r"↻ \d{1,2}:\d{2}:\d{2} (AM|PM) · next check \(every 5m\)")
        self.assertIn("Refresh now | refresh=true", out)

    def test_boost_control_present_when_cli_given(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300, cli=("/py", "/mod.py"))
        self.assertIn("Refresh every 1 min for 30 min", out)
        self.assertIn('param2="boost"', out)

    def test_boost_active_shows_stop_and_countdown(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 60, boost_remaining=600,
                              cli=("/py", "/mod.py"))
        self.assertIn("boosted to 1m", out)
        self.assertIn("Stop 1-minute boost", out)

    def test_missing_codex_shows_note(self):
        out = render_dropdown(CLAUDE, None, NOW, 300, codex_note="signed out")
        self.assertIn("Codex | color=#8e8e93", out)
        self.assertIn("signed out", out)
        self.assertNotIn("50%", out)  # no Codex usage numbers rendered

    def test_stale_claude_marked(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300, stale_claude=True)
        self.assertIn("last reading", out)

    def test_null_reset_does_not_crash(self):
        codex = CodexUsage(0.0, None, 5.0, "2026-07-04T12:00:00+00:00")
        out = render_dropdown(CLAUDE, codex, NOW, 300)
        self.assertIn("5-hour  0%", out)


if __name__ == "__main__":
    unittest.main()
