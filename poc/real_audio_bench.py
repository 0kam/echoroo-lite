"""Run a read-only WAV corpus through Perch ONNX or BirdNET 2.4/LiteRT.

This is a bounded PoC, not the application importer. Outputs go in a new folder.
Perch keeps top-five raw logits for inspection; they are not calibrated probabilities.
See REAL_AUDIO_NOTES.md for model acquisition and reproducible commands.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import resource
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parent


def fingerprint(path: Path) -> str:
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def inventory(paths: list[Path], timezone: str) -> list[dict]:
    rows = []
    for path in paths:
        info = sf.info(path)
        # Only the observed recorder filename convention is supported by this PoC.
        stamp = datetime.strptime(path.stem.rsplit('_', 2)[-2] + '_' +
                                  path.stem.rsplit('_', 2)[-1], '%Y%m%d_%H%M%S')
        if info.channels != 1 or info.frames <= 0:
            raise ValueError(f'Expected non-empty mono audio: {path}')
        rows.append(dict(path=str(path), datetime=stamp.replace(tzinfo=ZoneInfo(timezone)).isoformat(),
                         sample_rate=info.samplerate, frames=info.frames,
                         seconds=info.duration, channels=info.channels,
                         subtype=info.subtype, bytes=path.stat().st_size))
    return rows


def perch(paths: list[Path], output: Path) -> dict:
    import onnxruntime as ort
    from scipy.signal import resample_poly
    import sqlite_vec

    model_path = ROOT / 'models/perch/perch_v2_no_dft.onnx'
    labels_path = ROOT / 'models/perch-labels/assets/labels.csv'
    labels = labels_path.read_text().splitlines()
    assert labels.pop(0) == 'inat2024_fsd50k'
    assert len(labels) == 14795
    options = ort.SessionOptions()
    options.intra_op_num_threads = 8
    options.inter_op_num_threads = 1
    start = time.perf_counter()
    session = ort.InferenceSession(str(model_path), sess_options=options,
                                   providers=['CPUExecutionProvider'])
    load_s = time.perf_counter() - start
    db = sqlite3.connect(output / 'search.sqlite')
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.enable_load_extension(False)
    db.execute('CREATE TABLE segments(id INTEGER PRIMARY KEY, file TEXT, start_s REAL, end_s REAL)')
    db.execute('CREATE VIRTUAL TABLE vectors USING vec0(embedding float[1536] distance_metric=cosine)')
    timings, row_id, query = [], 0, None
    with (output / 'perch_top5.csv').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['segment_id', 'file', 'start_s', 'end_s', 'rank', 'label_index', 'label', 'raw_logit'])
        for path in paths:
            start = time.perf_counter()
            audio, rate = sf.read(path, dtype='float32')
            assert np.isfinite(audio).all()
            divisor = math.gcd(rate, 32000)
            audio = resample_poly(audio, 32000 // divisor, rate // divisor)
            assert len(audio) > 0
            decode_s = time.perf_counter() - start
            inference_s, save_s, windows = 0.0, 0.0, 0
            for offset in range(0, len(audio), 160000):
                chunk = audio[offset:offset + 160000]
                start_s, end_s = offset / 32000, (offset + len(chunk)) / 32000
                chunk = np.pad(chunk, (0, 160000 - len(chunk)))
                start = time.perf_counter()
                vector, logits = session.run(['embedding', 'label'], {'inputs': chunk[None, :]})
                inference_s += time.perf_counter() - start
                assert vector.shape == (1, 1536) and logits.shape == (1, len(labels))
                assert np.isfinite(vector).all() and np.isfinite(logits).all()
                assert np.linalg.norm(vector) > 0
                start = time.perf_counter()
                payload = vector.astype('<f4').tobytes()
                db.execute('INSERT INTO segments VALUES (?, ?, ?, ?)', (row_id, str(path), start_s, end_s))
                db.execute('INSERT INTO vectors(rowid, embedding) VALUES (?, ?)', (row_id, payload))
                for rank, index in enumerate(np.argsort(logits[0])[-5:][::-1], 1):
                    writer.writerow([row_id, str(path), start_s, end_s, rank, int(index), labels[index], float(logits[0, index])])
                if query is None:
                    query = payload
                row_id += 1
                windows += 1
                save_s += time.perf_counter() - start
            start = time.perf_counter()
            db.commit()
            f.flush()
            save_s += time.perf_counter() - start
            timings.append(dict(file=str(path), windows=windows, decode_resample_s=decode_s,
                                inference_s=inference_s, save_s=save_s))
            print(json.dumps(timings[-1]), flush=True)
    assert row_id == db.execute('SELECT count(*) FROM segments').fetchone()[0]
    assert row_id == db.execute('SELECT count(*) FROM vectors').fetchone()[0]
    start = time.perf_counter()
    matches = db.execute('SELECT rowid, distance FROM vectors WHERE embedding MATCH ? AND k = ? ORDER BY distance',
                         (query, min(row_id, 11))).fetchall()
    search_s = time.perf_counter() - start
    assert any(i == 0 and abs(distance) < 1e-5 for i, distance in matches), matches
    with (output / 'similarity_example.csv').open('w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['query_segment_id', 'match_segment_id', 'cosine_distance', 'file', 'start_s', 'end_s'])
        for i, distance in matches:
            if i != 0:
                writer.writerow([0, i, distance, *db.execute('SELECT file,start_s,end_s FROM segments WHERE id=?', (i,)).fetchone()])
    db.close()
    return dict(engine='Perch V2 no_dft ONNX', model_sha256=fingerprint(model_path),
                labels_sha256=fingerprint(labels_path), threads=8, provider=session.get_providers(),
                model_load_s=load_s, files=timings, vectors=row_id, search_s=search_s,
                database_bytes=(output / 'search.sqlite').stat().st_size,
                checks='finite outputs, expected shapes, nonzero embeddings, row counts, cosine self-match')


def birdnet_run(paths: list[Path], output: Path, lat: float, lon: float) -> dict:
    os.environ['BIRDNET_APP_DATA'] = str(ROOT / 'models/birdnet')
    import birdnet

    start = time.perf_counter()
    acoustic = birdnet.load('acoustic', '2.4', 'tf', library='litert')
    geo = birdnet.load('geo', '2.4', 'tf', library='litert')
    # ponytail: year-round geo for this runtime PoC; seasonal filtering needs date mapping.
    location = geo.predict(lat, lon, week=None, min_confidence=0.03)
    species = location.to_structured_array()['species_name'].tolist()
    assert species
    location.to_csv(output / 'geo.csv')
    load_geo_s = time.perf_counter() - start
    start = time.perf_counter()
    result = acoustic.predict(paths, n_workers=4, batch_size=1, top_k=5,
                              default_confidence_threshold=0.1, custom_species_list=species,
                              show_stats='benchmark')
    predict_s = time.perf_counter() - start
    assert result.n_inputs == len(paths)
    assert len(result.unprocessable_inputs) == 0, result.unprocessable_inputs
    assert np.isclose(result.input_durations.sum(), sum(sf.info(p).duration for p in paths))
    rows = result.to_structured_array()
    assert np.isfinite(rows['confidence']).all()
    assert set(rows['species_name']).issubset(species)
    result.to_csv(output / 'birdnet.csv', silent=True)
    return dict(engine='BirdNET 2.4 FP32 LiteRT', workers=4, batch_size=1,
                acoustic_sha256=fingerprint(acoustic.model_path), geo_sha256=fingerprint(geo.model_path),
                processed_files=result.n_inputs, unprocessable_files=0,
                geo=dict(latitude=lat, longitude=lon, week=None, aggregation='year_round_max',
                         threshold=0.03, species_count=len(species)),
                load_and_geo_s=load_geo_s, predict_s=predict_s, top_k=5, threshold=0.1,
                detections=len(rows), checks='all input files and durations, no failed files, finite scores, labels within geo-filtered species')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--engine', choices=['perch', 'birdnet'], required=True)
    parser.add_argument('--timezone', required=True)
    parser.add_argument('--latitude', type=float)
    parser.add_argument('--longitude', type=float)
    args = parser.parse_args()
    if args.engine == 'birdnet' and (args.latitude is None or args.longitude is None):
        parser.error('BirdNET requires --latitude and --longitude')
    paths = sorted(p.resolve() for p in args.input.rglob('*') if p.is_file() and p.suffix.lower() == '.wav') if args.input.is_dir() else [args.input.resolve()]
    if not paths:
        parser.error('No WAV files found')
    records = inventory(paths, args.timezone)
    args.output.mkdir(parents=True, exist_ok=False)
    (args.output / 'inventory.json').write_text(json.dumps(records, indent=2))
    start = time.perf_counter()
    result = perch(paths, args.output) if args.engine == 'perch' else birdnet_run(paths, args.output, args.latitude, args.longitude)
    result.update(total_wall_s=time.perf_counter() - start, recording_seconds=sum(r['seconds'] for r in records),
                  peak_main_process_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                  python=sys.version, packages={p: importlib.metadata.version(p) for p in ['numpy', 'scipy', 'soundfile', 'onnxruntime', 'birdnet', 'ai-edge-litert', 'sqlite-vec']})
    result['peak_main_process_rss_note'] = 'Includes model and preprocessing; excludes BirdNET child processes.'
    (args.output / 'summary.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
