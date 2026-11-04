import unittest

from experiments import analysis as a


def ok(t, server):
    return {"event": "request_ok", "t": t, "server": server}


def bad(t):
    return {"event": "request_failed", "t": t}


class AnalysisTests(unittest.TestCase):
    def test_recovery_time_uses_first_ok_from_another_server(self):
        recs = [ok(1, "srv1"), ok(2, "srv1"), bad(3), bad(4), ok(10, "srv2")]
        self.assertAlmostEqual(a.recovery_time(recs, 2.5, "srv1"), 7.5)

    def test_recovery_time_none_when_never_recovers(self):
        recs = [ok(1, "srv1"), bad(3), bad(4)]
        self.assertIsNone(a.recovery_time(recs, 2.5, "srv1"))

    def test_outage_around_measures_gap_between_ok_responses(self):
        recs = [ok(1, "s"), ok(2, "s"), ok(5.5, "s"), ok(6, "s")]
        self.assertAlmostEqual(a.outage_around(recs, 3), 3.5)

    def test_outage_none_when_traffic_never_returns(self):
        self.assertIsNone(a.outage_around([ok(1, "s")], 3))

    def test_success_rate(self):
        recs = [ok(1, "s"), ok(2, "s"), bad(3), bad(4)]
        self.assertEqual(a.success_rate(recs), 0.5)

    def test_steady_success_rate_skips_startup_failures(self):
        recs = [bad(1), bad(2), ok(3, "s"), ok(4, "s"), bad(5), ok(6, "s")]
        self.assertEqual(a.steady_success_rate(recs), 0.75)
        self.assertEqual(a.success_rate(recs), 0.5)

    def test_steady_success_rate_is_zero_when_nothing_ever_works(self):
        self.assertEqual(a.steady_success_rate([bad(1), bad(2)]), 0.0)
        self.assertEqual(a.steady_success_rate([]), 0.0)

    def test_steady_success_rate_counts_failures_after_the_first_success(self):
        recs = [ok(1, "s"), bad(2), bad(3), bad(4)]
        self.assertEqual(a.steady_success_rate(recs), 0.25)

    def test_longest_gap(self):
        recs = [ok(1, "s"), ok(2, "s"), ok(9, "s")]
        self.assertEqual(a.longest_gap(recs, 0, 10), 7)

    def test_stats_at_picks_latest_before_moment(self):
        recs = [{"event": "stats", "t": 1, "packet_in": 1}, {"event": "stats", "t": 5, "packet_in": 9}]
        self.assertEqual(a.stats_at(recs, 3)["packet_in"], 1)
        self.assertEqual(a.last_stats(recs)["packet_in"], 9)

    def test_mean_ignores_none(self):
        self.assertEqual(a.mean([1, None, 3]), 2)
        self.assertIsNone(a.mean([None]))


if __name__ == "__main__":
    unittest.main()
