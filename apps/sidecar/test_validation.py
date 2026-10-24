import json
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
from scipy.special import expit

from server import ValidationRequest
from species import initialize_species
from storage import CatalogError, connect, initialize
from validation import (CURVE_FLOOR, _load_population, answer, delete_check, estimate, extend, extension_needed,
                        quantile_strata, sample_indices, start_session, view_session)


class ValidationEstimatorTest(unittest.TestCase):
    def population(self, n=6000, seed=4):
        rng = np.random.default_rng(seed)
        x = rng.normal(-3, 2, n)
        probability = CURVE_FLOOR + (1 - CURVE_FLOOR) * expit(.8 + 1.2 * (x + .8))
        truth = rng.random(n) < probability
        scores = expit(x)
        strata, _, _ = quantile_strata(scores, seed)
        sampled = np.array([i for i, _ in sample_indices(strata, seed + 1, 10)])
        return scores, truth, strata, sampled

    def test_rank_bins_have_exact_weights_and_reproducible_ties(self):
        scores = np.ones(101) * .2
        first, edges, _ = quantile_strata(scores, 9)
        second, _, _ = quantile_strata(scores, 9)
        self.assertTrue(np.array_equal(first, second))
        self.assertEqual(np.bincount(first, minlength=6).tolist(), np.diff(edges).tolist())

    def test_census_reproduces_truth(self):
        scores, truth, strata, _ = self.population(6000)
        result = estimate(scores, strata, np.arange(len(scores)), np.where(truth, 'target', 'other'),
                          np.min(np.log(scores[strata == 5] / (1 - scores[strata == 5]))), 5)
        actual = truth.mean()
        self.assertAlmostEqual(result['density']['value'], actual, places=12)
        self.assertLess(result['density']['high'] - result['density']['low'], .02)
        self.assertLess(result['curve_density']['high'] - result['curve_density']['low'], 1e-12)

    def test_simulated_population_intervals_cover_density_and_recall(self):
        scores, truth, strata, sampled = self.population()
        x0 = np.min(np.log(scores[strata == 5] / (1 - scores[strata == 5])))
        result = estimate(scores, strata, sampled, np.where(truth[sampled], 'target', 'other'), x0, 8)
        self.assertLessEqual(result['density']['low'], truth.mean())
        self.assertGreaterEqual(result['density']['high'], truth.mean())
        if result['threshold'] is not None and result['recall'] is not None:
            actual = truth[scores >= result['threshold']].sum() / truth.sum()
            self.assertLessEqual(result['recall']['low'], actual)
            self.assertGreaterEqual(result['recall']['high'], actual)

    def test_rare_rule_and_undecided_reasons(self):
        scores = np.linspace(1e-5, .9, 6000)
        strata, _, bounds = quantile_strata(scores, 1)
        sampled = np.array([i for i, _ in sample_indices(strata, 2, 10)])
        none = estimate(scores, strata, sampled, np.full(len(sampled), 'other'), bounds[5]['low_x'], 3)
        self.assertEqual((none['reason'], none['threshold']), ('no_target', None))
        verdicts = np.full(len(sampled), 'other'); verdicts[np.argmax(scores[sampled])] = 'target'
        rare = estimate(scores, strata, sampled, verdicts, bounds[5]['low_x'], 4)
        self.assertEqual(rare['recall_status'], 'not_estimable_rare')
        self.assertIsNone(rare['recall']); self.assertIsNone(rare['multiplier'])
        unreachable = estimate(scores, strata, sampled, np.where(scores[sampled] < np.median(scores[sampled]), 'target', 'other'), bounds[5]['low_x'], 5)
        self.assertEqual(unreachable['reason'], 'target_unreachable')

    def test_extension_rule_and_large_population_speed(self):
        scores, truth, strata, sampled = self.population(6000, 7)
        x0 = np.min(np.log(scores[strata == 5] / (1 - scores[strata == 5])))
        result = estimate(scores, strata, sampled, np.where(truth[sampled], 'target', 'other'), x0, 8)
        should_extend = result['recall'] is not None and result['recall']['high'] - result['recall']['low'] > .30
        self.assertEqual(extension_needed(result), should_extend)
        rare = dict(result, recall=None, recall_status='not_estimable_rare')
        self.assertFalse(extension_needed(rare))
        rng = np.random.default_rng(10)
        million = rng.random(1_000_000)
        large_strata, _, bounds = quantile_strata(million, 11)
        chosen = np.array([i for i, _ in sample_indices(large_strata, 12, 10)])
        started = time.perf_counter()
        estimate(million, large_strata, chosen, np.where(million[chosen] > .7, 'target', 'other'), bounds[5]['low_x'], 13)
        # Windows CI has taken 3.4 s; 10 s still rejects order-of-magnitude slowdowns from normal local runs.
        self.assertLess(time.perf_counter() - started, 10.0)

    def test_large_population_is_external_and_answering_and_extension_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize(root); initialize_species(root)
            source = root / 'source.wav'; source.write_bytes(b'audio')
            stat = source.stat()
            with connect(root) as db:
                db.execute("INSERT INTO sites VALUES(1,'Site','Asia/Tokyo',35,138)")
                db.execute("INSERT INTO datasets VALUES(1,1,'Data',?)", (str(root),))
                db.execute('''INSERT INTO recordings VALUES(1,1,?,?,?,?,?,?,?,?)''',
                    (str(source), '2024-01-01T00:00:00', 'Asia/Tokyo', '2023-12-31T15:00:00+00:00',
                     600_000, 1, stat.st_size, stat.st_mtime_ns))
                species_id = db.execute("SELECT id FROM species WHERE scientific_name='Horornis diphone'").fetchone()[0]
            ctx = {'dataset': {'id': 1, 'name': 'Data'}, 'site': {'id': 1, 'name': 'Site', 'timezone': 'Asia/Tokyo',
                'latitude': 35, 'longitude': 138}, 'start_date': '2024-01-01', 'end_date': '2024-01-01',
                'fingerprint': 'scope', 'models': [], 'revision': 1}
            count = 200_003
            scores = np.linspace(.0001, .9999, count)
            windows = [{'recording_id': 1, 'start_s': i * 3.0, 'end_s': i * 3.0 + 3,
                        'score': float(score)} for i, score in enumerate(scores)]
            request = ValidationRequest(dataset_id=1, species_id=species_id, start_date=date(2024, 1, 1),
                                        end_date=date(2024, 1, 1), model='birdnet')
            with patch('results_workflow.context', return_value=ctx), \
                 patch('validation.model_windows', return_value=(windows, {'birdnet': 'model'}, [1])):
                check = start_session(root, request)
                with connect(root) as db:
                    raw = db.execute('SELECT snapshot FROM validation_sessions WHERE id=?', (check['id'],)).fetchone()[0]
                self.assertLess(len(raw.encode()), 50_000)
                self.assertNotIn('population', json.loads(raw))
                population_path = root / 'validations' / f"{check['id']}.npz"
                self.assertTrue(population_path.is_file())
                with np.load(population_path, allow_pickle=False) as saved:
                    self.assertEqual((len(saved['score']), saved['recording_id'].dtype, saved['score'].dtype, saved['bin'].dtype),
                                     (count, np.dtype('int64'), np.dtype('float64'), np.dtype('int8')))
                with patch('validation._load_population', side_effect=AssertionError('view loaded population')):
                    self.assertEqual(view_session(root, check['id'], ctx=ctx)['population'], count)
                with patch('results_workflow.context', return_value=ctx), patch('validation.extension_needed', return_value=True):
                    while check['question']:
                        question = check['question']
                        check = answer(root, check['id'], question['id'],
                                       'target' if question['start_s'] >= 500_000 else 'other')
                with connect(root) as db:
                    completed_raw = db.execute('SELECT snapshot FROM validation_sessions WHERE id=?', (check['id'],)).fetchone()[0]
                self.assertLess(len(completed_raw.encode()), 50_000)
                self.assertTrue(check['can_extend'])
                with patch('results_workflow.context', return_value=ctx):
                    check = extend(root, check['id'])
                self.assertEqual((check['stage'], check['extended']), ('extension', True))
                self.assertGreater(check['counts']['total'], 60)
                delete_check(root, check['id'])
                self.assertFalse(population_path.exists())
                with self.assertRaisesRegex(CatalogError, 'validation_population_missing'):
                    _load_population(root, check['id'])


if __name__ == '__main__':
    unittest.main()
