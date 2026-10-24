"""Species examples: named first, confirmed newest first, never a current question."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import examples  # noqa: F401  Imported before patching species.ebird_codes.
from examples import fetch_xeno_canto
from fastapi.testclient import TestClient
from quiz_test_bundle import quiz_bundle
from server import create_app
from storage import connect


class ExamplesCheck(unittest.TestCase):
    def setUp(self):
        self.bundle = quiz_bundle()
        self.bundle.__enter__()
        self.addCleanup(self.bundle.__exit__, None, None, None)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for hour in ('060000', '070000', '080000', '090000'):
            sf.write(self.root / f'20240808_{hour}.wav', np.zeros(360000), 1000)
        self.client = TestClient(create_app(self.root, self.root, 'test'), headers={'Authorization': 'Bearer test'})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.post('/imports', dict(folder=str(self.root), site_name='Site', dataset_name='Data',
                                   timezone='Asia/Tokyo', latitude=35, longitude=138))
        self.species = [self.client.get(f'/species?q={name}').json()[0] for name in ('ウグイス', 'シジュウカラ')]
        labels = ['Horornis diphone_Japanese Bush Warbler', 'Parus minor_Japanese Tit']
        with connect(self.root) as db:
            job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',4)").lastrowid
            for rid in range(1, 5):
                path = self.root / f'bird-{rid}.npz'
                scores = np.column_stack((np.tile([.02, .2, .8], 40), np.tile([.8, .02, .2], 40)))
                starts = np.arange(120) * 3
                np.savez(path, labels=np.tile(labels, 120), scores=scores.ravel(),
                         start_s=np.repeat(starts, 2), end_s=np.repeat(starts + 3, 2))
                db.execute('INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,?,?,?)',
                           (job, rid, 'birdnet', json.dumps({'model_sha256': 'birdnet', 'species': labels}), str(path)))
        self.scope = {'dataset_id': 1, 'start_date': '2024-08-08', 'end_date': '2024-08-08'}
    def post(self, path, body):
        response = self.client.post(path, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def examples(self, species, **query):
        response = self.client.get(f"/species/{species['id']}/examples", params={'dataset_id': 1, **query})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_order_exclusion_and_snapshots(self):
        warbler = self.species[0]  # Horornis diphone is in the bundled quiz.
        xc = self.root / 'XC1.wav'
        sf.write(xc, np.zeros(20000), 1000)
        reference = self.post('/references', {'path': str(xc)})
        with connect(self.root) as db:
            db.execute('INSERT INTO xc_recordings(id,metadata,reference_id) VALUES(1,?,?)', (json.dumps(
                {'id': 1, 'scientific_name': 'Horornis diphone', 'recordist': 'A. Recordist',
                 'country': 'France', 'quality': 'B',
                 'license': '//creativecommons.org/licenses/by-nc-sa/4.0/', 'url': 'https://xeno-canto.org/1'}), reference['id']))
            # A sound confirmed on the presence list; newer than any check answer.
            db.execute("INSERT INTO presence_marks(dataset_id,species_id,recording_id,start_s,end_s,created_at) VALUES(1,?,1,300,305,'2999-01-01 00:00:00')",
                       (warbler['id'],))
        free = self.examples(warbler)
        self.assertTrue(free['in_quiz'])
        self.assertEqual(free['ebird_code'], 'jabwar')  # From the shipped eBird taxonomy mapping.
        self.assertEqual([e['label']['origin'] for e in free['examples']], ['quiz', 'xeno-canto', 'presence'])
        self.assertEqual(free['examples'][1]['label']['recordist'], 'A. Recordist')
        self.assertEqual((free['examples'][1]['label']['country'],free['examples'][1]['label']['quality']),('France','B'))
        self.assertEqual((free['examples'][1]['start_s'], free['examples'][1]['end_s']), (0, 5))
        marked = free['examples'][2]
        self.assertEqual((marked['start_s'], marked['end_s']), (300, 305))
        self.assertIn('T', marked['label']['local_time'])

        # Every window of the current check is excluded, including a practice clip cut from one of them.
        validation = self.post('/validations', {**self.scope, 'species_id': warbler['id'], 'model': 'birdnet'})
        question = validation['question']
        clip = self.post('/quiz/clips', {'species_id': warbler['id'], 'source_kind': 'recording', 'source_id': question['source']['id'],
                                         'start_s': question['start_s'], 'end_s': question['start_s'] + 5})
        guarded = self.examples(warbler, validation_id=validation['id'])
        self.assertNotIn(clip['id'], [e['label']['id'] for e in guarded['examples'] if e['label']['origin'] == 'quiz'])
        refs = [{'kind': 'named', 'origin': 'quiz', 'id': 1}, {'kind': 'confirmed', 'origin': 'presence', 'id': 1}]
        self.post(f"/validations/{validation['id']}/answers", {'item_id': question['id'], 'verdict': 'target',
                                                               'version': question['version'], 'examples_snapshot': refs})
        with connect(self.root) as db:
            db.execute('UPDATE quiz_clips SET enabled=0 WHERE id=?', (clip['id'],))
            saved = db.execute('SELECT examples_snapshot FROM window_answers WHERE examples_snapshot IS NOT NULL').fetchall()
        self.assertEqual([json.loads(r[0]) for r in saved], [refs])
        self.assertEqual([e['label']['origin'] for e in self.examples(warbler)['examples']], ['quiz', 'xeno-canto', 'presence', 'validation'])
        self.assertNotIn('validation', [e['label']['origin'] for e in self.examples(warbler, validation_id=validation['id'])['examples']])

        # Without a Xeno-Canto key the one-button fetch reports the existing error code.
        response = self.client.post(f"/species/{warbler['id']}/examples/xeno-canto", json={'dataset_id': 1})
        self.assertEqual(response.json()['detail'], 'xc_key_required')

    def test_ebird_codes(self):
        from species import ebird_codes
        self.assertEqual(ebird_codes()['Tarsiger cyanurus'], 'refblu1')  # Shipped mapping built by scripts/build_ebird_codes.py.
        self.assertEqual(self.client.get(f"/species/{self.species[0]['id']}").json()['ebird_code'], 'jabwar')
        with patch('species.ebird_codes', return_value={}), patch('examples.ebird_codes', return_value={}):
            self.assertIsNone(self.client.get(f"/species/{self.species[0]['id']}").json()['ebird_code'])
            self.assertIsNone(self.examples(self.species[0])['ebird_code'])
        spec = importlib.util.spec_from_file_location('build_ebird_codes', Path(__file__).parents[2] / 'scripts/build_ebird_codes.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / 'taxonomy.csv', Path(folder) / 'codes.json'
            source.write_text('\ufeffCATEGORY,Species_Code,SCIENTIFIC_NAME\nspecies,jabwar,Horornis diphone\nissf,x,Horornis diphone x\n'
                              'species,azutit,Cyanistes cyanus\n', encoding='utf-8')
            with patch.object(script, 'OUTPUT', output):
                script.main(source)
            self.assertEqual(list(json.loads(output.read_text(encoding='utf-8')).items()),
                             [('Cyanistes cyanus', 'azutit'), ('Horornis diphone', 'jabwar')])

    def test_xeno_canto_falls_back_in_agreed_order(self):
        warbler = self.species[0]
        calls = []
        def search(_root, _scientific, country, quality, _page):
            calls.append((country, quality))
            recordings = [] if (country, quality) != ('', 'A') else [
                {'id': 41, 'scientific_name': 'Horornis diphone'}]
            return {'recordings': recordings}
        with patch('xeno_canto.search', side_effect=search), patch('xeno_canto.import_recording') as imported:
            fetch_xeno_canto(self.root, warbler['id'])
        self.assertEqual(calls, [('Japan', 'A'), ('Japan', 'B'), ('', 'A')])
        imported.assert_called_once_with(self.root, 41)

    def test_search_interval_wins_over_generic_xeno_canto_interval(self):
        warbler = self.species[0]
        path = self.root / 'XC77.wav';sf.write(path, np.zeros(20000), 1000)
        reference = self.post('/references', {'path': str(path)})
        with connect(self.root) as db:
            db.execute('INSERT INTO xc_recordings(id,metadata,reference_id) VALUES(77,?,?)', (json.dumps(
                {'id': 77, 'scientific_name': 'Horornis diphone', 'country': 'France', 'quality': 'B'}), reference['id']))
            source = {**reference, 'attribution': {'recordist': '', 'license': '', 'url': ''}}
            snapshot = {'references': [{'source': source, 'query': {'start_s': 16, 'end_s': 19}}]}
            db.execute('INSERT INTO search_runs(dataset_id,species_id,snapshot) VALUES(1,?,?)', (warbler['id'], json.dumps(snapshot)))
        result = self.examples(warbler)['examples']
        chosen = next(row for row in result if row['source']['id'] == reference['id'])
        self.assertEqual((chosen['label']['origin'], chosen['start_s'], chosen['end_s']), ('search-reference', 16, 19))
        self.assertNotIn('xeno-canto', [row['label']['origin'] for row in result if row['source']['id'] == reference['id']])


if __name__ == '__main__':
    unittest.main()
