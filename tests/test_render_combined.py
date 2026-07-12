import unittest
from datetime import datetime, timezone
from claude_usage import render_dropdown, Usage, CodexUsage, GRAY

NOW = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
CLAUDE = Usage(11.0, "2026-06-28T10:30:00+00:00", 43.0, "2026-07-04T12:00:00+00:00")
CODEX = CodexUsage(50.0, "2026-06-28T09:00:00+00:00", 28.0, "2026-07-04T12:00:00+00:00")


class TestRenderDropdown(unittest.TestCase):
    def test_both_providers_present(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300)
        self.assertIn("Claude | color=#8e8e93", out)
        # Detail rows use the same passive section style as the provider labels.
        self.assertIn(f"5-hour  11%  ·  resets in 3h 12m | color={GRAY}", out)
        self.assertIn(f"Weekly  43%  ·  resets in 6d 4h | color={GRAY}", out)
        self.assertIn("Codex | color=#8e8e93", out)
        self.assertIn(f"5-hour  50%  ·  resets in 1h 42m | color={GRAY}", out)
        self.assertIn(f"Weekly  28%  ·  resets in 6d 4h | color={GRAY}", out)
        self.assertNotIn('href="."', out)

    def test_next_check_and_refresh_present(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300)
        self.assertRegex(out, r"↻ \d{1,2}:\d{2}:\d{2} (AM|PM) · next check \(every 5m\)")
        self.assertIn("Check now | refresh=true", out)

    def test_boost_control_present_when_cli_given(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300, cli=("/py", "/mod.py"))
        self.assertIn("Check every 1 min for 30 min", out)
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

    def test_claude_mode_hides_codex_section(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300,
                              cli=("/py", "/mod.py"), display_mode="claude")
        self.assertIn("Claude | color=#8e8e93", out)
        self.assertNotIn("Codex | color=#8e8e93", out)
        self.assertIn("\nShow: Claude\n", out)
        self.assertIn('--Show: Claude ✓ | bash="/py" param1="/mod.py" '
                      'param2="show" param3="claude" terminal=false refresh=true', out)
        self.assertIn('--Show: Codex | bash="/py" param1="/mod.py" '
                      'param2="show" param3="codex" terminal=false refresh=true', out)

    def test_codex_mode_hides_claude_section(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300,
                              cli=("/py", "/mod.py"), display_mode="codex")
        self.assertNotIn("Claude | color=#8e8e93", out)
        self.assertIn("Codex | color=#8e8e93", out)
        self.assertIn("\nShow: Codex\n", out)
        self.assertIn('--Show: Codex ✓ | bash="/py" param1="/mod.py" '
                      'param2="show" param3="codex" terminal=false refresh=true', out)
        self.assertIn('--Show: Both | bash="/py" param1="/mod.py" '
                      'param2="show" param3="both" terminal=false refresh=true', out)

    def test_codex_weekly_only_shape(self):
        # Mid-2026 Codex shape: one weekly window, no 5-hour tier. The single
        # window must render with its real label and no phantom second line.
        cx = CodexUsage(6.0, "2026-07-04T12:00:00+00:00", None, None,
                        primary_window_s=604800)
        out = render_dropdown(None, cx, NOW, 300, display_mode="codex")
        self.assertIn("Weekly  6%", out)
        self.assertNotIn("5-hour", out)

    def test_codex_two_window_shape_regresses(self):
        # If the 5-hour window comes back, both lines reappear, labeled from
        # the payload's own durations.
        cx = CodexUsage(50.0, "2026-06-28T09:00:00+00:00",
                        28.0, "2026-07-04T12:00:00+00:00",
                        primary_window_s=18000, secondary_window_s=604800)
        out = render_dropdown(None, cx, NOW, 300, display_mode="codex")
        self.assertIn("5-hour  50%", out)
        self.assertIn("Weekly  28%", out)

    def test_codex_no_windows_shows_note(self):
        cx = CodexUsage(None, None, None, None)
        out = render_dropdown(None, cx, NOW, 300, display_mode="codex")
        self.assertIn("no active limits", out)

    def test_stale_claude_marked(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300, stale_claude=True)
        self.assertIn("last reading", out)

    def test_fable_line_present_when_scoped_limit_exists(self):
        claude = Usage(11.0, "2026-06-28T10:30:00+00:00", 43.0,
                       "2026-07-04T12:00:00+00:00",
                       fable_pct=40.0, fable_resets_at="2026-07-04T12:00:00+00:00")
        out = render_dropdown(claude, CODEX, NOW, 300)
        self.assertIn(f"Fable  40%  ·  resets in 6d 4h | color={GRAY}", out)

    def test_no_fable_line_when_absent(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300)  # CLAUDE has no fable_pct
        self.assertNotIn("Fable", out)

    def test_zero_percent_fable_still_renders(self):
        # 0.0 is a real allotment (0% used) and must show; only None hides it.
        claude = Usage(11.0, "2026-06-28T10:30:00+00:00", 43.0,
                       "2026-07-04T12:00:00+00:00",
                       fable_pct=0.0, fable_resets_at="2026-07-04T12:00:00+00:00")
        self.assertIn("Fable  0%", render_dropdown(claude, CODEX, NOW, 300))

    def test_provider_error_help_rendered_under_details(self):
        out = render_dropdown(None, CODEX, NOW, 300,
                              claude_note="Keychain locked — allow access",
                              claude_help="open Claude Code and sign in again, "
                                          "then Check now")
        lines = out.splitlines()
        details_i = next(i for i, l in enumerate(lines) if "/usage" in l)
        self.assertIn("open Claude Code and sign in again", lines[details_i + 1])
        self.assertIn(f"color={GRAY}", lines[details_i + 1])

    def test_no_help_line_when_providers_healthy(self):
        out = render_dropdown(CLAUDE, CODEX, NOW, 300)
        self.assertNotIn("sign in", out)

    def test_null_reset_does_not_crash(self):
        codex = CodexUsage(0.0, None, 5.0, "2026-07-04T12:00:00+00:00")
        out = render_dropdown(CLAUDE, codex, NOW, 300)
        self.assertIn("5-hour  0%", out)


if __name__ == "__main__":
    unittest.main()
