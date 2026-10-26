"""Offline checks for the investigation script's timing and equivalence accounting."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

spec=importlib.util.spec_from_file_location('bench_analysis',Path(__file__).resolve().parents[2]/'scripts/bench_analysis.py')
bench=importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)


class BenchmarkTest(unittest.TestCase):
    def test_snapshot_covers_sparse_birdnet_top_one(self):
        import soundfile as sf
        from analysis import save
        from species import initialize_species
        from storage import initialize,connect,inspect_audio
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); initialize(root); initialize_species(root)
            path=root/'20240808_060000.wav'; sf.write(path,np.zeros(48000*6),48000)
            with connect(root) as db:
                db.execute("INSERT INTO sites VALUES(1,'Test','Asia/Tokyo',35,138)")
                db.execute("INSERT INTO datasets VALUES(1,1,'Test',?)",(str(root),))
                db.execute('''INSERT INTO recordings(dataset_id,path,original_time,timezone,recorded_at,
                    seconds,sample_rate,size,mtime_ns) VALUES(1,?,?,?,?,?,?,?,?)''',inspect_audio(path,'Asia/Tokyo'))
                record=dict(db.execute('SELECT * FROM recordings').fetchone())
                job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'complete',1)").lastrowid
            names=['Horornis diphone_Japanese Bush Warbler','Parus minor_Japanese Tit']
            save(root,job,record,'birdnet',{'species':names},dict(labels=np.array(names*2),
                start_s=np.array([0,0,3,3]),end_s=np.array([3,3,6,6]),scores=np.array([.2,.4,.6,.1])),[])
            target=root/'snapshot.npz'; bench.snapshot(root,target)
            with np.load(target,allow_pickle=False) as saved:
                np.testing.assert_array_equal(saved['birdnet.top1'],[names[1],names[0]])
                self.assertEqual(saved['birdnet.detections'].shape,(2,2))

    def test_exclusive_stage_times(self):
        stages=bench.Stages()
        with patch.object(bench,'perf_counter',side_effect=[0.,1.,3.,5.]):
            with stages.time('outer'):
                with stages.time('inner'):
                    pass
        self.assertEqual(dict(stages.seconds),dict(inner=2.,outer=3.))
        self.assertEqual(dict(stages.calls),dict(inner=1,outer=1))

    def test_comparison_distinguishes_numeric_and_detection_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            first,second=Path(folder)/'first.npz',Path(folder)/'second.npz'
            arrays={'perch.scores':np.array([[.09,.2]]),'perch.embeddings':np.ones((1,1536),dtype=np.float32),
                    'perch.top1':np.array([1]),'perch.detections':np.array([[False,True]])}
            np.savez(first,**arrays); np.savez(second,**arrays)
            self.assertTrue(bench.compare(first,second)['identical'])
            arrays['perch.scores'][0,0]+=.02
            arrays['perch.embeddings'][0,0]+=.001
            arrays['perch.detections'][0,0]=True
            arrays['perch.top1'][0]=0
            np.savez(second,**arrays)
            result=bench.compare(first,second)
            self.assertFalse(result['identical'])
            self.assertAlmostEqual(result['max_abs_scores'],.02)
            self.assertAlmostEqual(result['max_abs_embeddings'],.001,places=6)
            self.assertEqual((result['top1_changes'],result['detection_changes']),(1,1))

    def test_bulk_insert_preserves_values_and_transaction_rollback(self):
        for variant in ('current','sqlite-bulk'):
            with self.subTest(variant=variant):
                connection=sqlite3.connect(':memory:')
                db=bench.Database(connection,bench.Stages(),variant)
                db.execute('CREATE TABLE vectors(rowid INTEGER PRIMARY KEY,embedding BLOB)')
                rows=[(i,np.full(1536,i,dtype='<f4').tobytes()) for i in range(1,521)]
                with db:
                    db.executemany('INSERT INTO vectors(rowid,embedding) VALUES(?,?)',rows)
                self.assertEqual(db.execute('SELECT * FROM vectors ORDER BY rowid').fetchall(),rows)
                with self.assertRaises(RuntimeError):
                    with db:
                        db.execute('DELETE FROM vectors')
                        raise RuntimeError('rollback')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0],520)
                db.close()
