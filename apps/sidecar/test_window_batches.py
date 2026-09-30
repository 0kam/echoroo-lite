"""Batch caching must preserve each recording's labels, scores and empty windows."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from reports import classification_batches


class WindowBatchCheck(unittest.TestCase):
    def test_batches_match_individual_outputs_and_invalidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'scores.npz'; path.write_bytes(b'test')
            analyses = [dict(id=i, recording_id=i, scores_path=str(path), engine='birdnet') for i in range(257)]
            records = {a['id']: {'seconds': 30} for a in analyses}
            outputs = [(np.array(['B', 'A']), np.array([0, 15]), np.array([[.1, .8], [.9, .2]]), np.array([[3, 6], [18, 21]])),
                       (np.array(['C']), np.array([], dtype=int), np.empty((0, 1)), np.empty((0, 1)))]
            def read(a, *_):
                return outputs[a['id'] % 2]
            with patch('reports.candidate_list', return_value=['B', 'A', 'C']), patch('reports.classification_windows', side_effect=read) as loader:
                for _ in range(2):
                    for a, allowed, arrays in classification_batches(root, None, analyses, records):
                        for actual, expected in zip(arrays, read(a)):
                            np.testing.assert_array_equal(actual, expected)
                self.assertEqual(loader.call_count, 257)
                path.write_bytes(b'changed')
                list(classification_batches(root, None, analyses, records))
                self.assertEqual(loader.call_count, 514)

class RevisionCheck(unittest.TestCase):
    def test_cached_context_tracks_edits_and_checks_sources(self):
        from results_workflow import build_fingerprint, context, fingerprint
        from storage import CatalogError, connect, initialize
        from species import initialize_species
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); initialize(root); initialize_species(root)
            path = root / 'recording.wav'; path.write_bytes(b'test'); stat = path.stat()
            with connect(root) as db:
                db.execute("INSERT INTO sites VALUES(1,'Site','Asia/Tokyo',35,138)")
                db.execute("INSERT INTO datasets VALUES(1,1,'Audio',?)", (temp,))
                db.execute("INSERT INTO recordings VALUES(1,1,?,'20240808_000000','Asia/Tokyo','2024-08-07T15:00:00+00:00',1,32000,?,?)", (str(path), stat.st_size, stat.st_mtime_ns))
            with patch('results_workflow.build_fingerprint', wraps=build_fingerprint) as build:
                before = fingerprint(root, 1)
                self.assertEqual(fingerprint(root, 1), before)
                self.assertEqual(build.call_count, 1)
                self.assertEqual(context(root, 1)['site']['name'], 'Site')
                with connect(root) as db:
                    db.execute("UPDATE sites SET name='Renamed' WHERE id=1")
                self.assertEqual(context(root, 1)['site']['name'], 'Renamed')
                with connect(root) as db:
                    db.execute('UPDATE sites SET latitude=36 WHERE id=1')
                self.assertNotEqual(fingerprint(root, 1), before)
                path.write_bytes(b'changed audio')
                with self.assertRaises(CatalogError):
                    fingerprint(root, 1)


if __name__ == '__main__':
    unittest.main()
