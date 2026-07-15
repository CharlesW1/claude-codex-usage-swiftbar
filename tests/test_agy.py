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
    agy_cache_save, agy_cache_load, AGY_PROD_HOST, AGY_DAILY_HOST,
    _parse_agy_ls_processes, _parse_lsof_listeners, _agy_local_request,
    _fetch_agy_local,
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

    @patch("claude_usage._fetch_agy_local")
    @patch("claude_usage.read_agy_creds")
    @patch("claude_usage.fetch_agy")
    @patch("claude_usage.parse_agy")
    @patch("claude_usage.agy_cache_save")
    @patch("claude_usage.agy_cache_load")
    def test_get_agy(self, mock_load, mock_save, mock_parse, mock_fetch,
                     mock_creds, mock_local):
        # Local server answers: used even though the on-disk token is expired.
        mock_local.return_value = {"groups": []}
        mock_parse.return_value = AgyUsage(70, None, None, None, 70, None, None, None)
        mock_creds.return_value = {"access_token": "tok", "expiry_ms": 500}
        res = _get_agy(1000)  # now_ms past expiry, but local wins
        self.assertEqual(res[0].gemini_weekly_pct, 70)
        self.assertFalse(res[1])  # not stale
        mock_save.assert_called_once()
        mock_fetch.assert_not_called()  # cloud path skipped
        mock_creds.assert_not_called()  # on-disk token never consulted

        # Local unavailable -> fall back to on-disk token + cloud. Happy.
        mock_local.return_value = None
        mock_save.reset_mock()
        mock_creds.return_value = {"access_token": "tok", "expiry_ms": 2000}
        mock_parse.return_value = AgyUsage(50, None, None, None, 50, None, None, None)
        res = _get_agy(1000)
        self.assertEqual(res[0].gemini_weekly_pct, 50)
        self.assertFalse(res[1])  # not stale
        mock_save.assert_called_once()

        # Local unavailable + expired on-disk token -> auth note.
        mock_fetch.reset_mock()
        res = _get_agy(3000)
        self.assertIsNone(res[0])
        self.assertEqual(res[2], "token expired \u2014 open Antigravity")
        mock_fetch.assert_not_called()

        # Local unavailable + cloud 401 -> auth note.
        mock_creds.return_value = {"access_token": "tok", "expiry_ms": 3000}
        mock_fetch.side_effect = UsageError("auth")
        res = _get_agy(1000)
        self.assertIsNone(res[0])
        self.assertEqual(res[2], "token expired \u2014 open Antigravity")

        # Transient -> stale cache.
        mock_creds.return_value = {"access_token": "tok", "expiry_ms": 3000}
        mock_fetch.side_effect = UsageError("offline")
        mock_load.return_value = AgyUsage(40, None, None, None, None, None, None, None)
        res = _get_agy(1000)
        self.assertEqual(res[0].gemini_weekly_pct, 40)
        self.assertTrue(res[1])  # stale

        # Backstop.
        mock_fetch.side_effect = Exception("surprise")
        res = _get_agy(1000)
        self.assertIsNone(res[0])
        self.assertFalse(res[1])
        self.assertEqual(res[2], "rate-limited or error")

    @patch("claude_usage._fetch_agy_local")
    @patch("claude_usage.read_agy_creds")
    @patch("claude_usage.fetch_agy")
    @patch("claude_usage.parse_agy")
    @patch("claude_usage.agy_cache_save")
    def test_get_agy_local_malformed_falls_through(self, mock_save, mock_parse,
                                                   mock_fetch, mock_creds,
                                                   mock_local):
        # Local answers but the payload can't be parsed -> use cloud, not cache.
        mock_local.return_value = {"garbage": True}
        mock_creds.return_value = {"access_token": "tok", "expiry_ms": 9000}
        mock_fetch.return_value = {"groups": []}
        good = AgyUsage(60, None, None, None, None, None, None, None)
        mock_parse.side_effect = [UsageError("bad_response"), good]
        res = _get_agy(1000)
        self.assertIs(res[0], good)
        self.assertFalse(res[1])
        mock_fetch.assert_called_once()

    @patch("claude_usage._fetch_agy_local")
    @patch("claude_usage.read_agy_creds")
    @patch("claude_usage.fetch_agy")
    @patch("claude_usage.parse_agy")
    @patch("claude_usage.agy_cache_save")
    def test_get_agy_local_exception_falls_through(self, mock_save, mock_parse,
                                                   mock_fetch, mock_creds,
                                                   mock_local):
        # An unexpected local-probe error must not skip the cloud fallback.
        mock_local.side_effect = Exception("boom")
        mock_creds.return_value = {"access_token": "tok", "expiry_ms": 9000}
        mock_fetch.return_value = {"groups": []}
        good = AgyUsage(60, None, None, None, None, None, None, None)
        mock_parse.return_value = good
        res = _get_agy(1000)
        self.assertIs(res[0], good)
        mock_fetch.assert_called_once()

    def test_parse_agy_ls_processes(self):
        out = (
            "2433 /Applications/Antigravity.app/Contents/Resources/bin/"
            "language_server --override_ide_name antigravity "
            "--csrf_token aaa --app_data_dir antigravity\n"
            "999 /opt/windsurf/language_server --csrf_token zzz\n"
            "111 /x/language_server --standalone antigravity\n"
            "2500 /Applications/Antigravity.app/x/language_server "
            "antigravity --csrf_token bbb\n"
        )
        # Both Antigravity servers returned; Windsurf and the CLI server skipped.
        self.assertEqual(_parse_agy_ls_processes(out),
                         [("2433", "aaa"), ("2500", "bbb")])
        self.assertEqual(_parse_agy_ls_processes(""), [])

    def test_parse_lsof_listeners(self):
        lsof = (
            "COMMAND   PID  USER   FD  TYPE  DEVICE SIZE/OFF NODE NAME\n"
            "language_ 2433 me 6u IPv4 0x1 0t0 TCP 127.0.0.1:49228 (LISTEN)\n"
            "language_ 2433 me 7u IPv6 0x2 0t0 TCP [::1]:49229 (LISTEN)\n"
        )
        self.assertEqual(_parse_lsof_listeners(lsof),
                         [("127.0.0.1", "49228"), ("::1", "49229")])
        self.assertEqual(_parse_lsof_listeners(""), [])

    @patch("urllib.request.urlopen")
    def test_agy_local_request_unwraps_response(self, mock_urlopen):
        payload = {"response": {"groups": [{"buckets": []}]}}
        resp = MagicMock()
        resp.read.return_value = json.dumps(payload).encode()
        mock_urlopen.return_value.__enter__.return_value = resp
        out = _agy_local_request("127.0.0.1", "49228", "csrf")
        self.assertEqual(out, {"groups": [{"buckets": []}]})
        # CSRF header and loopback URL are set on the request.
        req = mock_urlopen.call_args.args[0]
        self.assertIn("127.0.0.1:49228", req.full_url)
        self.assertEqual(req.headers.get("X-codeium-csrf-token"), "csrf")

    @patch("urllib.request.urlopen")
    def test_agy_local_request_ipv6(self, mock_urlopen):
        resp = MagicMock()
        resp.read.return_value = json.dumps({"groups": []}).encode()
        mock_urlopen.return_value.__enter__.return_value = resp
        self.assertEqual(_agy_local_request("::1", "49229", "c"), {"groups": []})
        req = mock_urlopen.call_args.args[0]
        self.assertIn("[::1]:49229", req.full_url)

    @patch("urllib.request.urlopen")
    def test_agy_local_request_flat_and_errors(self, mock_urlopen):
        # Already-flat payload is returned as-is.
        resp = MagicMock()
        resp.read.return_value = json.dumps({"groups": []}).encode()
        mock_urlopen.return_value.__enter__.return_value = resp
        self.assertEqual(_agy_local_request("127.0.0.1", "1", "c"), {"groups": []})
        # Network error -> None (never raises).
        mock_urlopen.side_effect = urllib.error.URLError("down")
        self.assertIsNone(_agy_local_request("127.0.0.1", "1", "c"))

    @patch("claude_usage._find_agy_local_server")
    @patch("claude_usage._agy_local_request")
    def test_fetch_agy_local(self, mock_req, mock_find):
        # No server found -> None, no request attempted.
        mock_find.return_value = None
        self.assertIsNone(_fetch_agy_local())
        mock_req.assert_not_called()
        # Probes listeners in order, returns first non-None.
        mock_find.return_value = ("csrf", [("127.0.0.1", "1"), ("127.0.0.1", "2")])
        mock_req.side_effect = [None, {"groups": []}]
        self.assertEqual(_fetch_agy_local(), {"groups": []})
        self.assertEqual(mock_req.call_count, 2)

if __name__ == "__main__":
    unittest.main()
