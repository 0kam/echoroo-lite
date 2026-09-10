"""Measure cold/warm view APIs in a copied catalogue; never write test answers to user data."""
import argparse
from collections import deque
import json
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
import urllib.request

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('binary', type=Path)
parser.add_argument('--source-data', required=True, type=Path)
parser.add_argument('--output', required=True, type=Path, help='New directory for the copied catalogue and measurements')
parser.add_argument('--models', required=True, type=Path)
args = parser.parse_args()
args.output.mkdir(parents=True, exist_ok=False)
data = args.output / 'data'; data.mkdir()
with sqlite3.connect(args.source_data / 'echoroo.sqlite') as source, sqlite3.connect(data / 'echoroo.sqlite') as destination:
    source.backup(destination)
# Audio and analysis paths stay unchanged. Missing per-recording view caches may be regenerated there.
measurements = []
with (args.output / 'sidecar.log').open('w') as log:
    start = time.perf_counter()
    child = subprocess.Popen([str(args.binary.resolve()), '--data', str(data.resolve()), '--models', str(args.models.resolve()), '--parent-stdin'],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True)
    try:
        connection = json.loads(child.stdout.readline())
        measurements.append({'path': 'startup', 'seconds': [time.perf_counter() - start]})
        threading.Thread(target=deque, args=(child.stdout, 0), daemon=True).start()
        def measure(path):
            elapsed = []
            for _ in range(3):
                start = time.perf_counter()
                request = urllib.request.Request(connection['url'] + path, headers={'Authorization': 'Bearer ' + connection['token']})
                with urllib.request.urlopen(request, timeout=600) as response:
                    payload = response.read()
                elapsed.append(time.perf_counter() - start)
            measurements.append({'path': path, 'seconds': elapsed, 'bytes': len(payload)})
            (args.output / 'measurements.json').write_text(json.dumps(measurements, indent=2))
            print(path, ', '.join(f'{seconds:.3f}s' for seconds in elapsed), flush=True)
            return json.loads(payload)
        measure('/health')
        catalog = measure('/catalog')
        library = measure('/quiz/library')
        if library['sessions']:
            session = measure(f"/quiz/sessions/{library['sessions'][0]['id']}")
            measure(f"/quiz/sessions/{session['id']}/questions/0/spectrogram?start_s=0&end_s=1")
        measure('/species?q=Horornis')
        measure('/references')
        measure('/xeno-canto/key')
        with sqlite3.connect(data / 'echoroo.sqlite') as db:
            datasets = [r[0] for r in db.execute('SELECT MIN(d.id) FROM datasets d JOIN recordings r ON r.dataset_id=d.id GROUP BY d.site_id')]
            validations = [r[0] for r in db.execute('SELECT id FROM validation_sessions ORDER BY id DESC LIMIT 2')]
        for dataset in datasets:
            prefix = f'/datasets/{dataset}'
            recordings = measure(prefix + '/recordings')
            if recordings:
                recording = recordings[0]
                measure(f"/sources/recording/{recording['id']}/spectrogram?start_s=0&end_s={min(15, recording['seconds'])}")
            overview = measure(prefix + '/overview')
            for row in overview['species'][:3]:
                for model in row['models']:
                    measure(prefix + f"/species/{row['species']['id']}?engine={model['engine']}")
            measure(f'/reports/options?dataset_id={dataset}')
            measure(prefix + '/exports')
            reports = measure(f'/reports?dataset_id={dataset}')
            for report in reports[:1]:
                measure(f"/reports/{report['id']}")
            runs = measure(f'/search-runs?dataset_id={dataset}')
            for run in runs[:1]:
                measure(f"/search-runs/{run['id']}")
        for validation in validations:
            measure(f'/validations/{validation}')
    finally:
        child.stdin.close()
        try:
            child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            child.terminate(); child.wait(timeout=30)
