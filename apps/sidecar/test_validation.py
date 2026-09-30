"""① sampling and threshold fitting, the day split, and exact finite-population bounds."""
import unittest
from zoneinfo import ZoneInfo

import numpy as np
from scipy.special import expit, logit
from scipy.stats import hypergeom

from validation import calibration_sample, exact_count_interval, fit_threshold, logit_scores, split_recordings


class CalibrationSampleCheck(unittest.TestCase):
    def test_one_window_per_equal_logit_bin_when_every_bin_is_filled(self):
        scores = expit(np.linspace(-6, 6, 400))
        chosen = calibration_sample(scores, np.random.default_rng(1))
        self.assertEqual(len(chosen), 20)
        self.assertEqual(sorted(band for _, band in chosen), list(range(20)))
        x = logit_scores(scores)
        edges = np.linspace(x.min(), x.max(), 21)
        for index, band in chosen:
            self.assertTrue(edges[band] - 1e-9 <= x[index] <= edges[band + 1] + 1e-9)
        self.assertEqual(len({index for index, _ in chosen}), 20)

    def test_empty_bins_are_filled_from_the_others_and_small_populations_are_taken_whole(self):
        # Two clusters leave most of the 20 bins empty.
        scores = np.r_[np.full(30, .01), np.full(30, .99)] * np.r_[np.linspace(.9, 1, 30), np.linspace(.999, 1, 30)]
        chosen = calibration_sample(scores, np.random.default_rng(2))
        self.assertEqual(len(chosen), 20)
        self.assertEqual(len({index for index, _ in chosen}), 20)
        occupied = {band for _, band in chosen}
        self.assertGreaterEqual(len(occupied), 2)
        self.assertEqual(sorted(i for i, _ in calibration_sample([.2, .4, .6], np.random.default_rng(3))), [0, 1, 2])
        self.assertEqual(calibration_sample([], np.random.default_rng(4)), [])
        self.assertEqual(len(calibration_sample(np.full(50, .3), np.random.default_rng(5))), 20)

    def test_the_draw_depends_only_on_scores_and_seed(self):
        scores = expit(np.random.default_rng(0).normal(size=300))
        self.assertEqual(calibration_sample(scores, np.random.default_rng(9)), calibration_sample(scores, np.random.default_rng(9)))


class ThresholdCheck(unittest.TestCase):
    population = expit(np.linspace(-8, 8, 2000))

    def test_lowest_threshold_with_fitted_share_of_at_least_90_percent(self):
        sample = expit(np.linspace(-8, 8, 20))
        verdicts = ['target' if s > .5 else 'other' for s in sample]
        result = fit_threshold(sample, verdicts, self.population)
        self.assertIsNone(result['reason'])
        self.assertGreater(result['curve']['slope'], 0)
        self.assertGreaterEqual(result['estimated_precision'], .9)
        # The share above the chosen threshold reaches 90%; one population step lower no longer does.
        chosen = result['threshold']
        self.assertIn(chosen, self.population)
        beta = result['curve']['intercept'], result['curve']['slope']
        fitted = lambda cut: expit(beta[0] + beta[1] * logit_scores(self.population[self.population >= cut])).mean()
        self.assertGreaterEqual(fitted(chosen), .9)
        lower = self.population[self.population < chosen].max()
        self.assertLess(fitted(lower), .9)
        # Unknown answers are left out, not counted as absent.
        self.assertEqual(fit_threshold(sample, [*verdicts[:-1], 'unknown'], self.population)['curve']['answers'], 19)

    def test_no_heard_sound_or_no_relation_cannot_set_a_threshold(self):
        sample = expit(np.linspace(-8, 8, 20))
        self.assertEqual(fit_threshold(sample, ['other'] * 20, self.population), {'threshold': None, 'reason': 'no_target'})
        self.assertEqual(fit_threshold(sample, ['unknown'] * 19 + ['target'], self.population)['reason'], 'no_relation')
        self.assertEqual(fit_threshold(sample, ['target'] * 20, self.population)['reason'], 'no_relation')
        backwards = ['target' if s < .5 else 'other' for s in sample]
        result = fit_threshold(sample, backwards, self.population)
        self.assertIsNone(result['threshold'])
        self.assertEqual(result['reason'], 'no_relation')
        self.assertLessEqual(result['curve']['slope'], 0)

    def test_a_weak_relation_that_never_reaches_90_percent_is_reported(self):
        sample = expit(np.linspace(-8, 8, 20))
        verdicts = ['target' if i % 2 == 0 or i > 15 else 'other' for i in range(20)]
        verdicts[19] = 'other'
        result = fit_threshold(sample, verdicts, self.population)
        self.assertIsNone(result['threshold'])
        self.assertIn(result['reason'], {'target_unreachable', 'no_relation'})

    def test_logit_scale_is_clipped_at_the_extremes(self):
        values = logit_scores([0, 1e-30, .5, 1])
        self.assertTrue(np.isfinite(values).all())
        self.assertEqual(values[0], values[1])
        self.assertAlmostEqual(values[2], 0)
        self.assertAlmostEqual(values[0], logit(1e-9))


class SplitCheck(unittest.TestCase):
    def test_days_split_in_half_reproducibly_and_one_day_splits_recordings(self):
        zone = ZoneInfo('Asia/Tokyo')
        records = {rid: {'id': rid, 'recorded_at': f'2024-08-0{day}T01:00:00+00:00', 'seconds': 60}
                   for rid, day in [(1, 1), (2, 1), (3, 2), (4, 3), (5, 4)]}
        split = split_recordings(records, zone, 7)
        self.assertEqual(split['unit'], 'local_day')
        self.assertEqual(split, split_recordings(dict(reversed(records.items())), zone, 7))
        self.assertEqual(sorted(split['calibration_recording_ids'] + split['evaluation_recording_ids']), [1, 2, 3, 4, 5])
        # Recordings of one day never straddle the split.
        self.assertEqual({1, 2} <= set(split['calibration_recording_ids']) or {1, 2} <= set(split['evaluation_recording_ids']), True)
        days = lambda ids: {records[i]['recorded_at'][:10] for i in ids}
        self.assertEqual(len(days(split['calibration_recording_ids'])), 2)
        one = split_recordings({1: records[1], 2: records[2]}, zone, 7)
        self.assertEqual((one['unit'], len(one['calibration_recording_ids']), len(one['evaluation_recording_ids'])), ('recording', 1, 1))
        single = split_recordings({1: records[1]}, zone, 7)
        self.assertEqual((single['calibration_recording_ids'], single['evaluation_recording_ids']), ([1], []))


class FiniteBoundCheck(unittest.TestCase):
    def test_exact_hypergeometric_limits(self):
        for N, n in [(5, 2), (10, 1), (10, 6), (1000, 30), (70080, 30)]:
            for alpha in (.05, .1):
                for k in sorted({0, 1, n // 2, n - 1, n}):
                    low, high = exact_count_interval(N, n, k, alpha)
                    self.assertGreaterEqual(hypergeom.sf(k - 1, N, low, n), alpha / 2)
                    self.assertGreaterEqual(hypergeom.cdf(k, N, high, n), alpha / 2)
                    if low > k:
                        self.assertLess(hypergeom.sf(k - 1, N, low - 1, n), alpha / 2)
                    if high < N - n + k:
                        self.assertLess(hypergeom.cdf(k, N, high + 1, n), alpha / 2)
        self.assertEqual(exact_count_interval(10, 10, 0, .05), (0, 0))
        self.assertEqual(exact_count_interval(10, 10, 10, .05), (10, 10))


if __name__ == '__main__':
    unittest.main()
