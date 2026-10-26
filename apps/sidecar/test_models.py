"""Offline engine selection, incremental inference and Kinglet result integration."""
import builtins
import csv
import os
import sys
import json
import tempfile
import time
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from analysis import digest
from models import REGISTRY, settings, machine_fingerprint
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


def v3_files(directory):
    names = ['Horornis diphone', 'Abeillia abeillei'] + [f'Outside{i}' for i in range(11558)]
    for relative in REGISTRY['birdnet-v3']['files']:
        path = directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'v3')
        if path.suffix == '.csv':
            with path.open('w', newline='', encoding='utf-8-sig') as stream:
                writer = csv.writer(stream, delimiter=';')
                writer.writerow(['idx','id','sci_name','com_name','class','order'])
                writer.writerows((i, i, name, '', 'Aves', '') for i, name in enumerate(names))


class FakeSession:
    def __init__(self, engine, calls):
        self.engine, self.calls = engine, calls

    def run(self, outputs, inputs):
        wave = next(iter(inputs.values()))
        if self.calls is not None:
            self.calls.append((self.engine, outputs, wave.copy()))
        if self.engine == 'perch':
            return [np.ones((len(wave), 1536), dtype=np.float32) if name == 'embedding' else np.tile([[-4., 4.]], (len(wave), 1)) for name in outputs]
        if self.engine == 'birdnet-v3':
            assert outputs == ['predictions'] and set(inputs) == {'input'}
            scores = np.full((len(wave), 11560), .01, dtype=np.float32)
            scores[:, :2] = .8
            return [scores]
        assert outputs == ['logits', 'embedding'] or outputs == ['embedding'] and set(inputs) == {'waveform'}
        assert wave.dtype == np.float32 and wave.shape[1] == 160000
        assert np.max(np.abs(wave)) <= 1
        logits = np.full((len(wave), 317), -8., dtype=np.float32)
        logits[:, :2] = 2 if self.engine == 'kinglet-s' else -4
        return [np.ones((len(wave),1536), dtype=np.float32) if name == 'embedding' else logits for name in outputs]


@contextmanager
def fake_birdnet(directory, calls):
    """Resolve the fake package's app-data path on its first import, like BirdNET."""
    packages = ['birdnet', 'birdnet.acoustic', 'birdnet.acoustic.models', 'birdnet.acoustic.models.v2_4',
                'birdnet.acoustic.models.v2_4.tf', 'birdnet.geo', 'birdnet.geo.models',
                'birdnet.geo.models.v2_4', 'birdnet.geo.models.v2_4.tf']
    modules = {name: ModuleType(name) for name in packages}
    for name, module in modules.items():
        module.__path__ = []
        if '.' in name:
            parent, _, child = name.rpartition('.')
            setattr(modules[parent], child, module)
    state = {'loads': []}
    original_import = builtins.__import__
    label = 'Horornis diphone_Japanese Bush Warbler'

    def importing(name, *args, **kwargs):
        if name == 'birdnet' or name.startswith('birdnet.'):
            state.setdefault('app_data', Path(os.environ.get('BIRDNET_APP_DATA', '/missing')))
        return original_import(name, *args, **kwargs)

    def exists(relative):
        return (state['app_data'] / relative).is_file()

    class Session:
        def result(self, samples, rate):
            starts = np.arange(0, len(samples)/rate, 3)
            rows = np.array([(start, start+3, label, .8) for start in starts],
                dtype=[('start_time','f8'),('end_time','f8'),('species_name','U128'),('confidence','f4')])
            return SimpleNamespace(unprocessable_inputs=[], to_structured_array=lambda: rows)

        def run(self, path):
            samples, rate = sf.read(path, dtype='float32')
            return self.result(samples, rate)

        def run_arrays(self, value):
            samples, rate = value
            calls.append(('<array>', len(samples), rate))
            return self.result(samples, rate)

    @contextmanager
    def predict_session(**kwargs):
        yield Session()

    def load(kind, *args, **kwargs):
        state['loads'].append(kind)
        relative = 'acoustic-models/v2.4/tf/model-fp32.tflite' if kind == 'acoustic' else 'geo-models/v2.4/tf/model-fp32.tflite'
        assert exists(relative), state
        model = SimpleNamespace(model_path=state['app_data']/relative)
        if kind == 'acoustic':
            model.predict_session = predict_session
        else:
            model.predict = lambda *a, **kw: SimpleNamespace(to_structured_array=lambda:
                np.array([(label,)], dtype=[('species_name','U128')]))
        return model

    modules['birdnet'].load = load
    modules['birdnet.acoustic.models.v2_4.tf'].AcousticTFDownloaderV2_4 = SimpleNamespace(
        _check_acoustic_model_available=lambda precision: exists('acoustic-models/v2.4/tf/model-fp32.tflite'))
    modules['birdnet.geo.models.v2_4.tf'].GeoTFDownloaderV2_4 = SimpleNamespace(
        _check_geo_model_available=lambda: exists('geo-models/v2.4/tf/model-fp32.tflite'))
    missing = object()
    previous = {name:sys.modules.get(name, missing) for name in modules}
    sys.modules.update(modules)
    try:
        with patch('builtins.__import__', side_effect=importing):
            yield state
    finally:
        for name, module in previous.items():
            if module is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def wait_job(client):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = client.get('/catalog').json()['jobs'][0]
        if job['status'] not in ('queued', 'running'):
            return job
        time.sleep(.01)
    raise AssertionError('Analysis timed out')


class ModelsTest(unittest.TestCase):
    def test_registry_selection_migration_defaults_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / 'models'; model_files(directory)
            data = root / 'data'
            def chosen(client, main='perch', extra=None):
                return client.put('/models', json={'main': main, 'extra': extra or []})
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                settings = client.get('/models').json()
                self.assertEqual({k: settings[k] for k in ('main','extra','confirmed')},
                                 dict(main='perch', extra=[], confirmed=False))
                models = settings['models']
                self.assertEqual([m['id'] for m in models], list(REGISTRY))
                self.assertEqual([m['id'] for m in models if m['enabled']], ['perch'])
                self.assertEqual(set(models[0]), {'id','name','window_s','available','enabled','main_capable',
                                               'experimental','species_count','license','seconds_per_hour','seconds_per_file'})
                for main, extra in [('unknown', []), ('birdnet', []), ('birdnet-v3', []),
                                    ('perch', ['unknown']), ('perch', ['birdnet']), ('perch', ['perch'])]:
                    response = chosen(client, main, extra)
                    self.assertEqual((response.status_code, response.json()), (400, {'detail': 'invalid_input'}))
                    self.assertEqual(client.get('/models').json(), settings)
                self.assertEqual(client.put('/models', json={'enabled': []}).status_code, 422)
                response = chosen(client, 'kinglet-s', ['perch','perch'])
                self.assertEqual(response.json()['extra'], ['perch'])
                self.assertTrue(response.json()['confirmed'])
                self.assertEqual(client.get('/health').json()['models'], ['perch', 'kinglet-s'])
                self.assertEqual(client.options('/models', headers={'Origin':'http://localhost:1420',
                    'Access-Control-Request-Method':'PUT'}).status_code, 200)
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                self.assertEqual(client.get('/models').json()['main'], 'kinglet-s')
                self.assertEqual(chosen(client).json()['extra'], [])
            (data / 'models.json').write_text(json.dumps({'enabled': ['perch', 'kinglet-xs']}))
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                migrated = client.get('/models').json()
                self.assertEqual((migrated['main'], migrated['extra'], migrated['confirmed']), ('perch', ['kinglet-xs'], True))
                self.assertNotIn('enabled', json.loads((data/'models.json').read_text()))
            (directory / REGISTRY['perch']['files'][0]).unlink()
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                self.assertEqual(chosen(client).json(), {'detail':'invalid_input'})
                self.assertEqual(chosen(client, 'kinglet-xs').status_code, 200)

    def test_main_kinglet_analysis_without_perch_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); directory=root/'models'; model_files(directory)
            for relative in REGISTRY['perch']['files']:
                (directory/relative).unlink()
            audio=root/'audio';audio.mkdir()
            sf.write(audio/'20240808_060000.wav',np.full(160000,.25),32000)
            data=root/'data'
            with TestClient(create_app(data,directory,'test'),headers={'Authorization':'Bearer test'}) as client, \
                    patch('analysis.load_onnx',return_value=(FakeSession('kinglet-xs',[]),digest(directory/REGISTRY['kinglet-xs']['files'][0]))):
                client.post('/imports',json=dict(folder=str(audio),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                self.assertEqual(client.put('/models',json={'main':'kinglet-xs','extra':[]}).status_code,200)
                self.assertEqual(client.post('/jobs',json={'dataset_id':1}).status_code,200)
                self.assertEqual(wait_job(client)['status'],'complete')
                self.assertEqual([r['engine'] for r in client.get('/recordings/1/results').json()],['kinglet-xs'])
                self.assertEqual(client.get('/datasets/1/overview').json()['unanalysed_recordings'],0)
                # Legacy Kinglet scores without vectors are offered for analysis again.
                with connect(data) as db:
                    db.execute('DELETE FROM vectors')
                    db.execute('DELETE FROM segments')
                overview=client.get('/datasets/1/overview').json()
                self.assertEqual(overview['unanalysed_recordings'],1)
                self.assertEqual(overview['pending_analysis'],[dict(engine='kinglet-xs',recordings=1,seconds=5.)])
                self.assertEqual(client.post('/jobs',json={'dataset_id':1}).status_code,200)
                self.assertEqual(wait_job(client)['status'],'complete')

    def test_benchmark_all_available_models_and_machine_invalidation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root/'models'; model_files(directory); v3_files(directory)
            for relative in REGISTRY['birdnet']['files']:
                path = directory/relative; path.parent.mkdir(parents=True, exist_ok=True); path.touch()
            loads, measured_paths, measurements = [], [], []
            def load(directory, engine):
                loads.append(engine)
                return FakeSession(engine, None), digest(directory/REGISTRY[engine]['files'][0])
            def job(data, paths, models, engine, loaded):
                self.assertEqual(set(loaded), {engine})
                self.assertNotEqual(data, root/'data')
                measured_paths.extend(paths)
                seconds = sum(sf.info(path).duration for path in paths)
                measurements.append((engine, len(paths), seconds))
                return len(paths), seconds, .4 + len(paths)*.3 + seconds/3600*18
            data = root/'data'
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization':'Bearer test'}) as client, \
                    patch('analysis.load_onnx', side_effect=load), fake_birdnet(directory, []), \
                    patch('model_benchmark.benchmark_job', side_effect=job):
                response = client.post('/models/benchmark')
                self.assertEqual(response.status_code,200,response.text)
                result = response.json()
                self.assertFalse(result['confirmed'])
                self.assertEqual(result['extra'], [])
                self.assertEqual(result['seconds_per_job'], .4)
                self.assertTrue(all(type(m['seconds_per_hour']) is int and m['seconds_per_hour'] == 18
                    and m['seconds_per_file'] == .3 for m in result['models']))
                self.assertEqual(loads, ['perch','birdnet-v3','kinglet-s','kinglet-xs'])
                expected = [(1,640), *[(1,160)]*3, *[(1,640)]*3, *[(4,640)]*3]
                self.assertEqual(measurements, [(engine,files,seconds) for engine in REGISTRY for files,seconds in expected])
                self.assertTrue(all(not path.exists() for path in measured_paths))
                with connect(data) as db:
                    self.assertTrue(all(db.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0] == 0
                        for table in ('sites','datasets','recordings','jobs','analyses','segments','vectors')))
                saved = settings(data)['benchmark']
                self.assertEqual(saved['models'], {engine:dict(seconds_per_hour=18,seconds_per_file=.3) for engine in REGISTRY})
                self.assertEqual(client.get('/models').json(),result)
                self.assertEqual(saved['machine'],machine_fingerprint())
                self.assertTrue(saved['timestamp'])
                with patch('models.machine_fingerprint', return_value={'machine':'other','cpu_count':1}):
                    cleared=client.get('/models').json()
                    self.assertTrue(all(m['seconds_per_hour'] is None and m['seconds_per_file'] is None for m in cleared['models']))
                    self.assertIsNone(cleared['seconds_per_job'])
                    self.assertEqual(settings(data)['benchmark'],{})

    def test_benchmark_fit_separates_audio_file_and_job_costs(self):
        from model_benchmark import fit_costs
        samples=[(files,seconds,.4+files*.3+18*seconds/3600)
                 for files,seconds in ((1,160),(1,640),(4,640))]
        self.assertEqual(fit_costs(samples),dict(seconds_per_job=.4,seconds_per_hour=18,seconds_per_file=.3))
        for samples in ([(1,160,.5),(1,640,.4),(4,640,.6)],[(1,160,.1),(1,640,2.),(4,640,.5)]):
            result=fit_costs(samples)
            self.assertGreaterEqual(result['seconds_per_job'],0)
            self.assertGreaterEqual(result['seconds_per_hour'],0)
            self.assertGreaterEqual(result['seconds_per_file'],0)

    def test_benchmark_then_birdnet_job_preserves_import_time_model_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); directory=root/'models'; model_files(directory)
            for relative in REGISTRY['birdnet']['files']:
                path=directory/relative; path.parent.mkdir(parents=True,exist_ok=True); path.touch()
            for engine in ('kinglet-s','kinglet-xs'):
                for relative in REGISTRY[engine]['files']:(directory/relative).unlink()
            audio=root/'audio'; audio.mkdir()
            sf.write(audio/'20240808_060000.wav',np.full(320000,.25),32000)
            data=root/'data'; calls=[]
            def load(directory,engine):
                return FakeSession(engine,None),digest(directory/REGISTRY[engine]['files'][0])
            with patch.dict(os.environ, {'BIRDNET_APP_DATA':str(root/'wrong-cache')}), \
                    fake_birdnet(directory,calls) as bird, patch('analysis.load_onnx',side_effect=load), \
                    TestClient(create_app(data,directory,'test'),headers={'Authorization':'Bearer test'}) as client:
                imported=client.post('/imports',json=dict(folder=str(audio),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                self.assertEqual(imported.status_code,200,imported.text)
                response=client.post('/models/benchmark')
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(bird['app_data'],(directory/'birdnet').resolve())
                self.assertEqual(bird['loads'],['acoustic','geo'])
                with connect(data) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM recordings').fetchone()[0],1)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0],0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0],0)
                self.assertEqual(client.put('/models',json={'main':'perch','extra':['birdnet']}).status_code,200)
                self.assertEqual(client.post('/jobs',json={'dataset_id':1}).status_code,200)
                job=wait_job(client)
                self.assertEqual((job['status'],job['error']),('complete',None))
                self.assertEqual({r['engine'] for r in client.get('/recordings/1/results').json()},{'perch','birdnet'})
                self.assertEqual(bird['loads'],['acoustic','geo','acoustic','geo'])

    def test_old_benchmarks_are_dropped_without_changing_selection(self):
        for old in ({'minutes_per_hour':{'perch':.3}}, {'seconds_per_hour':{'perch':67}},
                    *[{'method':method,'models':{'perch':{'seconds_per_hour':15,'seconds_per_file':.1}},'seconds_per_job':.2}
                      for method in ('marginal_v1','marginal_v2')]):
            with self.subTest(old=old), tempfile.TemporaryDirectory() as temp:
                root=Path(temp); directory=root/'models'; model_files(directory); data=root/'data'; data.mkdir()
                (data/'models.json').write_text(json.dumps(dict(main='kinglet-s',extra=['perch'],confirmed=True,
                    benchmark=dict(machine=machine_fingerprint(),timestamp='2026-10-26',**old))))
                with TestClient(create_app(data,directory,'test'),headers={'Authorization':'Bearer test'}) as client:
                    result=client.get('/models').json()
                    self.assertEqual((result['main'],result['extra'],result['confirmed']),('kinglet-s',['perch'],True))
                    self.assertTrue(all(m['seconds_per_hour'] is None and m['seconds_per_file'] is None for m in result['models']))
                    self.assertIsNone(result['seconds_per_job'])
                    self.assertEqual(settings(data)['benchmark'],{})

    def test_benchmark_rejects_a_running_job(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); directory=root/'models'; model_files(directory)
            audio=root/'audio'; audio.mkdir()
            sf.write(audio/'20240808_060000.wav', np.full(160000,.1), 32000)
            entered, release = threading.Event(), threading.Event()
            def run(*args):
                entered.set(); release.wait(10)
            with TestClient(create_app(root/'data', directory, 'test'), headers={'Authorization':'Bearer test'}) as client, \
                    patch('analysis.run', side_effect=run):
                client.post('/imports',json=dict(folder=str(audio),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                client.post('/jobs',json={'dataset_id':1})
                self.assertTrue(entered.wait(5))
                try:
                    response=client.post('/models/benchmark')
                    self.assertEqual((response.status_code,response.json()),(409,{'detail':'job_busy'}))
                finally:
                    release.set()

    def test_v3_probabilities_japan_filter_and_hidden_results(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); directory=root/'models'; model_files(directory); v3_files(directory)
            audio=root/'audio'; audio.mkdir()
            for day in ('08','09'):
                sf.write(audio/f'202408{day}_060000.wav',np.full(352000,.25),32000)
            calls=[]
            def load(directory, engine):
                return FakeSession(engine,calls), digest(directory/REGISTRY[engine]['files'][0])
            data=root/'data'
            with TestClient(create_app(data,directory,'test'),headers={'Authorization':'Bearer test'}) as client, \
                    patch('analysis.load_onnx',side_effect=load):
                client.post('/imports',json=dict(folder=str(audio),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                client.put('/models',json={'main':'perch','extra':['birdnet-v3']})
                client.post('/jobs',json={'dataset_id':1})
                self.assertEqual(wait_job(client)['status'],'complete')
                results=client.get('/recordings/1/results').json()
                v3=next(r for r in results if r['engine']=='birdnet-v3')
                self.assertEqual(v3['metadata']['species'],['Horornis diphone'])
                self.assertEqual(v3['metadata']['score_type'],'probability_uncalibrated')
                self.assertAlmostEqual(v3['candidates'][0]['score'],.8)
                self.assertTrue(all(c['label']=='Horornis diphone' for c in v3['candidates']))
                with connect(data) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM segments s JOIN analyses a ON a.id=s.analysis_id WHERE a.engine='birdnet-v3'").fetchone()[0],0)
                target=client.get('/species?q=Horornis diphone').json()[0]
                body=dict(dataset_id=1,species_id=target['id'],start_date='2024-08-08',end_date='2024-08-09')
                report=client.post('/reports/detection',json={**body,'engine':'birdnet-v3','model_sha256':v3['metadata']['model_sha256'],'threshold':.1})
                self.assertEqual(report.status_code,200,report.text)
                report=report.json()
                self.assertEqual(report['summaries'][0]['eligible_windows'],4)
                check=client.post('/validations',json={**body,'model':'birdnet-v3'}).json()
                question=check['question']
                client.post(f"/validations/{check['id']}/answers",json={'item_id':question['id'],'verdict':'target'})
                self.assertEqual(client.get(f"/datasets/1/species/{target['id']}").json()['presence']['status'],'confirmed')
                visible=client.get('/datasets/1/overview').json()
                exported=client.post('/workflow-exports',json={**body,'revision':visible['revision'],'species_ids':[target['id']],'folder':str(root),'locale':'en'})
                self.assertEqual(exported.status_code,200,exported.text)
                self.assertIn('BirdNET+ V3.0 (preview)',Path(exported.json()['report_path']).read_text())
                with connect(data) as db:
                    count=db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0]
                client.put('/models',json={'main':'perch','extra':[]})
                hidden=client.get('/datasets/1/overview').json()
                self.assertNotEqual(hidden['revision'],visible['revision'])
                self.assertEqual({m['engine'] for m in hidden['models']},{'perch'})
                self.assertEqual([r['engine'] for r in client.get('/recordings/1/results').json()],['perch'])
                detail=client.get(f"/datasets/1/species/{target['id']}").json()
                self.assertEqual({c['engine'] for c in detail['checks']},{'perch'})
                self.assertEqual(detail['presence']['status'],'unchecked')
                self.assertEqual(client.get(f"/validations/{check['id']}").json(),{'detail':'not_found'})
                self.assertEqual(client.post(f"/validations/{check['id']}/answers",json={'item_id':question['id'],'verdict':'other'}).json(),{'detail':'not_found'})
                with connect(data) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM window_answers').fetchone()[0],1)
                self.assertEqual(client.post('/reports/detection',json={**body,'engine':'birdnet-v3','model_sha256':v3['metadata']['model_sha256'],'threshold':.1}).json(),{'detail':'not_found'})
                self.assertEqual(client.get(f"/reports/{report['id']}").json(),{'detail':'not_found'})
                presence=client.get(f"/datasets/1/species/{target['id']}/presence").json()
                self.assertTrue(all(m['engine']=='perch' for r in presence['rows'] for m in r['models']))
                client.put('/models',json={'main':'perch','extra':['birdnet-v3']})
                self.assertEqual(client.post('/jobs',json={'dataset_id':1}).json(),{'detail':'no_audio'})
                self.assertEqual(client.get(f"/validations/{check['id']}").status_code,200)
                with connect(data) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM analyses').fetchone()[0],count)

    def test_main_search_and_saved_search_use_only_matching_engine_and_sha(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); directory=root/'models'; model_files(directory)
            audio=root/'audio'; audio.mkdir()
            for day in ('08','09'):
                sf.write(audio/f'202408{day}_060000.wav',np.full(320000,.25),32000)
            calls=[]
            def load(directory,engine):
                return FakeSession(engine,calls),digest(directory/REGISTRY[engine]['files'][0])
            data=root/'data'
            with TestClient(create_app(data,directory,'test'),headers={'Authorization':'Bearer test'}) as client, \
                    patch('analysis.load_onnx',side_effect=load), \
                    patch('search.load_onnx',side_effect=load), \
                    patch('search_runs.load_onnx',side_effect=load):
                client.post('/imports',json=dict(folder=str(audio),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                client.put('/models',json={'main':'perch','extra':['kinglet-s']})
                client.post('/jobs',json={'dataset_id':1})
                self.assertEqual(wait_job(client)['status'],'complete')
                sha=digest(directory/REGISTRY['kinglet-s']['files'][0])
                # A shared hash must still never mix vectors from different engines.
                with connect(data) as db:
                    db.execute("UPDATE analyses SET metadata=json_set(metadata,'$.model_sha256',?) WHERE engine='perch'",(sha,))
                    db.execute("UPDATE analyses SET metadata=json_set(metadata,'$.model_sha256','old-sha') WHERE engine='kinglet-s' AND recording_id=2")
                client.put('/models',json={'main':'kinglet-s','extra':['perch']})
                missing = client.get('/datasets/1/overview').json()
                self.assertEqual(missing['unanalysed_recordings'],2)
                self.assertEqual(missing['pending_analysis'],[dict(engine='perch',recordings=2,seconds=20.),
                    dict(engine='kinglet-s',recordings=1,seconds=10.)])
                target=client.get('/species?q=Horornis diphone').json()[0]
                clip=dict(source_kind='recording',source_id=2,start_s=0,end_s=3)
                searched=client.post('/search',json=dict(dataset_id=1,**clip))
                self.assertEqual(searched.status_code,200,searched.text)
                searched=searched.json()
                self.assertEqual(searched['query']['engine'],'kinglet-s')
                self.assertEqual((searched['searched_recordings'],searched['unsearched_recordings']),(1,1))
                self.assertTrue(searched['hits'])
                with connect(data) as db:
                    expected={r[0] for r in db.execute("SELECT s.id FROM segments s JOIN analyses a ON a.id=s.analysis_id WHERE a.engine='kinglet-s' AND a.recording_id=1")}
                self.assertEqual({h['segment_id'] for h in searched['hits']},expected)
                run=client.post('/search-runs',json=dict(dataset_id=1,species_id=target['id'],clips=[clip]))
                self.assertEqual(run.status_code,200,run.text)
                run=run.json()
                self.assertEqual(run['snapshot']['engine'],'kinglet-s')
                with connect(data) as db:
                    self.assertEqual({r[0] for r in db.execute('SELECT segment_id FROM search_hits WHERE run_id=?',(run['id'],))},expected)
                params=dict(start_date='2024-08-08',end_date='2024-08-09')
                client.put('/models',json={'main':'perch','extra':['kinglet-s']})
                self.assertIsNone(client.get(f"/datasets/1/species/{target['id']}",params=params).json()['search'])
                client.put('/models',json={'main':'kinglet-s','extra':[]})
                self.assertEqual(client.get(f"/datasets/1/species/{target['id']}",params=params).json()['search']['id'],run['id'])
                self.assertTrue(any(engine=='kinglet-s' and outputs==['embedding'] for engine,outputs,_ in calls))
                loud = root/'loud.wav'
                sf.write(loud,np.full(160000,2.,dtype=np.float32),32000,subtype='FLOAT')
                reference = client.post('/references',json={'path':str(loud)}).json()
                response = client.post('/search',json=dict(dataset_id=1,source_kind='reference',
                    source_id=reference['id'],start_s=0,end_s=5))
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(np.max(calls[-1][2]),1.)

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
                client.put('/models', json={'main':'perch', 'extra': []})
                initial = client.get('/datasets/1/overview', params={'start_date':'2024-08-08','end_date':'2024-08-08'}).json()
                self.assertEqual(initial['pending_analysis'],[dict(engine='perch',recordings=2,seconds=22.)])
                self.assertEqual(client.post('/jobs', json={'dataset_id':1}).status_code, 200)
                self.assertEqual({k: wait_job(client)[k] for k in ('status','total','done')}, dict(status='complete',total=2,done=2))
                first = client.get('/datasets/1/overview').json()
                self.assertEqual((first['analysed_recordings'], first['unanalysed_recordings']), (2, 0))
                self.assertEqual(first['pending_analysis'],[])
                with connect(data) as db:
                    original = [tuple(r) for r in db.execute("SELECT id,metadata FROM analyses WHERE engine='perch'")]
                client.put('/models', json={'main':'perch','extra':['kinglet-s']})
                missing = client.get('/datasets/1/overview').json()
                self.assertEqual((missing['analysed_recordings'],missing['unanalysed_recordings']), (0,2))
                self.assertEqual(missing['pending_analysis'],[dict(engine='kinglet-s',recordings=2,seconds=22.)])
                client.post('/jobs', json={'dataset_id':1})
                job = wait_job(client)
                self.assertEqual((job['status'],job['total'],job['done']), ('complete',2,2))
                self.assertEqual(loads, ['perch','kinglet-s'])
                with connect(data) as db:
                    self.assertEqual([tuple(r) for r in db.execute("SELECT id,metadata FROM analyses WHERE engine='perch'")], original)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM segments').fetchone()[0],12)
                self.assertEqual(client.post('/jobs', json={'dataset_id':1}).json(), {'detail':'no_audio'})
                self.assertEqual(client.get('/datasets/1/overview').json()['pending_analysis'],[])
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
                client.put('/models', json={'main':'perch','extra':['kinglet-s','kinglet-xs']})
                client.post('/jobs', json={'dataset_id':1})
                self.assertEqual(wait_job(client)['status'],'complete')
                self.assertEqual(loads,['perch','kinglet-s','kinglet-xs'])
                client.put('/models', json={'main':'perch','extra':['kinglet-s']})
                self.assertEqual(client.get('/datasets/1/overview').json()['unanalysed_recordings'],0)
                # A replaced model must run again even when that engine has saved analyses.
                (directory / REGISTRY['kinglet-s']['files'][0]).write_bytes(b'updated model')
                self.assertEqual(client.get('/datasets/1/overview').json()['unanalysed_recordings'],2)
                client.post('/jobs', json={'dataset_id':1})
                self.assertEqual(wait_job(client)['total'],2)
                self.assertEqual(loads,['perch','kinglet-s','kinglet-xs','kinglet-s'])


if __name__ == '__main__':
    unittest.main()
