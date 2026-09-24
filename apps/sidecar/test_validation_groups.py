"""Grouped species validation keeps one explicit, versioned listening queue."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from server import create_app
from storage import connect


class ValidationGroupCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for hour in ('060000', '070000', '080000', '090000'):
            sf.write(self.root / f'20240808_{hour}.wav', np.zeros(360000), 1000)
        self.client = TestClient(create_app(self.root, self.root, 'test'),
                                 headers={'Authorization': 'Bearer test'})
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.post('/imports', dict(folder=str(self.root), site_name='Site', dataset_name='Data',
                                   timezone='Asia/Tokyo', latitude=35, longitude=138))
        self.species = [self.client.get('/species?q=ウグイス').json()[0],
                        self.client.get('/species?q=シジュウカラ').json()[0],
                        self.client.get('/species?q=カワラヒワ').json()[0]]
        labels = ['Horornis diphone_Japanese Bush Warbler', 'Parus minor_Japanese Tit',
                  'Chloris sinica_Oriental Greenfinch']
        with connect(self.root) as db:
            job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',4)").lastrowid
            for rid in range(1, 5):
                path = self.root / f'bird-{rid}.npz'
                scores = np.column_stack((np.tile([.02, .2, .8], 40),
                                          np.tile([.8, .02, .2], 40),
                                          np.tile([.2, .8, .02], 40)))
                starts = np.arange(120) * 3
                np.savez(path, labels=np.tile(labels, 120), scores=scores.ravel(),
                         start_s=np.repeat(starts, 3), end_s=np.repeat(starts + 3, 3))
                meta = {'model_sha256': 'birdnet', 'species': labels}
                db.execute('''INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path)
                    VALUES(?,?,?,?,?)''', (job, rid, 'birdnet', json.dumps(meta), str(path)))
        self.scope = {'dataset_id': 1, 'start_date': '2024-08-08', 'end_date': '2024-08-08'}
        with patch('annotations.secrets.randbits', return_value=20):
            annotation = self.post('/annotations', self.scope)
        self.annotation_id = annotation['id']
        for index in range(10):
            question = annotation['question']
            ranges = []
            if index == 0:
                ranges = [{'species_id': species['id'], 'start_s': question['start_s'] + index + 1,
                           'end_s': question['start_s'] + index + 2} for species in self.species]
            annotation = self.post(f'/annotations/{self.annotation_id}/answers', {
                'item_id': question['id'], 'version': question['version'],
                'ranges': ranges, 'complete': True})

    def post(self, path, body):
        response = self.client.post(path, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_group_round_trip_correction_and_atomic_conflict(self):
        group_body = {**self.scope, 'species_ids': [species['id'] for species in self.species[:2]],
                      'annotation_session_id': self.annotation_id}
        group = self.post('/validations', group_body)
        sid = group['id']
        self.assertEqual(len(group['members']), 2)
        self.assertEqual(len(group['review_species']), 2)
        self.assertEqual(group['counts']['calibration'], {'total': 10, 'answered': 1})
        self.assertEqual(len(group['question']['reviews']), 2)
        self.assertNotEqual(group['history'][0]['position'], group['question']['position'])
        self.assertTrue(all(0 <= position < 10 for position in
                            (group['history'][0]['position'], group['question']['position'])))
        with connect(self.root) as db:
            future = db.execute('''SELECT i.id FROM validation_items i
                WHERE i.session_id=? AND i.phase='calibration' AND i.id!=?
                AND NOT EXISTS(SELECT 1 FROM validation_answers a WHERE a.item_id=i.id)
                ORDER BY i.position DESC LIMIT 1''', (sid, group['question']['id'])).fetchone()[0]
        self.assertEqual(self.client.get(f'/validations/{sid}?item_id={future}').status_code, 400)

        omitted = self.client.post(f'/validations/{sid}/answers', json={
            'item_id': group['question']['id'], 'verdict': 'other',
            'version': group['question']['version'], 'ranges': [],
            'reviews': [{**review, 'verdict': 'other'}
                        for review in group['question']['reviews'][:-1]]})
        self.assertEqual(omitted.json()['detail'], 'annotation_changed')
        self.assertEqual(self.client.get(f'/validations/{sid}').json()['counts']['calibration']['answered'], 1)

        first = group['history'][0]
        saved = self.client.get(f'/validations/{sid}?item_id={first["id"]}').json()['question']
        before = {}
        with connect(self.root) as db:
            for review in saved['reviews']:
                before[review['item_id']] = db.execute(
                    'SELECT COUNT(*) FROM validation_answers WHERE item_id=?',
                    (review['item_id'],)).fetchone()[0]
        conflicting = [{**review, 'verdict': 'target' if index == 0 else 'other'}
                       for index, review in enumerate(saved['reviews'])]
        failed = self.client.post(f'/validations/{sid}/answers', json={
            'item_id': saved['id'], 'verdict': 'target', 'version': saved['version'],
            'ranges': saved['ranges'], 'reviews': conflicting})
        self.assertEqual(failed.json()['detail'], 'annotation_conflict')
        with connect(self.root) as db:
            self.assertEqual(before, {item_id: db.execute(
                'SELECT COUNT(*) FROM validation_answers WHERE item_id=?', (item_id,)).fetchone()[0]
                for item_id in before})

        remaining = [r for r in saved['ranges'] if r['species_id'] == self.species[0]['id']]
        corrected = self.post(f'/validations/{sid}/answers', {
            'item_id': saved['id'], 'verdict': 'target', 'version': saved['version'],
            'ranges': remaining, 'reviews': conflicting})
        self.assertTrue(corrected['applicable'])
        self.assertEqual(len(corrected['history'][0]['reviews']), 2)
        with connect(self.root) as db:
            snapshots = [json.loads(row[0]) for row in db.execute(
                'SELECT snapshot FROM validation_sessions ORDER BY id')]
            source_ids = [{*snap['annotation_source']['answers'].values()} for snap in snapshots]
            self.assertEqual(source_ids[0], source_ids[1])

        group = corrected
        while group['question']:
            question = group['question']
            reviews = [{**review, 'verdict': 'other'} for review in question['reviews']]
            group = self.post(f'/validations/{sid}/answers', {
                'item_id': question['id'], 'verdict': 'other', 'version': question['version'],
                'ranges': [], 'reviews': reviews})
        group = self.post(f'/validations/{sid}/evaluation', {})
        self.assertEqual(group['method'], 'fixed_precision_audit_v1')
        self.assertIn('recall', group['counts'])
        self.assertLessEqual(group['counts']['evaluation']['total'], 60)
        self.assertEqual(group['counts']['evaluation']['answered'], 0)
        question = group['question']
        reviews = [{**review, 'verdict': 'target'} for review in question['reviews']]
        group = self.post(f'/validations/{sid}/answers', {
            'item_id': question['id'], 'verdict': 'target', 'version': question['version'],
            'ranges': [], 'reviews': reviews})
        self.assertEqual(group['counts']['evaluation']['answered'], 1)
        resumed = self.post('/validations', group_body)
        self.assertEqual((resumed['id'], resumed['counts']), (sid, group['counts']))

        conflict = self.client.post('/validations', json={**group_body,
            'species_ids': [self.species[0]['id'], self.species[2]['id']]})
        self.assertEqual(conflict.json()['detail'], 'validation_group_conflict')

        group = resumed
        while group['question']:
            question = group['question']
            reviews = [{**review, 'verdict': 'target'} for review in question['reviews']]
            group = self.post(f'/validations/{sid}/answers', {
                'item_id': question['id'], 'verdict': 'target', 'version': question['version'],
                'ranges': [], 'reviews': reviews})
        self.assertEqual(group['stage'], 'complete')
        completed = self.post('/validations', group_body)
        self.assertEqual(completed['id'], sid)
        self.assertEqual(completed['stage'], 'complete')
        self.assertEqual(completed['counts'], group['counts'])

    def test_legacy_session_cannot_join_fixed_group(self):
        bodies = [{**self.scope, 'species_id': species['id'],
                   'annotation_session_id': self.annotation_id} for species in self.species[:2]]
        sessions = [self.post('/validations', body) for body in bodies]
        with connect(self.root) as db:
            snapshot = json.loads(db.execute(
                'SELECT snapshot FROM validation_sessions WHERE id=?', (sessions[0]['id'],)).fetchone()[0])
            snapshot['method'] = 'stratified_finite_window_validation_v2'
            db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',
                       (json.dumps(snapshot), sessions[0]['id']))

        # The legacy validation remains individually resumable.
        self.assertEqual(self.post('/validations', bodies[0])['id'], sessions[0]['id'])
        response = self.client.post('/validations', json={**self.scope,
            'species_ids': [species['id'] for species in self.species[:2]],
            'annotation_session_id': self.annotation_id})
        self.assertEqual(response.json()['detail'], 'validation_group_conflict')
        with connect(self.root) as db:
            snapshots = [json.loads(db.execute(
                'SELECT snapshot FROM validation_sessions WHERE id=?', (session['id'],)).fetchone()[0])
                for session in sessions]
        self.assertTrue(all('group_ids' not in snapshot for snapshot in snapshots))


if __name__ == '__main__':
    unittest.main()
