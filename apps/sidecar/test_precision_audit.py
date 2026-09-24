"""Regression checks for the fixed 30-window precision audit."""
import json
import sqlite3
import unittest

from scipy.stats import hypergeom

from precision_audit import SAMPLE_SIZE, evaluate, prepare, rank, update
from validation import exact_count_interval


def database():
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('''CREATE TABLE validation_items(
        id INTEGER PRIMARY KEY, session_id INTEGER, phase TEXT, position INTEGER,
        recording_id INTEGER, start_s REAL, stratum TEXT, scores TEXT)''')
    return db


def snapshot():
    return {
        'rank_seed': 'shared-seed', 'models': {'birdnet': 'birdnet-sha', 'perch': 'perch-sha'},
        'thresholds': {'birdnet': {'threshold': .5, 'adjusted': True},
                       'perch': {'threshold': .5, 'adjusted': True}},
        'target_precision': .8, 'window_s': 15, 'results': {},
    }


def audit_snapshot(positive=30, negative=30):
    positive_sample = min(SAMPLE_SIZE, positive)
    negative_sample = min(SAMPLE_SIZE, negative)
    return {
        'models': {'birdnet': 'sha'},
        'thresholds': {'birdnet': {'threshold': .5, 'adjusted': True}},
        'target_precision': .8, 'audit_step': 'precision',
        'evaluation_audits': {'birdnet': {
            'population': positive + negative, 'predicted': positive, 'negative': negative,
            'positive_ids': list(range(1, positive_sample + 1)),
            'negative_ids': list(range(positive_sample + 1, positive_sample + negative_sample + 1)),
        }},
    }


def answers(ids, verdicts):
    return [{'id': iid, 'verdict': verdict} for iid, verdict in zip(ids, verdicts)]


class FixedPrecisionAuditCheck(unittest.TestCase):
    def test_shared_rank_keeps_each_marginal_sample_and_caps_it_at_30(self):
        items = [
            {'recording_id': 1, 'start_s': float(index * 15),
             'scores': {'birdnet': .9 if index < 40 else .1,
                        'perch': .9 if 20 <= index < 60 else .1}}
            for index in range(80)
        ]
        snap = snapshot()
        with database() as db:
            prepare(db, 1, snap, items)
            rows = {row['id']: (row['recording_id'], row['start_s'])
                    for row in db.execute('SELECT id, recording_id, start_s FROM validation_items')}

        expected = {}
        for engine in snap['models']:
            eligible = [item for item in items if item['scores'][engine] >= .5]
            expected[engine] = {(item['recording_id'], item['start_s'])
                                for item in sorted(eligible, key=lambda item: rank(snap, item))[:SAMPLE_SIZE]}
            actual = {rows[iid] for iid in snap['evaluation_audits'][engine]['positive_ids']}
            self.assertEqual(actual, expected[engine])
            self.assertEqual(len(actual), SAMPLE_SIZE)

        birdnet = set(snap['evaluation_audits']['birdnet']['positive_ids'])
        perch = set(snap['evaluation_audits']['perch']['positive_ids'])
        actual_overlap = {rows[iid] for iid in birdnet & perch}
        self.assertEqual(actual_overlap, expected['birdnet'] & expected['perch'])

        repeated = snapshot()
        with database() as db:
            prepare(db, 2, repeated, list(reversed(items)))
            repeated_rows = {row['id']: (row['recording_id'], row['start_s'])
                             for row in db.execute('SELECT id, recording_id, start_s FROM validation_items')}
        for engine in repeated['models']:
            self.assertEqual({repeated_rows[iid] for iid in repeated['evaluation_audits'][engine]['positive_ids']},
                             expected[engine])

    def test_one_sided_finite_population_bound_matches_hypergeometric_tail(self):
        population = 10000
        lower = exact_count_interval(population, 30, 28, .10)[0]
        self.assertGreaterEqual(lower / population, .8)
        self.assertGreaterEqual(hypergeom.sf(27, population, lower, 30), .05)
        self.assertLess(hypergeom.sf(27, population, lower - 1, 30), .05)

        snap = audit_snapshot(10000, 0)
        rows = answers(range(1, 31), ['target'] * 28 + ['other'] * 2)
        result = evaluate(snap, rows)['birdnet']
        self.assertTrue(result['precision_complete'])
        self.assertGreaterEqual(result['precision_lower_95'], .8)
        self.assertTrue(result['goal_met'])

    def test_partial_sample_has_no_formal_interval_or_goal_decision(self):
        snap = audit_snapshot()
        rows = answers(range(1, 30), ['target'] * 29)
        result = evaluate(snap, rows)['birdnet']
        self.assertEqual(result['precision_sample']['answered'], 29)
        self.assertFalse(result['precision_complete'])
        self.assertEqual(result['precision'], {'value': None, 'low': None, 'high': None})
        self.assertIsNone(result['precision_lower_95'])
        self.assertFalse(result['goal_met'])

    def test_unknown_is_retained_and_cannot_turn_27_confirmed_targets_into_a_pass(self):
        snap = audit_snapshot(10000, 0)
        rows = answers(range(1, 31), ['target'] * 27 + ['other'] * 2 + ['unknown'])
        result = evaluate(snap, rows)['birdnet']
        self.assertIsNone(result['precision']['value'])
        self.assertEqual(result['precision_sample']['unknown'], 1)
        self.assertLess(result['precision_lower_95'], .8)
        self.assertFalse(result['goal_met'])

    def test_zero_population_and_census_have_defined_results(self):
        empty = evaluate(audit_snapshot(0, 0), [])['birdnet']
        self.assertTrue(empty['precision_complete'])
        self.assertIsNone(empty['precision_lower_95'])
        self.assertFalse(empty['goal_met'])
        self.assertEqual(empty['status'], 'limited')

        snap = audit_snapshot(5, 0)
        result = evaluate(snap, answers(range(1, 6), ['target'] * 4 + ['other']))['birdnet']
        self.assertEqual(result['precision'], {'value': .8, 'low': .8, 'high': .8})
        self.assertEqual(result['precision_lower_95'], .8)
        self.assertTrue(result['goal_met'])

    def test_optional_recall_answers_and_corrections_preserve_precision_membership(self):
        snap = audit_snapshot(30, 4)
        positive_ids = list(snap['evaluation_audits']['birdnet']['positive_ids'])
        rows = answers(positive_ids, ['target'] * 28 + ['other'] * 2)
        update(snap, rows)
        before = json.loads(json.dumps(snap['results']['birdnet']['precision']))
        lower = snap['results']['birdnet']['precision_lower_95']
        membership = json.loads(json.dumps(snap['evaluation_audits']))

        snap['audit_step'] = 'recall'
        rows += answers(range(31, 35), ['other'] * 4)
        update(snap, rows)
        self.assertEqual(snap['results']['birdnet']['precision'], before)
        self.assertEqual(snap['results']['birdnet']['precision_lower_95'], lower)
        self.assertEqual(snap['evaluation_audits'], membership)

        corrected = [{**row, 'verdict': 'target'} if row['id'] == 30 else row for row in rows]
        update(snap, corrected)
        self.assertEqual(snap['evaluation_audits'], membership)
        self.assertEqual(snap['results']['birdnet']['precision_sample']['target'], 29)
        self.assertGreater(snap['results']['birdnet']['precision']['value'], before['value'])


if __name__ == '__main__':
    unittest.main()
