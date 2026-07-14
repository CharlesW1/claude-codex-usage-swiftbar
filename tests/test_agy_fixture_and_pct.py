import unittest
import os
import tempfile
import json
from unittest.mock import patch
from claude_usage import (
    parse_agy, _display_pct, AgyUsage, agy_cache_save, agy_cache_load,
    menubar_tiles
)
from datetime import datetime, timezone

class TestAgyFixtureAndPct(unittest.TestCase):
    def test_fixture_parsing(self):
        fixture_path = os.path.join(os.path.dirname(__file__), "fixtures", "agy_quota_sample.json")
        with open(fixture_path) as f:
            data = json.load(f)
            
        res = parse_agy(data)
        
        self.assertEqual(res.gemini_weekly_pct, 50)
        self.assertEqual(res.gemini_5h_pct, 75)
        self.assertEqual(res.external_weekly_pct, 25)
        self.assertEqual(res.external_5h_pct, 100)

    def test_display_pct(self):
        self.assertIsNone(_display_pct(None, "used"))
        self.assertIsNone(_display_pct(None, "remaining"))
        
        self.assertEqual(_display_pct(30, "used", False), 30)
        self.assertEqual(_display_pct(30, "remaining", False), 70)
        
        self.assertEqual(_display_pct(30, "used", True), 70)
        self.assertEqual(_display_pct(30, "remaining", True), 30)
        
        self.assertEqual(_display_pct(150, "remaining", False), 0)
        self.assertEqual(_display_pct(-50, "remaining", False), 100)

    @patch("claude_usage.AGY_CACHE_PATH", new_callable=lambda: tempfile.mktemp())
    def test_agy_cache_roundtrip(self, mock_path):
        a = AgyUsage(
            gemini_weekly_pct=25.0, gemini_weekly_resets_at="2030-01-01T00:00:00+00:00",
            gemini_5h_pct=None, gemini_5h_resets_at=None,
            external_weekly_pct=0.0, external_weekly_resets_at=None,
            external_5h_pct=100.0, external_5h_resets_at="2030-01-02T00:00:00+00:00"
        )
        agy_cache_save(a)
        
        loaded = agy_cache_load()
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.gemini_weekly_pct, 25.0)
        self.assertEqual(loaded.gemini_5h_pct, None)
        self.assertEqual(loaded.external_weekly_pct, 0.0)
        self.assertEqual(loaded.external_5h_pct, 100.0)
        self.assertEqual(loaded.gemini_weekly_resets_at, "2030-01-01T00:00:00+00:00")
        
    def test_menubar_tile_conversion(self):
        a = AgyUsage(
            gemini_weekly_pct=70.0, gemini_weekly_resets_at=None,
            gemini_5h_pct=None, gemini_5h_resets_at=None,
            external_weekly_pct=None, external_weekly_resets_at=None,
            external_5h_pct=40.0, external_5h_resets_at=None
        )
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        
        tiles_used = menubar_tiles(None, None, a, now, percent_mode="used")
        self.assertEqual(len(tiles_used), 4)
        agg_used = next(t for t in tiles_used if t.row.label == "AgG")
        agx_used = next(t for t in tiles_used if t.row.label == "AgX")
        
        self.assertEqual(agg_used.row.value, "30%")
        self.assertEqual(agx_used.row.value, "60%")
        
        tiles_rem = menubar_tiles(None, None, a, now, percent_mode="remaining")
        agg_rem = next(t for t in tiles_rem if t.row.label == "AgG")
        agx_rem = next(t for t in tiles_rem if t.row.label == "AgX")
        
        self.assertEqual(agg_rem.row.value, "70%")
        self.assertEqual(agx_rem.row.value, "40%")

if __name__ == "__main__":
    unittest.main()
