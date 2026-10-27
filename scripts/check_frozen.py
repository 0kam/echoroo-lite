"""Check the real frozen process handshake, auth, SQLite extension and clean exit."""
import json
import argparse
from collections import deque
from contextlib import ExitStack
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("binary")
parser.add_argument("--models", type=Path)
parser.add_argument("--data", type=Path, help="Keep the check's data in a new directory for desktop UI checks")
parser.add_argument("--diagnostics", action="store_true", help="Record periodic stack traces when a frozen check stalls")
parser.add_argument("--parent-eof", action="store_true", help="Check shutdown when the parent closes its pipe without a stop byte")
audio_input = parser.add_mutually_exclusive_group()
audio_input.add_argument("--recording", type=Path)
audio_input.add_argument("--sample-audio", type=Path, help="Convert a supplied call clip into a dated mono WAV for the full ML check")
args = parser.parse_args()

with ExitStack() as stack:
    if args.data:
        args.data = args.data.resolve()
        args.data.mkdir(parents=True, exist_ok=False)
        temp = str(args.data)
    else:
        temp = stack.enter_context(tempfile.TemporaryDirectory())
    if args.sample_audio:
        import soundfile as sf
        samples, rate = sf.read(args.sample_audio, dtype='float32')
        if samples.ndim == 2:
            samples = samples.mean(axis=1)
        assert len(samples) >= rate * 5, 'The ML check needs at least five seconds of audio'
        import numpy as np
        # Repeat short fixtures so the 30-s recordings exercise full search windows.
        samples = np.tile(samples, -(-rate * 30 // len(samples)))
        folder = Path(temp) / 'recordings'
        folder.mkdir()
        args.recording = folder / 'SMOKE_20240808_060000.wav'
        sf.write(args.recording, samples[:rate * 30], rate, subtype='PCM_16')
        # A second recording also exercises sampling across the whole period.
        sf.write(folder / 'SMOKE_20240809_060000.wav', samples[:rate * 30], rate, subtype='PCM_16')
    child = subprocess.Popen([args.binary, "--data", temp, "--models", str(args.models or Path(temp) / "models"), "--parent-stdin",
                              *(['--diagnostics'] if args.diagnostics else [])],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, errors='replace')
    try:
        connection = json.loads(child.stdout.readline())
        # Match the desktop launcher: library output must never fill the stdout pipe.
        threading.Thread(target=deque, args=(child.stdout, 0), daemon=True).start()
        def api(path, body=None, method=None):
            request = urllib.request.Request(connection["url"] + path,
                data=None if body is None else json.dumps(body).encode(), method=method,
                headers={"Authorization": "Bearer " + connection["token"], "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.load(response)
        assert api("/health")["status"] == "ok"
        selection = api('/models')
        thresholds = {m['id']: m['default_threshold'] for m in selection['models']}
        assert thresholds == {'perch': .1, 'kinglet-b': .5, 'kinglet-s': .5, 'kinglet-xs': .5,
                              'birdnet': .1, 'birdnet-v3': .1}, thresholds
        assert selection['main'] == 'perch' and selection['confirmed'] is False, selection
        assert selection['seconds_per_job'] is None
        assert all(m['seconds_per_hour'] is None and m['seconds_per_file'] is None for m in selection['models'])
        selection = api('/models', {'main': 'perch', 'extra': [m['id'] for m in selection['models']
            if m['available'] and m['id'] != 'perch']}, method='PUT')
        assert selection['confirmed'] is True
        started = time.perf_counter()
        for model in selection['models']:
            if model['available']:
                measured = api('/models/benchmark', {'engine': model['id']}, method='POST')
        assert all(type(m['seconds_per_hour']) is int and m['seconds_per_hour'] >= 0
                   for m in measured['models'] if m['available']), measured
        assert all(isinstance(m['seconds_per_file'], (int,float)) and m['seconds_per_file'] >= 0
                   and round(m['seconds_per_file'],1) == m['seconds_per_file']
                   for m in measured['models'] if m['available']), measured
        assert isinstance(measured['seconds_per_job'], (int,float)) and measured['seconds_per_job'] >= 0
        assert all('minutes_per_hour' not in m for m in measured['models']), measured
        print('Sequential offline marginal benchmark:', {m['id']: {key:m[key] for key in
            ('seconds_per_hour','seconds_per_file')} for m in measured['models']}, 'seconds_per_job:', measured['seconds_per_job'],
            'total_wall_s:', round(time.perf_counter() - started, 2))
        species = api("/species?q=Certhia%20familiaris")
        assert species[0]["common_names"]["ja"] == "キバシリ", species
        assert api('/xeno-canto/key') == {'configured': False}
        print('Quiz session smoke check skipped: starting a session requires clips from at least two species.')
        try:
            urllib.request.urlopen(connection["url"] + "/catalog", timeout=20)
        except urllib.error.HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("Unauthenticated request was accepted")
        if args.recording:
            target = api('/species?q=Horornis%20diphone')[0]
            imported = api("/imports", dict(folder=str(args.recording.resolve().parent), site_name="Test", dataset_name="Freeze check",
                timezone="Asia/Tokyo", latitude=35.49152777777778, longitude=138.20491666666666))
            records = api(f"/datasets/{imported['dataset_id']}/recordings")
            record = next(r for r in records if Path(r["path"]) == args.recording.resolve())
            engines = {m['id'] for m in selection['models'] if m['enabled'] and m['available']}
            pending = api(f"/datasets/{imported['dataset_id']}/overview")['pending_analysis']
            assert {row['engine'] for row in pending} == engines, pending
            assert all(row['recordings'] == len(records) and
                       abs(row['seconds']-sum(r['seconds'] for r in records)) < .001 for row in pending), pending
            started = time.perf_counter()
            api("/jobs", {"dataset_id": imported["dataset_id"]})
            previous_stage = None
            while True:
                job = api("/catalog")["jobs"][0]
                if job['stage'] != previous_stage:
                    print(f"Frozen analysis stage: {job['stage']} ({time.perf_counter() - started:.1f}s)", flush=True)
                    previous_stage = job['stage']
                if job["status"] not in {"running", "queued"}:
                    break
                assert time.perf_counter() - started < 240, job
                time.sleep(1)
            assert job["status"] == "complete", job
            assert api(f"/datasets/{imported['dataset_id']}/overview")['pending_analysis'] == []
            results = api(f"/recordings/{record['id']}/results")
            engines = {m['id'] for m in api('/models')['models'] if m['enabled'] and m['available']}
            assert {r["engine"] for r in results} == engines
            perch = next(r for r in results if r["engine"] == "perch")
            assert perch["metadata"]["display_candidate_filter"] == "applied", perch["metadata"]
            assert perch["candidates"] and all(0 <= c["score"] <= 1 for c in perch["candidates"])
            print(f"Frozen real-audio analysis passed in {time.perf_counter() - started:.2f}s.")
            started = time.perf_counter()
            reference = api('/references', {'path': str(args.recording.resolve())})
            search = api('/search', dict(dataset_id=imported['dataset_id'], source_kind='reference',
                source_id=reference['id'], start_s=0, end_s=min(3, record['seconds'])))
            assert search['searched_recordings'] == len(records) and search['hits']
            assert all(-1 <= h['similarity'] <= 1 and (h['recording_id'] != record['id'] or h['start_s'] >= 3) for h in search['hits'])
            view = api(f"/sources/reference/{reference['id']}/spectrogram?start_s=0&end_s={min(30, record['seconds'])}")
            assert view['width'] <= 1000 and view['height'] == 256 and view['frequency_scale'] == 'mel'
            linear = api(f"/sources/reference/{reference['id']}/spectrogram?start_s=0&end_s={min(30, record['seconds'])}&scale=linear")
            assert linear['height'] == 385 and linear['frequency_scale'] == 'linear'
            print(f"Frozen reference registration, spectrogram and real similarity search passed in {time.perf_counter() - started:.2f}s.")
            started = time.perf_counter()
            run = api('/search-runs', {'dataset_id': imported['dataset_id'], 'species_id': target['id'], 'clips': [
                {'source_kind': 'reference', 'source_id': reference['id'], 'start_s': 0, 'end_s': 3},
                {'source_kind': 'reference', 'source_id': reference['id'], 'start_s': 5, 'end_s': 8}]})
            assert len(run['snapshot']['references']) == 2
            listed = api(f"/datasets/{imported['dataset_id']}/species/{target['id']}/presence?source=search")
            hit = listed['rows'][0]
            assert hit['end_s'] - hit['start_s'] == 5 and listed['run_id'] == run['id']
            api('/presence/marks', {'dataset_id': imported['dataset_id'], 'species_id': target['id'], 'recording_id': hit['recording_id'],
                'start_s': hit['start_s'], 'end_s': hit['end_s'], 'run_id': run['id'], 'marked': True})
            assert api(f"/datasets/{imported['dataset_id']}/species/{target['id']}")['presence']['status'] == 'confirmed'
            print(f"Frozen multi-reference search and presence mark passed in {time.perf_counter() - started:.2f}s.")
            started = time.perf_counter()
            options = api(f"/reports/options?dataset_id={imported['dataset_id']}")
            dates = {'start_date': options['first_date'], 'end_date': options['last_date']}
            for engine in sorted(engines):
                model = next(m for m in options['models'] if m['engine'] == engine)
                report = api('/reports/detection', {**dates, 'dataset_id': imported['dataset_id'], 'engine': engine,
                    'model_sha256': model['model_sha256'], 'threshold': thresholds[engine], 'species_id': target['id']})
                assert report['window_s'] == (3 if engine == 'birdnet' else 5) and report['quality'] == 'not_evaluated'
                assert sum(c['recorded_seconds'] for c in report['cells']) > 0
            exported = api(f"/reports/{report['id']}/export", {'folder': temp,
                'audio': [{k: e[k] for k in ('recording_id', 'start_s', 'end_s')} for e in report['evidence'][:1]]})
            import zipfile
            with zipfile.ZipFile(exported['path']) as archive:
                assert {'species.csv', 'hourly.csv', 'evidence.csv', 'quality.csv', 'report.json'} <= set(archive.namelist())
                assert any(name.endswith('.wav') for name in archive.namelist()) or not report['evidence']
            print(f"Frozen detection reports and CSV/audio export passed in {time.perf_counter() - started:.2f}s.")
            started = time.perf_counter()
            if len(records) > 1:
                for engine in sorted(engines):
                    check=api('/validations',{**dates,'dataset_id':imported['dataset_id'],'species_id':target['id'],'model':engine})
                    question=check['question']
                    assert check['counts']['total']>0 and check['window_s']==(3 if engine=='birdnet' else 5) and 'scores' not in question
                a=question['start_s']
                detail=api(f"/sources/recording/{question['source']['id']}/spectrogram?start_s={a}&end_s={question['end_s']}&detail=true")
                assert detail['width']>1000
                answered=api(f"/validations/{check['id']}/answers",{'item_id':question['id'],'verdict':'unknown','version':question['version']})
                assert answered['counts']['answered']==1 and answered['status']=='in_progress'
                print(f"Frozen per-model check, high-resolution spectrogram and saved answer passed in {time.perf_counter()-started:.2f}s.")
            started = time.perf_counter()
            overview = api(f"/datasets/{imported['dataset_id']}/overview")
            suite = api('/workflow-exports', {**dates, 'dataset_id': imported['dataset_id'],
                'revision': overview['revision'], 'species_ids': [target['id']], 'folder': temp, 'locale': 'ja'})
            with zipfile.ZipFile(suite['path']) as archive:
                assert archive.testzip() is None
                manifest = json.loads(archive.read('manifest.json'))
                assert manifest['format'] == 'html' and manifest['scope']['recording_ids'] == sorted(r['id'] for r in records)
                assert '聞き分けクイズの補足記録' in archive.read('index.html').decode()
                assert '図を保存' in archive.read('index.html').decode()
                assert '聞いて確認した' in archive.read('index.html').decode() and 'presence.csv' in archive.namelist()
                assert all(entry['bytes'] > 0 for name, entry in manifest['files'].items() if name.endswith('.png'))
            assert Path(suite['report_path']).is_file()
            print(f"Frozen HTML report, Japanese figures, presence audio and data suite passed in {time.perf_counter() - started:.2f}s.")
        if args.parent_eof:
            child.stdin.close()
        else:
            child.stdin.write("\n")
            child.stdin.flush()
        assert child.wait(timeout=30) == 0
        print("Frozen sidecar: handshake, authenticated API, bundled species names, SQLite/vec initialization and shutdown passed.")
    finally:
        if child.poll() is None:
            # A one-file bootloader owns another process; request cleanup first.
            child.stdin.close()
            child.wait(timeout=240)
