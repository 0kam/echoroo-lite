"""Presence marks, one search per species, custom models from search labels, and the search deletion cascade."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from server import create_app
from storage import connect

E0, E1 = np.eye(2, 1536, dtype='<f4')


class Session:
    def run(self, outputs, feed):
        return [E0[None]]


@contextmanager
def site():
    """Six days of 90-second recordings with Perch embeddings and scores every 5 seconds."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        audio = root / 'audio'
        audio.mkdir()
        for day in range(1, 7):
            sf.write(audio / f'2024080{day}_060000.wav', np.full(90 * 1000, .1, dtype=np.float32), 1000)
        with TestClient(create_app(root, root, 'test'), headers={'Authorization': 'Bearer test'}) as client:
            client.post('/imports', json=dict(folder=str(audio), site_name='Site', dataset_name='Data', timezone='Asia/Tokyo', latitude=35, longitude=138))
            species = client.get('/species?q=ウグイス').json()[0]
            label = 'Horornis diphone_Japanese Bush Warbler'
            with connect(root) as db:
                job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',6)").lastrowid
                for rid in range(1, 7):
                    probs = np.tile([.3, .2, .2], 6)
                    path = root / f'perch-{rid}.npz'
                    np.savez(path, labels=['Horornis diphone', 'Other'], logits=np.log(np.column_stack([probs, 1 - probs])))
                    analysis = db.execute("INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,'perch',?,?)",
                        (job, rid, json.dumps({'model_sha256': 'perch-sha', 'candidate_filter': {'species': [label]}}), str(path))).lastrowid
                    for index in range(18):
                        segment = db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,?,?)', (analysis, index * 5, index * 5 + 5)).lastrowid
                        # Repeated separable examples for the small custom classifier.
                        vector = (E0 if index % 6 == 0 else E1) + np.eye(1, 1536, 10 + rid, dtype='<f4')[0] * .05
                        db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)', (segment, vector.tobytes()))
            yield root, client, species


class SpeciesFlowCheck(unittest.TestCase):
    def test_presence_search_custom_model_and_deletion(self):
        with site() as (root, client, species):
            sid = species['id']
            scope = {'dataset_id': 1, 'species_id': sid, 'start_date': '2024-08-01', 'end_date': '2024-08-06'}
            clip = {'source_kind': 'recording', 'source_id': 1, 'start_s': 0, 'end_s': 5}
            search = {'dataset_id': 1, 'species_id': sid, 'clips': [clip]}
            with patch('search_runs.load_perch', return_value=(Session(), 'perch-sha')), patch('search.load_perch', return_value=(Session(), 'perch-sha')):
                run = client.post('/search-runs', json=search)
                self.assertEqual(run.status_code, 200, run.text)
                run = run.json()
                self.assertEqual(client.post('/search-runs', json=search).json()['detail'], 'search_exists')
            # The search list: 5-second hits by similarity; its own reference interval is excluded.
            listed = client.get(f'/datasets/1/species/{sid}/presence', params={'source': 'search', **{k: scope[k] for k in ('start_date', 'end_date')}}).json()
            rows = listed['rows']
            self.assertEqual((listed['run_id'], len(rows)), (run['id'], 100))  # 107 hits, capped at the list size
            self.assertEqual([r['score'] for r in rows], sorted((r['score'] for r in rows), reverse=True))
            self.assertTrue(all(r['end_s'] - r['start_s'] == 5 for r in rows))
            mark = lambda row, marked: client.post('/presence/marks', json={'dataset_id': 1, 'species_id': sid, 'recording_id': row['recording_id'],
                'start_s': row['start_s'], 'end_s': row['end_s'], 'run_id': run['id'], 'marked': marked})
            self.assertEqual(mark(rows[0], True).json(), {'marked': True})
            mark(rows[1], True)
            mark(rows[1], False)
            presence = client.get(f'/datasets/1/species/{sid}', params=scope).json()['presence']
            self.assertEqual((presence['status'], len(presence['evidence'])), ('confirmed', 1))
            self.assertEqual(presence['evidence'][0]['origin'], 'mark')
            self.assertEqual(client.post('/presence/marks', json={'dataset_id': 1, 'species_id': sid, 'recording_id': 1, 'start_s': 0, 'end_s': 7,
                'run_id': None, 'marked': True}).json()['detail'], 'invalid_interval')
            mark(rows[0], False)
            self.assertEqual(client.post('/presence/misses', json={**scope, 'source': 'search', 'listened': 8}).json(), {'listened': 8})
            presence = client.get(f'/datasets/1/species/{sid}', params=scope).json()['presence']
            self.assertEqual((presence['status'], presence['miss']['listened']), ('not_found', 8))
            mark(rows[0], True)
            self.assertEqual(client.get(f'/datasets/1/species/{sid}', params=scope).json()['presence']['status'], 'confirmed')
            # The AI list interleaves each model's ranking and says which model flagged a sound.
            ai = client.get(f'/datasets/1/species/{sid}/presence', params={'source': 'ai', **{k: scope[k] for k in ('start_date', 'end_date')}}).json()
            self.assertTrue(ai['rows'] and all(r['end_s'] - r['start_s'] == 5 and r['models'][0]['engine'] == 'perch' for r in ai['rows']))
            # Custom model material: labels on search hits, needing three of each.
            ready = client.get(f'/datasets/1/species/{sid}/custom-model', params={k: scope[k] for k in ('start_date', 'end_date')}).json()
            self.assertEqual((ready['can_train'], ready['reasons']), (False, ['custom_positive_examples_needed', 'custom_negative_examples_needed']))
            voice = [h for h in ready['hits'] if h['similarity'] > .99][:3]
            with connect(root) as db:
                silent = [r[0] for r in db.execute('SELECT h.segment_id FROM search_hits h WHERE h.run_id=? AND h.similarity<.5 ORDER BY h.segment_id LIMIT 3', (run['id'],))]
            for segment in [h['segment_id'] for h in voice]:
                client.post(f"/search-runs/{run['id']}/labels", json={'segment_id': segment, 'label': 'target'})
            for segment in silent:
                client.post(f"/search-runs/{run['id']}/labels", json={'segment_id': segment, 'label': 'other'})
            client.post(f"/search-runs/{run['id']}/labels", json={'segment_id': silent[0], 'label': None})
            self.assertEqual(client.get(f'/datasets/1/species/{sid}/custom-model', params={k: scope[k] for k in ('start_date', 'end_date')}).json()['examples']['negative_5s'], 2)
            client.post(f"/search-runs/{run['id']}/labels", json={'segment_id': silent[0], 'label': 'other'})
            ready = client.get(f'/datasets/1/species/{sid}/custom-model', params={k: scope[k] for k in ('start_date', 'end_date')}).json()
            self.assertEqual((ready['can_train'], ready['examples']['positive_5s'], ready['examples']['negative_5s']), (True, 3, 3))
            self.assertEqual(sum(h['label'] == 'target' for h in ready['hits']), 3)
            model = client.post('/custom-models', json=scope)
            self.assertEqual(model.status_code, 200, model.text)
            model = model.json()
            self.assertEqual((model['applicable'], model['run_id'], model['training']['positive_5s'], model['inference']['scored_5s']), (True, run['id'], 3, 108))
            detail = client.get(f'/datasets/1/species/{sid}', params={**scope, 'engine': 'custom', 'model_id': model['id']}).json()
            self.assertEqual(detail['selected'], f"custom:{model['id']}")
            self.assertEqual(detail['report']['threshold'], .5)
            self.assertEqual(detail['report']['summaries'][0]['eligible_windows'], 108)
            ai = client.get(f'/datasets/1/species/{sid}/presence', params={'source': 'ai', **{k: scope[k] for k in ('start_date', 'end_date')}}).json()
            self.assertEqual({m['engine'] for r in ai['rows'] for m in r['models']}, {'perch', 'custom'})
            # The custom model is checked like any other model, without the windows it learned from.
            check = client.post('/validations', json={**scope, 'model': 'custom', 'model_id': model['id']})
            self.assertEqual(check.status_code, 200, check.text)
            check = check.json()
            with connect(root) as db:
                training = {(e['recording_id'], e['start_s'], e['end_s']) for e in json.loads(db.execute('SELECT snapshot FROM custom_models').fetchone()[0])['training']['examples']}
                items = {tuple(r) for r in db.execute('SELECT recording_id,start_s,end_s FROM validation_items WHERE session_id=?', (check['id'],))}
            self.assertFalse(items & training)
            heard = lambda question: 'target' if question['start_s'] % 30 == 0 else 'other'
            while check['question']:
                check = client.post(f"/validations/{check['id']}/answers", json={'item_id': check['question']['id'], 'verdict': heard(check['question'])}).json()
            self.assertIn(check['status'], ('decided', 'undecided'))
            reported = client.get(f'/datasets/1/species/{sid}', params={**scope, 'engine': 'custom', 'model_id': model['id']}).json()['report']
            summary = reported['summaries'][0]
            self.assertEqual((summary['density'], summary['window_s'], summary['model_sha256']),
                             (check['results']['custom']['density'], 5, model['artifact_sha256']))
            self.assertEqual(summary['validation_status'], check['status'])
            perch_check = client.post('/validations', json={**scope, 'model': 'perch'}).json()
            with connect(root) as db:
                custom_windows = {tuple(r) for r in db.execute('SELECT recording_id,start_s,end_s FROM validation_items WHERE session_id=?',(check['id'],))}
                perch_windows = {tuple(r) for r in db.execute('SELECT recording_id,start_s,end_s FROM validation_items WHERE session_id=?',(perch_check['id'],))}
            self.assertEqual(perch_check['counts']['answered'], len(custom_windows & perch_windows))
            self.assertGreater(perch_check['counts']['answered'], 0)
            client.post(f"/validations/{perch_check['id']}/delete")
            self.assertEqual(client.get(f"/validations/{check['id']}").json()['status'],check['status'])
            # Only exact 5-second "Not heard" answers become no-voice material.
            ready = client.get(f'/datasets/1/species/{sid}/custom-model', params={k: scope[k] for k in ('start_date', 'end_date')}).json()
            with connect(root) as db:
                pieces = db.execute('''SELECT COUNT(*) FROM segments s JOIN analyses n ON n.id=s.analysis_id JOIN validation_items i
                    ON i.recording_id=n.recording_id AND s.start_s=i.start_s AND s.end_s=i.end_s JOIN window_answers a
                    ON a.recording_id=i.recording_id AND a.start_s=i.start_s AND a.end_s=i.end_s WHERE i.session_id=? AND a.verdict='other'
                    AND s.id NOT IN (SELECT segment_id FROM model_labels)''', (check['id'],)).fetchone()[0]
            self.assertGreater(pieces, 0)
            self.assertEqual(ready['examples']['negative_from_checks'], pieces)
            # Deleting the search removes its marks, labels and the model built from it, with that model's check.
            preview = client.get(f"/search-runs/{run['id']}/delete-preview").json()
            self.assertEqual({k: preview[k] for k in ('marks', 'labels', 'custom_models', 'checks')}, {'marks': 1, 'labels': 6, 'custom_models': 1, 'checks': 1})
            self.assertEqual(client.post(f"/search-runs/{run['id']}/delete").status_code, 200)
            with connect(root) as db:
                counts = [db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in
                          ('search_runs', 'search_hits', 'presence_marks', 'model_labels', 'custom_models', 'validation_sessions', 'window_answers', 'presence_misses')]
            self.assertEqual(counts, [0] * 8)
            self.assertFalse((root / 'custom-models' / str(model['id'])).exists())
            self.assertEqual(client.get(f'/datasets/1/species/{sid}', params=scope).json()['presence']['status'], 'unchecked')
            with patch('search_runs.load_perch', return_value=(Session(), 'perch-sha')), patch('search.load_perch', return_value=(Session(), 'perch-sha')):
                self.assertEqual(client.post('/search-runs', json=search).status_code, 200)


if __name__ == '__main__':
    unittest.main()
