"""Marginal audio/file costs from warmed real jobs in disposable catalogs."""
import json
import tempfile
import threading
from pathlib import Path
from statistics import median
from time import perf_counter

import numpy as np
import soundfile as sf

from analysis import prepare_models, run
from models import REGISTRY, available, list_models, save_benchmark
from species import initialize_species
from storage import CatalogError, connect, initialize, inspect_audio


def fit_costs(samples):
    """Nonnegative fit of recording time = files * fixed + hours * marginal."""
    from scipy.optimize import nnls
    matrix = np.array([[files, seconds/3600] for files, seconds, _ in samples])
    fixed, marginal = nnls(matrix, np.array([elapsed for _, _, elapsed in samples]))[0]
    return dict(seconds_per_hour=round(float(marginal)), seconds_per_file=round(float(fixed), 1))


def benchmark_job(data, paths, models, engine, loaded):
    initialize(data)
    initialize_species(data)
    model = REGISTRY[engine]
    (data / 'models.json').write_text(json.dumps(dict(
        main=engine if model['main_capable'] else 'perch',
        extra=[] if model['main_capable'] else [engine], confirmed=True,
        directory=str(Path(models).resolve()))), encoding='utf-8')
    with connect(data) as db:
        db.execute("INSERT INTO sites VALUES(1,'Benchmark','Asia/Tokyo',35,138)")
        db.execute("INSERT INTO datasets VALUES(1,1,'Benchmark',?)", (str(paths[0].parent),))
        db.executemany('''INSERT INTO recordings(dataset_id,path,original_time,timezone,recorded_at,
            seconds,sample_rate,size,mtime_ns) VALUES(1,?,?,?,?,?,?,?,?)''',
            (inspect_audio(path, 'Asia/Tokyo') for path in paths))
        records = [dict(r) for r in db.execute('SELECT * FROM recordings')]
        site = dict(db.execute('SELECT * FROM sites').fetchone())
        job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'queued',?)", (len(records),)).lastrowid
    recording_times = []
    started = perf_counter()
    run(data, models, job, records, site, threading.Event(), {r['id']:[engine] for r in records}, loaded, recording_times)
    elapsed = perf_counter() - started
    with connect(data) as db:
        status = db.execute('SELECT status,error FROM jobs WHERE id=?', (job,)).fetchone()
    if status['status'] != 'complete':
        raise CatalogError(status['error'] or 'analysis_failed')
    return len(records), sum(r['seconds'] for r in records), sum(recording_times), max(0., elapsed-sum(recording_times))


def benchmark(root, models):
    rate = 48000
    rng = np.random.default_rng(20261026)
    minute = rng.normal(0, .005, rate * 60).astype(np.float32)
    t = np.arange(rate, dtype=np.float32) / rate
    for second in (5, 20, 40):
        minute[second * rate:(second + 1) * rate] += .05 * np.sin(2 * np.pi * (1200 * t + 800 * t * t))
    results, job_costs = {}, []
    with tempfile.TemporaryDirectory(prefix='echoroo-benchmark-') as folder:
        folder = Path(folder).resolve()
        paths = {}
        for seconds in (60, 240):
            path = folder / f'BENCHMARK_{seconds}_20240808_060000.wav'
            with sf.SoundFile(path, 'w', samplerate=rate, channels=1, subtype='PCM_16') as output:
                for _ in range(seconds//60):
                    output.write(minute)
            paths[seconds] = path
        for engine in REGISTRY:
            if not available(models, engine):
                continue
            site = dict(latitude=35, longitude=138)
            with prepare_models(models, [engine], site) as loaded:
                benchmark_job(folder / f'{engine}-warmup', [paths[240]], models, engine, loaded)
                samples = []
                for seconds in (60, 240):
                    measured = [benchmark_job(folder / f'{engine}-{seconds}-{repeat}', [paths[seconds]], models, engine, loaded)
                                for repeat in range(3)]
                    samples.append((measured[0][0], measured[0][1], median(row[2] for row in measured)))
                    job_costs.append(median(row[3] for row in measured))
                results[engine] = fit_costs(samples)
            del loaded
    # Shared job setup/result preparation is counted once, using the largest measured median.
    save_benchmark(root, results, round(max(job_costs, default=0.), 1))
    return list_models(root, models)
