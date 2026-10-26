"""Offline, sequential timings of real analysis jobs in disposable catalogs."""
import json
import tempfile
import threading
from pathlib import Path
from time import perf_counter

import numpy as np
import soundfile as sf

from analysis import prepare_models, run
from models import REGISTRY, available, list_models, save_benchmark
from species import initialize_species
from storage import CatalogError, connect, initialize, inspect_audio


def benchmark(root, models):
    rate = 32000
    rng = np.random.default_rng(20261026)
    samples = rng.normal(0, .005, rate * 60).astype(np.float32)
    t = np.arange(rate, dtype=np.float32) / rate
    for second in (5, 20, 40):
        samples[second * rate:(second + 1) * rate] += .05 * np.sin(2 * np.pi * (1200 * t + 800 * t * t))
    results = {}
    with tempfile.TemporaryDirectory(prefix='echoroo-benchmark-') as folder:
        path = Path(folder).resolve() / 'BENCHMARK_20240808_060000.wav'
        sf.write(path, samples, rate, subtype='PCM_16')
        for engine, model in REGISTRY.items():
            if not available(models, engine):
                continue
            data = path.parent / engine
            initialize(data)
            initialize_species(data)
            # Only this engine's results enter the temporary job's result preparation.
            (data / 'models.json').write_text(json.dumps(dict(
                main=engine if model['main_capable'] else 'perch',
                extra=[] if model['main_capable'] else [engine], confirmed=True,
                directory=str(Path(models).resolve()))), encoding='utf-8')
            with connect(data) as db:
                db.execute("INSERT INTO sites VALUES(1,'Benchmark','Asia/Tokyo',35,138)")
                db.execute("INSERT INTO datasets VALUES(1,1,'Benchmark',?)", (str(path.parent),))
                db.execute('''INSERT INTO recordings(dataset_id,path,original_time,timezone,recorded_at,
                    seconds,sample_rate,size,mtime_ns) VALUES(1,?,?,?,?,?,?,?,?)''', inspect_audio(path, 'Asia/Tokyo'))
                record = dict(db.execute('SELECT * FROM recordings').fetchone())
                site = dict(db.execute('SELECT * FROM sites').fetchone())
            with prepare_models(models, [engine], site) as loaded:
                for warmup in (True, False):
                    with connect(data) as db:
                        job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'queued',1)").lastrowid
                    started = perf_counter() if not warmup else None
                    run(data, models, job, [record], site, threading.Event(), {record['id']: [engine]}, loaded)
                    elapsed = perf_counter() - started if not warmup else None
                    with connect(data) as db:
                        status = db.execute('SELECT status,error FROM jobs WHERE id=?', (job,)).fetchone()
                    if status['status'] != 'complete':
                        raise CatalogError(status['error'] or 'analysis_failed')
                results[engine] = round(elapsed * 3600 / record['seconds'])
            del loaded
    save_benchmark(root, results)
    return list_models(root, models)
