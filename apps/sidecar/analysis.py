"""The real-audio PoC pipeline, with per-recording persistence."""
import csv
import hashlib
import json
import os
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import soundfile as sf

from audio import resample
from models import REGISTRY, available, enabled_engines, window_s

from species import birdnet_species_mapping
from storage import CatalogError, checked_source, connect


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
    if not available(models, 'perch'):
        raise CatalogError('models_missing')
    engines = enabled_engines(root, models)
    hashes = {engine: digest(Path(models) / REGISTRY[engine]['files'][0]) for engine in engines}
    classes = {engine: digest(Path(models) / REGISTRY[engine]['files'][1])
               for engine in engines if REGISTRY[engine]['experimental']}
    with connect(root) as db:
        latest = {(r['recording_id'], r['engine']): r for r in db.execute('''SELECT * FROM analyses
            WHERE id IN (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)''')}
        changed = bool(db.execute('SELECT 1 FROM changed_locations WHERE site_id=?', (site.get('id'),)).fetchone())
    pending = {}
    for record in records:
        missing = []
        for engine in engines:
            row = latest.get((record['id'], engine))
            meta = json.loads(row['metadata']) if row else {}
            matches = row is not None and Path(row['scores_path']).is_file() and meta.get('model_sha256') == hashes[engine]
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
        if embeddings is not None:
            for i, vector in enumerate(embeddings):
                sid = db.execute("INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,?,?)",
                                 (aid, i * window_s(engine), min((i + 1) * window_s(engine), recording["seconds"]))).lastrowid
                db.execute("INSERT INTO vectors(rowid,embedding) VALUES(?,?)",
                           (sid, vector.astype("<f4").tobytes()))

    # Build shared display windows before analysis is reported as complete.
    from reports import classification_windows
    allowed = (meta.get('candidate_filter') or {}).get('species') if engine == 'perch' else meta.get('species')
    if allowed is not None:
        classification_windows({'scores_path':str(path),'engine':engine},recording['seconds'],allowed)


def run(root, models, job, records, site, cancel, pending=None):
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
        labels_path = models / "perch-labels/assets/labels.csv"
        use_birdnet = 'birdnet' in engines
        progress("loading_models", 0)
        acoustic = bird_meta = species = None
        if use_birdnet:
            os.environ["BIRDNET_APP_DATA"] = str(models / "birdnet")
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
        kinglets = {}
        for engine in REGISTRY:
            if engine not in engines or not REGISTRY[engine]['experimental']:
                continue
            progress('loading_' + engine, 0)
            detector, sha = load_onnx(models, engine)
            classes_path = models / REGISTRY[engine]['files'][1]
            with classes_path.open(newline='', encoding='utf-8') as stream:
                rows = list(csv.DictReader(stream))
            if ([int(row['index']) for row in rows] != list(range(317)) or
                    len({row['scientific_name'] for row in rows}) != 317):
                raise CatalogError('invalid_scores')
            names = [row['scientific_name'] for row in rows]
            meta = dict(model=REGISTRY[engine]['name'], model_sha256=sha,
                        classes_sha256=digest(classes_path), window_s=window_s(engine),
                        score_type=REGISTRY[engine]['score_type'], species=names,
                        stored_top_k=None, display_top_k=5, last_window='zero_pad',
                        resampling='scipy.resample_poly')
            kinglets[engine] = (detector, names, meta)
        done = 0
        if use_birdnet:
            progress("loading_birdnet", 0)
        bird_context = acoustic.predict_session(n_workers=1, batch_size=1, top_k=None, max_n_files=1,
            default_confidence_threshold=0, custom_species_list=species) if acoustic else nullcontext()
        # Reuse BirdNET's public session when this edition includes it.
        with bird_context as bird_session:
            for record in records:
                if cancel.is_set():
                    break
                pairs = pending.get(record['id'], [])
                if not pairs:
                    continue
                path = checked_source(record)
                if bird_session and 'birdnet' in pairs:
                    progress("birdnet", done)
                    result = bird_session.run(path)
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
                if cancel.is_set():
                    break
                if not any(engine != 'birdnet' for engine in pairs):
                    continue
                audio, rate = sf.read(path, dtype="float32")
                audio = resample(audio, rate)
                for engine, (detector, names, meta) in kinglets.items():
                    if engine not in pairs or cancel.is_set():
                        continue
                    progress(engine, done)
                    batches = []
                    # Bound waveform memory while retaining the zero-padded final window.
                    for start in range(0, len(audio), 32 * 160000):
                        if cancel.is_set():
                            break
                        chunk = audio[start:start + 32 * 160000]
                        waveform = np.pad(chunk, (0, -len(chunk) % 160000)).reshape(-1, 160000)
                        logits, = detector.run(['logits'], {'waveform': np.clip(waveform, -1, 1).astype(np.float32)})
                        if logits.shape != (len(waveform), len(names)) or not np.isfinite(logits).all():
                            raise CatalogError('invalid_scores')
                        batches.append(1 / (1 + np.exp(-np.clip(logits.astype(np.float64), -709, 709))))
                    if cancel.is_set():
                        break
                    scores = np.concatenate(batches).astype(np.float32)
                    candidates = [(i * 5, min((i + 1) * 5, record['seconds']), names[j], float(row[j]))
                                  for i, row in enumerate(scores) for j in np.argsort(-row, kind='stable')[:5]]
                    checked_source(record)
                    # Kinglet stores a dense window x class sigmoid matrix, without embeddings.
                    save(root, job, record, engine, meta, {'scores': scores, 'labels': np.array(names)}, candidates)
                    done += 1
                    progress(engine, done)
                if cancel.is_set():
                    break
                if 'perch' not in pairs:
                    continue
                progress("perch", done)
                vectors, logits = [], []
                for start in range(0, len(audio), 160000):
                    if cancel.is_set():
                        break
                    window = audio[start:start + 160000]
                    window = np.pad(window, (0, 160000 - len(window)))
                    embedding, scores = session.run(["embedding", "label"], {"inputs": window[None].astype(np.float32)})
                    if (embedding.shape != (1, 1536) or scores.shape != (1, len(labels))
                            or not np.isfinite(embedding).all() or not np.isfinite(scores).all()
                            or np.linalg.norm(embedding) == 0):
                        raise CatalogError("invalid_scores")
                    vectors.append(embedding[0])
                    logits.append(scores[0])
                if cancel.is_set():
                    break
                checked_source(record)
                save(root, job, record, "perch", perch_meta,
                     {"logits": np.array(logits), "labels": np.array(labels)}, [], vectors)
                done += 1
                progress("perch", done)
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
        import traceback
        traceback.print_exc()
        with connect(root) as db:
            db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?",
                       (str(error) if isinstance(error, CatalogError) else "analysis_failed", job))
