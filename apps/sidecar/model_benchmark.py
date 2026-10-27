"""Marginal audio/file costs from warmed real jobs in disposable catalogs."""
import json
import tempfile
import threading
from pathlib import Path
from time import perf_counter

import numpy as np
import soundfile as sf

from analysis import prepare_models, run
from models import REGISTRY, available, list_models, save_benchmark, settings
from species import initialize_species
from storage import CatalogError, connect, initialize, inspect_audio


def fit_costs(samples):
    """Separate audio, file and job costs from three warmed jobs; clamp timing noise."""
    short, long, split = samples
    marginal = max(0., (long[2] - short[2]) * 3600 / (long[1] - short[1]))
    fixed = max(0., (split[2] - long[2]) / (split[0] - long[0]))
    job = max(0., short[2] - short[1] / 3600 * marginal - short[0] * fixed)
    return dict(seconds_per_job=round(job, 1), seconds_per_hour=round(marginal),
                seconds_per_file=round(fixed, 1))


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
    started = perf_counter()
    run(data, models, job, records, site, threading.Event(), {r['id']:[engine] for r in records}, loaded)
    elapsed = perf_counter() - started
    with connect(data) as db:
        status = db.execute('SELECT status,error FROM jobs WHERE id=?', (job,)).fetchone()
    if status['status'] != 'complete':
        raise CatalogError(status['error'] or 'analysis_failed')
    return len(records), sum(r['seconds'] for r in records), elapsed


def benchmark(root, models, engine=None):
    if engine is not None and (engine not in REGISTRY or not available(models, engine)):
        raise CatalogError('invalid_input')
    engines = (engine,) if engine is not None else REGISTRY
    previous = settings(root)['benchmark'] if engine is not None else {}
    rate = 48000
    rng = np.random.default_rng(20261026)
    audio = rng.normal(0, .005, rate * 80).astype(np.float32)
    t = np.arange(rate, dtype=np.float32) / rate
    for second in (5, 20, 40):
        audio[second * rate:(second + 1) * rate] += .05 * np.sin(2 * np.pi * (1200 * t + 800 * t * t))
    results, job_costs = {}, []
    with tempfile.TemporaryDirectory(prefix='echoroo-benchmark-') as folder:
        folder = Path(folder).resolve()
        paths = {}
        for seconds in (20, 80):
            path = folder / f'BENCHMARK_{seconds}_20240808_060000.wav'
            sf.write(path, audio[:seconds*rate], rate, subtype='PCM_16')
            paths[seconds] = path
        short_files = [paths[20]]
        for index, clock in enumerate(('060020', '060040', '060100'), start=1):
            path = folder / f'BENCHMARK_20_20240808_{clock}.wav'
            sf.write(path, audio[index*20*rate:(index+1)*20*rate], rate, subtype='PCM_16')
            short_files.append(path)
        for engine in engines:
            if not available(models, engine):
                continue
            site = dict(latitude=35, longitude=138)
            with prepare_models(models, [engine], site) as loaded:
                benchmark_job(folder / f'{engine}-warmup', [paths[20]], models, engine, loaded)
                samples = [benchmark_job(folder / f'{engine}-{index}', inputs, models, engine, loaded)
                           for index, inputs in enumerate(([paths[20]], [paths[80]], short_files))]
                results[engine] = fit_costs(samples)
                job_costs.append(results[engine].pop('seconds_per_job'))
            del loaded
    # Count shared job setup/result preparation once, using the largest fitted cost.
    save_benchmark(root, {**previous.get('models', {}), **results},
                   round(max([previous.get('seconds_per_job', 0.), *job_costs]), 1))
    return list_models(root, models)
