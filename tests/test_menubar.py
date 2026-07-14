import unittest
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import patch, MagicMock

from claude_usage import (
    Usage, CodexUsage, AgyUsage,
    menubar_tiles, filter_menubar_tiles, render_menubar_image, MenuTile
)

class TestMenuBar(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 12, 17, 49, 0, tzinfo=timezone.utc)
        self.c = Usage(50.0, None, 50.0, None)
        self.x = CodexUsage(50.0, None, 50.0, None)
        self.a = AgyUsage(40.0, None, 50.0, None,
                          30.0, None, 60.0, None)

    def test_logical_coordinates(self):
        tiles = menubar_tiles(self.c, self.x, self.a, self.now)
        self.assertEqual(len(tiles), 4)

        cld = next(t for t in tiles if t.row.label == "Cld")
        self.assertEqual(cld.logical_col, 0)
        self.assertEqual(cld.logical_row, 0)

        cdx = next(t for t in tiles if t.row.label == "Cdx")
        self.assertEqual(cdx.logical_col, 0)
        self.assertEqual(cdx.logical_row, 1)

        agg = next(t for t in tiles if t.row.label == "AgG")
        self.assertEqual(agg.logical_col, 1)
        self.assertEqual(agg.logical_row, 0)

        agx = next(t for t in tiles if t.row.label == "AgX")
        self.assertEqual(agx.logical_col, 1)
        self.assertEqual(agx.logical_row, 1)

    def test_filter_by_enabled(self):
        tiles = menubar_tiles(self.c, self.x, self.a, self.now)

        filtered = filter_menubar_tiles(tiles, {"claude", "agy"})
        self.assertEqual(len(filtered), 3)
        self.assertTrue(all(t.provider in {"claude", "agy"} for t in filtered))

        empty = filter_menubar_tiles(tiles, set())
        self.assertEqual(len(empty), 0)

    def test_agy_none_pct(self):
        a = AgyUsage(None, None, None, None, 50.0, None, None, None)
        tiles = menubar_tiles(self.c, self.x, a, self.now)
        agg = next(t for t in tiles if t.row.label == "AgG")
        self.assertEqual(agg.row.value, "—")
        self.assertEqual(agg.row.timer, "")

    def test_weekly_codex_timer_uses_days_and_hours(self):
        reset = (self.now + timedelta(days=6, hours=13, minutes=11)).isoformat()
        codex = CodexUsage(27.0, reset, None, None,
                           primary_window_s=7 * 86400)
        cdx = next(t for t in menubar_tiles(None, codex, None, self.now)
                   if t.row.label == "Cdx")
        self.assertEqual(cdx.row.timer, "6d 13h")

    @patch("claude_usage.ensure_renderer")
    @patch("subprocess.run")
    def test_render_collapse(self, mock_run, mock_ensure):
        mock_ensure.return_value = "/bin/render"
        mock_run.return_value = MagicMock(returncode=0, stdout="b64")

        # Test {agy} collapse
        tiles = filter_menubar_tiles(menubar_tiles(None, None, self.a, self.now), {"agy"})
        render_menubar_image(tiles)
        args = mock_run.call_args[0][0]
        # "0" should be the physical column
        # args = [bin, label, val, color, timer, color, col, label, val, color, timer, color, col]
        self.assertEqual(args[6], "0")
        self.assertEqual(args[12], "0")

        # Test single font size
        mock_run.reset_mock()
        tiles = filter_menubar_tiles(menubar_tiles(self.c, None, None, self.now), {"claude"})
        render_menubar_image(tiles)
        env = mock_run.call_args[1]["env"]
        self.assertEqual(env["MB_FONT"], "15.0")

if __name__ == "__main__":
    unittest.main()
