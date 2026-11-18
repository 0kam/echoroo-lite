"""Test sounds: recordings set aside per species when its AI is made, kept out of the material, scored every round."""
import unittest

import labels
import test_sounds
from storage import connect
from test_species_flow import site


class TestSoundsCheck(unittest.TestCase):
    def answer(self, root, sid, recordings, extra=()):
        """Per recording: heard at 0, 30 and 60 s (the separable voice pieces), not heard at 5-25 s."""
        with connect(root) as db:
            for recording in recordings:
                for start in (0, 30, 60):
                    labels.put(db, sid, recording, start, start + 5, 'present', 'training')
                for start in (5, 10, 15, 20, 25, *extra):
                    labels.put(db, sid, recording, start, start + 5, 'absent', 'training')

    def test_recordings_are_set_aside_scored_each_round_and_never_material(self):
        with site() as (root, client, species):
            sid = species['id']
            scope = {'dataset_id': 1, 'species_id': sid, 'start_date': '2024-08-01', 'end_date': '2024-08-06'}
            url = f'/datasets/1/species/{sid}/test-sounds'
            # Twelve heard sounds are too few to set any aside.
            self.answer(root, sid, range(1, 5))
            self.assertEqual(client.get(url).json(), {'state': 'none', 'heard': 12, 'needed': 13})
            first = client.post('/custom-models', json=scope).json()
            self.assertEqual((first['training']['positive_5s'], first['training']['negative_5s']), (12, 20))
            self.assertEqual(client.get(url).json()['state'], 'none')
            # With 18, recordings are set aside while the rest can still make the AI (20 answers): three of six.
            self.answer(root, sid, (5, 6))
            again = client.post('/custom-models', json=scope).json()
            with connect(root) as db:
                test = test_sounds.recordings(db, 1, sid)
            self.assertEqual(len(test), 3)
            self.assertEqual((again['round'], again['training']['positive_5s'], again['training']['negative_5s']), (2, 9, 15))
            report = client.get(url).json()
            self.assertEqual((report['state'], report['sounds'], report['aim']), ('ready', {'heard': 9, 'not_heard': 15}, 10))
            # The first round was made before any were set aside, from sounds that are test sounds now: it gets no score.
            self.assertEqual([(r['round'], r['found'], r['false'], r['current'], r['made_from_test']) for r in report['rounds']],
                             [(1, None, None, False, True), (2, 9, 0, True, False)])
            self.assertEqual((report['threshold'], len(report['recordings'])), (0.5, 3))
            # No sound of a test recording is offered on the species-AI page: the AI under test would be choosing them.
            state = client.get(f'/datasets/1/species/{sid}/custom-model', params={'start_date': '2024-08-01', 'end_date': '2024-08-06', 'batch': True}).json()
            self.assertFalse({row['recording_id'] for row in state['batch']} & test)
            self.assertFalse(any(row['test'] for row in state['batch']))
            self.assertEqual((report['missed'], report['false'], report['new']), ([], [], []))
            ready = client.get(f'/datasets/1/species/{sid}/custom-model', params={'batch': True}).json()
            self.assertEqual((ready['test'], ready['examples']['positive_5s']), ({'heard': 9, 'not_heard': 15}, 9))
            # Test sounds are answered: they are not asked again.
            self.assertFalse({row['recording_id'] for row in ready['batch']} & test and
                             {(row['recording_id'], row['start_s']) for row in ready['batch']} & {(r, s) for r in test for s in (0, 5, 30, 60)})
            # A later answer in a test recording is a test sound; one elsewhere is material. A wrong "heard" shows as missed.
            inside, outside = min(test), min(set(range(1, 7)) - test)
            with connect(root) as db:
                labels.put(db, sid, inside, 40, 45, 'present', 'species_mark')
                labels.put(db, sid, outside, 40, 45, 'absent', 'species_mark')
            report = client.get(url).json()
            self.assertEqual(report['sounds'], {'heard': 10, 'not_heard': 15})
            self.assertEqual([(r['round'], r['found']) for r in report['rounds']], [(1, None), (2, 9)])
            self.assertEqual([(m['recording_id'], m['start_s']) for m in report['missed']], [(inside, 40)])
            third = client.post('/custom-models', json=scope).json()
            self.assertEqual((third['round'], third['training']['positive_5s'], third['training']['negative_5s']), (3, 9, 16))
            self.assertEqual(sorted(p.name for p in (root / 'custom-models' / str(third['id']) / 'rounds').iterdir()),
                             ['round-1.npz', 'round-2.npz', 'round-3.npz'])
            self.assertEqual([r['round'] for r in client.get(url).json()['rounds']], [1, 2, 3])
            # The list of checked sounds says which are test sounds.
            listed = client.get('/datasets/1/heard/records', params={'limit': 500}).json()['rows']
            self.assertEqual({row['test'] for row in listed if row['recording_id'] in test}, {True})
            self.assertEqual({row['test'] for row in listed if row['recording_id'] not in test}, {False})

    def test_recordings_with_check_answers_go_first_and_the_rest_must_still_make_an_ai(self):
        labelled = {}
        for recording, origin in ((1, 'label'), (2, 'check'), (3, 'label'), (4, 'label'), (5, 'label')):
            for index in range(8):
                labelled[recording * 100 + index] = {'label': 'target' if index < 4 else 'other', 'origin': origin, 'recording_id': recording}
        with site() as (root, client, species), connect(root) as db:
            added = test_sounds.choose(db, 1, species['id'], labelled, 20, 3)
            # 4 heard per recording: three recordings reach 10; a fourth is not needed, and two are left for the AI... but
            # two recordings hold only 16 answers, so only two are set aside and the AI keeps 24.
            self.assertEqual((added[0], len(added)), (2, 2))
            self.assertEqual(test_sounds.choose(db, 1, species['id'], labelled, 20, 3), [])
            few = {key: value for key, value in labelled.items() if value['recording_id'] <= 3}
            self.assertEqual(test_sounds.choose(db, 1, 'another', few, 20, 3), [])  # 12 heard: none set aside



if __name__ == '__main__':
    unittest.main()
