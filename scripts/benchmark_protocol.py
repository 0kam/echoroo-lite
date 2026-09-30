"""Measure the public app pipeline on repeated real, one-minute audio in an isolated catalog.

The replayed dates are workload metadata, never ecological observations. Existing output
directories are refused. Run a 48-file pilot before the full 17,520-file workload.
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import psutil
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]


def prepare(source, output, count):
    slots = []
    for path in sorted(source.rglob('*.wav')):
        info = sf.info(path)
        if info.channels == 1:
            slots.extend((path, minute, info.samplerate) for minute in range(int(info.duration // 60)))
    if not slots:
        raise ValueError('No full one-minute mono WAV intervals found')
    pool, recordings = output / 'pool', output / 'recordings'
    pool.mkdir(); recordings.mkdir()
    chosen = [slots[i * len(slots) // min(count, len(slots))] for i in range(min(count, len(slots)))]
    origin = []
    for i, (path, minute, rate) in enumerate(chosen):
        with sf.SoundFile(path) as stream:
            stream.seek(minute * 60 * rate)
            samples = stream.read(60 * rate, dtype='int16')
        assert len(samples) == rate * 60
        target = pool / f'{i}.wav'
        sf.write(target, samples, rate, subtype='PCM_16')
        origin.append({'path': str(path), 'start_s': minute * 60, 'seconds': 60,
                       'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    for i in range(count):
        date = datetime(2025, 1, 1) + timedelta(minutes=30 * i)
        # Hard links save input disk space only; every file is decoded and inferred separately.
        os.link(pool / f'{i % len(chosen)}.wav', recordings / f'REPLAY_{date:%Y%m%d_%H%M%S}.wav')
    result = {'purpose': 'performance_replay_only', 'count': count, 'seconds': count * 60,
              'unique_intervals': origin, 'mapping': 'recording_index modulo unique_intervals count',
              'input_storage': 'hard_links', 'synthetic_start': '2025-01-01T00:00:00+09:00'}
    (output / 'inputs.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def run(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    # Leave room for the OS and abort before creating a large workload on a nearly full disk.
    if shutil.disk_usage(output).free < args.count * 1_500_000 + 12_000_000_000:
        raise RuntimeError('Insufficient free space for this workload and a 12 GB reserve')
    data = output / 'data'
    results = {'status': 'running', 'platform': platform.platform(), 'python': sys.version,
               'cpu': platform.processor(), 'logical_cpus': os.cpu_count(),
               'ram_bytes': psutil.virtual_memory().total, 'timings_s': {}}
    if args.binary:
        with args.binary.open('rb') as stream:
            results['binary_sha256'] = hashlib.file_digest(stream, 'sha256').hexdigest()
    def write():
        temporary = output / 'result.tmp'
        temporary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(output / 'result.json')
    def stage(name):
        results['stage'] = name; write(); print(name, flush=True)
        return time.perf_counter()
    started = stage('prepare_inputs')
    inputs = prepare(args.source.resolve(), output, args.count)
    results.update(recordings=args.count, audio_hours=args.count / 60,
                   unique_audio_hours=len(inputs['unique_intervals']) / 60)
    results['timings_s']['prepare_inputs'] = time.perf_counter() - started
    overall = stage('start_service')
    command = [str(args.binary.resolve())] if args.binary else [sys.executable, str(ROOT / 'apps/sidecar/server.py')]
    log = (output / 'service.log').open('w', encoding='utf-8')
    child = subprocess.Popen([*command, '--data', str(data), '--models', str(args.models.resolve()), '--parent-stdin'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
    stop = threading.Event()
    peak = [0]
    def monitor():
        while not stop.is_set():
            try:
                parent = psutil.Process(child.pid)
                total = 0
                for process in [parent, *parent.children(recursive=True)]:
                    try:
                        total += process.memory_info().rss
                    except psutil.NoSuchProcess:
                        pass
                peak[0] = max(peak[0], total)
            except psutil.NoSuchProcess:
                pass
            stop.wait(1)
    threading.Thread(target=monitor, daemon=True).start()
    try:
        connection = json.loads(child.stdout.readline())
        def drain():
            for line in child.stdout:
                log.write(line); log.flush()
        threading.Thread(target=drain, daemon=True).start()
        def api(path, body=None):
            request = urllib.request.Request(connection['url'] + path,
                data=None if body is None else json.dumps(body).encode(),
                headers={'Authorization': 'Bearer ' + connection['token'], 'Content-Type': 'application/json'})
            with urllib.request.urlopen(request, timeout=3600) as response:
                return json.load(response)
        results['timings_s']['start_service'] = time.perf_counter() - overall
        started = stage('import')
        body = {'folder': str(output / 'recordings'), 'site_name': 'PERFORMANCE REPLAY — not field observations',
                'dataset_name': f'{args.count} one-minute files', 'timezone': 'Asia/Tokyo',
                'latitude': 35.49152777777778, 'longitude': 138.20491666666666}
        assert api('/imports/preview', body)['count'] == args.count
        imported = api('/imports', body)
        assert imported['count'] == args.count and not imported['skipped']
        dataset = imported['dataset_id']
        records = api(f'/datasets/{dataset}/recordings')
        assert len(records) == args.count and sum(r['seconds'] for r in records) == args.count * 60
        results['timings_s']['import'] = time.perf_counter() - started
        started = stage('analysis')
        job_id = api('/jobs', {'dataset_id': dataset})['id']
        last = 0
        while True:
            job = next(j for j in api('/catalog')['jobs'] if j['id'] == job_id)
            if time.perf_counter() - last >= 30 or job['status'] not in ('queued', 'running'):
                results['job'] = job
                results['analysis_elapsed_s'] = time.perf_counter() - started
                results['sampled_peak_process_tree_rss_bytes'] = peak[0]
                write(); print(f"{job['status']} {job['done']}/{job['total']} {results['analysis_elapsed_s']:.1f}s", flush=True)
                last = time.perf_counter()
            if job['status'] not in ('queued', 'running'):
                assert job['status'] == 'complete' and job['done'] == args.count * 2, job
                break
            time.sleep(1)
        results['timings_s']['analysis'] = time.perf_counter() - started
        with sqlite3.connect(data / 'echoroo.sqlite') as db:
            results['analyses'] = dict(db.execute('SELECT engine,COUNT(*) FROM analyses GROUP BY engine'))
            results['segments'] = db.execute('SELECT COUNT(*) FROM segments').fetchone()[0]
        assert results['analyses'] == {'birdnet': args.count, 'perch': args.count}
        assert results['segments'] == args.count * 12
        if args.compare:
            import numpy as np
            baseline = json.loads((args.compare / 'inputs.json').read_text(encoding='utf-8'))
            assert baseline == inputs, 'Baseline must use exactly the same workload'
            differences = {}
            for path in sorted((data / 'analyses/1').glob('*.npz')):
                with np.load(path, allow_pickle=False) as current, np.load(args.compare / 'data/analyses/1' / path.name, allow_pickle=False) as old:
                    assert current.files == old.files
                    for key in current.files:
                        if np.issubdtype(current[key].dtype, np.number):
                            np.testing.assert_allclose(current[key], old[key], rtol=1e-6, atol=1e-7)
                            differences[key] = max(differences.get(key, 0), float(np.max(np.abs(current[key] - old[key]), initial=0)))
                        else:
                            np.testing.assert_array_equal(current[key], old[key])
            results['baseline_max_absolute_differences'] = differences
        results['models'] = [{k: r['metadata'].get(k) for k in ('model', 'model_sha256', 'labels_sha256')}
                             for r in api(f"/recordings/{records[0]['id']}/results")]
        started = stage('search')
        targets = []
        for scientific, filename in [('Horornis diphone', 'uguisu_song_01.mp3'), ('Phasianus versicolor', 'kiji_call_01.mp3')]:
            taxon = api('/species?q=' + urllib.parse.quote(scientific))[0]
            assert taxon['scientific_name'] == scientific
            ref = api('/references', {'path': str(ROOT / 'apps/sidecar/data/quiz/mp3' / filename)})
            targets.append({'species_id': taxon['id'], 'clips': [dict(source_kind='reference', source_id=ref['id'], start_s=s, end_s=s+3) for s in (0, 5)]})
        quick = api('/search', {'dataset_id': dataset, **targets[0]['clips'][0]})
        assert quick['searched_recordings'] == args.count and quick['searched_segments'] == args.count * 12
        results['timings_s']['quick_search'] = time.perf_counter() - started
        started = stage('saved_multi_reference_search')
        runs = [api('/search-runs', {'dataset_id': dataset, **target}) for target in targets]
        results['timings_s']['saved_multi_reference_search'] = time.perf_counter() - started
        started = stage('presence_list')
        listed = api(f"/datasets/{dataset}/species/{targets[0]['species_id']}/presence?source=search")
        assert listed['rows'] and listed['run_id'] == runs[0]['id']
        results['timings_s']['presence_list'] = time.perf_counter() - started
        options = api(f'/reports/options?dataset_id={dataset}')
        dates = {k: options[v] for k, v in [('start_date', 'first_date'), ('end_date', 'last_date')]}
        reports = []
        for model in options['models']:
            started = stage(f"report_{model['engine']}")
            report = api('/reports/detection', {'dataset_id': dataset, **dates, 'engine': model['engine'],
                'model_sha256': model['model_sha256'], 'threshold': .1, 'species_id': targets[0]['species_id']})
            assert sum(c['eligible_windows'] for c in report['cells']) == args.count * 4
            reports.append(report)
            results['timings_s'][results['stage']] = time.perf_counter() - started
        started = stage('export')
        exports = output / 'exports'; exports.mkdir()
        for report in reports:
            exported = api(f"/reports/{report['id']}/export", {'folder': str(exports), 'audio': []})
            with zipfile.ZipFile(exported['path']) as archive:
                assert {'species.csv', 'hourly.csv', 'evidence.csv', 'quality.csv'} <= set(archive.namelist())
                assert len(archive.read('hourly.csv')) > 100
        results['timings_s']['export'] = time.perf_counter() - started
        results['report_ids'] = [r['id'] for r in reports]
        results['service_pipeline_s'] = time.perf_counter() - overall
        results['status'] = 'complete'
    except BaseException as error:
        results.update(status='failed', error=str(error)); write()
        raise
    finally:
        child.stdin.close()
        results['service_exit_code'] = child.wait(timeout=600)
        stop.set()
        results['sampled_peak_process_tree_rss_bytes'] = peak[0]
        results['rss_note'] = '1-second samples; summed RSS counts shared pages more than once and can miss short peaks.'
        results['saved_data_bytes'] = sum(p.stat().st_size for p in data.rglob('*') if p.is_file())
        results['storage_note'] = 'App data only; input pool, hard links, models and exports are separate.'
        results['scope'] = 'Public service APIs including CSV/audio export; GUI drawing and user interaction are not timed.'
        results['annual_compute_budget_met'] = args.count == 17520 and results['status'] == 'complete' and results['service_pipeline_s'] <= 43200 and results['service_exit_code'] == 0
        write(); log.close()
    assert results['service_exit_code'] == 0
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--models', type=Path, default=ROOT / 'poc/models')
    parser.add_argument('--binary', type=Path)
    parser.add_argument('--compare', type=Path, help='Check all saved scores and labels against an earlier identical workload')
    parser.add_argument('--count', type=int, default=48, choices=range(3, 17521), metavar='3..17520')
    run(parser.parse_args())
