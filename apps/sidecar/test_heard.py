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

            # Both heard sounds were another species: they move to the tit and are no longer rows of the warbler,
            # for which they are "not heard" all the same (history, and material for its AI below).
            ids = [r['id'] for r in heard['rows']]
            self.assertEqual(client.post('/heard/fix', json=dict(dataset_id=1, ids=ids, action='other', species_id=tit)).json()['changed'], 2)
            summary = {r['species']['scientific_name']: r['site'] for r in client.get('/datasets/1/heard').json()['rows']}
            self.assertEqual(summary, {'Horornis diphone': {'present': 0, 'absent': 1, 'unknown': 0},
                                       'Parus cinereus': {'present': 2, 'absent': 0, 'unknown': 0}})
            self.assertEqual([(r['species']['scientific_name'], r['start_s']) for r in client.get('/datasets/1/heard/records', params={'species_id': warbler}).json()['rows']],
                             [('Horornis diphone', 10)])
            # The list was shown before the change: its ids are no longer the current answers.
            stale = client.post('/heard/fix', json=dict(dataset_id=1, ids=ids, action='unknown'))
            self.assertEqual((stale.status_code, stale.json()['detail']), (400, 'annotation_changed'))
            # The species page no longer counts the sounds as evidence, and the species AI counts them as "not heard".
            self.assertEqual(client.get(f'/datasets/1/species/{warbler}/presence').json()['presence']['status'], 'unchecked')
            self.assertEqual(client.get(f'/datasets/1/species/{warbler}/custom-model').json()['examples'],
                             {'positive_5s': 0, 'negative_5s': 3, 'other_answers': 2, 'no_bird_answers': 0})

            sound = client.get('/datasets/1/heard/sound', params=dict(recording_id=1, start_s=0, end_s=5)).json()
            self.assertEqual(sound['file'].rsplit('/', 1)[-1].rsplit('\\', 1)[-1], '20240808_060000.wav')
            self.assertEqual(sorted((r['species']['scientific_name'], r['verdict'], r['screen']) for r in sound['now']),
                [('Parus cinereus', 'present', 'review')])
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
            self.assertEqual(rows['Horornis diphone']['site'], {'present': 1, 'absent': 1, 'unknown': 0})
            # A family answer named as a species is replaced by it, not turned into "family not heard".
            picidae = next(r['id'] for r in client.get('/datasets/1/heard/records', params={'species_id': family['id']}).json()['rows'])
            woodpecker = self.species(client, 'Dendrocopos major')
            client.post('/heard/fix', json=dict(dataset_id=1, ids=[picidae], action='other', species_id=woodpecker))
            rows = {r['species']['scientific_name']: r['site'] for r in client.get('/datasets/1/heard').json()['rows']}
            self.assertNotIn('Picidae', rows)
            self.assertEqual(rows['Dendrocopos major'], {'present': 1, 'absent': 0, 'unknown': 0})
            self.assertEqual(client.get('/datasets/1/heard/species', params={'q': 'きつつきか'}).json()['families'][0]['scientific_name'], 'Picidae')
            client.post('/heard/fix', json=dict(dataset_id=1, ids=[current], action='delete'))
            self.assertEqual(client.get('/datasets/1/heard', params={'species_id': warbler}).json()['site'], {'present': 0, 'absent': 1, 'unknown': 0})
            with connect(self.data) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM labels WHERE taxon_id=? AND start_s=0', (warbler,)).fetchone()[0], 0)
            self.assertEqual(client.post('/heard/fix', json=dict(dataset_id=1, ids=[1], action='other')).status_code, 422)

    def test_no_bird_sound_is_a_record_of_the_sound_and_no_call_for_every_species(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.site(temp)
            warbler, tit = self.species(client, 'Horornis diphone'), self.species(client, 'Parus cinereus')
            examples = lambda species: client.get(f'/datasets/1/species/{species}/custom-model').json()['examples']
            listed = lambda: sorted((r['species'].get('rank', r['species']['scientific_name']), r['verdict'], r['screen'], r['start_s'])
                                    for r in client.get('/datasets/1/heard/records').json()['rows'])
            with connect(self.data) as db:
                labels.put(db, warbler, 1, 0, 5, 'present', 'sound_map')
                labels.put(db, tit, 1, 0, 5, 'absent', 'training')
            # Put right to "no bird sound": the warbler heard there becomes "not heard" (kept as history, not a row).
            row = client.get('/datasets/1/heard/records', params={'species_id': warbler}).json()['rows'][0]
            self.assertEqual(client.post('/heard/fix', json=dict(dataset_id=1, ids=[row['id']], action='no_bird')).json()['changed'], 1)
            self.assertEqual(listed(), [('Parus cinereus', 'absent', 'training', 0), ('none', 'present', 'review', 0)])
            sound = client.get('/datasets/1/heard/sound', params=dict(recording_id=1, start_s=0, end_s=5)).json()
            self.assertEqual([(r['species']['scientific_name'], r['verdict'], r['current']) for r in sound['history'] if r['species']['id'] == warbler],
                             [('Horornis diphone', 'absent', True), ('Horornis diphone', 'present', False)])
            # The sound map's answer, for the map to call: no species, verdict 'no_bird'.
            saved = client.post('/heard/map-answer', json=dict(dataset_id=1, recording_id=1, start_s=5, end_s=10, verdict='no_bird'))
            self.assertEqual([(r['species']['rank'], r['verdict'], r['screen']) for r in saved.json()['now']], [('none', 'present', 'sound_map')])
            self.assertEqual(client.post('/heard/map-answer', json=dict(dataset_id=1, recording_id=1, start_s=5, end_s=10, verdict='no_bird', species_id=tit)).status_code, 422)
            # It is not a species: not in the counts table, the species search, the pickers, or the site's species list.
            summary = client.get('/datasets/1/heard').json()
            self.assertEqual(([r['species']['scientific_name'] for r in summary['rows']], summary['no_bird'], summary['total']),
                             (['Parus cinereus'], {'site': 2, 'all': 2}, {'site': 3, 'all': 3}))
            self.assertEqual(client.get('/species', params={'q': 'no bird'}).json(), [])
            self.assertEqual(client.get('/datasets/1/heard/species', params={'q': 'no bird'}).json(), {'species': [], 'families': []})
            self.assertEqual(client.get('/datasets/1/heard/others', params=dict(recording_id=1, start_s=5, end_s=10, of=tit)).json(), [])
            self.assertFalse(any(s['species'].get('rank') or s['presence']['status'] == 'confirmed' for s in client.get('/datasets/1/overview').json()['species']))
            self.assertEqual(client.get('/datasets/1/heard/records', params={'verdict': 'present'}).json()['total'], 0)
            # "No call" material for every species, but a species' own answer on the piece wins.
            self.assertEqual(examples(warbler), {'positive_5s': 0, 'negative_5s': 2, 'other_answers': 1, 'no_bird_answers': 1})
            self.assertEqual(examples(tit), {'positive_5s': 0, 'negative_5s': 2, 'other_answers': 0, 'no_bird_answers': 1})
            self.assertEqual(client.get('/datasets/1/heard').json()['rows'][0]['ai']['needed'], 18)
            # A species heard there afterwards takes "no bird sound" back.
            client.post('/heard/map-answer', json=dict(dataset_id=1, recording_id=1, start_s=5, end_s=10, verdict='present', species_id=tit))
            self.assertEqual(listed(), [('Parus cinereus', 'absent', 'training', 0), ('Parus cinereus', 'present', 'sound_map', 5), ('none', 'present', 'review', 0)])
            self.assertEqual(examples(tit), {'positive_5s': 1, 'negative_5s': 1, 'other_answers': 1, 'no_bird_answers': 0})
            self.assertEqual(examples(warbler)['no_bird_answers'], 0)
            # The record itself: a species was calling after all, or deleted; it has no "heard / not heard".
            none = next(r for r in client.get('/datasets/1/heard/records').json()['rows'] if r['species'].get('rank') == 'none')
            self.assertEqual(client.post('/heard/fix', json=dict(dataset_id=1, ids=[none['id']], action='absent')).status_code, 400)
            client.post('/heard/fix', json=dict(dataset_id=1, ids=[none['id']], action='other', species_id=warbler))
            self.assertEqual(listed(), [('Horornis diphone', 'present', 'review', 0), ('Parus cinereus', 'absent', 'training', 0), ('Parus cinereus', 'present', 'sound_map', 5)])
            # A "not heard" answer put right to "no bird sound" leaves the list too, as a "heard" one does: one row for the sound.
            row = next(r for r in client.get('/datasets/1/heard/records', params={'species_id': tit}).json()['rows'] if r['verdict'] == 'absent')
            client.post('/heard/fix', json=dict(dataset_id=1, ids=[row['id']], action='no_bird'))
            self.assertEqual(listed(), [('Parus cinereus', 'present', 'sound_map', 5), ('none', 'present', 'review', 0)])
            self.assertEqual(examples(tit), {'positive_5s': 1, 'negative_5s': 1, 'other_answers': 2, 'no_bird_answers': 0})

    def test_a_complete_sound_is_no_call_for_every_species_not_heard_in_it(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.site(temp)
            warbler, tit, bulbul = (self.species(client, name) for name in ('Horornis diphone', 'Parus cinereus', 'Hypsipetes amaurotis'))
            at = dict(recording_id=1, start_s=0, end_s=5)
            answer = lambda verdict, species=None: client.post('/heard/map-answer', json=dict(dataset_id=1, verdict=verdict, **at, **({'species_id': species} if species else {})))
            examples = lambda species: client.get(f'/datasets/1/species/{species}/custom-model').json()['examples']
            listed = lambda: sorted((r['species'].get('rank', r['species']['scientific_name']), r['verdict']) for r in client.get('/datasets/1/heard/records').json()['rows'])
            pick = lambda: client.put('/explore/picked', json=dict(dataset_id=1, **at, sound_type='', quality='', others=False)).json()
            shape = lambda: (lambda p: ([s['scientific_name'] for s in [p['species'], *p['background']] if s], p['complete']))(pick())
            answer('present', warbler)
            self.assertEqual((shape(), examples(tit)['negative_5s']), ((['Horornis diphone'], False), 0))
            # Complete with one species: "no call" for another species, not for the one heard; one row in the list.
            self.assertEqual(answer('complete').status_code, 200)
            self.assertEqual(shape(), (['Horornis diphone'], True))
            self.assertEqual(examples(tit), {'positive_5s': 0, 'negative_5s': 1, 'other_answers': 0, 'no_bird_answers': 1})
            self.assertEqual(examples(warbler), {'positive_5s': 1, 'negative_5s': 0, 'other_answers': 1, 'no_bird_answers': 0})
            self.assertEqual(listed(), [('Horornis diphone', 'present')])
            # A species added later is heard there, not "no call"; the sound stays complete for the rest.
            answer('present', tit)
            self.assertEqual((shape(), examples(tit)['negative_5s'], examples(bulbul)['negative_5s']), ((['Horornis diphone', 'Parus cinereus'], True), 0, 1))
            # Taken back: no "no call" from it any more.
            answer('incomplete')
            self.assertEqual((shape()[1], examples(bulbul)['negative_5s'], listed()), (False, 0, [('Horornis diphone', 'present'), ('Parus cinereus', 'present')]))
            # The last species withdrawn from a complete sound leaves "no bird sound"; a species heard takes that back, as on every screen.
            answer('complete'); answer('withdraw', warbler); answer('withdraw', tit)
            self.assertEqual((shape(), listed(), examples(tit)['no_bird_answers']), (([], True), [('none', 'present')], 1))
            answer('present', bulbul)
            self.assertEqual((shape(), listed(), examples(tit)['negative_5s']), ((['Hypsipetes amaurotis'], False), [('Hypsipetes amaurotis', 'present')], 0))
            # With nothing heard, "complete" is "no bird sound", and taking it back leaves nothing.
            answer('withdraw', bulbul); answer('incomplete')
            self.assertEqual((shape(), listed(), examples(tit)['negative_5s']), (([], False), [], 0))
            answer('complete')
            self.assertEqual((listed(), answer('complete', tit).status_code, answer('withdraw').status_code), ([('none', 'present')], 422, 422))
            # Unticking the sound takes back what was recorded on it: the species heard, the mark and the pick. Another
            # sound, and a "not heard" given on another screen, stay.
            other = dict(dataset_id=1, recording_id=1, start_s=5, end_s=10)
            client.put('/explore/picked', json=dict(**other, sound_type='', quality='', others=False))
            client.post('/heard/map-answer', json=dict(**other, verdict='present', species_id=tit))
            client.post('/heard/map-answer', json=dict(**other, verdict='complete'))
            with connect(self.data) as db:
                labels.put(db, bulbul, 1, 0, 5, 'absent', 'training')
            answer('withdraw', tit); answer('present', warbler); answer('complete'); pick()
            self.assertEqual((examples(tit)['negative_5s'], examples(warbler)['negative_5s'], examples(bulbul)['negative_5s']), (1, 1, 2))
            self.assertEqual(client.delete('/explore/picked', params=dict(dataset=1, **at)).json(), {'deleted': True})
            self.assertEqual(listed(), [('Hypsipetes amaurotis', 'absent'), ('Parus cinereus', 'present')])
            self.assertEqual((examples(tit)['negative_5s'], examples(warbler), examples(bulbul)['negative_5s']),
                             (0, {'positive_5s': 0, 'negative_5s': 1, 'other_answers': 0, 'no_bird_answers': 1}, 2))
            with connect(self.data) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM labels WHERE start_s=0 AND taxon_id<>?', (bulbul,)).fetchone()[0], 0)
            items = client.get('/explore/picked', params={'dataset': 1}).json()['items']
            self.assertEqual([(i['start_s'], i['species']['scientific_name'], i['complete']) for i in items], [(5, 'Parus cinereus', True)])
            self.assertEqual(shape(), ([], False))
            answer('complete')
            # The species entered last on the map come first.
            answer('present', tit); answer('present', warbler)
            self.assertEqual([s['scientific_name'] for s in client.get('/explore/picked', params={'dataset': 1}).json()['recent']], ['Horornis diphone', 'Parus cinereus'])

    def test_the_sound_map_takes_back_only_what_it_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.site(temp)
            warbler, tit, bulbul = (self.species(client, name) for name in ('Horornis diphone', 'Parus cinereus', 'Hypsipetes amaurotis'))
            at, later = dict(recording_id=1, start_s=0, end_s=5), dict(recording_id=1, start_s=5, end_s=10)
            answer = lambda verdict, species=None, where=at: self.assertEqual(client.post('/heard/map-answer', json=dict(
                dataset_id=1, verdict=verdict, **where, **({'species_id': species} if species else {}))).status_code, 200)
            pick = lambda where=at: client.put('/explore/picked', json=dict(dataset_id=1, **where, sound_type='', quality='B', others=False)).json()
            unpick = lambda where=at: self.assertEqual(client.delete('/explore/picked', params=dict(dataset=1, **where)).json(), {'deleted': True})
            listed = lambda: sorted((r['species'].get('rank', r['species']['scientific_name']), r['verdict'], r['screen'], r['start_s'])
                                    for r in client.get('/datasets/1/heard/records').json()['rows'])
            models = lambda: [client.get(f'/datasets/1/species/{s}/custom-model').json() for s in (warbler, tit, bulbul)]
            with connect(self.data) as db:
                # Other screens' answers: a check and the species-AI page (whose recording is a test recording), and "no bird sound".
                labels.put(db, tit, 1, 0, 5, 'present', 'validation')
                labels.put(db, warbler, 1, 0, 5, 'absent', 'training')
                db.execute('INSERT INTO test_recordings(site_id,species_id,recording_id) VALUES(1,?,1)', (warbler,))
                labels.put_no_bird(db, 1, 5, 10, 'rollup_fix')
                rows = lambda: [tuple(r) for r in db.execute('SELECT * FROM labels ORDER BY id')]
                tests = lambda: [tuple(r) for r in db.execute('SELECT * FROM test_recordings')]
                before = (rows(), tests())
            state = (listed(), models())
            self.assertEqual(listed(), [('Horornis diphone', 'absent', 'training', 0), ('Parus cinereus', 'present', 'validation', 0), ('none', 'present', 'rollup_fix', 5)])
            # The form shows another screen's species apart from its own; the same species pressed here is the map's own record.
            item = pick()
            self.assertEqual(([(s['scientific_name'], s['own']) for s in [item['species'], *item['background']]], item['complete'], item['no_bird']),
                             ([('Parus cinereus', False)], False, False))
            # A species chosen on the map is the pick's species, ahead of an earlier answer from another screen.
            answer('present', bulbul)
            item = pick()
            self.assertEqual([s['scientific_name'] for s in [item['species'], *item['background']]], ['Hypsipetes amaurotis', 'Parus cinereus'])
            answer('present', tit); answer('present', warbler); answer('complete')
            item = pick()
            self.assertEqual(([s['own'] for s in [item['species'], *item['background']]], item['complete']), ([True, True, True], True))
            # The newest answer is the current one, whatever its screen: the map's "heard" over the page's "not heard".
            self.assertEqual(listed()[:3], [('Horornis diphone', 'present', 'sound_map', 0), ('Hypsipetes amaurotis', 'present', 'sound_map', 0), ('Parus cinereus', 'present', 'sound_map', 0)])
            # One chip taken back: the answer underneath is current again.
            answer('withdraw', tit); answer('withdraw', warbler)
            self.assertEqual(listed(), [('Horornis diphone', 'absent', 'training', 0), ('Hypsipetes amaurotis', 'present', 'sound_map', 0),
                                        ('Parus cinereus', 'present', 'validation', 0), ('none', 'present', 'rollup_fix', 5)])
            item = pick()
            self.assertEqual(([(s['scientific_name'], s['own']) for s in [item['species'], *item['background']]], item['complete']),
                             ([('Hypsipetes amaurotis', True), ('Parus cinereus', False)], True))
            # A species the map hears where another screen recorded "no bird sound" takes that back, as on every screen;
            # the map's own "no bird sound" there is a row of its own.
            answer('present', bulbul, later)
            self.assertNotIn(('none', 'present', 'rollup_fix', 5), listed())
            answer('withdraw', bulbul, later)
            self.assertEqual((pick(later)['no_bird'], pick(later)['complete']), (True, False))
            answer('complete', where=later)
            self.assertEqual((pick(later)['complete'], ('none', 'present', 'sound_map', 5) in listed()), (True, True))
            answer('present', warbler, later)
            # Unticking: the pick and the map's own records go; every other screen's answer, the test recordings and
            # the material of each species are exactly as before the map touched the sounds.
            unpick(); unpick(later)
            with connect(self.data) as db:
                self.assertEqual((rows(), tests()), before)
            self.assertEqual((listed(), models()), state)
            self.assertEqual(client.get('/explore/picked', params={'dataset': 1}).json()['items'], [])

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

    def test_an_answer_on_the_sound_map_is_kept_apart_from_the_sounds_ticked_for_export(self):
        with tempfile.TemporaryDirectory() as temp:
            client = self.site(temp)
            warbler = self.species(client, 'Horornis diphone')
            at = dict(recording_id=1, start_s=0, end_s=5)
            self.assertEqual(client.get('/datasets/1/heard/sound', params={**at, 'blank': True}).json()['now'], [])
            answer = lambda verdict: client.post('/heard/map-answer', json=dict(dataset_id=1, species_id=warbler, verdict=verdict, **at))
            self.assertEqual([(r['verdict'], r['screen']) for r in answer('absent').json()['now']], [('absent', 'sound_map')])
            self.assertEqual([r['verdict'] for r in answer('present').json()['now']], ['present'])
            # The ticked sound takes its species from the answer; unticking takes the map's "heard" back with it, and the
            # answer under it is current again.
            pick = dict(dataset_id=1, **at, sound_type='', quality='', others=False)
            saved = client.put('/explore/picked', json=pick)
            self.assertEqual((saved.status_code, saved.json()['species']['scientific_name']), (200, 'Horornis diphone'))
            self.assertEqual(client.request('DELETE', '/explore/picked', params=dict(dataset=1, **at)).status_code, 200)
            self.assertEqual([r['verdict'] for r in client.get('/datasets/1/heard/sound', params=at).json()['now']], ['absent'])
            self.assertEqual(answer('maybe').status_code, 422)

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
