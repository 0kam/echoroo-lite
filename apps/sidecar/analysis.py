"""The real-audio PoC pipeline, with per-recording persistence."""
import csv
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import soundfile as sf

from audio import resample

from storage import CatalogError, checked_source, connect


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_perch(models):
    # Disable the non-Windows uploader before ORT initializes (also avoids its shutdown race).
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    import onnxruntime as ort
    ort.disable_telemetry_events()
    path = Path(models) / "perch/perch_v2_no_dft.onnx"
    if not path.is_file():
        raise CatalogError("models_missing")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 8
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), options, providers=["CPUExecutionProvider"]), digest(path)


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
                                 (aid, i * 5, min((i + 1) * 5, recording["seconds"]))).lastrowid
                db.execute("INSERT INTO vectors(rowid,embedding) VALUES(?,?)",
                           (sid, vector.astype("<f4").tobytes()))

    # Build shared display windows before analysis is reported as complete.
    from reports import classification_windows
    allowed = meta.get('species') if engine == 'birdnet' else (meta.get('geo_filter') or {}).get('species')
    if allowed is not None:
        classification_windows({'scores_path':str(path),'engine':engine},recording['seconds'],allowed)


def run(root, models, job, records, site, cancel):
    def progress(stage, done):
        with connect(root) as db:
            db.execute("UPDATE jobs SET status='running',stage=?,done=? WHERE id=?", (stage, done, job))

    try:
        # Model acquisition is an explicit setup step, never a hidden job download.
        models = Path(models)
        model_path = models / "perch/perch_v2_no_dft.onnx"
        labels_path = models / "perch-labels/assets/labels.csv"
        if not all(p.is_file() for p in (model_path, labels_path)) or not list((models / "birdnet").rglob("*.tflite")):
            raise CatalogError("models_missing")
        os.environ["BIRDNET_APP_DATA"] = str(models / "birdnet")
        import birdnet
        from birdnet.acoustic.models.v2_4.tf import AcousticTFDownloaderV2_4
        from birdnet.geo.models.v2_4.tf import GeoTFDownloaderV2_4

        # birdnet 1.1.1 exposes no offline flag; reuse its cache checks before load.
        if not (AcousticTFDownloaderV2_4._check_acoustic_model_available("fp32")
                and GeoTFDownloaderV2_4._check_geo_model_available()):
            raise CatalogError("models_missing")

        progress("loading_models", 0)
        acoustic = birdnet.load("acoustic", "2.4", "tf", library="litert")
        geo = birdnet.load("geo", "2.4", "tf", library="litert")
        progress("loading_geo", 0)
        # ponytail: year-round geo until seasonal filtering is specified and checked.
        species = geo.predict(site["latitude"], site["longitude"], week=None,
                              min_confidence=0.03).to_structured_array()["species_name"].tolist()
        if not species:
            raise CatalogError("no_geo_species")
        bird_meta = {"model": "BirdNET 2.4 FP32 LiteRT", "model_sha256": digest(acoustic.model_path),
                     "geo_sha256": digest(geo.model_path), "latitude": site["latitude"],
                     "longitude": site["longitude"], "week": None, "geo_threshold": 0.03,
                     "species": species, "score_type": "sigmoid_uncalibrated", "window_s": 3,
                     "stored_threshold": 0, "stored_top_k": None, "display_top_k": 5,
                     "display_threshold": 0.1}
        progress("loading_perch", 0)
        session, model_sha = load_perch(models)
        with labels_path.open(newline="", encoding="utf-8") as stream:
            labels = [r[0] for r in list(csv.reader(stream))[1:]]
        perch_meta = {"model": "Perch V2 no_dft", "model_sha256": model_sha,
                      "labels_sha256": digest(labels_path), "window_s": 5,
                      "score_type": "logit", "stored_top_k": None, "display_top_k": 5,
                      "geo_filter": {key: bird_meta[key] for key in
                                     ("geo_sha256", "latitude", "longitude", "week", "geo_threshold", "species")},
                      "resampling": "scipy.resample_poly", "last_window": "zero_pad"}
        done = 0
        progress("loading_birdnet", 0)
        # Reuse BirdNET's public session: one worker for the job, one file persisted per run.
        with acoustic.predict_session(n_workers=1, batch_size=1, top_k=None, max_n_files=1,
                                      default_confidence_threshold=0, custom_species_list=species) as bird_session:
            for record in records:
                if cancel.is_set():
                    break
                path = checked_source(record)
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
                progress("perch", done)
                if cancel.is_set():
                    break
                audio, rate = sf.read(path, dtype="float32")
                audio = resample(audio, rate)
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
        with connect(root) as db:
            db.execute("UPDATE jobs SET status=?,stage='finished' WHERE id=?",
                       ("cancelled" if cancel.is_set() else "complete", job))
    except Exception as error:
        import traceback
        traceback.print_exc()
        with connect(root) as db:
            db.execute("UPDATE jobs SET status='failed',error=? WHERE id=?",
                       (str(error) if isinstance(error, CatalogError) else "analysis_failed", job))
