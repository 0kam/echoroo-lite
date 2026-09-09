"""Run bounded audio and dataset-scoped similarity checks without downloading models."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from audio import read_interval, source
from search import query_embeddings, rank_segments
from server import create_app
from storage import CatalogError, connect


class SearchCheck(unittest.TestCase):
    def test_audio_selection_and_scoped_ranking(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tone = np.sin(2 * np.pi * 1000 * np.arange(24000 * 65) / 24000).astype(np.float32) * 0.5
            mono, stereo = root / 'rec_20240808_060000.wav', root / 'reference.wav'
            sf.write(mono, tone, 24000)
            sf.write(stereo, np.column_stack([tone, tone / 2]), 24000)
            with TestClient(create_app(root, root / 'models', 'test'), headers={'Authorization': 'Bearer test'}) as client:
                imported = client.post('/imports', json=dict(folder=temp, site_name='Test', dataset_name='Group',
                    timezone='Asia/Tokyo', latitude=35, longitude=138)).json()
                self.assertEqual(imported['count'], 1)
                ref = client.post('/references', json={'path': str(stereo)}).json()
                self.assertEqual(client.post('/references', json={'path': str(stereo)}).json()['id'], ref['id'])
                self.assertEqual(len(client.get('/references').json()), 1)
                url = f"/sources/reference/{ref['id']}"
                self.assertEqual(client.get(url + '/audio', headers={'Authorization': ''}).status_code, 401)
                self.assertEqual(client.get(url + '/audio?token=test', headers={'Authorization': '', 'Range': 'bytes=0-43'}).status_code, 206)
                self.assertEqual(client.get('/sources/unknown/1/audio').status_code, 422)
                self.assertEqual(client.post('/references', json={'path': str(root / 'test.txt')}).json()['detail'], 'reference_format')
                view = client.get(url + '/spectrogram?start_s=1&end_s=61').json()
                pixels = np.frombuffer(base64.b64decode(view['pixels']), dtype=np.uint8).reshape(view['height'], view['width'])
                self.assertLessEqual(view['width'], 1000)
                frequency = 12000 - np.argmax(pixels.mean(axis=1)) * 31.25
                self.assertAlmostEqual(frequency, 1000, delta=32)
                for start, end in [(-1, 1), (1, 1), (0, 61), (64, 66), ('nan', 2)]:
                    self.assertIn(client.get(url + f'/spectrogram?start_s={start}&end_s={end}').status_code, (400, 422))
                samples, interval = read_interval(ref, 1, 7)
                self.assertEqual(len(samples), 6 * 32000)
                self.assertEqual(interval['source_channels'], 2)
                self.assertAlmostEqual(np.max(samples[100:-100]), 0.375, delta=0.002)
                e0, e1 = np.eye(2, 1536, dtype='<f4')
                inputs = []
                class Session:
                    def run(self, outputs, feed):
                        inputs.append(feed['inputs'].copy())
                        return [e0[None]]
                with patch('search.load_perch', return_value=(Session(), 'model')):
                    vectors, query = query_embeddings(root, ref, 1, 7)
                    self.assertEqual(len(vectors), 2)
                    self.assertEqual([w['padding_s'] for w in query['windows']], [0, 4])
                    self.assertTrue(np.all(inputs[1][0, 32000:] == 0))
                    self.assertEqual(inputs[0].shape, (1, 160000))
                    self.assertEqual(query['requested_start_s'], 1)
                    self.assertEqual(len(query_embeddings(root, ref, 1, 1.2)[0]), 1)
                record = source(root, 'recording', 1)
                with connect(root) as db:
                    db.execute("INSERT INTO sites VALUES(2,'Other site','Asia/Tokyo',35,138)")
                    db.execute("INSERT INTO datasets VALUES(2,2,'Other','/other')")
                    for rid, dataset in [(2, 2), (3, 1), (4, 1)]:
                        path = root / f'{rid}.wav'
                        sf.write(path, tone, 24000)
                        stat = path.stat()
                        db.execute('INSERT INTO recordings VALUES(?,?,?,?,?,?,?,?,?,?)', (rid, dataset, str(path),
                            record['original_time'], record['timezone'], record['recorded_at'], 65, 24000, stat.st_size, stat.st_mtime_ns))
                    for jid in range(1, 6):
                        db.execute("INSERT INTO jobs(id,dataset_id,status,total) VALUES(?,1,'complete',1)", (jid,))
                    # Old compatible analysis, latest compatible, other dataset, and a changed model.
                    for aid, rid, model in [(1, 1, 'model'), (2, 1, 'model'), (3, 2, 'model'), (4, 3, 'model'), (5, 3, 'other')]:
                        db.execute("INSERT INTO analyses VALUES(?,?,?,'perch',?,'unused.npz')", (aid, aid, rid, json.dumps({'model_sha256': model})))
                    for sid, aid, start, vector in [(1, 1, 10, e0), (2, 2, 0, e0), (3, 2, 5, e1),
                        (4, 2, 10, e0 * .8 + e1 * .6), (5, 2, 15, -e0), (6, 3, 0, e0), (7, 4, 0, e0), (8, 5, 0, e0)]:
                        db.execute('INSERT INTO segments VALUES(?,?,?,?)', (sid, aid, start, start + 5))
                        db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)', (sid, vector.tobytes()))
                with connect(root) as db: db.execute('UPDATE datasets SET site_id=1 WHERE id=2')
                site_hits, site_coverage = rank_segments(root, 1, [e0], 'model', record, 0, 5, 30)
                self.assertIn(6, [h['segment_id'] for h in site_hits])
                self.assertEqual(site_coverage['total_recordings'], 4)
                with connect(root) as db: db.execute('UPDATE datasets SET site_id=2 WHERE id=2')
                # Reference registration of the same path must still exclude its overlapping window.
                same = client.post('/references', json={'path': str(mono)}).json()
                hits, coverage = rank_segments(root, 1, [e0], 'model', same, 0, 5, 30)
                self.assertEqual([h['segment_id'] for h in hits], [4, 3, 5])
                self.assertAlmostEqual(hits[0]['similarity'], .8, places=5)
                self.assertAlmostEqual(hits[-1]['similarity'], -1)
                self.assertEqual(coverage, dict(total_recordings=3, searched_recordings=1, searched_segments=4, unsearched_recordings=2))
                hits, _ = rank_segments(root, 1, [e0, e1], 'model', same, 0, 5, 1)
                self.assertEqual(hits[0]['segment_id'], 3)  # Maximum, not mean across query windows.
                with self.assertRaisesRegex(CatalogError, 'no_search_embeddings'):
                    rank_segments(root, 1, [e0], 'missing', same, 0, 5, 30)
                with patch('search.load_perch', return_value=(Session(), 'model')):
                    result = client.post('/search', json=dict(dataset_id=1, source_kind='reference', source_id=same['id'], start_s=0, end_s=5))
                    self.assertEqual(result.status_code, 200, result.text)
                    self.assertEqual(result.json()['hits'][0]['segment_id'], 4)
                sf.write(mono, tone[:24000], 24000)
                with self.assertRaisesRegex(CatalogError, 'source_changed'):
                    rank_segments(root, 1, [e0], 'model', ref, 0, 5, 30)
                self.assertEqual(client.get('/sources/recording/1/spectrogram?start_s=0&end_s=1').json()['detail'], 'source_changed')
                sf.write(stereo, np.zeros(24000), 24000)
                self.assertEqual(client.post('/references', json={'path': str(stereo)}).json()['detail'], 'source_changed')
                sf.write(root / 'silence.wav', np.zeros(24000), 24000)
                silent = client.post('/references', json={'path': str(root / 'silence.wav')}).json()
                with self.assertRaisesRegex(CatalogError, 'silent_query'):
                    query_embeddings(root, silent, 0, 1)


if __name__ == '__main__':
    unittest.main()
