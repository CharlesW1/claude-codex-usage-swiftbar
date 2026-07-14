import unittest
import os
import tempfile
from unittest.mock import patch
from claude_usage import (
    enabled_load, enabled_save, enabled_toggle, PROVIDERS,
    percent_mode_load, percent_mode_save,
)

class TestDisplayMode(unittest.TestCase):
    @patch("claude_usage.LEGACY_AGY_PERCENT_MODE_PATH", new_callable=lambda: tempfile.mktemp())
    @patch("claude_usage.PERCENT_MODE_PATH", new_callable=lambda: tempfile.mktemp())
    def test_percent_mode_defaults_to_used_and_persists(self, mock_path, legacy_path):
        self.assertEqual(percent_mode_load(), "used")
        percent_mode_save("remaining")
        self.assertEqual(percent_mode_load(), "remaining")
        percent_mode_save("invalid")
        self.assertEqual(percent_mode_load(), "remaining")

    @patch("claude_usage.DISPLAY_MODE_PATH", new_callable=lambda: tempfile.mktemp())
    def test_migration_and_load(self, mock_path):
        # Missing file
        self.assertEqual(enabled_load(), set(PROVIDERS))
        
        # Old values
        with open(mock_path, "w") as f: f.write("both")
        self.assertEqual(enabled_load(), set(PROVIDERS))
        with open(mock_path, "w") as f: f.write("claude")
        self.assertEqual(enabled_load(), {"claude"})
        with open(mock_path, "w") as f: f.write("codex")
        self.assertEqual(enabled_load(), {"codex"})
        
        # Normalization
        with open(mock_path, "w") as f: f.write("  claude , Garbage , Agy, claude")
        self.assertEqual(enabled_load(), {"claude", "agy"})

        # Empty string yields empty set (actually wait, spec says "A file that is missing, empty, or normalizes to the empty set on first read -> default {claude, codex, agy}")
        # "Empty is only reachable by an explicit user toggle-off"
        # Wait, how do I distinguish explicit empty from fresh empty?
        # A saved empty set will be stored as "".
        # Let's say if the file exists and is empty, does it mean explicit toggle-off? The spec says "A file that is missing, empty, or normalizes to the empty set on first read -> default... Empty is only reachable by an explicit user toggle-off". 
        # Ah! If the user toggles all off, what is written? An empty file. But how do we distinguish it? We can write a special token like `none` or we can just rely on the file existence? But empty string `f.read().strip()` is falsy.
        # Wait, the spec says "storage: comma-separated sorted slugs". If empty, maybe "none"?
        # Actually, let's write `__empty__` or just let empty mean empty if the file exists. 
        # "A file that is missing, empty, or normalizes to the empty set on first read -> default"
        # If it normalizes to empty on first read it defaults. So if they toggle everything off, how is it saved?
        # Maybe it's fine to just save it as `none` if empty.

    @patch("claude_usage.DISPLAY_MODE_PATH", new_callable=lambda: tempfile.mktemp())
    def test_save_and_toggle(self, mock_path):
        enabled_save({"codex", "claude"})
        with open(mock_path) as f:
            self.assertEqual(f.read().strip(), "claude,codex")
        
        enabled_toggle("agy")
        self.assertEqual(enabled_load(), {"claude", "codex", "agy"})
        
        enabled_toggle("claude")
        self.assertEqual(enabled_load(), {"codex", "agy"})
        
        enabled_toggle("garbage")
        self.assertEqual(enabled_load(), {"codex", "agy"})
        
        enabled_toggle("codex")
        enabled_toggle("agy")
        self.assertEqual(enabled_load(), set())
        
        # Verify it stays empty on reload
        self.assertEqual(enabled_load(), set())

if __name__ == "__main__":
    unittest.main()
