"""Sound-map cache, clustering, atomic replacement, and API checks."""
import json
import struct
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

import explore
from models import REGISTRY, main_identity
from server import create_app
from storage import SCHEMA_VERSION, CatalogError, connect, initialize


class ExploreCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / 'data'
        self.models = base / 'models'
        for relative in REGISTRY['perch']['files']:
            path = self.models / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test model' if path.suffix == '.onnx' else b'label')
        self.app = create_app(self.root, self.models, 'test')
        self.client = TestClient(self.app, headers={'Authorization': 'Bearer test'})
        self.client.__enter__()
        with connect(self.root) as db:
            db.execute("INSERT INTO sites VALUES(1,'Test','Asia/Tokyo',35,138)")
            db.execute("INSERT INTO datasets VALUES(1,1,'Data','unused')")

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def add_analysis(self, count, seed):
        engine, model_sha = main_identity(self.root)
        source = self.root / f'recording-{seed}.wav'
        source.write_bytes(bytes([seed]) * 100)
        stat = source.stat()
        scores_path = self.root / f'scores-{seed}.npz'
        rng = np.random.default_rng(seed)
        logits = rng.normal(size=(count, 3)).astype(np.float32)
        np.savez_compressed(scores_path, logits=logits,
            labels=np.array(['Horornis diphone', 'Zosterops japonicus', 'Corvus macrorhynchos']))
        vectors = rng.normal(size=(count, 1536)).astype('<f4')
        with connect(self.root) as db:
            recording = db.execute('''INSERT INTO recordings(dataset_id,path,original_time,timezone,recorded_at,
                seconds,sample_rate,size,mtime_ns) VALUES(1,?,'2024-01-01T00:00:00','Asia/Tokyo',?, ?,32000,?,?)''',
                (str(source), f'2024-01-{seed:02d}T00:00:00+09:00', count * 5, stat.st_size, stat.st_mtime_ns)).lastrowid
            job = db.execute("INSERT INTO jobs(dataset_id,status,total,stage) VALUES(1,'complete',1,'finished')").lastrowid
            analysis = db.execute('''INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path)
                VALUES(?,?,?,?,?)''', (job, recording, engine,
                    json.dumps({'model_sha256': model_sha, 'window_s': 5}), str(scores_path))).lastrowid
            for index, vector in enumerate(vectors):
                segment = db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,?,?)',
                                     (analysis, index * 5, (index + 1) * 5)).lastrowid
                db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)', (segment, vector.tobytes()))
        return analysis

    def wait_job(self, job_id):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            jobs = self.client.get('/catalog').json()['jobs']
            job = next(row for row in jobs if row['id'] == job_id)
            if job['status'] not in ('queued', 'running'):
                return job
            time.sleep(.02)
        self.fail('explore job did not finish')

    def test_schema_two_is_upgraded_without_losing_catalog_data(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertTrue(initialize(root))
            with connect(root) as db:
                db.execute("INSERT INTO sites VALUES(1,'Kept','Asia/Tokyo',35,138)")
                for table in ('explore_clusters', 'explore_points', 'explore_runs'):
                    db.execute(f'DROP TABLE {table}')
                db.execute('PRAGMA user_version=2')
            self.assertTrue(initialize(root))
            with connect(root) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(db.execute('SELECT name FROM sites WHERE id=1').fetchone()[0], 'Kept')
                self.assertEqual({row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'explore_%'")},
                    {'explore_runs', 'explore_points', 'explore_clusters'})

    def test_build_cache_staleness_binary_endpoints_and_busy(self):
        first = self.add_analysis(32, 1)
        response = self.client.post('/explore', json={'dataset_id': 1})
        self.assertEqual(response.status_code, 200, response.text)
        job = self.wait_job(response.json()['id'])
        self.assertEqual((job['status'], job['error'], job['done'], job['total']), ('complete', None, 5, 5))

        summary = self.client.get('/explore', params={'dataset': 1})
        self.assertEqual(summary.status_code, 200, summary.text)
        summary = summary.json()
        self.assertEqual((summary['status'], summary['engine'], summary['segment_count']), ('ready', 'perch', 32))
        self.assertIsNotNone(summary['created_at'])
        self.assertEqual(sum(row['size'] for row in summary['clusters']), 32)
        self.assertEqual([row['size'] for row in summary['clusters']],
                         sorted((row['size'] for row in summary['clusters']), reverse=True))
        self.assertTrue(all(set(row['rep']) == {'segment_id', 'recording_id', 'path', 'start_s', 'end_s',
                                                'recorded_at', 'timezone'} for row in summary['clusters']))

        with patch.object(explore, 'np', None):
            points_response = self.client.get('/explore/points', params={'dataset': 1})
        self.assertEqual(points_response.status_code, 200, points_response.text)
        self.assertEqual(points_response.headers['content-type'], 'application/octet-stream')
        content = points_response.content
        count = struct.unpack_from('<I', content)[0]
        self.assertEqual(count, 32)
        offset = 4
        x = np.frombuffer(content, '<f4', count, offset); offset += count * 4
        y = np.frombuffer(content, '<f4', count, offset); offset += count * 4
        clusters = np.frombuffer(content, '<i4', count, offset); offset += count * 4
        segment_ids = np.frombuffer(content, '<i8', count, offset)
        with connect(self.root) as db:
            run = db.execute('SELECT * FROM explore_runs').fetchone()
            params = json.loads(run['params'])
            rows = db.execute('SELECT * FROM explore_points ORDER BY segment_id').fetchall()
            self.assertEqual(db.execute('SELECT COUNT(*) FROM explore_clusters').fetchone()[0], len(summary['clusters']))
        self.assertEqual(len(rows), count)
        self.assertEqual(params['k_selection']['parallel'], 'processes')
        np.testing.assert_array_equal(segment_ids, [row['segment_id'] for row in rows])
        np.testing.assert_allclose(x, [row['x'] for row in rows])
        np.testing.assert_allclose(y, [row['y'] for row in rows])
        np.testing.assert_array_equal(clusters, [row['cluster'] for row in rows])
        index_path = self.root / 'explore' / params['cache_index']
        old_index = json.loads(index_path.read_text())
        self.assertEqual(old_index['analyses'][str(first)]['start'], 0)
        old_vectors = (self.root / 'explore' / index_path.name.replace('.json', '.f32')).read_bytes()
        stopped = threading.Event(); stopped.set()
        with self.assertRaisesRegex(CatalogError, 'cancelled'):
            explore.build(self.root, 1, cancel=stopped)
        with connect(self.root) as db:
            self.assertEqual(db.execute('SELECT id FROM explore_runs').fetchone()['id'], run['id'])

        second = self.add_analysis(8, 2)
        reads = []
        original = explore._stream_vectors
        def traced(root, analyses, *args):
            reads.append([row['analysis_id'] for row in analyses])
            return original(root, analyses, *args)
        with patch('explore._stream_vectors', side_effect=traced):
            explore.build(self.root, 1)
        self.assertEqual(reads, [[second]])
        new_index = json.loads(index_path.read_text())
        self.assertEqual(new_index['analyses'][str(first)], old_index['analyses'][str(first)])
        self.assertEqual((new_index['analyses'][str(second)]['start'], new_index['rows']), (32, 40))
        new_vectors = (self.root / 'explore' / index_path.name.replace('.json', '.f32')).read_bytes()
        self.assertEqual(new_vectors[:len(old_vectors)], old_vectors)
        with connect(self.root) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM explore_runs').fetchone()[0], 1)
            run = db.execute('SELECT id FROM explore_runs').fetchone()['id']
            self.assertEqual(db.execute('SELECT COUNT(*) FROM explore_points WHERE run_id=?', (run,)).fetchone()[0], 40)
            self.assertEqual(db.execute('SELECT SUM(size) FROM explore_clusters WHERE run_id=?', (run,)).fetchone()[0], 40)
            representative = db.execute('SELECT segment_id FROM explore_points WHERE run_id=? ORDER BY dist,segment_id LIMIT 1',
                                        (run,)).fetchone()['segment_id']
            score_mean = db.execute('SELECT AVG(score_mean) FROM explore_clusters WHERE run_id=?', (run,)).fetchone()[0]
            db.execute('UPDATE explore_points SET cluster=0 WHERE run_id=?', (run,))
            db.execute('DELETE FROM explore_clusters WHERE run_id=?', (run,))
            db.execute('INSERT INTO explore_clusters VALUES(?,0,40,?,?,NULL)',
                       (run, representative, score_mean))

        page = self.client.get('/explore/clusters/0/segments', params={'dataset': 1, 'offset': 0})
        self.assertEqual(page.status_code, 200, page.text)
        page = page.json()
        self.assertEqual((page['total'], len(page['segments'])), (40, 20))
        self.assertEqual(set(page['segments'][0]), {'segment_id', 'recording_id', 'path', 'start_s', 'end_s',
                                                    'recorded_at', 'timezone', 'dist'})
        self.assertEqual([row['dist'] for row in page['segments']], sorted(row['dist'] for row in page['segments']))
        second_page = self.client.get('/explore/clusters/0/segments', params={'dataset': 1, 'offset': 20}).json()
        self.assertEqual((second_page['total'], len(second_page['segments'])), (40, 20))

        self.add_analysis(4, 3)
        self.assertEqual(self.client.get('/explore', params={'dataset': 1}).json()['status'], 'stale')

        entered = threading.Event()
        def slow(root, dataset_id, job_id, cancel):
            entered.set()
            cancel.wait(5)
            with connect(root) as db:
                db.execute("UPDATE jobs SET status='cancelled',stage='finished' WHERE id=?", (job_id,))
        with patch('explore.build', side_effect=slow):
            started = self.client.post('/explore', json={'dataset_id': 1})
            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(entered.wait(5))
            busy = self.client.post('/explore', json={'dataset_id': 1})
            self.assertEqual((busy.status_code, busy.json()), (409, {'detail': 'job_busy'}))
            self.assertEqual(self.client.post('/operations/cancel').json(), {'status': 'cancelling'})
            self.assertEqual(self.wait_job(started.json()['id'])['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
