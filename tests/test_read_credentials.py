import json
import os
import tempfile
import unittest
import claude_usage
from claude_usage import read_credentials, UsageError


def _oauth(expires_at=9_999_999_999_999):
    return {"accessToken": "tok-a", "refreshToken": "ref-a", "expiresAt": expires_at}


class TestReadCredentials(unittest.TestCase):
    """Claude Code stores its OAuth blob in the macOS Keychain on some installs
    and in ~/.claude/.credentials.json on others (current CLI favors the file).
    read_credentials() must work from either source."""

    def setUp(self):
        self._o_run = claude_usage.subprocess.run
        self._o_ccpath = claude_usage.CLAUDE_CODE_CREDS_PATH
        self._o_credspath = claude_usage.CREDS_PATH
        tmp = tempfile.mkdtemp()
        claude_usage.CLAUDE_CODE_CREDS_PATH = os.path.join(tmp, "claude_code_creds.json")
        claude_usage.CREDS_PATH = os.path.join(tmp, "our_cache_creds.json")  # empty: no override

    def tearDown(self):
        claude_usage.subprocess.run = self._o_run
        claude_usage.CLAUDE_CODE_CREDS_PATH = self._o_ccpath
        claude_usage.CREDS_PATH = self._o_credspath

    def _mock_keychain_missing(self):
        def fake_run(cmd, **kwargs):
            class R:
                returncode = 44
                stdout = ""
                stderr = "item not found"
            return R()
        claude_usage.subprocess.run = fake_run

    def _mock_keychain_present(self, oauth):
        def fake_run(cmd, **kwargs):
            class R:
                returncode = 0
                stdout = json.dumps({"claudeAiOauth": oauth}) if "-w" in cmd else \
                          '"acct"<blob>="tester"'
                stderr = ""
            return R()
        claude_usage.subprocess.run = fake_run

    def test_falls_back_to_claude_code_file_when_keychain_absent(self):
        self._mock_keychain_missing()
        with open(claude_usage.CLAUDE_CODE_CREDS_PATH, "w") as f:
            json.dump({"claudeAiOauth": _oauth()}, f)

        creds = read_credentials()
        self.assertEqual(creds["access_token"], "tok-a")
        self.assertEqual(creds["refresh_token"], "ref-a")

    def test_uses_keychain_when_present(self):
        self._mock_keychain_present(_oauth(expires_at=123))
        creds = read_credentials()
        self.assertEqual(creds["access_token"], "tok-a")

    def test_raises_no_token_when_both_sources_absent(self):
        self._mock_keychain_missing()
        # CLAUDE_CODE_CREDS_PATH points at a nonexistent file in setUp.
        with self.assertRaises(UsageError) as ctx:
            read_credentials()
        self.assertEqual(ctx.exception.kind, "no_token")

    def test_malformed_claude_code_file_does_not_crash(self):
        self._mock_keychain_missing()
        with open(claude_usage.CLAUDE_CODE_CREDS_PATH, "w") as f:
            f.write("not json")
        with self.assertRaises(UsageError) as ctx:
            read_credentials()
        self.assertEqual(ctx.exception.kind, "no_token")


if __name__ == "__main__":
    unittest.main()
