"""Region/season markers use recording scope, not the selected acoustic engines."""
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

import region
from results_workflow import context, region_statuses
from server import create_app
from storage import connect
from test_models import fake_birdnet


class RegionCheck(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.models = self.root / 'models'
        self.geo_path = self.models / 'birdnet/geo-models/v2.4/tf/model-fp32.tflite'
        self.geo_path.parent.mkdir(parents=True)
        self.geo_path.touch()
        for day in ('20240807', '20240808'):
            sf.write(self.root / f'{day}_060000.wav', np.zeros(15000), 1000)
        self.client = TestClient(create_app(self.root, self.models, 'test'),
                                 headers={'Authorization': 'Bearer test'})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        imported = self.client.post('/imports', json=dict(folder=str(self.root), site_name='Site',
            dataset_name='Data', timezone='Asia/Tokyo', latitude=35, longitude=138))
        self.assertEqual(imported.status_code, 200, imported.text)
        self.taxon = self.client.get('/species?q=Horornis diphone').json()[0]
        self.name = self.taxon['scientific_name']

    def test_season_changes_overview_revision_and_location_invalidates_cached_badges(self):
        with connect(self.root) as db:
            db.executemany('''INSERT INTO presence_marks(dataset_id,species_id,recording_id,start_s,end_s)
                VALUES(1,?,?,0,3)''', ((self.taxon['id'], rid) for rid in (1, 2)))

        def predict(key, latitude, longitude, week):
            return {self.name} if (latitude, longitude, week) == (35, 138, 29) else set()

        def overview(day):
            response = self.client.get('/datasets/1/overview', params={'start_date': day, 'end_date': day})
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()

        with patch('results_workflow.predict_species', side_effect=predict) as predictor:
            first = overview('2024-08-07')
            self.assertEqual(first['species'][0]['region'], 'usual')
            self.assertEqual(overview('2024-08-07'), first)
            self.assertEqual(predictor.call_count, 1)
            second = overview('2024-08-08')
            self.assertEqual(second['species'][0]['region'], 'unusual')
            self.assertNotEqual(first['revision'], second['revision'])
            self.assertEqual(predictor.call_count, 2)
            with connect(self.root) as db:
                db.execute('UPDATE sites SET latitude=36,longitude=139 WHERE id=1')
            moved = overview('2024-08-07')
            self.assertEqual(moved['species'][0]['region'], 'unusual')
            self.assertNotEqual(first['fingerprint'], moved['fingerprint'])
            self.assertNotEqual(first['revision'], moved['revision'])
            self.assertEqual(predictor.call_count, 3)
            self.geo_path.unlink()
            missing = overview('2024-08-07')
            self.assertIsNone(missing['species'][0]['region'])
            self.assertNotEqual(moved['revision'], missing['revision'])
            self.assertEqual(predictor.call_count, 3)

    def test_any_recorded_week_counts_and_unmapped_species_stay_unknown(self):
        ctx = context(self.root, 1)
        names = [self.name, 'Parus cinereus', 'Example unmapped bird']
        with patch('results_workflow.predict_species', side_effect=[set(), {self.name}]) as predictor:
            self.assertEqual(region_statuses(self.root, ctx, names),
                             dict(zip(names, ['usual', 'unusual', None])))
        self.assertEqual([call.args[-1] for call in predictor.call_args_list], [29, 30])
        self.assertEqual(ctx['models'], [])

    def test_kinglet_only_results_get_the_same_geo_marker(self):
        from analysis import save
        (self.root / 'models.json').write_text(json.dumps({'main': 'kinglet-s', 'extra': [], 'confirmed': True,
                                                         'directory': str(self.models)}))
        with connect(self.root) as db:
            record = dict(db.execute('SELECT * FROM recordings WHERE id=1').fetchone())
            job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',1)").lastrowid
        save(self.root, job, record, 'kinglet-s', {'model_sha256': 'kinglet', 'species': [self.name]},
             {'labels': [self.name], 'scores': np.full((3, 1), .8)}, [])
        with patch('results_workflow.predict_species', return_value={self.name}):
            response = self.client.get('/datasets/1/overview').json()
            row = next(row for row in response['species'] if row['species']['id'] == self.taxon['id'])
            self.assertEqual([model['engine'] for model in row['models']], ['kinglet-s'])
            self.assertEqual(row['region'], 'usual')

    def test_no_recordings_or_unavailable_geo_returns_unknown(self):
        ctx = context(self.root, 1, '2024-08-10', '2024-08-10')
        with patch('results_workflow.predict_species') as predictor:
            self.assertEqual(region_statuses(self.root, ctx, [self.name]), {self.name: None})
            predictor.assert_not_called()
        ctx = context(self.root, 1)
        for error in (FileNotFoundError('no geo labels'), RuntimeError('unusable geo model')):
            with self.subTest(error=error), patch('results_workflow.predict_species', side_effect=error):
                self.assertEqual(region_statuses(self.root, ctx, [self.name]), {self.name: None})

    def test_local_midnight_recordings_use_only_overlapping_dates(self):
        with connect(self.root) as db:
            db.execute("UPDATE recordings SET recorded_at='2024-08-07T14:59:59+00:00',seconds=2 WHERE id=1")
            db.execute("UPDATE recordings SET recorded_at='2024-08-07T00:00:00+00:00' WHERE id=2")
        for first, last, weeks in [('2024-08-07', '2024-08-07', [29]),
                                   ('2024-08-08', '2024-08-08', [30]),
                                   ('2024-08-07', '2024-08-08', [29, 30])]:
            with self.subTest(first=first, last=last), patch('results_workflow.predict_species', return_value=set()) as predictor:
                region_statuses(self.root, context(self.root, 1, first, last), [self.name])
                self.assertEqual([call.args[-1] for call in predictor.call_args_list], weeks)
        with connect(self.root) as db:
            db.execute('UPDATE recordings SET seconds=1 WHERE id=1')
        with patch('results_workflow.predict_species') as predictor:
            region_statuses(self.root, context(self.root, 1, '2024-08-08', '2024-08-08'), [self.name])
            predictor.assert_not_called()


class GeoCacheCheck(unittest.TestCase):
    def setUp(self):
        region.predict_species.cache_clear()
        region.geo_session.cache_clear()
        self.addCleanup(region.predict_species.cache_clear)
        self.addCleanup(region.geo_session.cache_clear)
        self.addCleanup(region._sessions.close)

    def test_week_number_month_and_year_boundaries(self):
        for text, week in [('2024-01-01', 1), ('2024-01-07', 1), ('2024-01-08', 2),
                           ('2024-01-14', 2), ('2024-01-15', 3), ('2024-01-21', 3),
                           ('2024-01-22', 4), ('2024-01-28', 4), ('2024-01-29', 4),
                           ('2024-01-31', 4), ('2024-02-01', 5), ('2024-02-29', 8),
                           ('2024-12-31', 48)]:
            with self.subTest(date=text):
                self.assertEqual(region.week_number(date.fromisoformat(text)), week)

    def test_lazy_session_and_predictions_are_reused_per_location_and_week(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            path = directory / 'birdnet/geo-models/v2.4/tf/model-fp32.tflite'
            path.parent.mkdir(parents=True)
            path.touch()
            with fake_birdnet(directory, []) as state, patch('birdnet.load') as load:
                session = load.return_value.predict_session.return_value.__enter__.return_value
                session.run.return_value.to_structured_array.return_value = np.array(
                    [('Horornis diphone_Japanese Bush Warbler',)], dtype=[('species_name', 'U128')])
                key = region.model_key(directory)
                load.assert_not_called()
                self.assertEqual(region.predict_species(key, 35, 138, 29), frozenset({'Horornis diphone'}))
                self.assertEqual(region.predict_species(key, 35, 138, 29), frozenset({'Horornis diphone'}))
                region.predict_species(key, 35, 138, 30)
                region.predict_species(key, 36, 138, 29)
                region.predict_species(key, 35, 139, 29)
                self.assertEqual(session.run.call_count, 4)
                load.assert_called_once_with('geo', '2.4', 'tf', library='litert')
                load.return_value.predict_session.assert_called_once_with(min_confidence=0.03)
                self.assertEqual(state['app_data'], (directory / 'birdnet').resolve())

    def test_missing_model_is_not_downloaded(self):
        with tempfile.TemporaryDirectory() as temp:
            with fake_birdnet(Path(temp), []) as state, patch('birdnet.load') as load:
                with self.assertRaises(FileNotFoundError):
                    region.predict_species((temp, None), 35, 138, 1)
                load.assert_not_called()


if __name__ == '__main__':
    unittest.main()
