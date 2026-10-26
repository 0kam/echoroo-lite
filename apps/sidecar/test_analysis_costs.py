"""Shared audio, unchanged persistence, missing-pair estimates and startup warm-up."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

import analysis
from models import REGISTRY
from server import create_app
from storage import connect
from test_models import FakeSession, fake_birdnet, model_files, v3_files, wait_job


class WaveSession(FakeSession):
    def run(self, outputs, inputs):
        values = super().run(outputs, inputs)
        means = next(iter(inputs.values())).mean(axis=1)
        for name, value in zip(outputs, values):
            value[:, 0] += means
        return values


def saved_results(root):
    result = {}
    with connect(root) as db:
        for row in db.execute('SELECT * FROM analyses ORDER BY id'):
            with np.load(row['scores_path'], allow_pickle=False) as scores:
                arrays = {key:(scores[key].shape, scores[key].dtype.str, scores[key].tobytes()) for key in scores.files}
            candidates = [tuple(r) for r in db.execute('''SELECT start_s,end_s,label,score FROM candidates
                WHERE analysis_id=? ORDER BY start_s,label''', (row['id'],))]
            vectors = [tuple(r) for r in db.execute('''SELECT s.start_s,s.end_s,v.embedding FROM segments s
                JOIN vectors v ON v.rowid=s.id WHERE s.analysis_id=? ORDER BY s.start_s''', (row['id'],))]
            result[row['recording_id'],row['engine']] = (json.loads(row['metadata']),arrays,candidates,vectors)
    return result


class AnalysisCostsTest(unittest.TestCase):
    def test_shared_decode_matches_separate_engine_jobs_and_pending_pairs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root/'models'; model_files(directory); v3_files(directory)
            for relative in REGISTRY['birdnet']['files']:
                path = directory/relative; path.parent.mkdir(parents=True,exist_ok=True); path.touch()
            audio = root/'audio'; audio.mkdir(); rng = np.random.default_rng(123)
            for day, rate, seconds in ((8,48000,10.125),(9,44100,7.75)):
                sf.write(audio/f'202408{day:02}_060000.wav', rng.normal(0,.1,round(rate*seconds)), rate, subtype='PCM_16')
            def load(directory, engine):
                return WaveSession(engine,None), analysis.digest(directory/REGISTRY[engine]['files'][0])
            body = dict(folder=str(audio),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138)
            selection = dict(main='perch',extra=[engine for engine in REGISTRY if engine!='perch'])
            data = root/'shared'
            bird_calls = []
            with fake_birdnet(directory,bird_calls), patch('analysis.load_onnx',side_effect=load), \
                    TestClient(create_app(data,directory,'test'),headers={'Authorization':'Bearer test'}) as client:
                self.assertEqual(client.post('/imports',json=body).status_code,200)
                self.assertEqual(client.put('/models',json=selection).status_code,200)
                expected = [dict(engine=engine,recordings=2,seconds=17.875) for engine in REGISTRY]
                self.assertEqual(client.get('/datasets/1/overview').json()['pending_analysis'],expected)
                with patch('analysis.sf.read',wraps=sf.read) as read, \
                        patch('analysis.resample',wraps=analysis.resample) as resample:
                    self.assertEqual(client.post('/jobs',json={'dataset_id':1}).status_code,200)
                    self.assertEqual(wait_job(client)['status'],'complete')
                    self.assertEqual((read.call_count,resample.call_count),(2,2))
                    self.assertEqual(bird_calls,[('<array>',486000,48000),('<array>',341775,44100)])
                self.assertEqual(client.get('/datasets/1/overview').json()['pending_analysis'],[])
                shared = saved_results(data)
                # File changes without a catalog revision must still invalidate the estimate.
                classes = directory/REGISTRY['kinglet-s']['files'][1]
                with classes.open('a') as stream:
                    stream.write('\n')
                self.assertEqual(client.get('/datasets/1/overview').json()['pending_analysis'],
                                 [dict(engine='kinglet-s',recordings=2,seconds=17.875)])
                classes.write_bytes(classes.read_bytes()[:-1])
                with connect(data) as db:
                    row = db.execute("SELECT scores_path FROM analyses WHERE recording_id=1 AND engine='perch'").fetchone()
                    Path(row[0]).unlink()
                    db.execute('UPDATE sites SET latitude=36 WHERE id=1')
                    db.execute('INSERT INTO changed_locations VALUES(1)')
                expected = [dict(engine='perch',recordings=1,seconds=10.125),
                            dict(engine='birdnet',recordings=2,seconds=17.875)]
                self.assertEqual(client.get('/datasets/1/overview').json()['pending_analysis'],expected)
                with connect(data) as db:
                    records = [dict(r) for r in db.execute('SELECT * FROM recordings')]
                    site = dict(db.execute('SELECT * FROM sites').fetchone())
                pairs = analysis.plan(data,directory,records,site)
                self.assertEqual(pairs,{1:['perch','birdnet'],2:['birdnet']})
                client.put('/models',json={'main':'kinglet-xs','extra':['kinglet-s']})
                self.assertEqual(client.get('/datasets/1/overview').json()['pending_analysis'],[])
                # A selected comparison with missing assets is excluded.
                (directory/REGISTRY['kinglet-s']['files'][0]).unlink()
                self.assertEqual(client.get('/datasets/1/overview').json()['pending_analysis'],[])
                (directory/REGISTRY['kinglet-s']['files'][0]).write_bytes(b'kinglet-s')
                client.put('/models',json={'main':'kinglet-xs','extra':['birdnet']})
                with patch('analysis.sf.read',side_effect=RuntimeError('Decode failed')):
                    self.assertEqual(client.post('/jobs',json={'dataset_id':1}).status_code,200)
                    failed = wait_job(client)
                self.assertEqual((failed['status'],failed['error']),('failed','unreadable_audio'))

            reference = root/'separate'
            with fake_birdnet(directory,[]), patch('analysis.load_onnx',side_effect=load), \
                    TestClient(create_app(reference,directory,'test'),headers={'Authorization':'Bearer test'}) as client:
                self.assertEqual(client.post('/imports',json=body).status_code,200)
                client.put('/models',json=selection)
                with connect(reference) as db:
                    records = [dict(r) for r in db.execute('SELECT * FROM recordings')]
                    site = dict(db.execute('SELECT * FROM sites').fetchone())
                for engine in REGISTRY:
                    with connect(reference) as db:
                        job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'queued',2)").lastrowid
                    analysis.run(reference,directory,job,records,site,threading.Event(),{r['id']:[engine] for r in records})
                    with connect(reference) as db:
                        self.assertEqual(db.execute('SELECT status FROM jobs WHERE id=?',(job,)).fetchone()[0],'complete')
                self.assertEqual(saved_results(reference),shared)

    def test_birdnet_array_input_matches_original_file_windows(self):
        # Isolate BirdNET's import-time app-data global from other tests' fake packages.
        code = '''
import tempfile
from pathlib import Path
import numpy as np
import soundfile as sf
from birdnet.acoustic.inference.core.producer import get_file_segments_with_overlap, get_data_segments_with_overlap
with tempfile.TemporaryDirectory() as temp:
    rng = np.random.default_rng(321)
    for rate in (32000,44100,48000,96000):
        path = Path(temp)/'record.wav'
        sf.write(path,rng.normal(0,.1,rate*7+123),rate,subtype='PCM_16')
        native,_ = sf.read(path,dtype='float32')
        options = dict(segment_duration_s=3,overlap_duration_s=0,speed=1,target_sample_rate=48000)
        original = list(get_file_segments_with_overlap(path,len(native),rate,**options))
        shared = list(get_data_segments_with_overlap(native,rate,**options))
        assert len(original)==len(shared)
        for first,second in zip(original,shared):
            np.testing.assert_array_equal(first,second)
'''
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run([sys.executable,'-c',code],capture_output=True,text=True,timeout=30,
                env={**os.environ,'BIRDNET_APP_DATA':temp})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_startup_warms_imports_once_without_waiting(self):
        entered, release = threading.Event(), threading.Event()
        def warming():
            entered.set()
            release.wait(5)
        with tempfile.TemporaryDirectory() as temp, patch('analysis._warmup_thread',None), \
                patch('analysis.warm_imports',side_effect=warming) as warm:
            try:
                with TestClient(create_app(Path(temp)/'data',Path(temp)/'models','test'),
                        headers={'Authorization':'Bearer test'}) as client:
                    self.assertTrue(entered.wait(2))
                    self.assertEqual(client.get('/health').status_code,200)
                    thread = analysis.start_warmup()
                    self.assertTrue(thread.is_alive())
                    warm.assert_called_once()
            finally:
                release.set()
                if analysis._warmup_thread is not None:
                    analysis._warmup_thread.join(2)
