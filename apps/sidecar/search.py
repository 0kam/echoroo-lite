"""Exact similarity search over the latest compatible embeddings in one dataset."""
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from analysis import load_perch, load_onnx
from models import main_engine
from audio import read_interval, source
from storage import CatalogError, checked_source, connect


def query_embeddings(models, row, start, end, loaded=None, cancel=None, engine='perch'):
    samples, interval = read_interval(row, start, end, maximum=30)
    if end - start < 0.2 - 1e-9:
        raise CatalogError("query_interval")
    if not np.any(samples):
        raise CatalogError("silent_query")
    session, model_sha = loaded or (load_perch(models) if engine == 'perch' else load_onnx(models, engine))
    vectors, windows = [], []
    for offset in range(0, len(samples), 160000):
        if cancel and cancel.is_set():raise CatalogError('cancelled')
        chunk = samples[offset:offset + 160000]
        padded = np.pad(chunk, (0, 160000 - len(chunk)))
        if engine != 'perch':
            padded = np.clip(padded, -1, 1)
        vector = session.run(["embedding"], {'inputs' if engine == 'perch' else 'waveform': padded[None].astype(np.float32)})[0][0]
        if vector.shape != (1536,) or not np.isfinite(vector).all() or np.linalg.norm(vector) == 0:
            raise CatalogError("invalid_scores")
        vectors.append(vector.astype("<f4"))
        windows.append({"start_s": interval["start_s"] + offset / 32000,
                        "end_s": min(interval["start_s"] + (offset + len(chunk)) / 32000, interval["end_s"]),
                        "padding_s": (160000 - len(chunk)) / 32000})
    checked_source(row)
    return vectors, {**interval, "windows": windows, "engine": engine, "model_sha256": model_sha,
                     "aggregation": "maximum_cosine", "source_size": row["size"], "source_mtime_ns": row["mtime_ns"]}


def compatible_analyses(db, dataset_id, model_sha, engine='perch'):
    return db.execute('''SELECT a.id AS analysis_id, r.* FROM analyses a JOIN site_recordings r ON r.id=a.recording_id
        WHERE r.scope_id=? AND a.id IN (SELECT MAX(id) FROM analyses WHERE engine=? GROUP BY recording_id)
        AND json_extract(a.metadata, '$.model_sha256')=?
        AND EXISTS (SELECT 1 FROM segments s WHERE s.analysis_id=a.id)''',
        (dataset_id, engine, model_sha)).fetchall()


def search_context(db, dataset_id, model_sha, first=None, last=None, engine='perch'):
    if first and last and first > last:
        raise CatalogError('search_period')
    records = db.execute('SELECT id,recorded_at,timezone FROM site_recordings WHERE scope_id=?', (dataset_id,)).fetchall()
    selected = set()
    for row in records:
        day = datetime.fromisoformat(row['recorded_at']).astimezone(ZoneInfo(row['timezone'])).date()
        if (first is None or first <= day) and (last is None or day <= last):
            selected.add(row['id'])
    if not selected:
        raise CatalogError('no_search_recordings')
    return [r for r in compatible_analyses(db, dataset_id, model_sha, engine) if r['id'] in selected], len(selected)


def rank_segments(root, dataset_id, vectors, model_sha, query_source, start, end, limit, first=None, last=None, engine='perch'):
    with connect(root) as db:
        compatible, total = search_context(db, dataset_id, model_sha, first, last, engine)
        if not compatible:
            raise CatalogError("no_search_embeddings")
        # Verify sources before treating their stored embeddings as current evidence.
        for row in compatible:
            checked_source(row)
        aids = [row["analysis_id"] for row in compatible]
        marks = ",".join("?" for _ in aids)
        count = db.execute(f"SELECT COUNT(*) FROM segments WHERE analysis_id IN ({marks})", aids).fetchone()[0]
        distances = ["vec_distance_cosine(v.embedding, ?)" for _ in vectors]
        distance = distances[0] if len(distances) == 1 else f"min({','.join(distances)})"
        # ponytail: exact scan inside the dataset; add vec0 partitions if annual-scale search is too slow.
        rows = db.execute(f"""SELECT s.id AS segment_id, s.analysis_id, s.start_s, s.end_s,
            r.id AS recording_id, r.path, r.seconds, r.recorded_at, r.timezone, {distance} AS distance
            FROM segments s JOIN vectors v ON v.rowid=s.id JOIN analyses a ON a.id=s.analysis_id
            JOIN recordings r ON r.id=a.recording_id WHERE s.analysis_id IN ({marks})
            AND NOT (r.path=? AND s.start_s < ? AND s.end_s > ?)
            ORDER BY distance, s.id LIMIT ?""",
                          [*(v.tobytes() for v in vectors), *aids, query_source["path"], end, start, limit]).fetchall()
    return [{**dict(row), "similarity": min(1.0, max(-1.0, 1 - row["distance"]))} for row in rows], {
        "total_recordings": total, "searched_recordings": len(compatible), "searched_segments": count,
        "unsearched_recordings": total - len(compatible)}


def search(root, models, body, cancel=None):
    engine = main_engine(root)
    row = source(root, body.source_kind, body.source_id)
    vectors, query = query_embeddings(models, row, body.start_s, body.end_s, cancel=cancel, engine=engine)
    if cancel and cancel.is_set():raise CatalogError('cancelled')
    hits, coverage = rank_segments(root, body.dataset_id, vectors, query["model_sha256"], row,
                                   query["start_s"], query["end_s"], body.limit, body.start_date, body.end_date, engine)
    return {"source": row, "query": query, "hits": hits, 'start_date': body.start_date,
            'end_date': body.end_date, 'date_scope': 'recording_start_local', **coverage}
