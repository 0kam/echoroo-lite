"""Sounds checked by ear: counted per species, listed, put right, and other species added on a question card."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

import labels
from analysis import digest
from models import REGISTRY
from server import create_app
from species import initialize_species
from storage import connect, initialize
from test_models import FakeSession, model_files, wait_job


class HeardTest(unittest.TestCase):
    def site(self, temp, files=('20240808_060000.wav',)):
        root = Path(temp); directory = root / 'models'; model_files(directory)
        audio = root / 'audio'; audio.mkdir()
        for name in files:
            sf.write(audio / name, np.full(32000 * 20, .25), 32000)
        self.data = root / 'data'
        client = TestClient(create_app(self.data, directory, 'test'), headers={'Authorization': 'Bearer test'})
        client.__enter__(); self.addCleanup(client.__exit__, None, None, None)
        session = FakeSession('kinglet-xs', None)
        with patch('analysis.load_onnx', return_value=(session, digest(directory / REGISTRY['kinglet-xs']['files'][0]))):
            client.post('/imports', json=dict(folder=str(audio), site_name='Site', dataset_name='Data',
                                              timezone='Asia/Tokyo', latitude=35, longitude=138))
            client.put('/models', json={'main': 'kinglet-xs', 'extra': []})
            client.post('/jobs', json={'dataset_id': 1})
            self.assertEqual(wait_job(client)['status'], 'complete')
        return client

    def species(self, client, name):
        return next(s for s in client.get('/species', params={'q': name}).json() if s['scientific_name'] == name)['id']

    def test_answers_from_every_screen_are_counted_listed_and_put_right(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.site(temp)
            warbler, tit = self.species(client, 'Horornis diphone'), self.species(client, 'Parus cinereus')
            mark = lambda start, marked=True: client.post('/presence/marks', json=dict(dataset_id=1, species_id=warbler,
                recording_id=1, start_s=start, end_s=start + 5, marked=marked))
            self.assertEqual(mark(0).status_code, 200)
            self.assertEqual(mark(5).status_code, 200)
            with connect(self.data) as db:
                labels.put(db, warbler, 1, 10, 15, 'absent', 'training')
                labels.put(db, tit, 1, 0, 5, 'unknown', 'validation', {'session_id': None})
            summary = client.get('/datasets/1/heard').json()
            self.assertEqual([(r['species']['scientific_name'], r['site'], r['ai']['state'], r['ai']['needed']) for r in summary['rows']],
                [('Horornis diphone', {'present': 2, 'absent': 1, 'unknown': 0}, 'needed', 17),
                 ('Parus cinereus', {'present': 0, 'absent': 0, 'unknown': 1}, 'needed', 20)])
            self.assertEqual((summary['total'], summary['requirements']), ({'site': 4, 'all': 4}, {'total': 20, 'each': 3}))
            self.assertEqual(client.get('/datasets/1/heard', params={'species_id': warbler}).json()['site'], {'present': 2, 'absent': 1, 'unknown': 0})

            listed = client.get('/datasets/1/heard/records').json()
            self.assertEqual(listed['total'], 4)
            self.assertEqual([(r['species']['scientific_name'], r['verdict'], r['screen'], r['start_s']) for r in listed['rows']],
                [('Parus cinereus', 'unknown', 'validation', 0), ('Horornis diphone', 'absent', 'training', 10),
                 ('Horornis diphone', 'present', 'species_mark', 5), ('Horornis diphone', 'present', 'species_mark', 0)])
            self.assertEqual(listed['rows'][0]['local_time'][:19], '2024-08-08T06:00:00')
            self.assertEqual(listed['options']['screens'], ['species_mark', 'training', 'validation'])
            heard = client.get('/datasets/1/heard/records', params={'species_id': warbler, 'verdict': 'present', 'order': 'time'}).json()
            self.assertEqual([r['start_s'] for r in heard['rows']], [0, 5])
            self.assertEqual(client.get('/datasets/1/heard/records', params={'day': '2024-08-09'}).json()['total'], 0)
            self.assertEqual(len(client.get('/datasets/1/heard/records', params={'limit': 1, 'offset': 1}).json()['rows']), 1)

            # Both heard sounds were another species: the warbler is "not heard" there, the tit "heard".
            ids = [r['id'] for r in heard['rows']]
            self.assertEqual(client.post('/heard/fix', json=dict(dataset_id=1, ids=ids, action='other', species_id=tit)).json()['changed'], 2)
            summary = {r['species']['scientific_name']: r['site'] for r in client.get('/datasets/1/heard').json()['rows']}
            self.assertEqual(summary, {'Horornis diphone': {'present': 0, 'absent': 3, 'unknown': 0},
                                       'Parus cinereus': {'present': 2, 'absent': 0, 'unknown': 0}})
            # The list was shown before the change: its ids are no longer the current answers.
            stale = client.post('/heard/fix', json=dict(dataset_id=1, ids=ids, action='unknown'))
            self.assertEqual((stale.status_code, stale.json()['detail']), (400, 'annotation_changed'))
            # The species page no longer counts the sounds as evidence, and the species AI counts them as "not heard".
            self.assertEqual(client.get(f'/datasets/1/species/{warbler}/presence').json()['presence']['status'], 'unchecked')
            self.assertEqual(client.get(f'/datasets/1/species/{warbler}/custom-model').json()['examples'],
                             {'positive_5s': 0, 'negative_5s': 3, 'other_answers': 2})

            sound = client.get('/datasets/1/heard/sound', params=dict(recording_id=1, start_s=0, end_s=5)).json()
            self.assertEqual(sound['file'].rsplit('/', 1)[-1].rsplit('\\', 1)[-1], '20240808_060000.wav')
            self.assertEqual(sorted((r['species']['scientific_name'], r['verdict'], r['screen']) for r in sound['now']),
                [('Horornis diphone', 'absent', 'review'), ('Parus cinereus', 'present', 'review')])
            self.assertEqual([(r['species']['scientific_name'], r['verdict'], r['screen'], r['current']) for r in sound['history']],
                [('Parus cinereus', 'present', 'review', True), ('Horornis diphone', 'absent', 'review', True),
                 ('Parus cinereus', 'unknown', 'validation', False), ('Horornis diphone', 'present', 'species_mark', False)])
            # "Back to this record": the earlier answer is given again.
            old = sound['history'][3]
            client.post('/heard/fix', json=dict(dataset_id=1, ids=[old['current_id']], action=old['verdict']))
            sound = client.get('/datasets/1/heard/sound', params=dict(recording_id=1, start_s=0, end_s=5)).json()
            self.assertEqual([r['verdict'] for r in sound['now'] if r['species']['id'] == warbler], ['present'])
            # Adding a species leaves the answer as it is; deleting takes the sound back to "never answered".
            current = next(r['id'] for r in sound['now'] if r['species']['id'] == warbler)
            family = client.get('/datasets/1/heard/species', params={'q': 'キツツキ'}).json()['families'][0]
            self.assertEqual((family['scientific_name'], family['rank']), ('Picidae', 'family'))
            client.post('/heard/fix', json=dict(dataset_id=1, ids=[current], action='add', species_id=family['id']))
            rows = {r['species']['scientific_name']: r for r in client.get('/datasets/1/heard').json()['rows']}
            self.assertEqual((rows['Picidae']['site']['present'], rows['Picidae']['ai']), (1, {'state': 'family'}))
            self.assertEqual(rows['Horornis diphone']['site'], {'present': 1, 'absent': 2, 'unknown': 0})
            client.post('/heard/fix', json=dict(dataset_id=1, ids=[current], action='delete'))
            self.assertEqual(client.get('/datasets/1/heard', params={'species_id': warbler}).json()['site'], {'present': 0, 'absent': 2, 'unknown': 0})
            with connect(self.data) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM labels WHERE taxon_id=? AND start_s=0', (warbler,)).fetchone()[0], 0)
            self.assertEqual(client.post('/heard/fix', json=dict(dataset_id=1, ids=[1], action='other')).status_code, 422)

    def test_another_species_is_added_on_a_question_card_and_taken_back(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.site(temp)
            warbler, tit, bulbul = (self.species(client, name) for name in ('Horornis diphone', 'Parus cinereus', 'Hypsipetes amaurotis'))
            body = dict(dataset_id=1, recording_id=1, start_s=5, end_s=10, of=warbler, screen='validation')
            added = client.post('/heard/others', json={**body, 'species_id': tit, 'on': True}).json()
            self.assertEqual([(o['species']['scientific_name'], o['removable']) for o in added], [('Parus cinereus', True)])
            with connect(self.data) as db:
                labels.put(db, bulbul, 1, 5, 10, 'present', 'sound_map')
            listed = client.get('/datasets/1/heard/others', params=dict(recording_id=1, start_s=5, end_s=10, of=warbler)).json()
            self.assertEqual([(o['species']['scientific_name'], o['removable']) for o in listed], [('Parus cinereus', True), ('Hypsipetes amaurotis', False)])
            row = client.get('/datasets/1/heard/records', params={'species_id': tit}).json()['rows'][0]
            self.assertEqual((row['verdict'], row['screen'], row['extra']), ('present', 'validation', True))
            # It is evidence on the tit's page like any other heard sound.
            self.assertEqual(client.get(f'/datasets/1/species/{tit}/presence').json()['presence']['status'], 'confirmed')
            # Taking back removes only what a card added; the sound-map answer stays.
            for species in (tit, bulbul):
                left = client.post('/heard/others', json={**body, 'species_id': species, 'on': False}).json()
            self.assertEqual([o['species']['scientific_name'] for o in left], ['Hypsipetes amaurotis'])
            self.assertEqual(client.post('/heard/others', json={**body, 'species_id': warbler, 'on': True}).status_code, 400)
            self.assertEqual(client.post('/heard/others', json={**body, 'end_s': 500, 'species_id': tit, 'on': True}).status_code, 400)

    def test_examples_for_a_search_avoid_mixed_sounds_and_spread_over_days(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.site(temp, ('20240808_060000.wav', '20240809_050000.wav'))
            warbler, tit = self.species(client, 'Horornis diphone'), self.species(client, 'Parus cinereus')
            with connect(self.data) as db:
                for recording, start in ((1, 0), (1, 5), (1, 10), (1, 15), (2, 0), (2, 5), (2, 10)):
                    labels.put(db, warbler, recording, start, start + 5, 'present', 'species_mark')
                labels.put(db, warbler, 1, 1, 4, 'present', 'validation')  # the same call as the 0-5 s answer
                labels.put(db, tit, 2, 0, 5, 'present', 'sound_map')
                labels.put(db, tit, 1, 15, 20, 'absent', 'training')  # "not heard" does not make a sound mixed
            found = client.get(f'/datasets/1/species/{warbler}/heard-examples').json()
            self.assertEqual((found['heard'], found['mixed'], len(found['examples'])), (7, 1, 5))
            self.assertEqual(found['mixed_example']['species'][0]['scientific_name'], 'Parus cinereus')
            chosen = [(e['recording_id'], e['start_s']) for e in found['examples']]
            self.assertNotIn((2, 0), chosen)
            self.assertEqual({recording for recording, _ in chosen}, {1, 2})
            self.assertEqual(sum(recording == 2 for recording, _ in chosen), 2)

    def test_labels_table_made_before_review_is_rebuilt(self):
        with tempfile.TemporaryDirectory() as temp:
            initialize(temp)
            initialize_species(temp)
            with connect(temp) as db:
                db.execute('DROP TABLE labels')
                db.executescript(labels.SCHEMA.replace(", 'review'", ''))
            initialize(temp)
            with connect(temp) as db:
                self.assertIn("'review'", db.execute("SELECT sql FROM sqlite_master WHERE name='labels'").fetchone()[0])
                self.assertEqual(db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name IN ('label_key','label_recording')").fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
