import unittest
import claude_usage
from claude_usage import current_token, UsageError

NOW_MS = 1_000_000_000_000
HOUR = 60 * 60 * 1000


def creds(expires_at):
    return {
        "access_token": "old-access",
        "refresh_token": "old-refresh",
        "expires_at": expires_at,
        "account": "tester",
        "oauth": {
            "accessToken": "old-access",
            "refreshToken": "old-refresh",
            "expiresAt": expires_at,
            "scopes": ["user:inference"],
            "subscriptionType": "max",
        },
    }


class RefreshBase(unittest.TestCase):
    def setUp(self):
        self._o_read = claude_usage.read_credentials
        self._o_refresh = claude_usage.token_refresh
        self._o_kc = claude_usage.keychain_write
        self._o_file = claude_usage.creds_file_save
        self.kc_writes = []
        self.file_writes = []
        claude_usage.keychain_write = lambda account, oauth: self.kc_writes.append((account, oauth))
        claude_usage.creds_file_save = lambda oauth: self.file_writes.append(oauth)

    def tearDown(self):
        claude_usage.read_credentials = self._o_read
        claude_usage.token_refresh = self._o_refresh
        claude_usage.keychain_write = self._o_kc
        claude_usage.creds_file_save = self._o_file


class TestCurrentToken(RefreshBase):
    def test_uses_cached_token_when_valid(self):
        claude_usage.read_credentials = lambda: creds(NOW_MS + HOUR)

        def must_not_refresh(rt):
            raise AssertionError("refresh should not be called for a valid token")
        claude_usage.token_refresh = must_not_refresh

        self.assertEqual(current_token(NOW_MS), "old-access")
        self.assertEqual(self.kc_writes, [])

    def test_refreshes_within_skew_and_persists(self):
        claude_usage.read_credentials = lambda: creds(NOW_MS + 60 * 1000)  # 1 min ahead < 5 min skew
        claude_usage.token_refresh = lambda rt: {
            "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 28800,
        }
        tok = current_token(NOW_MS)
        self.assertEqual(tok, "new-access")
        self.assertEqual(len(self.kc_writes), 1)
        acct, oauth = self.kc_writes[0]
        self.assertEqual(acct, "tester")
        self.assertEqual(oauth["accessToken"], "new-access")
        self.assertEqual(oauth["refreshToken"], "new-refresh")
        self.assertEqual(oauth["expiresAt"], NOW_MS + 28800 * 1000)
        self.assertEqual(oauth["scopes"], ["user:inference"])  # preserved
        self.assertEqual(oauth["subscriptionType"], "max")  # preserved
        self.assertEqual(len(self.file_writes), 1)  # also persisted to file

    def test_keeps_old_refresh_token_when_not_rotated(self):
        claude_usage.read_credentials = lambda: creds(0)  # expired
        claude_usage.token_refresh = lambda rt: {
            "access_token": "new-access", "refresh_token": None, "expires_in": 3600,
        }
        current_token(NOW_MS)
        _, oauth = self.kc_writes[0]
        self.assertEqual(oauth["refreshToken"], "old-refresh")

    def test_force_refreshes_even_when_valid(self):
        claude_usage.read_credentials = lambda: creds(NOW_MS + HOUR)
        claude_usage.token_refresh = lambda rt: {
            "access_token": "forced", "refresh_token": "r2", "expires_in": 3600,
        }
        self.assertEqual(current_token(NOW_MS, force=True), "forced")

    def test_refresh_failure_propagates_auth(self):
        claude_usage.read_credentials = lambda: creds(0)

        def boom(rt):
            raise UsageError("auth", "refresh token expired")
        claude_usage.token_refresh = boom

        with self.assertRaises(UsageError) as ctx:
            current_token(NOW_MS)
        self.assertEqual(ctx.exception.kind, "auth")


if __name__ == "__main__":
    unittest.main()
