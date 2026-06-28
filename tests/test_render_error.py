import unittest
from claude_usage import render_error, UsageError


class TestRenderError(unittest.TestCase):
    def test_no_token(self):
        out = render_error(UsageError("no_token"))
        self.assertTrue(out.splitlines()[0].startswith("􀇿 Claude ?"))
        self.assertIn("Keychain access denied", out)
        self.assertIn("Refresh | refresh=true", out)

    def test_auth(self):
        out = render_error(UsageError("auth"))
        self.assertIn("auth", out.splitlines()[0])
        self.assertIn("Open Claude Code to refresh", out)

    def test_offline(self):
        out = render_error(UsageError("offline"))
        self.assertIn("could not reach api.anthropic.com", out)

    def test_bad_response_includes_detail(self):
        out = render_error(UsageError("bad_response", "missing five_hour"))
        self.assertIn("missing five_hour", out)


if __name__ == "__main__":
    unittest.main()
