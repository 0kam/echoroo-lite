"""Offline engine selection, incremental inference and Kinglet result integration."""
import csv
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from analysis import digest
from models import REGISTRY
from server import create_app
from species import bundled_names
from storage import connect


def model_files(directory):
    names = ['Horornis diphone', 'Phoebastria albatrus']
    names += [name for name in bundled_names() if name not in names][:315]
    for engine in ('perch', 'kinglet-s', 'kinglet-xs'):
        model, labels = (directory / name for name in REGISTRY[engine]['files'])
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(engine.encode())
        labels.parent.mkdir(parents=True, exist_ok=True)
        with labels.open('w', newline='', encoding='utf-8') as stream:
            writer = csv.writer(stream)
            if engine == 'perch':
                writer.writerows([['label'], ['Horornis diphone'], ['Other']])
            else:
                writer.writerow(['index', 'ebird_code', 'scientific_name', 'japanese_name'])
                writer.writerows((i, str(i), name, '') for i, name in enumerate(names))
    return names


class FakeSession:
    def __init__(self, engine, calls):
        self.engine, self.calls = engine, calls

    def run(self, outputs, inputs):
        wave = next(iter(inputs.values()))
        self.calls.append((self.engine, outputs, wave.copy()))
        if self.engine == 'perch':
            return np.ones((len(wave), 1536), dtype=np.float32), np.tile([[-4., 4.]], (len(wave), 1))
        assert outputs == ['logits'] and set(inputs) == {'waveform'}
        assert wave.dtype == np.float32 and wave.shape[1] == 160000
        assert np.max(np.abs(wave)) <= 1
        logits = np.full((len(wave), 317), -8., dtype=np.float32)
        logits[:, :2] = 2 if self.engine == 'kinglet-s' else -4
        return [logits]


def wait_job(client):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get('/catalog').json()['jobs'][0]
        if job['status'] not in ('queued', 'running'):
            return job
        time.sleep(.01)
    raise AssertionError('Analysis timed out')


class ModelsTest(unittest.TestCase):
    def test_registry_selection_persists_and_keeps_perch(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / 'models'; model_files(directory)
            data = root / 'data'
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                models = client.get('/models').json()
                self.assertEqual([m['id'] for m in models], list(REGISTRY))
                self.assertEqual([m['id'] for m in models if m['enabled']], ['perch', 'kinglet-s', 'kinglet-xs'])
                self.assertEqual(set(models[0]), {'id','name','window_s','available','enabled','required','experimental','species_count','license'})
                for enabled in (['unknown'], ['birdnet']):
                    response = client.put('/models', json={'enabled': enabled})
                    self.assertEqual((response.status_code, response.json()), (400, {'detail': 'invalid_input'}))
                    self.assertEqual(client.get('/models').json(), models)
                response = client.put('/models', json={'enabled': ['kinglet-s', 'kinglet-s']})
                self.assertEqual([m['id'] for m in response.json() if m['enabled']], ['perch', 'kinglet-s'])
                self.assertEqual(client.get('/health').json()['models'], ['perch', 'kinglet-s'])
                preflight = client.options('/models', headers={'Origin':'http://localhost:1420', 'Access-Control-Request-Method':'PUT'})
                self.assertEqual(preflight.status_code, 200)
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                self.assertEqual([m['id'] for m in client.get('/models').json() if m['enabled']], ['perch', 'kinglet-s'])
                self.assertEqual([m['id'] for m in client.put('/models', json={'enabled': []}).json() if m['enabled']], ['perch'])
            (data / 'models.json').unlink()
            for relative in REGISTRY['birdnet']['files']:
                path = directory / relative; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                self.assertTrue(all(m['enabled'] and m['available'] for m in client.get('/models').json()))
                self.assertTrue(client.get('/health').json()['birdnet'])

    def test_incremental_jobs_and_kinglet_reports_validation_and_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / 'models'; names = model_files(directory)
            audio = root / 'audio'; audio.mkdir()
            # Two recordings, two complete windows and a one-second padded tail each.
            for day in ('08', '09'):
                sf.write(audio / f'202408{day}_060000.wav', np.full(352000, .25), 32000)
            data = root / 'data'; calls = []; loads = []
            def load(directory, engine):
                loads.append(engine)
                return FakeSession(engine, calls), digest(directory / REGISTRY[engine]['files'][0])
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization': 'Bearer test'}) as client, \
                    patch('analysis.load_onnx', side_effect=load):
                client.post('/imports', json=dict(folder=str(audio), site_name='Site', dataset_name='Data',
                    timezone='Asia/Tokyo', latitude=35, longitude=138))
                target = client.get('/species?q=Horornis diphone').json()[0]
                albatross = client.get('/species?q=Phoebastria albatrus').json()[0]
                self.assertEqual(albatross['common_names'], {'ja':'アホウドリ', 'en':'Short-tailed Albatross'})
                client.put('/models', json={'enabled': []})
                self.assertEqual(client.post('/jobs', json={'dataset_id':1}).status_code, 200)
                self.assertEqual({k: wait_job(client)[k] for k in ('status','total','done')}, dict(status='complete',total=2,done=2))
                first = client.get('/datasets/1/overview').json()
                self.assertEqual((first['analysed_recordings'], first['unanalysed_recordings']), (2, 0))
                with connect(data) as db:
                    original = [tuple(r) for r in db.execute("SELECT id,metadata FROM analyses WHERE engine='perch'")]
                client.put('/models', json={'enabled':['kinglet-s']})
                missing = client.get('/datasets/1/overview').json()
                self.assertEqual((missing['analysed_recordings'],missing['unanalysed_recordings']), (0,2))
                client.post('/jobs', json={'dataset_id':1})
                job = wait_job(client)
                self.assertEqual((job['status'],job['total'],job['done']), ('complete',2,2))
                self.assertEqual(loads, ['perch','kinglet-s'])
                with connect(data) as db:
                    self.assertEqual([tuple(r) for r in db.execute("SELECT id,metadata FROM analyses WHERE engine='perch'")], original)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM segments').fetchone()[0],6)
                self.assertEqual(client.post('/jobs', json={'dataset_id':1}).json(), {'detail':'no_audio'})
                result = next(r for r in client.get('/recordings/1/results').json() if r['engine']=='kinglet-s')
                self.assertEqual(result['metadata']['species'], names)
                self.assertEqual(result['metadata']['classes_sha256'], digest(directory / REGISTRY['kinglet-s']['files'][1]))
                self.assertEqual(result['metadata']['score_type'], 'sigmoid_uncalibrated')
                self.assertTrue(all(c['species_id'] for c in result['candidates']))
                self.assertAlmostEqual(result['candidates'][0]['score'], 1/(1+np.exp(-2)), places=6)
                with np.load(result['scores_path'], allow_pickle=False) as saved:
                    self.assertEqual(set(saved.files), {'scores','labels'})
                    self.assertEqual(saved['scores'].shape, (3,317))
                kinglet_wave = next(wave for engine, _, wave in calls if engine=='kinglet-s')
                self.assertEqual(kinglet_wave.shape, (3,160000))
                self.assertTrue(np.all(kinglet_wave[-1,32000:]==0))
                detail = client.get(f"/datasets/1/species/{target['id']}").json()
                self.assertEqual(detail['selected'], 'kinglet-s')
                self.assertEqual(detail['report']['window_s'], 5)
                self.assertIsNone(detail['region'])
                self.assertFalse(detail['mixed_models'])
                presence = client.get(f"/datasets/1/species/{target['id']}/presence").json()
                self.assertTrue(any(m['engine']=='kinglet-s' for r in presence['rows'] for m in r['models']))
                dates = dict(start_date='2024-08-08',end_date='2024-08-09')
                body = dict(dataset_id=1,species_id=target['id'],**dates)
                report = client.post('/reports/detection', json={**body,'engine':'kinglet-s',
                    'model_sha256':result['metadata']['model_sha256'],'threshold':.1}).json()
                self.assertEqual((report['window_s'],report['summaries'][0]['eligible_windows']), (5,4))
                check = client.post('/validations', json={**body,'model':'kinglet-s'}).json()
                self.assertEqual((check['window_s'],check['population']), (5,4))
                self.assertEqual(check['question']['end_s']-check['question']['start_s'],5)
                overview = client.get('/datasets/1/overview').json()
                exported = client.post('/workflow-exports', json={**body,'revision':overview['revision'],
                    'species_ids':[target['id']],'folder':str(root),'locale':'en'} )
                self.assertEqual(exported.status_code,200,exported.text)
                html = Path(exported.json()['report_path']).read_text()
                self.assertIn('Kinglet-S (preview)',html)
                import zipfile
                with zipfile.ZipFile(exported.json()['path']) as archive:
                    manifest = json.loads(archive.read('manifest.json'))
                    snap = next(s for s in manifest['results'] if s['engine']=='kinglet-s')
                    self.assertEqual(len(archive.read(f"scores/{snap['figure_id']}.csv").decode('utf-8-sig').splitlines()),5)
                    self.assertIn('Kinglet-S (preview)',archive.read(f"figures/{snap['figure_id']}.svg").decode())
                client.put('/models', json={'enabled':['kinglet-s','kinglet-xs']})
                client.post('/jobs', json={'dataset_id':1})
                self.assertEqual(wait_job(client)['status'],'complete')
                self.assertEqual(loads,['perch','kinglet-s','kinglet-xs'])
                client.put('/models', json={'enabled':['kinglet-s']})
                self.assertEqual(client.get('/datasets/1/overview').json()['unanalysed_recordings'],0)
                # A replaced model must run again even when that engine has saved analyses.
                (directory / REGISTRY['kinglet-s']['files'][0]).write_bytes(b'updated model')
                client.post('/jobs', json={'dataset_id':1})
                self.assertEqual(wait_job(client)['total'],2)
                self.assertEqual(loads,['perch','kinglet-s','kinglet-xs','kinglet-s'])


if __name__ == '__main__':
    unittest.main()
