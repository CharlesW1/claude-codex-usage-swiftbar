import os
import tempfile
import time
import unittest
import claude_usage


class TestBoostState(unittest.TestCase):
    def setUp(self):
        self._orig = claude_usage.BOOST_UNTIL_PATH
        self._tmp = tempfile.mkdtemp()
        claude_usage.BOOST_UNTIL_PATH = os.path.join(self._tmp, "boost")

    def tearDown(self):
        claude_usage.BOOST_UNTIL_PATH = self._orig

    def test_no_file_means_not_boosting(self):
        self.assertIsNone(claude_usage.boost_remaining(time.time()))

    def test_future_until_reports_remaining(self):
        with open(claude_usage.BOOST_UNTIL_PATH, "w") as f:
            f.write(str(time.time() + 600))
        rem = claude_usage.boost_remaining(time.time())
        self.assertIsNotNone(rem)
        self.assertGreater(rem, 590)

    def test_past_until_is_none(self):
        with open(claude_usage.BOOST_UNTIL_PATH, "w") as f:
            f.write(str(time.time() - 5))
        self.assertIsNone(claude_usage.boost_remaining(time.time()))

    def test_stop_clears(self):
        with open(claude_usage.BOOST_UNTIL_PATH, "w") as f:
            f.write(str(time.time() + 600))
        claude_usage.boost_stop()
        self.assertIsNone(claude_usage.boost_remaining(time.time()))


if __name__ == "__main__":
    unittest.main()
