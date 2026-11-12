"""Hierarchical sound-map cache, build, migration, and API checks."""
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
        labels = np.array(['Horornis diphone', 'Zosterops japonicus',
                           'Corvus macrorhynchos', 'Water'])
        patterns = np.array([[0, -1, -2, 8], [3, 2, 1, 0],
                             [0, 0, 0, 0], [1, 2, 3, 4]], dtype=np.float32)
        logits = np.resize(patterns, (count, len(labels)))
        np.savez_compressed(scores_path, logits=logits,
                            labels=labels)
        vectors = rng.normal(size=(count, 1536)).astype('<f4')
        with connect(self.root) as db:
            recording = db.execute('''INSERT INTO recordings(dataset_id,path,original_time,timezone,recorded_at,
                seconds,sample_rate,size,mtime_ns) VALUES(1,?,'2024-01-01T00:00:00','Asia/Tokyo',?, ?,32000,?,?)''',
                (str(source), f'2024-01-{seed:02d}T00:00:00+09:00', count * 5, stat.st_size, stat.st_mtime_ns)).lastrowid
            job = db.execute("INSERT INTO jobs(dataset_id,status,total,stage) VALUES(1,'complete',1,'finished')").lastrowid
            analysis = db.execute('''INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path)
                VALUES(?,?,?,?,?)''', (job, recording, engine,
                    json.dumps({'model_sha256': model_sha, 'window_s': 5,
                                'candidate_filter': {'species': labels[:3].tolist()}}),
                    str(scores_path))).lastrowid
            for index, vector in enumerate(vectors):
                segment = db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,?,?)',
                                     (analysis, index * 5, (index + 1) * 5)).lastrowid
                db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)', (segment, vector.tobytes()))
        return analysis

    def wait_job(self, job_id):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            jobs = self.client.get('/catalog').json()['jobs']
            job = next(row for row in jobs if row['id'] == job_id)
            if job['status'] not in ('queued', 'running'):
                return job
            time.sleep(.02)
        self.fail('explore job did not finish')

    def assert_tree(self, expected_count):
        with connect(self.root) as db:
            run = db.execute('SELECT * FROM explore_runs').fetchone()
            nodes = [dict(row) for row in db.execute('SELECT * FROM explore_nodes WHERE run_id=? ORDER BY node',
                                                     (run['id'],))]
            points = [dict(row) for row in db.execute('SELECT * FROM explore_points WHERE run_id=? ORDER BY position',
                                                      (run['id'],))]
            point_positions = {row['segment_id']: row['position'] for row in points}
        self.assertEqual(len(points), expected_count)
        self.assertEqual((nodes[0]['node'], nodes[0]['parent'], nodes[0]['name'], nodes[0]['depth'],
                          nodes[0]['lo'], nodes[0]['hi']), (0, None, '', 0, 0, expected_count))
        by_parent = {}
        for node in nodes:
            by_parent.setdefault(node['parent'], []).append(node)
        for parent, children in by_parent.items():
            if parent is not None:
                children.sort(key=lambda row: row['lo'])
        for node in nodes:
            size = node['hi'] - node['lo']
            children = by_parent.get(node['node'], [])
            self.assertGreater(size, 0)
            self.assertEqual(bool(children), size > explore.TERMINAL_MAX)
            if children:
                self.assertGreaterEqual(len(children), 2)
                self.assertEqual([child['hi'] - child['lo'] for child in children],
                                 sorted((child['hi'] - child['lo'] for child in children), reverse=True))
                self.assertEqual(children[0]['lo'], node['lo'])
                self.assertEqual(children[-1]['hi'], node['hi'])
                self.assertTrue(all(left['hi'] == right['lo'] for left, right in zip(children, children[1:])))
                for index, child in enumerate(children, 1):
                    expected_name = f"{node['name']}-{index}" if node['name'] else str(index)
                    self.assertEqual(child['name'], expected_name)
            position = point_positions[node['rep_segment_id']]
            self.assertLessEqual(node['lo'], position)
            self.assertLess(position, node['hi'])
            self.assertGreaterEqual(node['hint_rate'], 0)
            self.assertLessEqual(node['hint_rate'], 1)
            top = json.loads(node['top_species'])
            self.assertLessEqual(len(top), 3)
            shares = [item['share'] for item in top]
            self.assertEqual(shares, sorted(shares, reverse=True))
            self.assertLessEqual(sum(shares), 1 + 1e-12)
            self.assertNotIn('Water', {item['scientific_name'] for item in top})
        return run, nodes, points

    def test_schema_two_through_four_are_upgraded_without_losing_catalog_data(self):
        for version in (2, 3, 4):
            with self.subTest(version=version), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                self.assertTrue(initialize(root))
                with connect(root) as db:
                    db.execute("INSERT INTO sites VALUES(1,'Kept','Asia/Tokyo',35,138)")
                    db.executescript('''DROP TABLE explore_nodes; DROP TABLE explore_points; DROP TABLE explore_runs;''')
                    if version in (3, 4):
                        db.executescript('''
                            CREATE TABLE explore_runs (id INTEGER PRIMARY KEY,site_id INTEGER NOT NULL REFERENCES sites,
                                engine TEXT NOT NULL,model_sha TEXT NOT NULL,params TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                                UNIQUE(site_id,engine,model_sha));
                        ''')
                        if version == 3:
                            db.executescript('''
                                CREATE TABLE explore_points (run_id INTEGER,segment_id INTEGER,x REAL,y REAL,cluster INTEGER,dist REAL,
                                    PRIMARY KEY(run_id,segment_id));
                                CREATE TABLE explore_clusters (run_id INTEGER,cluster INTEGER,size INTEGER,rep_segment_id INTEGER,
                                    score_mean REAL,top_species TEXT,PRIMARY KEY(run_id,cluster));
                            ''')
                        else:
                            db.executescript('''
                                CREATE TABLE explore_points (run_id INTEGER,position INTEGER,segment_id INTEGER,x REAL,y REAL,
                                    PRIMARY KEY(run_id,position));
                                CREATE TABLE explore_nodes (run_id INTEGER,node INTEGER,parent INTEGER,name TEXT,depth INTEGER,
                                    lo INTEGER,hi INTEGER,rep_segment_id INTEGER,hint_rate REAL,top_species TEXT,top_share REAL,
                                    PRIMARY KEY(run_id,node));
                            ''')
                        folder = root / 'explore'; folder.mkdir()
                        (folder / 'points-old.bin').write_bytes(b'old')
                        (folder / 'pca50-old.f32').write_bytes(b'old')
                        db.execute("INSERT INTO explore_runs(id,site_id,engine,model_sha,params) VALUES(1,1,'perch','old',?)",
                                   (json.dumps({'points_file': 'points-old.bin', 'pca_file': 'pca50-old.f32'}),))
                    db.execute(f'PRAGMA user_version={version}')
                self.assertTrue(initialize(root))
                with connect(root) as db:
                    self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                    self.assertEqual(db.execute('SELECT name FROM sites WHERE id=1').fetchone()[0], 'Kept')
                    tables = {row[0] for row in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'explore_%'")}
                    self.assertEqual(tables, {'explore_runs', 'explore_points', 'explore_nodes'})
                    columns = [row['name'] for row in db.execute('PRAGMA table_info(explore_points)')]
                    self.assertEqual(columns, ['run_id', 'position', 'segment_id', 'x', 'y', 'candidates'])
                    node_columns = [row['name'] for row in db.execute('PRAGMA table_info(explore_nodes)')]
                    self.assertNotIn('top_share', node_columns)
                if version in (3, 4):
                    self.assertFalse((root / 'explore' / 'points-old.bin').exists())
                    self.assertFalse((root / 'explore' / 'pca50-old.f32').exists())

    def test_exact_build_binary_api_cache_staleness_busy_and_cancel(self):
        first = self.add_analysis(32, 1)
        response = self.client.post('/explore', json={'dataset_id': 1})
        self.assertEqual(response.status_code, 200, response.text)
        job = self.wait_job(response.json()['id'])
        self.assertEqual((job['status'], job['error'], job['done'], job['total']), ('complete', None, 5, 5))
        run, nodes, points = self.assert_tree(32)
        params = json.loads(run['params'])
        self.assertEqual((params['method'], params['layout']), ('exact', 'full'))
        self.assertEqual(params['map_version'], explore.MAP_VERSION)
        self.assertEqual(params['candidate_filter'], {'perch': 'applied'})
        self.assertEqual((params['candidate_min'], params['score_threshold']),
                         (explore.CANDIDATE_MIN, explore.SCORE_THRESHOLD))
        self.assertEqual(set(params['stage_seconds']), {'cache', 'pca', 'tree', 'layout', 'summarizing'})
        self.assertEqual((params['node_count'], params['depth']),
                         (len(nodes), max(row['depth'] for row in nodes)))

        summary = self.client.get('/explore', params={'dataset': 1}).json()
        self.assertEqual((summary['status'], summary['engine'], summary['segment_count']), ('ready', 'perch', 32))
        self.assertNotIn('stale_reason', summary)
        self.assertEqual(summary['node']['node'], 0)
        self.assertNotIn('top_share', summary['node'])
        self.assertTrue(all(set(item) == {'id', 'scientific_name', 'share'}
                            for item in summary['node']['top_species']))
        self.assertEqual(summary['path'], [{'node': 0, 'name': '', 'size': 32}])
        self.assertEqual([row['size'] for row in summary['children']],
                         sorted((row['size'] for row in summary['children']), reverse=True))
        child = summary['children'][0]
        opened = self.client.get('/explore', params={'dataset': 1, 'node': child['node']}).json()
        self.assertEqual(child['child_count'], len(opened['children']))
        self.assertEqual(summary['node']['child_count'], len(summary['children']))
        child_summary = self.client.get('/explore', params={'dataset': 1, 'node': child['node']}).json()
        self.assertEqual(child_summary['path'][-1],
                         {'node': child['node'], 'name': child['name'], 'size': child['size']})
        unknown = self.client.get('/explore', params={'dataset': 1, 'node': 999999})
        self.assertEqual((unknown.status_code, unknown.json()), (404, {'detail': 'not_found'}))

        with patch.object(explore, 'np', None):
            points_response = self.client.get('/explore/points', params={'dataset': 1})
        self.assertEqual(points_response.headers['content-type'], 'application/octet-stream')
        content = points_response.content
        count = struct.unpack_from('<I', content)[0]
        offset = 4
        x = np.frombuffer(content, '<f4', count, offset); offset += count * 4
        y = np.frombuffer(content, '<f4', count, offset); offset += count * 4
        segment_ids = np.frombuffer(content, '<i8', count, offset)
        self.assertEqual(count, 32)
        np.testing.assert_array_equal(segment_ids, [row['segment_id'] for row in points])
        np.testing.assert_allclose(x, [row['x'] for row in points])
        np.testing.assert_allclose(y, [row['y'] for row in points])
        self.assertEqual((self.root / 'explore' / params['pca_file']).stat().st_size, 32 * 50 * 4)
        point = self.client.get('/explore/points/3', params={'dataset': 1})
        self.assertEqual(point.status_code, 200, point.text)
        self.assertEqual(point.json()['segment_id'], int(segment_ids[3]))
        stored_candidates = json.loads(points[3]['candidates'])
        self.assertEqual([[item['scientific_name'], item['score']] for item in point.json()['candidates']],
                         stored_candidates)
        past_end = self.client.get(f'/explore/points/{count}', params={'dataset': 1})
        self.assertEqual((past_end.status_code, past_end.json()), (404, {'detail': 'not_found'}))

        with np.load(self.root / 'scores-1.npz') as saved:
            logits = saved['logits'].astype(np.float64)
        probabilities = np.exp(logits - logits.max(axis=1, keepdims=True))
        probabilities /= probabilities.sum(axis=1, keepdims=True)
        expected_hint_rate = float((probabilities[:, :3].max(axis=1) >= explore.SCORE_THRESHOLD).mean())
        self.assertAlmostEqual(nodes[0]['hint_rate'], expected_hint_rate)
        for row in points:
            candidates = json.loads(row['candidates'])
            self.assertLessEqual(len(candidates), 3)
            self.assertEqual([item[1] for item in candidates],
                             sorted((item[1] for item in candidates), reverse=True))
            self.assertTrue(all(item[1] >= explore.CANDIDATE_MIN for item in candidates))
            self.assertNotIn('Water', {item[0] for item in candidates})

        terminal = next(row for row in nodes if row['hi'] - row['lo'] <= explore.TERMINAL_MAX)
        for node in (nodes[0], terminal):
            page = self.client.get(f"/explore/nodes/{node['node']}/segments", params={'dataset': 1}).json()
            self.assertEqual(page['total'], node['hi'] - node['lo'])
            self.assertEqual([row['dist'] for row in page['segments']],
                             sorted(row['dist'] for row in page['segments']))
            self.assertTrue(all(set(row) == {'segment_id', 'recording_id', 'path', 'start_s', 'end_s',
                                              'recorded_at', 'timezone', 'candidates', 'dist'}
                                for row in page['segments']))
            self.assertTrue(all(set(candidate) == {'id', 'scientific_name', 'score'}
                                for row in page['segments'] for candidate in row['candidates']))
            near = node['lo']
            near_page = self.client.get(f"/explore/nodes/{node['node']}/segments",
                                        params={'dataset': 1, 'near': near}).json()
            self.assertEqual((near_page['segments'][0]['segment_id'], near_page['segments'][0]['dist']),
                             (points[near]['segment_id'], 0))
        near = terminal['lo']
        first_page = self.client.get('/explore/nodes/0/segments',
                                     params={'dataset': 1, 'near': near}).json()
        second_page = self.client.get('/explore/nodes/0/segments',
                                      params={'dataset': 1, 'near': near, 'offset': 20}).json()
        self.assertEqual((second_page['total'], len(second_page['segments'])), (32, 12))
        distances = [row['dist'] for row in first_page['segments'] + second_page['segments']]
        self.assertEqual(distances, sorted(distances))
        past_end = self.client.get('/explore/nodes/0/segments', params={'dataset': 1, 'near': count})
        self.assertEqual((past_end.status_code, past_end.json()), (404, {'detail': 'not_found'}))

        index_path = self.root / 'explore' / params['cache_index']
        old_index = json.loads(index_path.read_text())
        old_vectors = index_path.with_name(index_path.name.replace('.json', '.f32')).read_bytes()
        old_files = [self.root / 'explore' / params[key] for key in ('points_file', 'pca_file')]
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
        # A plain function, not a mock: a mock keeps the mapped arrays it was called with until
        # garbage collection, and Windows cannot delete a mapped file.
        with patch('explore._stream_vectors', traced):
            explore.build(self.root, 1)
        self.assertEqual(reads, [[second]])
        new_index = json.loads(index_path.read_text())
        self.assertEqual(old_index['analyses'][str(first)], new_index['analyses'][str(first)])
        self.assertEqual((new_index['analyses'][str(second)]['start'], new_index['rows']), (32, 40))
        self.assertEqual(index_path.with_name(index_path.name.replace('.json', '.f32')).read_bytes()[:len(old_vectors)],
                         old_vectors)
        self.assertTrue(all(not path.exists() for path in old_files))

        self.add_analysis(4, 3)
        stale = self.client.get('/explore', params={'dataset': 1}).json()
        self.assertEqual((stale['status'], stale['stale_reason']), ('stale', 'data'))
        self.assertEqual(stale['node']['node'], 0)  # the old map stays browsable until it is rebuilt
        entered = threading.Event()
        def slow(root, dataset_id, job_id, cancel):
            entered.set(); cancel.wait(5)
            with connect(root) as db:
                db.execute("UPDATE jobs SET status='cancelled',stage='finished' WHERE id=?", (job_id,))
        with patch('explore.build', side_effect=slow):
            started = self.client.post('/explore', json={'dataset_id': 1})
            self.assertTrue(entered.wait(5))
            busy = self.client.post('/explore', json={'dataset_id': 1})
            self.assertEqual((busy.status_code, busy.json()), (409, {'detail': 'job_busy'}))
            self.assertEqual(self.client.post('/operations/cancel').json(), {'status': 'cancelling'})
            self.assertEqual(self.wait_job(started.json()['id'])['status'], 'cancelled')

    def test_stored_scores_are_not_softmaxed_or_filtered(self):
        path = self.root / 'kinglet-scores.npz'
        np.savez(path, labels=['A', 'B', 'C', 'D'],
                 scores=np.array([[.8, .1, .04, .03], [.01, .02, .03, .04]], dtype=np.float32))
        maximum, winners, candidates = explore._score_rows(path, 'kinglet-s', 2)
        np.testing.assert_allclose(maximum, [.8, .04])
        self.assertEqual(winners, ['A', 'D'])
        self.assertEqual(candidates, [[['A', .8], ['B', .1]], []])

    def test_tree_uses_layout_and_member_order_uses_pca(self):
        self.add_analysis(24, 4)
        seen = {}
        original_layout = explore.layout_points
        original_tree = explore.binary_tree
        original_flatten = explore.flatten_tree

        def traced_layout(projected, cancel=None):
            seen['pca'] = np.asarray(projected).copy()
            layout, mode = original_layout(projected, cancel)
            seen['layout'] = layout.copy()
            return layout, mode

        def traced_tree(layout, cancel=None):
            seen['tree'] = np.asarray(layout).copy()
            return original_tree(layout, cancel)

        def traced_flatten(tree, root, projected, cancel=None):
            seen['flatten'] = np.asarray(projected).copy()
            return original_flatten(tree, root, projected, cancel)

        with patch.object(explore, 'layout_points', traced_layout), \
                patch.object(explore, 'binary_tree', traced_tree), \
                patch.object(explore, 'flatten_tree', traced_flatten):
            explore.build(self.root, 1)
        self.assertEqual(seen['tree'].shape, (24, 2))
        np.testing.assert_array_equal(seen['tree'], seen['layout'])
        np.testing.assert_array_equal(seen['flatten'], seen['pca'])

    def test_old_map_method_is_stale_but_browsable_and_data_takes_precedence(self):
        self.add_analysis(24, 5)
        explore.build(self.root, 1)
        with connect(self.root) as db:
            run = db.execute('SELECT id,params FROM explore_runs').fetchone()
            params = json.loads(run['params'])
            params.pop('map_version')
            db.execute('UPDATE explore_runs SET params=? WHERE id=?', (json.dumps(params), run['id']))
        stale = self.client.get('/explore', params={'dataset': 1}).json()
        self.assertEqual((stale['status'], stale['stale_reason']), ('stale', 'method'))
        self.assertEqual(stale['node']['node'], 0)
        self.add_analysis(4, 6)
        stale = self.client.get('/explore', params={'dataset': 1}).json()
        self.assertEqual((stale['status'], stale['stale_reason']), ('stale', 'data'))
        self.assertEqual(stale['node']['node'], 0)

    def test_stopped_builds_leave_no_temporary_files(self):
        # Windows cannot delete, replace or shrink a file that is still memory-mapped.
        folder = self.root / 'explore'

        def stop(*args, **kwargs):
            raise CatalogError('cancelled')

        def stopped_build(function):
            with patch.object(explore, function, stop), self.assertRaisesRegex(CatalogError, 'cancelled'):
                explore.build(self.root, 1)
            with connect(self.root) as db:
                self.assertIsNone(db.execute('SELECT 1 FROM explore_runs').fetchone())

        self.add_analysis(24, 7)
        stopped_build('_stream_vectors')  # while the cache is first written
        self.assertEqual(list(folder.iterdir()), [])
        stopped_build('layout_points')  # while the staged PCA file is mapped
        cache = sorted(path.name for path in folder.iterdir())
        self.assertEqual([Path(name).suffix for name in cache], ['.f32', '.i64', '.json'])
        vectors = folder / cache[0]
        self.assertEqual(vectors.stat().st_size, 24 * explore.EMBEDDING_DIMS * 4)
        self.add_analysis(8, 8)
        stopped_build('_stream_vectors')  # while the cache is extended
        self.assertEqual(vectors.stat().st_size, 24 * explore.EMBEDDING_DIMS * 4)
        abandoned = folder / '.stage-abandoned'
        abandoned.mkdir()
        (abandoned / 'pca50-source.f32').write_bytes(b'left by a build that crashed')
        explore.build(self.root, 1)
        self.assert_tree(32)
        self.assertEqual(list(folder.glob('.stage-*')), [])

    def cache_files(self):
        folder = self.root / 'explore'
        return [next(folder.glob(f'embeddings-*{suffix}')).read_bytes() for suffix in ('.f32', '.i64')]

    def expected_cache(self):
        with connect(self.root) as db:
            rows = db.execute('''SELECT s.id,v.embedding FROM segments s JOIN vectors v ON v.rowid=s.id
                ORDER BY s.analysis_id,s.start_s,s.id''').fetchall()
        return [b''.join(row['embedding'] for row in rows),
                np.array([row['id'] for row in rows], dtype='<i8').tobytes()]

    def test_cache_bytes_are_the_vectors_in_row_order_when_written_and_extended(self):
        engine, model_sha = main_identity(self.root)
        def cache():
            analyses = explore._context(self.root, 1)[3]
            matrix, segment_ids, index, _ = explore.embedding_cache(self.root, 1, engine, model_sha, analyses)
            self.assertEqual((matrix.shape, segment_ids.shape), ((index['rows'], 1536), (index['rows'],)))
            del matrix, segment_ids
        self.add_analysis(7, 1); self.add_analysis(5, 2)
        with patch.object(explore, 'VECTOR_BATCH', 4):  # several appends per analysis
            cache()
            self.assertEqual(self.cache_files(), self.expected_cache())
            self.add_analysis(6, 3)
            cache()
        self.assertEqual(self.cache_files(), self.expected_cache())
        self.assertEqual(len(self.cache_files()[0]), 18 * 1536 * 4)

    def test_first_stage_reports_progress_and_stops_when_cancelled(self):
        self.add_analysis(10, 1); self.add_analysis(22, 2)
        with connect(self.root) as db:
            job = db.execute("INSERT INTO jobs(dataset_id,status,total,stage) VALUES(1,'queued',2,'explore_checking')").lastrowid
        calls = []
        original = explore._progress
        def traced(root, job_id, stage, done, total=5, **kwargs):
            calls.append((stage, done, total))
            original(root, job_id, stage, done, total, **kwargs)
        with patch.object(explore, '_progress', traced), patch.object(explore, 'VECTOR_BATCH', 10):
            explore.build(self.root, 1, job)
        self.assertEqual(calls[:6], [('explore_checking', 0, 2), ('explore_cache', 0, 0), ('explore_cache', 0, 32),
                                     ('explore_cache', 10, 32), ('explore_cache', 20, 32), ('explore_cache', 30, 32)])
        self.assertEqual(calls[6:8], [('explore_cache', 32, 32), ('explore_pca', 1, 5)])

        stop = threading.Event()
        checked = []
        def check_then_stop(row):
            checked.append(row['analysis_id']); stop.set()
        for path in (self.root / 'explore').glob('*'):  # the next builds start from no files
            path.unlink()
        with patch.object(explore, 'checked_source', check_then_stop), \
                self.assertRaisesRegex(CatalogError, 'cancelled'):
            explore.build(self.root, 1, job, stop)
        self.assertEqual(len(checked), 1)
        with connect(self.root) as db:
            row = db.execute('SELECT status,stage,error FROM jobs WHERE id=?', (job,)).fetchone()
        self.assertEqual(tuple(row), ('cancelled', 'explore_checking', None))

        stop.clear()
        def stop_after_first_batch(root, job_id, stage, done, total=5, **kwargs):
            if stage == 'explore_cache' and done:
                stop.set()
            original(root, job_id, stage, done, total, **kwargs)
        with patch.object(explore, '_progress', stop_after_first_batch), patch.object(explore, 'VECTOR_BATCH', 10), \
                self.assertRaisesRegex(CatalogError, 'cancelled'):
            explore.build(self.root, 1, job, stop)
        self.assertEqual(list((self.root / 'explore').iterdir()), [])

    def test_missing_recordings_are_named(self):
        for seed in (1, 2, 3):
            self.add_analysis(8, seed)
        for seed in (2, 3):
            (self.root / f'recording-{seed}.wav').unlink()
        job = self.wait_job(self.client.post('/explore', json={'dataset_id': 1}).json()['id'])
        code = f"source_missing:1:{self.root / 'recording-2.wav'}"  # the first missing file and how many more
        self.assertEqual((job['status'], job['error']), ('failed', code))
        self.assertEqual(self.client.get('/explore', params={'dataset': 1}).json()['last_error'], code)

    def test_low_disk_refusal_and_last_failure_until_the_next_build(self):
        self.add_analysis(32, 1)
        Usage = type(explore.shutil.disk_usage(self.root))
        with patch('explore.shutil.disk_usage', return_value=Usage(10**9, 10**9 - 1000, 1000)):
            job = self.wait_job(self.client.post('/explore', json={'dataset_id': 1}).json()['id'])
        need = 32 * explore.CACHE_ROW_BYTES + 32 * 1024 + 256 * 2**20
        self.assertEqual((job['status'], job['error']), ('failed', f'explore_disk_space:{need}:1000'))
        self.assertEqual(list((self.root / 'explore').iterdir()), [])
        summary = self.client.get('/explore', params={'dataset': 1}).json()
        self.assertEqual((summary['status'], summary['last_error']), ('none', job['error']))
        with connect(self.root) as db:  # an unrelated analysis job afterwards does not hide it
            db.execute("INSERT INTO jobs(dataset_id,status,total,stage) VALUES(1,'complete',1,'finished')")
        self.assertEqual(self.client.get('/explore', params={'dataset': 1}).json()['last_error'], job['error'])

        job = self.wait_job(self.client.post('/explore', json={'dataset_id': 1}).json()['id'])
        self.assertEqual(job['status'], 'complete')
        summary = self.client.get('/explore', params={'dataset': 1}).json()
        self.assertEqual(summary['status'], 'ready')
        self.assertNotIn('last_error', summary)
        with connect(self.root) as db:  # the app closed during a later build
            db.execute("INSERT INTO jobs(dataset_id,status,total,stage) VALUES(1,'interrupted',5,'explore_pca')")
        self.assertEqual(self.client.get('/explore', params={'dataset': 1}).json()['last_error'], 'explore_interrupted')

    def test_failed_build_is_written_to_the_log_file(self):
        import logging
        from server import keep_log
        def close_log():
            for handler in logging.getLogger().handlers[:]:
                logging.getLogger().removeHandler(handler); handler.close()
        self.addCleanup(close_log)
        keep_log(self.root)
        self.add_analysis(8, 1)
        self.add_analysis(8, 2)
        (self.root / 'recording-1.wav').unlink()
        (self.root / 'recording-2.wav').unlink()
        job = self.wait_job(self.client.post('/explore', json={'dataset_id': 1}).json()['id'])
        missing = self.root / 'recording-1.wav'
        self.assertEqual((job['status'], job['error']), ('failed', f'source_missing:1:{missing}'))
        log = (self.root / 'sidecar.log').read_text(encoding='utf-8')
        self.assertIn(f"Sound map build {job['id']} failed", log)
        self.assertIn(f'CatalogError: source_missing:1:{missing}', log)
        self.assertIn(str(self.root / 'recording-2.wav'), log)  # every missing path, not only the first

    def test_leaf_tree_and_landmark_layout_with_patched_thresholds(self):
        self.add_analysis(200, 4)
        with patch.object(explore, 'EXACT_MAX', 50), patch.object(explore, 'LEAF_COUNT', 8), \
                patch.object(explore, 'FIT_MAX', 60), patch.object(explore, 'LANDMARK_MAX', 40):
            explore.build(self.root, 1)
            run, nodes, _ = self.assert_tree(200)
        params = json.loads(run['params'])
        self.assertEqual((params['method'], params['layout']), ('leaves', 'landmarks'))
        self.assertEqual(params['map_version'], explore.MAP_VERSION)
        self.assertEqual(params['tree']['requested_leaves'], 8)
        self.assertEqual(params['segment_count'], 200)
        self.assertGreater(len(nodes), 1)


if __name__ == '__main__':
    unittest.main()
