import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import urllib.error

# Import what will be implemented
from claude_usage import (
    AgyUsage, UsageError, classify_agy_bucket, parse_agy,
    read_agy_creds, _parse_agy_expiry, fetch_agy, _get_agy,
    agy_cache_save, agy_cache_load, AGY_PROD_HOST, AGY_DAILY_HOST
)

class TestAgy(unittest.TestCase):
    def test_classify_agy_bucket(self):
        # Gemini markers
        self.assertEqual(classify_agy_bucket({"modelId": "gemini-2.5-flash"}), "gemini")
        self.assertEqual(classify_agy_bucket({"quotaId": "SOME_GEMINI_QUOTA"}), "gemini")
        
        # External markers
        self.assertEqual(classify_agy_bucket({"tokenType": "CLAUDE"}), "external")
        self.assertEqual(classify_agy_bucket({"quotaId": "ext_gpt4"}), "external")
        self.assertEqual(classify_agy_bucket({"modelId": "openai-gpt4"}), "external")
        self.assertEqual(classify_agy_bucket({"quotaId": "external_api"}), "external")
        self.assertEqual(classify_agy_bucket({"tokenType": "byok_tokens"}), "external")
        
        # Unclassified
        self.assertIsNone(classify_agy_bucket({"quotaId": "unknown"}))
        self.assertIsNone(classify_agy_bucket({}))

    def test_parse_agy_both_present(self):
        data = {
            "groups": [
                {"displayName": "Gemini Models", "buckets": [
                    {"window": "604800s", "remainingFraction": 0.88, "resetTime": "2026-07-15T00:00:00Z"},
                    {"window": "18000s", "remainingFraction": 0.94, "resetTime": "2026-07-12T22:00:00Z"}]},
                {"displayName": "Claude and GPT models", "buckets": [
                    {"window": "604800s", "remainingFraction": 0.29, "resetTime": "2026-07-16T00:00:00Z"},
                    {"window": "18000s", "remainingFraction": 1.0}]}
            ]
        }
        res = parse_agy(data)
        self.assertEqual(res.gemini_weekly_pct, 88)
        self.assertEqual(res.gemini_5h_pct, 94)
        self.assertEqual(res.external_weekly_pct, 29)
        self.assertEqual(res.external_5h_pct, 100)

    def test_parse_agy_missing_category(self):
        data = {"groups": [{"displayName": "Gemini Models", "buckets": [
            {"window": "604800s", "remainingFraction": 0.2}]}]}
        res = parse_agy(data)
        self.assertEqual(res.gemini_weekly_pct, 20)
        self.assertIsNone(res.external_weekly_pct)

    def test_parse_agy_malformed(self):
        data = {"groups": [{"displayName": "Gemini Models", "buckets": [
            {"window": "604800s", "remainingFraction": "not a number"}]}]}
        with self.assertRaises(UsageError) as cm:
            parse_agy(data)
        self.assertEqual(cm.exception.kind, "bad_response")

    def test_parse_agy_remaining_amount(self):
        # without limit
        data = {"groups": [{"displayName": "Gemini Models", "buckets": [
            {"window": "604800s", "remainingAmount": 50}]}]}
        with self.assertRaises(UsageError) as cm:
            parse_agy(data)
        self.assertEqual(cm.exception.kind, "bad_response")
        
        # with limit (wait, spec says "If a limit is present, round((1 - amount/limit) * 100)")
        data = {"groups": [{"displayName": "Gemini Models", "buckets": [
            {"window": "604800s", "remainingAmount": 50, "limit": 200}]}]}
        res = parse_agy(data)
        self.assertEqual(res.gemini_weekly_pct, 25)

    def test_parse_agy_aggregation(self):
        data = {
            "groups": [{"displayName": "Gemini Models", "buckets": [
                {"window": "604800s", "remainingFraction": 0.5},
                {"window": "604800s", "remainingFraction": 0.1},
                {"window": "604800s"}
            ]}]
        }
        res = parse_agy(data)
        self.assertEqual(res.gemini_weekly_pct, 10)

    def test_parse_agy_top_level_bucket(self):
        data = {"buckets": [{"displayName": "Gemini weekly", "window": "604800s",
                             "remainingFraction": 0.2}]}
        self.assertEqual(parse_agy(data).gemini_weekly_pct, 20)

    def test_parse_agy_structurally_unusable(self):
        with self.assertRaises(UsageError) as cm:
            parse_agy({"no_buckets": True})
        self.assertEqual(cm.exception.kind, "bad_response")

    @patch("claude_usage.AGY_TOKEN_PATH", new_callable=lambda: tempfile.mktemp())
    def test_read_agy_creds(self, mock_path):
        # Missing file
        with self.assertRaises(UsageError) as cm:
            read_agy_creds()
        self.assertEqual(cm.exception.kind, "no_token")

        # Malformed json
        with open(mock_path, "w") as f:
            f.write("not json")
        with self.assertRaises(UsageError) as cm:
            read_agy_creds()
        self.assertEqual(cm.exception.kind, "no_token")

        # Missing token
        with open(mock_path, "w") as f:
            json.dump({"token": {}}, f)
        with self.assertRaises(UsageError) as cm:
            read_agy_creds()
        self.assertEqual(cm.exception.kind, "no_token")

        # Success
        with open(mock_path, "w") as f:
            json.dump({"token": {"access_token": "tok", "expiry": "123"}}, f)
        creds = read_agy_creds()
        self.assertEqual(creds["access_token"], "tok")

    def test_parse_agy_expiry(self):
        self.assertIsNone(_parse_agy_expiry("bad"))
        self.assertIsNone(_parse_agy_expiry(-1))
        self.assertIsNone(_parse_agy_expiry(100)) # too small
        
        self.assertEqual(_parse_agy_expiry(1500000000), 1500000000000) # s -> ms
        self.assertEqual(_parse_agy_expiry(1500000000000), 1500000000000) # ms -> ms
        self.assertEqual(_parse_agy_expiry("1500000000"), 1500000000000)
        
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(_parse_agy_expiry(dt.isoformat()), int(dt.timestamp() * 1000))
        
        dt_naive = datetime(2026, 1, 1)
        self.assertEqual(_parse_agy_expiry(dt_naive.isoformat()), int(dt.timestamp() * 1000))

    @patch("urllib.request.urlopen")
    def test_fetch_agy(self, mock_urlopen):
        quota_resp = MagicMock()
        quota_resp.read.return_value = b'{"groups":[]}'
        mock_urlopen.return_value.__enter__.return_value = quota_resp
        fetch_agy("tok")

        req = mock_urlopen.call_args.args[0]
        self.assertTrue(req.full_url.endswith(":retrieveUserQuotaSummary"))
        self.assertEqual(req.data, b"{}")
        self.assertEqual(req.headers["Authorization"], "Bearer tok")
        
        # F1: Fallback from prod to daily on 404
        mock_urlopen.reset_mock()
        def side_effect(request, *args, **kwargs):
            if request.full_url.startswith(AGY_PROD_HOST):
                raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)
            else:
                resp = MagicMock()
                resp.read.return_value = b'{"groups":[]}'
                cm = MagicMock()
                cm.__enter__.return_value = resp
                return cm
        mock_urlopen.side_effect = side_effect
        fetch_agy("tok")
        self.assertEqual(mock_urlopen.call_count, 2)
        req1 = mock_urlopen.call_args_list[0].args[0]
        req2 = mock_urlopen.call_args_list[1].args[0]
        self.assertTrue(req1.full_url.startswith(AGY_PROD_HOST))
        self.assertTrue(req2.full_url.startswith(AGY_DAILY_HOST))

        # 401
        mock_urlopen.side_effect = urllib.error.HTTPError("url", 401, "msg", {}, None)
        with self.assertRaises(UsageError) as cm:
            fetch_agy("tok")
        self.assertEqual(cm.exception.kind, "auth")

        # 500
        mock_urlopen.side_effect = urllib.error.HTTPError("url", 500, "msg", {}, None)
        with self.assertRaises(UsageError) as cm:
            fetch_agy("tok")
        self.assertEqual(cm.exception.kind, "bad_response")

        # offline
        mock_urlopen.side_effect = urllib.error.URLError("reason")
        with self.assertRaises(UsageError) as cm:
            fetch_agy("tok")
        self.assertEqual(cm.exception.kind, "offline")

        # non-json
        quota_resp.read.return_value = b"not json"
        mock_urlopen.side_effect = None
        with self.assertRaises(UsageError) as cm:
            fetch_agy("tok")
        self.assertEqual(cm.exception.kind, "bad_response")

    @patch("claude_usage.read_agy_creds")
    @patch("claude_usage.fetch_agy")
    @patch("claude_usage.parse_agy")
    @patch("claude_usage.agy_cache_save")
    @patch("claude_usage.agy_cache_load")
    @patch("subprocess.run")
    def test_get_agy(self, mock_run, mock_load, mock_save, mock_parse, mock_fetch,
                     mock_creds):
        # Happy
        mock_creds.return_value = {"access_token": "tok", "expiry_ms": 2000}
        mock_parse.return_value = AgyUsage(50, None, None, None, 50, None, None, None)
        res = _get_agy(1000)
        self.assertEqual(res[0].gemini_weekly_pct, 50)
        self.assertFalse(res[1]) # not stale
        mock_save.assert_called_once()
        mock_run.assert_not_called()
        
        # Expired (yields auth note without subprocess call)
        mock_fetch.reset_mock()
        res = _get_agy(3000)
        self.assertIsNone(res[0])
        self.assertEqual(res[2], "token expired \u2014 open Antigravity")
        mock_fetch.assert_not_called()
        mock_run.assert_not_called()
        
        # 401 (yields auth note without subprocess call)
        mock_creds.return_value = {"access_token": "tok", "expiry_ms": 3000}
        mock_fetch.side_effect = UsageError("auth")
        res = _get_agy(1000)
        self.assertIsNone(res[0])
        self.assertEqual(res[2], "token expired \u2014 open Antigravity")
        mock_run.assert_not_called()
        
        # Transient
        mock_creds.return_value = {"access_token": "tok", "expiry_ms": 3000}
        mock_fetch.side_effect = UsageError("offline")
        mock_load.return_value = AgyUsage(40, None, None, None, None, None, None, None)
        res = _get_agy(1000)
        self.assertEqual(res[0].gemini_weekly_pct, 40)
        self.assertTrue(res[1]) # stale
        
        # Backstop
        mock_fetch.side_effect = Exception("surprise")
        res = _get_agy(1000)
        self.assertIsNone(res[0])
        self.assertFalse(res[1])
        self.assertEqual(res[2], "rate-limited or error")

if __name__ == "__main__":
    unittest.main()
