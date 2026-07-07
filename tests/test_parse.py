import unittest
from datetime import datetime, timezone
from claude_usage import parse_usage, time_until, Usage, UsageError

GOOD = {
    "five_hour": {"utilization": 46.0, "resets_at": "2026-06-28T10:30:00.673002+00:00"},
    "seven_day": {"utilization": 23.0, "resets_at": "2026-07-04T12:00:00+00:00"},
}


class TestParseUsage(unittest.TestCase):
    def test_parses_good_payload(self):
        u = parse_usage(GOOD)
        self.assertEqual(u.session_pct, 46.0)
        self.assertEqual(u.weekly_pct, 23.0)
        self.assertEqual(u.session_resets_at, "2026-06-28T10:30:00.673002+00:00")

    def test_missing_key_raises_bad_response(self):
        with self.assertRaises(UsageError) as ctx:
            parse_usage({"five_hour": {"utilization": 1.0}})  # no resets_at / seven_day
        self.assertEqual(ctx.exception.kind, "bad_response")


def _with_limits(*limits):
    d = dict(GOOD)
    d["limits"] = list(limits)
    return d


FABLE_LIMIT = {
    "kind": "weekly_scoped", "group": "weekly", "percent": 40,
    "resets_at": "2026-07-11T12:00:00+00:00",
    "scope": {"model": {"id": None, "display_name": "Fable"}, "surface": None},
}


class TestParseFable(unittest.TestCase):
    def test_extracts_fable_from_limits(self):
        u = parse_usage(_with_limits(FABLE_LIMIT))
        self.assertEqual(u.fable_pct, 40.0)
        self.assertEqual(u.fable_resets_at, "2026-07-11T12:00:00+00:00")

    def test_absent_when_no_limits(self):
        self.assertIsNone(parse_usage(GOOD).fable_pct)

    def test_absent_when_no_fable_scope(self):
        other = {"kind": "weekly_scoped",
                 "scope": {"model": {"display_name": "Opus"}}, "percent": 5}
        self.assertIsNone(parse_usage(_with_limits(other)).fable_pct)

    def test_zero_percent_fable_is_kept(self):
        z = dict(FABLE_LIMIT, percent=0)
        self.assertEqual(parse_usage(_with_limits(z)).fable_pct, 0.0)

    def test_malformed_limits_do_not_break_core_parse(self):
        # Non-dict entries, missing/None scope+model, and a non-numeric percent
        # must degrade to "no Fable" while 5-hour/weekly still parse.
        bad = _with_limits(
            "not-a-dict", 42, None,
            {"scope": None}, {"scope": {"model": None}},
            {"scope": {"model": {"display_name": "Fable"}}, "percent": "oops"},
        )
        u = parse_usage(bad)
        self.assertEqual(u.session_pct, 46.0)  # core data intact
        self.assertIsNone(u.fable_pct)         # bad Fable percent dropped, no crash

    def test_non_list_limits_ignored(self):
        self.assertIsNone(parse_usage(dict(GOOD, limits="nope")).fable_pct)


class TestTimeMath(unittest.TestCase):
    def test_time_until_seconds(self):
        now = datetime(2026, 6, 28, 7, 18, 0, tzinfo=timezone.utc)
        secs = time_until("2026-06-28T10:30:00+00:00", now)
        self.assertEqual(int(secs), 3 * 3600 + 12 * 60)

    def test_time_until_accepts_z_suffix(self):
        now = datetime(2026, 6, 28, 10, 0, 0, tzinfo=timezone.utc)
        secs = time_until("2026-06-28T10:30:00Z", now)
        self.assertEqual(int(secs), 30 * 60)


if __name__ == "__main__":
    unittest.main()
