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
        folder = Path(temp) / 'recordings'
        folder.mkdir()
        args.recording = folder / 'SMOKE_20240808_060000.wav'
        sf.write(args.recording, samples[:rate * 30], rate, subtype='PCM_16')
    child = subprocess.Popen([args.binary, "--data", temp, "--models", str(args.models or Path(temp) / "models"), "--parent-stdin",
                              *(['--diagnostics'] if args.diagnostics else [])],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, errors='replace')
    try:
        connection = json.loads(child.stdout.readline())
        # Match the desktop launcher: library output must never fill the stdout pipe.
        threading.Thread(target=deque, args=(child.stdout, 0), daemon=True).start()
        def api(path, body=None):
            request = urllib.request.Request(connection["url"] + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={"Authorization": "Bearer " + connection["token"], "Content-Type": "application/json"})
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        assert api("/health")["status"] == "ok"
        species = api("/species?q=Certhia%20familiaris")
        assert species[0]["common_names"]["ja"] == "キバシリ", species
        assert api('/xeno-canto/key') == {'configured': False}
        library = api('/quiz/library')
        assert len(library['clips']) == 26 and len({c['species_id'] for c in library['clips']}) == 26
        quiz = api('/quiz/sessions', {'clip_ids': [c['id'] for c in library['clips']], 'count': 2})
        assert quiz['purpose'] == 'practice' and 'clip' not in quiz['question']
        quiz = api(f"/quiz/sessions/{quiz['id']}/answers", {'position': 0, 'species_id': None})
        assert quiz['unknown'] == 1 and quiz['question']['feedback']['status'] == 'unknown'
        request = urllib.request.Request(connection['url'] + f"/quiz/sessions/{quiz['id']}/questions/0/audio", headers={'Authorization': 'Bearer ' + connection['token']})
        with urllib.request.urlopen(request, timeout=30) as response:
            assert len(response.read()) > 1000
        print('Frozen 26-species quiz, managed MP3 audio, answer persistence and feedback passed.')
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
            started = time.perf_counter()
            api("/jobs", {"dataset_id": imported["dataset_id"], "recording_id": record["id"]})
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
            results = api(f"/recordings/{record['id']}/results")
            assert {r["engine"] for r in results} == {"birdnet", "perch"}
            perch = next(r for r in results if r["engine"] == "perch")
            assert perch["metadata"]["display_geo"] == "applied", perch["metadata"]
            assert perch["candidates"] and all(0 <= c["score"] <= 1 for c in perch["candidates"])
            print(f"Frozen real-audio analysis passed in {time.perf_counter() - started:.2f}s.")
            started = time.perf_counter()
            reference = api('/references', {'path': str(args.recording.resolve())})
            search = api('/search', dict(dataset_id=imported['dataset_id'], source_kind='reference',
                source_id=reference['id'], start_s=0, end_s=min(3, record['seconds'])))
            assert search['searched_recordings'] == 1 and search['hits']
            assert all(-1 <= h['similarity'] <= 1 and h['start_s'] >= 3 for h in search['hits'])
            view = api(f"/sources/reference/{reference['id']}/spectrogram?start_s=0&end_s={min(30, record['seconds'])}")
            assert view['width'] <= 1000 and view['height'] == 385
            print(f"Frozen reference registration, spectrogram and real similarity search passed in {time.perf_counter() - started:.2f}s.")
            started = time.perf_counter()
            runs = api('/search-runs', {'dataset_id': imported['dataset_id'], 'targets': [{
                'species_id': target['id'], 'clips': [
                    {'source_kind': 'reference', 'source_id': reference['id'], 'start_s': 0, 'end_s': 3},
                    {'source_kind': 'reference', 'source_id': reference['id'], 'start_s': 5, 'end_s': 8}]}]})
            run = runs[0]
            assert len(run['snapshot']['references']) == 2 and run['hit_count'] > 0
            sample = api(f"/search-runs/{run['id']}/samples", {'low': -1, 'high': 1, 'count': 3})
            assert len(sample['hits']) == 3
            hit = sample['hits'][0]
            api(f"/search-runs/{run['id']}/judgements", {'segment_id': hit['segment_id'], 'verdict': 'unknown'})
            restored = api(f"/search-runs/{run['id']}?sample_id={sample['sample_id']}")
            assert next(h for h in restored['hits'] if h['segment_id'] == hit['segment_id'])['verdict'] == 'unknown'
            print(f"Frozen multi-reference search, random sample and judgement persistence passed in {time.perf_counter() - started:.2f}s.")
            started = time.perf_counter()
            options = api(f"/reports/options?dataset_id={imported['dataset_id']}")
            dates = {'start_date': options['first_date'], 'end_date': options['last_date']}
            for engine in ('birdnet', 'perch'):
                model = next(m for m in options['models'] if m['engine'] == engine)
                report = api('/reports/detection', {**dates, 'dataset_id': imported['dataset_id'], 'engine': engine,
                    'model_sha256': model['model_sha256'], 'threshold': .1, 'species_id': target['id']})
                assert report['window_s'] == 15 and report['quality'] == 'not_evaluated'
                assert sum(c['recorded_seconds'] for c in report['cells']) > 0
            report = api('/reports/search', {**dates, 'run_id': run['id'], 'threshold': .5})
            assert report['summaries'][0]['eligible_windows'] == run['hit_count']
            assert report['judgements']['unknown'] == 1
            exported = api(f"/reports/{report['id']}/export", {'folder': temp,
                'audio': [{'recording_id': hit['recording_id'], 'start_s': hit['start_s'], 'end_s': hit['end_s']}]})
            import zipfile
            with zipfile.ZipFile(exported['path']) as archive:
                assert {'species.csv', 'hourly.csv', 'evidence.csv', 'quality.csv', 'report.json'} <= set(archive.namelist())
                assert any(name.endswith('.wav') for name in archive.namelist())
            print(f"Frozen detection/search reports and CSV/audio export passed in {time.perf_counter() - started:.2f}s.")
            started = time.perf_counter()
            annotation=api('/annotations',{**dates,'dataset_id':imported['dataset_id']})
            question=annotation['question'];a=question['start_s']
            assert annotation['total']>0 and 'scores' not in question
            detail=api(f"/sources/recording/{record['id']}/spectrogram?start_s={a}&end_s={a+15}&detail=true")
            assert detail['width']>1000
            annotated=api(f"/annotations/{annotation['id']}/answers",{'item_id':question['id'],'version':question['version'],
                'ranges':[{'species_id':target['id'],'start_s':a+1,'end_s':a+2},
                          {'species_id':None,'start_s':a+5,'end_s':a+6}],'complete':True})
            assert annotated['heard'][0]['species']['id']==target['id']
            print(f"Frozen shared sampling, high-resolution spectrogram and versioned ranges passed in {time.perf_counter()-started:.2f}s.")
            started = time.perf_counter()
            overview = api(f"/datasets/{imported['dataset_id']}/overview")
            suite = api('/workflow-exports', {**dates, 'dataset_id': imported['dataset_id'],
                'revision': overview['revision'], 'species_ids': [target['id']], 'folder': temp, 'locale': 'ja'})
            with zipfile.ZipFile(suite['path']) as archive:
                assert archive.testzip() is None
                manifest = json.loads(archive.read('manifest.json'))
                assert manifest['format'] == 'html' and manifest['scope']['recording_ids'] == [record['id']]
                assert '聞き分けクイズの補足記録' in archive.read('index.html').decode()
                assert '図を保存' in archive.read('index.html').decode()
                assert len(json.loads(archive.read('references.json'))) == 2
                assert len(json.loads(archive.read('annotations/ranges.json')))==2
                assert all(entry['bytes'] > 0 for name, entry in manifest['files'].items() if name.endswith('.png'))
            assert Path(suite['report_path']).is_file()
            print(f"Frozen HTML report, Japanese figures, references, audio and data suite passed in {time.perf_counter() - started:.2f}s.")
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
