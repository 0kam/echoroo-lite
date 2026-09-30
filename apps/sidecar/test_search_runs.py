"""Persistent evidence checks with real vectors, plus the XC network boundary contract."""
import io
import json
import os
import random
import tempfile
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from server import SearchRunRequest, create_app
from search_runs import create_runs
from storage import CatalogError, connect
from xeno_canto import XCRedirect, download


class EvidenceCheck(unittest.TestCase):
    def test_local_recording_dates_and_full_distribution(self):
        import csv
        import zipfile
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for stamp in ('20240807_235958', '20240808_000000', '20240808_120000'):
                sf.write(root / f'rec_{stamp}.wav', np.ones(24000 * 10, dtype=np.float32) * .1, 24000)
            e0 = np.eye(1, 1536, dtype='<f4')[0]
            class Session:
                def run(self, outputs, feed):
                    return [e0[None]]
            with TestClient(create_app(root, root, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                client.post('/imports', json=dict(folder=temp, site_name='Test', dataset_name='Dates',
                    timezone='Asia/Tokyo', latitude=35, longitude=138))
                species = client.get('/species?q=ウグイス').json()[0]
                with connect(root) as db:
                    db.execute("INSERT INTO jobs(id,dataset_id,status,total) VALUES(1,1,'complete',2)")
                    for rid in (1, 2):
                        db.execute("INSERT INTO analyses VALUES(?,1,?,'perch',?,'unused.npz')", (rid, rid, json.dumps({'model_sha256': 'model'})))
                        for start in (0, 5):
                            sid = db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,?,?)', (rid, start, start + 5)).lastrowid
                            db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)', (sid, e0.tobytes()))
                clip = {'source_kind': 'recording', 'source_id': 1, 'start_s': 0, 'end_s': .5}
                body = {'dataset_id': 1, 'targets': [{'species_id': species['id'], 'clips': [clip]}],
                        'start_date': '2024-08-08', 'end_date': '2024-08-08'}
                with patch('search_runs.load_perch', return_value=(Session(), 'model')), patch('search.load_perch', return_value=(Session(), 'model')):
                    run = client.post('/search-runs', json=body).json()[0]
                    self.assertEqual((run['snapshot']['total_recordings'], run['snapshot']['searched_recordings']), (2, 1))
                    self.assertEqual({h['recording_id'] for h in run['hits']}, {2})
                    self.assertEqual(run['snapshot']['date_scope'], 'recording_start_local')
                    quick = client.post('/search', json={**clip, 'dataset_id': 1, 'start_date': body['start_date'], 'end_date': body['end_date']}).json()
                    self.assertEqual({h['recording_id'] for h in quick['hits']}, {2})
                    for first, last, error in [('2024-08-09', '2024-08-08', 'search_period'), ('2024-08-09', None, 'no_search_recordings')]:
                        self.assertEqual(client.post('/search-runs', json={**body, 'start_date': first, 'end_date': last}).json()['detail'], error)
                    previous = client.post('/search-runs', json={**body, 'start_date': None, 'end_date': '2024-08-07'}).json()[0]
                    self.assertEqual([(h['recording_id'], h['start_s']) for h in previous['hits']], [(1, 5)])
                self.assertEqual(run['distribution'][-1]['count'], 2)
                sample = client.post(f"/search-runs/{run['id']}/samples", json={'low': -1, 'high': 1, 'count': 1}).json()
                self.assertEqual(sum(b['count'] for b in sample['distribution']), 2)
                for verdict in ('target', 'unknown'):
                    client.post(f"/search-runs/{run['id']}/judgements", json={'segment_id': sample['hits'][0]['segment_id'], 'verdict': verdict})
                band = client.get(f"/search-runs/{run['id']}").json()['distribution'][-1]
                self.assertEqual((band['count'], band['target'], band['unknown']), (2, 0, 1))
                report = client.post('/reports/search', json={'run_id': run['id'], 'start_date': '2024-08-08', 'end_date': '2024-08-08'}).json()
                self.assertEqual(report['distribution'][-1], band)
                output = client.post(f"/reports/{report['id']}/export", json={'folder': temp}).json()
                with zipfile.ZipFile(output['path']) as archive:
                    summary = next(csv.DictReader(io.StringIO(archive.read('species.csv').decode('utf-8-sig'))))
                    self.assertEqual(summary['search_start_date'], '2024-08-08')
                    self.assertEqual(summary['search_date_scope'], 'recording_start_local')
            with TestClient(create_app(root, root, 'test'), headers={'Authorization': 'Bearer test'}) as restarted:
                saved = restarted.get(f"/search-runs/{run['id']}").json()
                self.assertEqual(saved['snapshot'], run['snapshot'])
                self.assertEqual(saved['distribution'][-1], band)

    def test_saved_multi_reference_search_and_xc(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            recording = root / 'rec_20240808_060000.wav'
            sf.write(recording, np.ones(24_000 * 30, dtype=np.float32) * .1, 24000)
            e0, e1 = np.eye(2, 1536, dtype='<f4')
            class Session:
                def run(self, outputs, feed):
                    return [e0[None] if float(feed['inputs'].mean()) < .15 else e1[None]]
            app = create_app(root, root, 'test')
            with TestClient(app, headers={'Authorization': 'Bearer test'}) as client:
                imported = client.post('/imports', json=dict(folder=temp, site_name='Test', dataset_name='Group',
                    timezone='Asia/Tokyo', latitude=35, longitude=138)).json()
                self.assertEqual(imported['count'], 1)
                species = client.get('/species?q=ウグイス').json()[0]
                other = client.get('/species?q=メジロ').json()[0]
                stereo = root / 'reference.wav'
                sf.write(stereo, np.ones(24_000 * 5, dtype=np.float32) * .2, 24000)
                ref = client.post('/references', json={'path': str(stereo)}).json()
                with connect(root) as db:
                    db.execute("INSERT INTO jobs(id,dataset_id,status,total) VALUES(1,1,'complete',2)")
                    db.execute("INSERT INTO analyses VALUES(1,1,1,'perch',?, 'unused.npz')", (json.dumps({'model_sha256': 'model'}),))
                    for sid, vector in enumerate([e0, e1, e0 * .8 + e1 * .6, -e0, -e1, e0 * .6 + e1 * .8], 1):
                        db.execute('INSERT INTO segments VALUES(?,1,?,?)', (sid, (sid - 1) * 5, sid * 5))
                        db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)', (sid, vector.tobytes()))
                clips = [{'source_kind': 'recording', 'source_id': 1, 'start_s': 0, 'end_s': 5},
                         {'source_kind': 'reference', 'source_id': ref['id'], 'start_s': 0, 'end_s': 5}]
                body = {'dataset_id': 1, 'targets': [{'species_id': species['id'], 'clips': clips},
                        {'species_id': other['id'], 'clips': clips[:1]}]}
                statements = []
                @contextmanager
                def traced_connect(path):
                    with connect(path) as db:
                        db.set_trace_callback(statements.append)
                        yield db
                stopped=threading.Event();stopped.set()
                with patch('search_runs.load_perch', return_value=(Session(), 'model')), self.assertRaisesRegex(CatalogError,'cancelled'):
                    create_runs(root,root,SearchRunRequest(**body),stopped)
                with connect(root) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM search_runs').fetchone()[0],0)
                with patch('search_runs.load_perch', return_value=(Session(), 'model')), \
                        patch('search_runs.connect', traced_connect):
                    response = client.post('/search-runs', json=body)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(sum('JOIN vectors v' in sql for sql in statements), 1)
                run, second = response.json()
                self.assertEqual(run['hit_count'], 5)  # The local reference's own interval is excluded.
                self.assertEqual(run['hits'][0]['segment_id'], 2)
                self.assertEqual(run['hits'][0]['reference_index'], 1)
                self.assertAlmostEqual(run['hits'][0]['similarity'], 1)
                self.assertEqual(second['hits'][-1]['segment_id'], 4)
                self.assertAlmostEqual(second['hits'][-1]['similarity'], -1)
                self.assertEqual(second['distribution'][0]['count'], 1)
                self.assertEqual(run['snapshot']['species']['id'], species['id'])
                self.assertEqual(run['snapshot']['references'][1]['query']['model_sha256'], 'model')
                sample = client.post('/search-runs/1/samples', json={'low': .79, 'high': 1, 'count': 2}).json()
                saved_sample = sample['samples'][0]
                self.assertEqual((saved_sample['population'], saved_sample['count'], saved_sample['requested_count']), (3, 2, 2))
                expected = random.Random(int(saved_sample['seed'])).sample([2, 3, 6], 2)
                self.assertEqual({h['segment_id'] for h in sample['hits']}, set(expected))
                self.assertEqual(client.get('/search-runs/1?sample_id=999').status_code, 400)
                self.assertEqual(client.post('/search-runs/1/samples', json={'low': .9, 'high': .8}).status_code, 400)
                self.assertEqual(client.post('/search-runs/1/samples', json={'low': -.9, 'high': -.8, 'count': 2}).json()['detail'], 'empty_similarity_band')
                hit = sample['hits'][0]
                for verdict in ('target', 'unknown', 'target'):
                    response = client.post('/search-runs/1/judgements', json={'segment_id': hit['segment_id'], 'verdict': verdict, 'note': 'checked'})
                    self.assertEqual(response.status_code, 200)
                self.assertEqual(client.post('/search-runs/1/judgements', json={'segment_id': 1, 'verdict': 'target'}).status_code, 400)
                self.assertEqual(client.post('/search-runs/1/judgements', json={'segment_id': 2, 'verdict': 'unreviewed'}).status_code, 422)
                refreshed = client.get(f"/search-runs/1?sample_id={sample['sample_id']}").json()
                self.assertEqual(next(h for h in refreshed['hits'] if h['segment_id'] == hit['segment_id'])['verdict'], 'target')
                self.assertTrue(all(h['verdict'] is None for h in client.get('/search-runs/2').json()['hits']))
                self.assertEqual([r['id'] for r in client.get('/search-runs/1').json()['related']],[1,2])
                # Reanalysis does not rewrite old hit IDs, judgements, or source/model snapshots.
                with connect(root) as db:
                    db.execute("INSERT INTO jobs(id,dataset_id,status,total) VALUES(2,1,'complete',1)")
                    db.execute("INSERT INTO analyses VALUES(2,2,1,'perch',?, 'new.npz')", (json.dumps({'model_sha256': 'new'}),))
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM search_judgements').fetchone()[0], 3)
                self.assertEqual(client.get('/search-runs/1').json()['snapshot'], run['snapshot'])
                with patch('search_runs.load_perch', return_value=(Session(), 'model')):
                    self.assertEqual(client.post('/search-runs', json=body).json()['detail'], 'no_search_embeddings')
                # XC key is never reflected and only selected, known recording IDs download.
                self.assertEqual(client.get('/xeno-canto/key').json(), {'configured': False})
                self.assertEqual(client.get(f"/xeno-canto/search?species_id={species['id']}").json()['detail'], 'xc_key_required')
                self.assertEqual(client.post('/xeno-canto/key', json={'key': 'secret-test-key'}).json(), {'configured': True})
                if os.name != 'nt':
                    self.assertEqual((root / '.xeno-canto-key').stat().st_mode & 0o777, 0o600)
                urls = []
                def upstream(url, output, maximum):
                    urls.append(url)
                    if '/api/' in url:
                        output.write(json.dumps({'page': 1, 'numPages': 2, 'numRecordings': 601,
                            'recordings': [dict(id=str(100 + i), gen='Horornis', sp='diphone', en='Japanese Bush Warbler',
                                rec='Test recordist', cnt='Japan', q='A', lic='https://creativecommons.org/licenses/by/4.0/') for i in range(501)]}).encode())
                    else:
                        output.write(stereo.read_bytes())
                with patch('xeno_canto.download', side_effect=upstream):
                    found = client.get(f"/xeno-canto/search?species_id={species['id']}&country=Japan&quality=A").json()
                    self.assertEqual(len(found['recordings']), 501)  # Do not silently truncate an upstream page.
                    params = parse_qs(urlparse(urls[0]).query)
                    self.assertEqual(params['query'], ['sp:"Horornis diphone" cnt:"Japan" q:A'])
                    self.assertNotIn('secret-test-key', json.dumps(found))
                    selected = client.post('/xeno-canto/100/import', json={}).json()
                    self.assertEqual(selected['attribution']['recordist'], 'Test recordist')
                    self.assertTrue(Path(selected['path']).is_relative_to(root / 'references'))
                    self.assertEqual(client.post('/xeno-canto/100/import', json={}).json()['id'], selected['id'])
                    self.assertEqual(len(urls), 2)
                    self.assertEqual(client.post('/xeno-canto/999/import', json={}).status_code, 400)
                    self.assertEqual(client.get('/references').json()[0]['attribution']['id'], 100)
                client.post('/xeno-canto/key', json={'key': ''})
                self.assertFalse((root / '.xeno-canto-key').exists())
            with TestClient(create_app(root, root, 'restart'), headers={'Authorization': 'Bearer restart'}) as client:
                persisted = client.get('/search-runs/1').json()
                self.assertEqual(persisted['snapshot'], run['snapshot'])
                self.assertEqual(len(persisted['samples']), 1)
                self.assertEqual(len(client.get('/search-runs?dataset_id=1').json()), 2)
                self.assertEqual(next(h for h in persisted['hits'] if h['segment_id'] == hit['segment_id'])['verdict'], 'target')
            with self.assertRaisesRegex(CatalogError, 'xc_redirect'):
                XCRedirect().redirect_request(Request('https://xeno-canto.org/1/download'), None, 302, '', {}, 'http://127.0.0.1/private')
            class Response(io.BytesIO):
                headers = {}
            with patch('xeno_canto.build_opener') as opener:
                opener.return_value.open.return_value = Response(b'123456')
                with self.assertRaisesRegex(CatalogError, 'xc_too_large'):
                    download('https://xeno-canto.org/1/download', io.BytesIO(), 5)


if __name__ == '__main__':
    unittest.main()
