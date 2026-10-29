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
from validation import (CURVE_FLOOR, LEGACY_METHOD, METHOD, QUANTILES, _load_population, a1_strata,
                        answer, answered_items, delete_check, estimate, extend, extension_needed,
                        logit_scores, quantile_strata, sample_indices, start_session, view_session)


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

    def test_a1_sparse_detections_are_the_top_bin_and_small_bins_are_censused(self):
        scores = np.full(3200, .02)
        scores[::500] = .1
        strata = a1_strata(scores, 9)
        np.testing.assert_array_equal(strata == 5, scores >= .1)
        cuts = np.floor(QUANTILES[:-1] / QUANTILES[-2] * np.sum(scores < .1)).astype(int)
        np.testing.assert_array_equal(np.bincount(strata, minlength=6)[:5], np.diff(cuts))
        np.testing.assert_array_equal(strata, a1_strata(scores, 9))
        self.assertFalse(np.array_equal(strata, a1_strata(scores, 10)))
        sampled = sample_indices(strata, 12, 10)
        self.assertEqual({i for i, b in sampled if b == 5}, set(np.flatnonzero(scores >= .1)))
        self.assertEqual(len(sampled), 50 + np.sum(scores >= .1))

    def test_a1_keeps_rank_bins_when_detections_fill_the_top_bin(self):
        for detections in (10, 40):
            with self.subTest(detections=detections):
                scores = np.full(320, .01)
                scores[:detections] = .1
                np.testing.assert_array_equal(a1_strata(scores, 9), quantile_strata(scores, 9)[0])

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
        self.assertIsNone(rare['recall'])
        for result in (none, rare):
            self.assertNotIn('multiplier', result)
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

    def test_legacy_population_is_external_and_still_resumes_estimates_and_extends(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/'models.json').write_text(json.dumps({'main':'perch','extra':['birdnet'],'confirmed':True}))
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
                with patch('validation.METHOD', LEGACY_METHOD):
                    check = start_session(root, request)
                self.assertEqual(check['method'], LEGACY_METHOD)
                population = _load_population(root, check['id'])
                np.testing.assert_array_equal(population['bin'], quantile_strata(scores, int(check['seeds']['rank']))[0])
                self.assertEqual(start_session(root, request)['id'], check['id'])
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
                while check['question']:
                    question = check['question']
                    check = answer(root, check['id'], question['id'],
                                   'target' if question['start_s'] >= 500_000 else 'other')
                self.assertEqual((check['stage'], check['can_extend']), ('complete', False))
                with connect(root) as db:
                    snap = json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?', (check['id'],)).fetchone()[0])
                    rows = answered_items(db, check['id'])
                sampled = [int(round(row['start_s'] / 3)) for row in rows]
                expected = estimate(scores, population['bin'], sampled, [r['verdict'] for r in rows],
                                    snap['x0'], int(snap['draw_seed']))
                expected.update(model_sha256='model', status='decided' if expected['threshold'] is not None else 'undecided')
                self.assertEqual(check['results']['birdnet'], expected)
                self.assertEqual(start_session(root, request)['id'], check['id'])
                delete_check(root, check['id'])
                self.assertFalse(population_path.exists())
                with self.assertRaisesRegex(CatalogError, 'validation_population_missing'):
                    _load_population(root, check['id'])


class ValidationSessionTest(unittest.TestCase):
    def test_a1_session_threshold_and_curve_centre_for_empty_and_sparse_top_bins(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize(root); initialize_species(root)
            source = root / 'source.wav'; source.write_bytes(b'audio')
            stat = source.stat()
            with connect(root) as db:
                db.execute("INSERT INTO sites VALUES(1,'Site','Asia/Tokyo',35,138)")
                db.execute("INSERT INTO datasets VALUES(1,1,'Data',?)", (str(root),))
                db.execute('INSERT INTO recordings VALUES(1,1,?,?,?,?,?,?,?,?)',
                           (str(source), '2024-01-01T00:00:00', 'Asia/Tokyo', '2023-12-31T15:00:00+00:00',
                            1600, 1, stat.st_size, stat.st_mtime_ns))
                species_id = db.execute("SELECT id FROM species WHERE scientific_name='Horornis diphone'").fetchone()[0]
            ctx = {'dataset': {'id': 1}, 'site': {'id': 1, 'timezone': 'Asia/Tokyo'},
                   'start_date': '2024-01-01', 'end_date': '2024-01-01', 'fingerprint': 'scope'}
            with patch('results_workflow.context', return_value=ctx), \
                 patch('validation.require_visible'), patch('validation.custom_model_status', return_value=(True, None)), \
                 patch('custom_models.training_windows', return_value=set()), patch('validation.secrets.randbits', return_value=9):
                for engine, threshold in (('perch', .1), ('custom', .5)):
                    for detections in (0, 2):
                        with self.subTest(engine=engine, detections=detections):
                            scores = np.full(320, threshold - .01)
                            scores[:detections] = threshold + .01
                            windows = [{'recording_id': 1, 'start_s': float(i*5), 'end_s': float(i*5+5), 'score': score}
                                       for i, score in enumerate(scores)]
                            request = ValidationRequest(dataset_id=1, species_id=species_id, start_date=date(2024, 1, 1),
                                                        end_date=date(2024, 1, 1), model=engine, model_id=1 if engine=='custom' else None)
                            with patch('validation.model_windows', return_value=(windows, {engine: 'model'}, [1])):
                                check = start_session(root, request)
                            population = _load_population(root, check['id'])
                            np.testing.assert_array_equal(population['bin'] == 5, scores >= threshold)
                            with connect(root) as db:
                                snap = json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?', (check['id'],)).fetchone()[0])
                            self.assertEqual(snap['method'], METHOD)
                            self.assertEqual(snap['detection_threshold'], threshold)
                            self.assertEqual(snap['x0'], logit_scores([threshold + .01 if detections else threshold])[0])
                            self.assertEqual(check['counts']['bins'][5]['total'], detections)
                            self.assertEqual(check['bin_bounds'][5]['low_x'], snap['x0'] if detections else None)
                            self.assertFalse(check['can_extend'])
                            delete_check(root, check['id'])


if __name__ == '__main__':
    unittest.main()
