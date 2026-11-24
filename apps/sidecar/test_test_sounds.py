"""Test sounds: made first per species and site, frozen or given up before growing, kept out of the material, scored every round."""
import unittest

import labels
import test_sounds
from storage import connect
from test_species_flow import site

DATES = {'start_date': '2024-08-01', 'end_date': '2024-08-06'}
HEARD_AT, NOT_HEARD_AT = (0, 30, 60), (5, 10, 15, 20, 25)  # per recording: the separable voice pieces, and five others


class TestSoundsCheck(unittest.TestCase):
    def test_test_sounds_come_first_then_are_frozen_and_never_material(self):
        with site() as (root, client, species):
            sid = species['id']
            scope = {'dataset_id': 1, 'species_id': sid, **DATES}
            url, phase = f'/datasets/1/species/{sid}/test-sounds', f'/datasets/1/species/{sid}/test-phase'
            state = lambda: client.get(f'/datasets/1/species/{sid}/custom-model', params={**DATES, 'batch': True}).json()
            with connect(root) as db:
                segment = {(r[0], r[1]): r[2] for r in db.execute('SELECT a.recording_id,s.start_s,s.id FROM segments s JOIN analyses a ON a.id=s.analysis_id')}
            label = lambda recording, start, value: client.post(f'/datasets/1/species/{sid}/labels', json={'segment_id': segment[recording, start], 'label': value})
            def query(sql):
                with connect(root) as db:
                    return [r[0] for r in db.execute(sql)]
            stored = lambda: set(query('SELECT recording_id FROM test_recordings'))
            # A species without an AI starts by making its test sounds: the AI cannot be made yet, and looking writes nothing.
            first = state()
            self.assertEqual((first['phase'], first['can_train'], first['reasons'][0], len(first['batch'])), ('collecting', False, 'custom_test_sounds_first', 10))
            self.assertEqual(first['test'], {'heard': 0, 'not_heard': 0, 'answered': 0, 'aim_heard': 10, 'aim_not_heard': 20, 'ask_after': 60, 'enough': 3, 'complete': False})
            self.assertEqual(client.post('/custom-models', json=scope).json()['detail'], 'custom_test_sounds_first')
            # Growing cannot start without "heard" test sounds.
            self.assertEqual(client.post(phase).json()['detail'], 'test_phase_refused')
            self.assertEqual(query('SELECT COUNT(*) FROM test_phases'), [0])
            # An answer on the page makes its recording a test recording; every answer of the species there is a test sound.
            with connect(root) as db:
                labels.put(db, sid, 1, 40, 45, 'absent', 'species_mark')
            for recording in (1, 2, 3):
                for start in HEARD_AT:
                    label(recording, start, 'target')
                for start in NOT_HEARD_AT:
                    label(recording, start, 'other')
            label(2, 35, 'unknown')
            now = state()
            self.assertEqual(stored(), {1, 2, 3})
            self.assertEqual((now['test']['heard'], now['test']['not_heard'], now['test']['answered'], now['test']['complete']), (9, 16, 26, False))
            self.assertEqual((now['examples']['positive_5s'], now['examples']['negative_5s']), (0, 0))
            # A recording with check answers counts too, though it is stored only when the set is frozen.
            with connect(root) as db:
                for start in HEARD_AT:
                    labels.put(db, sid, 4, start, start + 5, 'present', 'validation')
                for start in NOT_HEARD_AT:
                    labels.put(db, sid, 4, start, start + 5, 'absent', 'validation')
            now = state()
            self.assertEqual((now['test']['heard'], now['test']['not_heard'], now['test']['complete'], stored()), (12, 21, True, {1, 2, 3}))
            self.assertEqual(client.post(phase).json(), {'state': 'fixed'})
            self.assertEqual(stored(), {1, 2, 3, 4})
            self.assertEqual(client.post(phase).json()['detail'], 'test_phase_refused')
            # Growing: no sound of a test recording is offered, and answers on the page are material now.
            now = state()
            self.assertEqual((now['phase'], now['reasons'][0]), ('fixed', 'custom_more_answers_needed'))
            self.assertTrue({row['recording_id'] for row in now['batch']} <= {5, 6})
            for recording in (5, 6):
                for start in HEARD_AT:
                    label(recording, start, 'target')
                for start in (*NOT_HEARD_AT, 35, 40):
                    label(recording, start, 'other')
            self.assertEqual(stored(), {1, 2, 3, 4})
            made = client.post('/custom-models', json=scope).json()
            self.assertEqual((made['round'], made['training']['positive_5s'], made['training']['negative_5s']), (1, 6, 14))
            # The test sounds were there before the first AI, so every round has a score.
            report = client.get(url).json()
            self.assertEqual((report['state'], report['sounds'], report['aim'], report['threshold'], len(report['recordings'])),
                             ('ready', {'heard': 12, 'not_heard': 21}, 10, 0.5, 4))
            self.assertEqual([(r['round'], r['found'], r['false'], r['current'], r['made_from_test']) for r in report['rounds']], [(1, 12, 0, True, False)])
            self.assertEqual((report['missed'], report['false'], report['new']), ([], [], []))
            now = state()
            self.assertTrue({row['recording_id'] for row in now['batch']} <= {5, 6})
            self.assertFalse(any(row['test'] for row in now['batch']))
            # A later answer in a test recording is a test sound; one elsewhere is material. A wrong "heard" shows as missed.
            with connect(root) as db:
                labels.put(db, sid, 1, 45, 50, 'present', 'species_mark')
                labels.put(db, sid, 5, 45, 50, 'absent', 'species_mark')
            report = client.get(url).json()
            self.assertEqual((report['sounds'], [(m['recording_id'], m['start_s']) for m in report['missed']]), ({'heard': 13, 'not_heard': 21}, [(1, 45)]))
            again = client.post('/custom-models', json=scope).json()
            self.assertEqual((again['round'], again['training']['positive_5s'], again['training']['negative_5s']), (2, 6, 15))
            self.assertEqual([r['round'] for r in client.get(url).json()['rounds']], [1, 2])
            # The list of checked sounds says which are test sounds.
            listed = client.get('/datasets/1/heard/records', params={'limit': 500}).json()['rows']
            self.assertEqual({row['test'] for row in listed if row['recording_id'] <= 4}, {True})
            self.assertEqual({row['test'] for row in listed if row['recording_id'] > 4}, {False})
            # A species grown before the phases were stored: fixed when recordings were set aside for it, else without test sounds.
            with connect(root) as db:
                db.execute('DELETE FROM test_phases')
                self.assertEqual(test_sounds.phase(db, 1, sid), 'fixed')
                db.execute('DELETE FROM test_recordings')
                self.assertEqual((test_sounds.phase(db, 1, sid), test_sounds.phase(db, 1, 'no-ai')), ('skipped', 'collecting'))

    def test_a_species_with_few_calls_grows_with_the_test_sounds_found(self):
        with site() as (root, client, species):
            sid = species['id']
            state = lambda: client.get(f'/datasets/1/species/{sid}/custom-model', params={**DATES, 'batch': True}).json()
            phase = f'/datasets/1/species/{sid}/test-phase'
            with connect(root) as db:
                segments = [r[0] for r in db.execute('SELECT s.id FROM segments s JOIN analyses a ON a.id=s.analysis_id WHERE a.recording_id=1 ORDER BY s.start_s')]
            label = lambda segment, value: client.post(f'/datasets/1/species/{sid}/labels', json={'segment_id': segment, 'label': value})
            for index, value in enumerate(('target', 'other', 'other')):
                label(segments[index], value)
            # Below three "heard" test sounds growing cannot start; from three it can, though the aims are not met.
            self.assertEqual(client.post(phase).json()['detail'], 'test_phase_refused')
            label(segments[3], 'target'); label(segments[4], 'target')
            self.assertFalse(state()['test']['complete'])
            self.assertEqual(client.post(phase).json(), {'state': 'fixed'})
            after = state()
            self.assertEqual((after['phase'], after['test']['heard'], after['test']['not_heard'], after['examples']['positive_5s']), ('fixed', 3, 2, 0))
            self.assertEqual(client.get(f'/datasets/1/species/{sid}/test-sounds', params=DATES).json()['sounds'], {'heard': 3, 'not_heard': 2})
            # Answers on the page are material from now on, never from the test recording.
            offered = after['batch'][0]
            self.assertNotEqual(offered['recording_id'], 1)
            label(offered['segment_id'], 'target')
            self.assertEqual((state()['examples']['positive_5s'], state()['test']['heard']), (1, 3))


if __name__ == '__main__':
    unittest.main()
