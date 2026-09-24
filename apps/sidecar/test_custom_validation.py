"""Independent 15-second validation for a fixed custom-model version."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

from server import create_app
from storage import CatalogError, connect, initialize
from validation import annotation_source_current, answer, finish_calibration, start_session, view_session


SPECIES = 'custom-species'


class CustomValidationCheck(unittest.TestCase):
    def test_holdout_sampling_checkpoints_corrections_and_stock_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize(root)
            with connect(root) as db:
                db.execute("CREATE TABLE species(id TEXT PRIMARY KEY, scientific_name TEXT NOT NULL UNIQUE, names TEXT NOT NULL DEFAULT '{}')")
                db.execute("INSERT INTO species VALUES(?, 'Test bird', '{}')", (SPECIES,))
                db.execute("INSERT INTO sites VALUES(1, 'Site', 'Asia/Tokyo', 35, 138)")
                db.execute("INSERT INTO datasets VALUES(1, 1, 'Dataset', ?)", (temp,))
                for recording_id in range(1, 5):
                    db.execute("INSERT INTO recordings VALUES(?,?,?,'2024-08-08T00:00:00','Asia/Tokyo','2024-08-07T15:00:00+00:00',1000,24000,1,1)",
                        (recording_id, 1, str(root / f'{recording_id}.wav')))
            ctx = {'dataset': {'id': 1}, 'site': {'id': 1, 'timezone': 'Asia/Tokyo'},
                   'start_date': '2024-08-08', 'end_date': '2024-08-08',
                   'fingerprint': 'scope-fingerprint', 'location_stale': False}
            model = {'id': 7, 'dataset_id': 1, 'species': {'id': SPECIES},
                'scope': {'start_date': '2024-08-08', 'end_date': '2024-08-08'},
                'cv': {'threshold': .73}, 'artifact_sha256': 'custom-artifact',
                'split': {'unit': 'recording', 'calibration_recording_ids': [1, 2],
                          'evaluation_recording_ids': [3, 4]},
                'applicable': True, 'reason': None}
            items = [
                {'recording_id': 3 + (index % 2), 'start_s': float(index * 15),
                 'scores': {'custom': .9 if index < 65 else .2}}
                for index in range(130)
            ]
            with patch('results_workflow.context', return_value=ctx), \
                    patch('custom_models.get_model', return_value=model), \
                    patch('custom_models.validation_population', return_value=(items, model)):
                session = start_session(root, SimpleNamespace(model_id=7))
                repeated = start_session(root, SimpleNamespace(model_id=7))
            self.assertEqual(repeated['id'], session['id'])
            self.assertEqual((session['kind'], session['model_id'], session['stage']), ('custom', 7, 'evaluation'))
            self.assertEqual(session['counts'], {'calibration': {'total': 0, 'answered': 0},
                                                  'evaluation': {'total': 30, 'answered': 0},
                                                  'recall': {'total': 30, 'answered': 0}})
            self.assertEqual(session['thresholds']['custom'], {'threshold': .73, 'adjusted': False})
            with patch('results_workflow.context', return_value=ctx), \
                    patch('custom_models.get_model', return_value=model), \
                    patch('custom_models.validation_population', return_value=(items, model)), \
                    TestClient(create_app(root, root, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                response = client.post('/validations', json={'model_id': 7})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual((response.json()['id'], response.json()['model_id']), (session['id'], 7))
            with connect(root) as db:
                snapshot = json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?', (session['id'],)).fetchone()[0])
                rows = db.execute('SELECT phase,recording_id,stratum FROM validation_items WHERE session_id=?', (session['id'],)).fetchall()
            self.assertEqual(snapshot['method'], 'fixed_precision_audit_v1')
            self.assertEqual(snapshot['evaluation_audits']['custom']['predicted'], 65)
            self.assertEqual(snapshot['evaluation_audits']['custom']['negative'], 65)
            self.assertEqual({row['phase'] for row in rows}, {'evaluation'})
            self.assertEqual({row['recording_id'] for row in rows}, {3, 4})
            self.assertEqual({key: sum(row['stratum'] == key for row in rows) for key in ('0', '1')}, {'0': 30, '1': 30})

            stock_items = [{'recording_id': 3, 'start_s': float(index * 15), 'scores': {'birdnet': .8}}
                           for index in range(4)]
            stock = SimpleNamespace(dataset_id=1, species_id=SPECIES,
                start_date=date(2024, 8, 8), end_date=date(2024, 8, 8),
                run_id=None, annotation_session_id=None, model_id=None)
            with patch('results_workflow.context', return_value=ctx), \
                    patch('validation.population', return_value=(stock_items, {'birdnet': 'birdnet-sha'}, [])):
                ordinary = start_session(root, stock)
            self.assertNotEqual(ordinary['id'], session['id'])
            self.assertEqual((ordinary['kind'], ordinary['model_id']), ('detection', None))

            unknown_item = None
            with patch('results_workflow.context', return_value=ctx), \
                    patch('custom_models.get_model', return_value=model), \
                    patch('validation.checked_source'), patch('quiz.report_results', return_value={}):
                for index in range(10):
                    item_id = session['question']['id']
                    verdict = 'unknown' if index == 0 else 'target'
                    if verdict == 'unknown':
                        unknown_item = item_id
                    session = answer(root, session['id'], item_id, verdict)
                    if index == 8:
                        self.assertEqual(session['evaluated_count'], 0)
                        self.assertFalse(session['results']['custom']['precision_complete'])
                self.assertEqual(session['evaluated_count'], 0)
                self.assertEqual(session['thresholds']['custom']['threshold'], .73)
                self.assertEqual(session['results']['custom']['unknown'], 1)
                self.assertEqual(session['results']['custom']['precision_sample']['answered'], 10)
                self.assertIsNone(session['results']['custom']['precision_lower_95'])
                self.assertFalse(session['results']['custom']['goal_met'])
                session = answer(root, session['id'], unknown_item, 'other')
            self.assertEqual(session['results']['custom']['unknown'], 0)
            self.assertEqual(session['thresholds']['custom'], {'threshold': .73, 'adjusted': False})
            with connect(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM validation_answers WHERE item_id=?', (unknown_item,)).fetchone()[0], 2)
                annotation = db.execute("INSERT INTO annotation_sessions(dataset_id,fingerprint,snapshot) VALUES(1,'scope-fingerprint','{}')").lastrowid
                item = db.execute("INSERT INTO annotation_items(session_id,position,recording_id,start_s,end_s,stratum) VALUES(?,0,1,0,15,'test')", (annotation,)).lastrowid
                complete = db.execute("INSERT INTO annotation_answers(item_id,ranges,complete,quiz_snapshot,palette_ids) VALUES(?,'[]',1,'{}','[]')", (item,)).lastrowid
                source = {'annotation_source': {'answers': {str(item): complete}}}
                self.assertTrue(annotation_source_current(db, source))
                db.execute("INSERT INTO annotation_answers(item_id,ranges,complete,quiz_snapshot,palette_ids) VALUES(?,'[]',0,'{}','[]')", (item,))
                self.assertFalse(annotation_source_current(db, source))

            stale = {**model, 'applicable': False, 'reason': 'custom_model_labels_changed'}
            with patch('results_workflow.context', return_value=ctx), \
                    patch('custom_models.get_model', return_value=stale):
                view = view_session(root, session['id'])
                self.assertFalse(view['applicable'])
                self.assertEqual(view['model_reason'], 'custom_model_labels_changed')
                with self.assertRaisesRegex(CatalogError, 'custom_model_labels_changed'):
                    answer(root, session['id'], session['question']['id'], 'other')
                with self.assertRaisesRegex(CatalogError, 'custom_model_labels_changed'):
                    finish_calibration(root, session['id'])


if __name__ == '__main__':
    unittest.main()
