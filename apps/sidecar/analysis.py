"""The real-audio PoC pipeline, with per-recording persistence."""
import csv
import hashlib
import json
import os
import threading
from functools import lru_cache
from time import perf_counter
from contextlib import contextmanager, nullcontext
from pathlib import Path

import numpy as np
import soundfile as sf

from audio import resample
from models import REGISTRY, available, enabled_engines, main_engine, window_s, configure_birdnet, file_sha256

from species import birdnet_species_mapping
from storage import CatalogError, checked_source, connect


_warmup_thread = None
_warmup_lock = threading.Lock()


def warm_imports():
    try:
        os.environ['ORT_DISABLE_TELEMETRY'] = '1'
        from scipy import signal, special, optimize
        import onnxruntime
        onnxruntime.disable_telemetry_events()
        signal.resample_poly(np.zeros(48, dtype=np.float32), 2, 3)
        signal.resample(np.zeros(32, dtype=np.float32), 48)
        special.softmax(np.zeros((1, 2), dtype=np.float32), axis=1)
    except Exception:
        import traceback
        traceback.print_exc()


def start_warmup():
    global _warmup_thread
    with _warmup_lock:
        if _warmup_thread is None:
            _warmup_thread = threading.Thread(target=warm_imports, name='analysis-imports', daemon=True)
            _warmup_thread.start()
    return _warmup_thread


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_onnx(models, engine):
    # Disable the non-Windows uploader before ORT initializes (also avoids its shutdown race).
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    import onnxruntime as ort
    ort.disable_telemetry_events()
    path = Path(models) / REGISTRY[engine]['files'][0]
    if not path.is_file():
        raise CatalogError("models_missing")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 8
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"]), digest(path)


def load_perch(models):
    return load_onnx(models, 'perch')


def perch_labels(models):
    path = Path(models) / "perch-labels/assets/labels.csv"
    if not path.is_file():
        raise CatalogError("models_missing")
    with path.open(newline="", encoding="utf-8") as stream:
        return [row[0] for row in list(csv.reader(stream))[1:]]


def perch_label_index(labels, scientific_name):
    mapped = birdnet_species_mapping().get(scientific_name)
    for candidate in (scientific_name, mapped):
        if candidate in labels:
            return labels.index(candidate)
    raise CatalogError("species_not_in_model")


def birdnet_available(models):
    return available(models, 'birdnet')


def plan(root, models, records, site):
    """Freeze missing recording/engine pairs before starting the worker."""
    if not available(models, main_engine(root)):
        raise CatalogError('models_missing')
    return pending_pairs(root, models, records, site)


def pending_pairs(root, models, records, site):
    """The same read-only missing-pair calculation for jobs and time estimates."""
    engines = enabled_engines(root, models)
    if not engines or not records:
        return {}
    hashes = {engine: file_sha256(Path(models) / REGISTRY[engine]['files'][0]) for engine in engines}
    classes = {engine: file_sha256(Path(models) / REGISTRY[engine]['files'][1])
               for engine in engines if REGISTRY[engine]['experimental']}
    with connect(root) as db:
        latest = {(r['recording_id'], r['engine']): r for r in db.execute('''SELECT a.*,
            EXISTS (SELECT 1 FROM segments s JOIN vectors v ON v.rowid=s.id WHERE s.analysis_id=a.id) AS embedded
            FROM analyses a WHERE recording_id IN (SELECT value FROM json_each(?)) AND
            id IN (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)''',
            (json.dumps([r['id'] for r in records]),))}
        changed = bool(db.execute('SELECT 1 FROM changed_locations WHERE site_id=?', (site.get('id'),)).fetchone())
    pending = {}
    for record in records:
        missing = []
        for engine in engines:
            row = latest.get((record['id'], engine))
            meta = json.loads(row['metadata']) if row else {}
            matches = row is not None and Path(row['scores_path']).is_file() and meta.get('model_sha256') == hashes[engine]
            if matches and REGISTRY[engine]['main_capable']:
                matches = bool(row['embedded'])
            if engine in classes:
                matches &= meta.get('classes_sha256') == classes[engine]
            if engine == 'birdnet':
                if changed or any(key in meta for key in ('latitude', 'longitude')):
                    matches &= all(meta.get(key) == site[key] for key in ('latitude', 'longitude'))
            if not matches:
                missing.append(engine)
        if missing:
            pending[record['id']] = missing
    return pending


def load_dense(models, engine):
    detector, sha = load_onnx(models, engine)
    path = Path(models) / REGISTRY[engine]['files'][1]
    v3 = engine == 'birdnet-v3'
    with path.open(newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream, delimiter=';' if v3 else ','))
    count = REGISTRY[engine]['species_count']
    names = [r['sci_name' if v3 else 'scientific_name'] for r in rows]
    if ([int(r['idx' if v3 else 'index']) for r in rows] != list(range(count))
            or len(set(names)) != count):
        raise CatalogError('invalid_scores')
    allowed = names
    if v3:
        japan = {name.partition('_')[0] for name in candidate_filter()['species']}
        allowed = [name for name in names if name in japan]
    meta = dict(model=REGISTRY[engine]['name'], model_sha256=sha,
                classes_sha256=digest(path), window_s=window_s(engine),
                score_type=REGISTRY[engine]['score_type'], species=allowed,
                stored_top_k=None, display_top_k=5, last_window='zero_pad',
                resampling='scipy.resample_poly')
    if v3:
        meta['candidate_filter'] = candidate_filter()
    return detector, names, meta


def infer_onnx(detector, engine, audio, count, cancel=None):
    """Shared real inference/windowing path for analysis and the local benchmark."""
    vectors, batches = [], []
    batch_size = 1 if engine == 'perch' else 32
    for start in range(0, len(audio), batch_size * 160000):
        if cancel and cancel.is_set():
            raise CatalogError('cancelled')
        chunk = audio[start:start + batch_size * 160000]
        waveform = np.pad(chunk, (0, -len(chunk) % 160000)).reshape(-1, 160000).astype(np.float32)
        if engine == 'perch':
            embedding, scores = detector.run(['embedding', 'label'], {'inputs': waveform})
        elif engine == 'birdnet-v3':
            scores, = detector.run(['predictions'], {'input': np.clip(waveform, -1, 1)})
            embedding = None
            if np.any((scores < 0) | (scores > 1)):
                raise CatalogError('invalid_scores')
        else:
            logits, embedding = detector.run(['logits', 'embedding'], {'waveform': np.clip(waveform, -1, 1)})
            scores = 1 / (1 + np.exp(-np.clip(logits.astype(np.float64), -709, 709)))
        if scores.shape != (len(waveform), count) or not np.isfinite(scores).all():
            raise CatalogError('invalid_scores')
        if embedding is not None:
            if (embedding.shape != (len(waveform), 1536) or not np.isfinite(embedding).all()
                    or np.any(np.linalg.norm(embedding, axis=1) == 0)):
                raise CatalogError('invalid_scores')
            vectors.extend(embedding)
        batches.append(scores)
    return np.concatenate(batches).astype(np.float32), vectors or None


@lru_cache(maxsize=1)
def candidate_filter():
    path = Path(__file__).parent / "data/japan-birds.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    return {"source": data["sources"], "sha256": digest(path), "species": data["species"]}


def species_mapping_sha256():
    return digest(Path(__file__).parent / "data/japan-birds.json")


def save(root, job, recording, engine, meta, scores, candidates, embeddings=None):
    output = Path(root) / "analyses" / str(job)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"{recording['id']}-{engine}.npz"
    np.savez_compressed(path, **scores)
    with connect(root) as db:
        aid = db.execute("INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,?,?,?)",
                         (job, recording["id"], engine, json.dumps(meta), str(path))).lastrowid
        db.executemany("INSERT INTO candidates(analysis_id,start_s,end_s,label,score) VALUES(?,?,?,?,?)",
                       ((aid, *row) for row in candidates))
        if embeddings is not None and len(embeddings):
            vectors = np.asarray(embeddings, dtype='<f4')
            width = window_s(engine)
            first = db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,0,?)',
                               (aid, min(width, recording['seconds']))).lastrowid
            db.executemany('INSERT INTO segments(id,analysis_id,start_s,end_s) VALUES(?,?,?,?)',
                ((first+i, aid, i*width, min((i+1)*width, recording['seconds'])) for i in range(1, len(vectors))))
            db.executemany('INSERT INTO vectors(rowid,embedding) VALUES(?,?)',
                           ((first+i, vector.tobytes()) for i, vector in enumerate(vectors)))

    # Build shared display windows before analysis is reported as complete.
    from reports import classification_windows
    allowed = (meta.get('candidate_filter') or {}).get('species') if engine == 'perch' else meta.get('species')
    if allowed is not None:
        classification_windows({'scores_path':str(path),'engine':engine},recording['seconds'],allowed)


@contextmanager
def prepare_models(models, engines, site, progress=None):
    """Load once and keep inference sessions alive across recording jobs."""
    models = Path(models)
    progress = progress or (lambda stage, done: None)
    labels_path = models / "perch-labels/assets/labels.csv"
    use_birdnet = 'birdnet' in engines
    progress("loading_models", 0)
    acoustic = bird_meta = species = None
    if use_birdnet:
        configure_birdnet(models)
        import birdnet
        from birdnet.acoustic.models.v2_4.tf import AcousticTFDownloaderV2_4
        from birdnet.geo.models.v2_4.tf import GeoTFDownloaderV2_4
        if not (AcousticTFDownloaderV2_4._check_acoustic_model_available("fp32")
                and GeoTFDownloaderV2_4._check_geo_model_available()):
            raise CatalogError("models_missing")
        acoustic = birdnet.load("acoustic", "2.4", "tf", library="litert")
        geo = birdnet.load("geo", "2.4", "tf", library="litert")
        progress("loading_geo", 0)
        # ponytail: year-round geo until seasonal filtering is specified and checked.
        species = geo.predict(site["latitude"], site["longitude"], week=None,
                              min_confidence=0.03).to_structured_array()["species_name"].tolist()
        mapped = birdnet_species_mapping()
        species = [label for label in species if str(label).partition('_')[0] in mapped]
        if not species:
            raise CatalogError("no_geo_species")
        bird_meta = {"model": "BirdNET 2.4 FP32 LiteRT", "model_sha256": digest(acoustic.model_path),
                     "geo_sha256": digest(geo.model_path), "latitude": site["latitude"],
                     "longitude": site["longitude"], "week": None, "geo_threshold": 0.03,
                     "species": species, "species_mapping_sha256": species_mapping_sha256(),
                     "score_type": "sigmoid_uncalibrated", "window_s": 3,
                     "stored_threshold": 0, "stored_top_k": None, "display_top_k": 5,
                     "display_threshold": 0.1}
    session = None
    if 'perch' in engines:
        progress("loading_perch", 0)
        session, model_sha = load_perch(models)
        labels = perch_labels(models)
        perch_meta = {"model": "Perch V2 no_dft", "model_sha256": model_sha,
                      "labels_sha256": digest(labels_path), "window_s": 5,
                      "score_type": "logit", "stored_top_k": None, "display_top_k": 5,
                      "candidate_filter": candidate_filter(),
                      "resampling": "scipy.resample_poly", "last_window": "zero_pad"}
    detectors = {}
    if session is not None:
        detectors['perch'] = (session, labels, perch_meta)
    for engine in REGISTRY:
        if engine in engines and engine not in ('perch', 'birdnet'):
            progress('loading_' + engine, 0)
            detectors[engine] = load_dense(models, engine)
    if use_birdnet:
        progress("loading_birdnet", 0)
    bird_context = acoustic.predict_session(n_workers=1, batch_size=1, top_k=None, max_n_files=1,
        default_confidence_threshold=0, custom_species_list=species) if acoustic else nullcontext()
    # Reuse BirdNET's public session when this edition includes it.
    with bird_context as bird_session:
        if bird_session is not None:
            detectors['birdnet'] = (bird_session, None, bird_meta)
        yield detectors


def run(root, models, job, records, site, cancel, pending=None, loaded=None, recording_times=None):
    def progress(stage, done):
        with connect(root) as db:
            db.execute("UPDATE jobs SET status='running',stage=?,done=? WHERE id=?", (stage, done, job))

    try:
        # Model acquisition is an explicit setup step, never a hidden job download.
        models = Path(models)
        pending = plan(root, models, records, site) if pending is None else pending
        engines = {engine for pairs in pending.values() for engine in pairs}
        with connect(root) as db:
            db.execute('UPDATE jobs SET total=? WHERE id=?', (sum(map(len, pending.values())), job))
        done = 0
        model_context = prepare_models(models, engines, site, progress) if loaded is None else nullcontext(loaded)
        with model_context as detectors:
            included_labels = {engine:np.flatnonzero(np.isin(names, meta['species']))
                for engine, (_, names, meta) in detectors.items() if engine not in ('perch', 'birdnet')}
            for record in records:
                if cancel.is_set():
                    break
                pairs = pending.get(record['id'], [])
                if not pairs:
                    continue
                started = perf_counter() if recording_times is not None else None
                try:
                    path = checked_source(record)
                    audio = None
                    try:
                        native, rate = sf.read(path, dtype='float32')
                    except (OSError, RuntimeError):
                        raise CatalogError('unreadable_audio') from None
                    if any(engine != 'birdnet' for engine in pairs):
                        audio = resample(native, rate)
                    if 'birdnet' in pairs:
                        bird_session, _, bird_meta = detectors['birdnet']
                        progress("birdnet", done)
                        # BirdNET retains its native duration and per-window 48-kHz conversion.
                        result = bird_session.run_arrays((native, rate))
                        if len(result.unprocessable_inputs):
                            raise CatalogError("unreadable_audio")
                        rows = result.to_structured_array()
                        if not np.isfinite(rows["confidence"]).all():
                            raise CatalogError("invalid_scores")
                        groups = {}
                        for row in rows:
                            start, end = float(row["start_time"]), min(float(row["end_time"]), record["seconds"])
                            if row["confidence"] >= 0.1:
                                groups.setdefault(start, []).append((start, end, str(row["species_name"]), float(row["confidence"])))
                        candidates = [row for group in groups.values() for row in sorted(group, key=lambda r: -r[3])[:5]]
                        checked_source(record)
                        save(root, job, record, "birdnet", bird_meta,
                             {"start_s": rows["start_time"], "end_s": rows["end_time"],
                              "labels": rows["species_name"].astype(str), "scores": rows["confidence"]}, candidates)
                        done += 1
                    del native
                    if cancel.is_set():
                        break
                    if not any(engine != 'birdnet' for engine in pairs):
                        continue
                    for engine in pairs:
                        if engine == 'birdnet' or cancel.is_set():
                            continue
                        progress(engine, done)
                        detector, names, meta = detectors[engine]
                        scores, vectors = infer_onnx(detector, engine, audio, len(names), cancel)
                        candidates = []
                        if engine != 'perch':
                            included = included_labels[engine]
                            candidates = [(i * 5, min((i + 1) * 5, record['seconds']), names[j], float(row[j]))
                                          for i, row in enumerate(scores)
                                          for j in included[np.argsort(-row[included], kind='stable')[:5]]]
                        checked_source(record)
                        save(root, job, record, engine, meta,
                             {'logits' if engine == 'perch' else 'scores': scores, 'labels': np.array(names)},
                             candidates, vectors)
                        done += 1
                        progress(engine, done)
                finally:
                    if recording_times is not None:
                        recording_times.append(perf_counter() - started)
        if not cancel.is_set():
            progress('preparing_results', done)
            with connect(root) as db:
                dataset_id = db.execute('SELECT dataset_id FROM jobs WHERE id=?', (job,)).fetchone()[0]
            from results_workflow import overview
            overview(root, dataset_id)
        with connect(root) as db:
            db.execute("UPDATE jobs SET status=?,stage='finished' WHERE id=?",
                       ("cancelled" if cancel.is_set() else "complete", job))
    except Exception as error:
        if isinstance(error, CatalogError) and str(error) == 'cancelled':
            with connect(root) as db:
                db.execute("UPDATE jobs SET status='cancelled',stage='finished' WHERE id=?", (job,))
            return
        import traceback
        traceback.print_exc()
        with connect(root) as db:
            db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?",
                       (str(error) if isinstance(error, CatalogError) else "analysis_failed", job))
