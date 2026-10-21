"""Small one-species classifiers over fixed Perch embeddings.

Material: 5-second search hits labelled "voice" / "no voice" on the model page,
plus "not heard" answers from 5-second checks. A built model scores and is
checked on every 5-second piece of the site.
"""
import hashlib
import json
import os
import shutil
import tempfile
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from reports import analysis_metadata, calendar_cells, effort, finalize_cells, window_in_scope, window_key
from species import names_for
from storage import CatalogError, checked_source, connect

MIN_CLASS_EXAMPLES = 3
L2 = 1.0
FEATURE_SPEC = 'perch_embedding_1536_l2_v1'


def _digest(path):
    value = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def _normalise(matrix):
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if matrix.ndim != 2 or matrix.shape[1] != 1536 or not np.isfinite(matrix).all() or np.any(norms == 0):
        raise CatalogError('invalid_vectors')
    return matrix / norms


def _fit(x, y):
    def objective(value):
        weights, intercept = value[:-1], value[-1]
        logits = x @ weights + intercept
        return float((np.logaddexp(0, logits) - y * logits).mean() + L2 * np.dot(weights, weights) / (2 * len(y)))

    def gradient(value):
        weights, intercept = value[:-1], value[-1]
        residual = expit(x @ weights + intercept) - y
        return np.r_[x.T @ residual / len(y) + L2 * weights / len(y), residual.mean()]
    result = minimize(objective, np.zeros(x.shape[1] + 1), jac=gradient, method='L-BFGS-B')
    if not result.success or not np.isfinite(result.x).all():
        raise CatalogError('custom_model_fit_failed')
    return result.x[:-1].astype(np.float32), float(result.x[-1])


def _vector_batches(root, analysis_ids, size=256):
    if not analysis_ids:
        return
    with connect(root) as db:
        cursor = db.execute('''SELECT s.id AS segment_id,s.analysis_id,a.recording_id,s.start_s,s.end_s,v.embedding
            FROM segments s JOIN analyses a ON a.id=s.analysis_id JOIN vectors v ON v.rowid=s.id
            WHERE s.analysis_id IN (SELECT value FROM json_each(?)) AND ABS(s.end_s-s.start_s-5)<0.000001
            ORDER BY a.recording_id,s.start_s''', (json.dumps(analysis_ids),))
        while rows := cursor.fetchmany(size):
            valid, vectors = [], []
            for row in rows:
                vector = np.frombuffer(row['embedding'], dtype='<f4')
                if len(vector) == 1536 and np.isfinite(vector).all() and np.linalg.norm(vector) > 0:
                    valid.append({k: row[k] for k in ('segment_id', 'analysis_id', 'recording_id', 'start_s', 'end_s')})
                    vectors.append(vector)
            if valid:
                yield valid, _normalise(np.stack(vectors))


def _perch_analyses(db, ctx):
    return [dict(r) for r in db.execute('''SELECT a.* FROM analyses a JOIN site_recordings r ON r.id=a.recording_id
        WHERE r.scope_id=? AND a.engine='perch' AND a.id IN (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)
        ORDER BY a.recording_id''', (ctx['dataset']['id'],))]


def _materials(db, ctx, species_id):
    """Labelled 5-second pieces from search answers, presence marks and checks."""
    from search_runs import site_run
    run = site_run(db, ctx['site']['id'], species_id)
    if run is None:
        raise CatalogError('custom_search_needed')
    labels = {r['segment_id']: {'label': r['label'], 'origin': 'search'} for r in db.execute(
        'SELECT segment_id,label FROM model_labels WHERE run_id=?', (run['id'],))}
    for row in db.execute('''SELECT h.segment_id FROM presence_marks m
            JOIN search_hits h ON h.run_id=m.run_id JOIN segments s ON s.id=h.segment_id
            JOIN analyses a ON a.id=s.analysis_id
            WHERE m.run_id=? AND m.species_id=? AND a.recording_id=m.recording_id
              AND s.start_s=m.start_s AND s.end_s=m.end_s''', (run['id'], species_id)):
        labels[row['segment_id']] = {'label': 'target', 'origin': 'presence'}
    analyses = {r['recording_id']: r['id'] for r in _perch_analyses(db, ctx)}
    for row in db.execute('''SELECT DISTINCT i.recording_id,i.start_s,i.end_s FROM validation_items i
            JOIN validation_sessions v ON v.id=i.session_id JOIN datasets d ON d.id=v.dataset_id
            JOIN window_answers a ON a.id=(SELECT MAX(id) FROM window_answers w
                WHERE w.species_id=v.species_id AND w.recording_id=i.recording_id
                  AND w.start_s=i.start_s AND w.end_s=i.end_s)
            WHERE d.site_id=? AND v.species_id=? AND ABS(i.end_s-i.start_s-5)<0.000001 AND a.verdict='other' ''',
            (ctx['site']['id'], species_id)):
        for segment in db.execute('''SELECT id FROM segments WHERE analysis_id=? AND start_s=? AND end_s=?
                AND ABS(end_s-start_s-5)<0.000001''', (analyses.get(row['recording_id']), row['start_s'], row['end_s'])):
            # A search label on the same piece is the more specific answer.
            labels.setdefault(segment[0], {'label': 'other', 'origin': 'check'})
    return run, labels


def readiness(root, ctx, species_id, limit=30, offset=0):
    from search_runs import top_hits
    with connect(root) as db:
        run, labels = _materials(db, ctx, species_id)
        chosen = {segment: value['label'] for segment, value in labels.items() if value['origin'] != 'check'}
        hits = [{**hit, 'label': chosen.get(hit['segment_id'])} for hit in top_hits(db, run['id'], limit, offset)]
        total = db.execute('SELECT COUNT(*) FROM search_hits WHERE run_id=?', (run['id'],)).fetchone()[0]
    zone = ZoneInfo(ctx['site']['timezone'])
    for hit in hits:
        hit['local_time'] = window_key(hit, hit['start_s'], zone)[1]
    positive = sum(value['label'] == 'target' for value in labels.values())
    negative = sum(value['label'] == 'other' for value in labels.values())
    negative_from_checks = sum(value == {'label': 'other', 'origin': 'check'} for value in labels.values())
    positive_from_presence = sum(value == {'label': 'target', 'origin': 'presence'} for value in labels.values())
    reasons = [reason for reason, missing in [('custom_positive_examples_needed', positive < MIN_CLASS_EXAMPLES),
                                              ('custom_negative_examples_needed', negative < MIN_CLASS_EXAMPLES)] if missing]
    return {'run_id': run['id'], 'species': names_for_id(root, species_id), 'hits': hits, 'hit_count': total,
            'examples': {'positive_5s': positive, 'negative_5s': negative, 'negative_from_checks': negative_from_checks,
                         'positive_from_presence': positive_from_presence},
            'requirements': {'positive_5s': MIN_CLASS_EXAMPLES, 'negative_5s': MIN_CLASS_EXAMPLES},
            'can_train': not reasons, 'reasons': reasons, 'models': list_models(root, ctx, species_id, every=True)}


def names_for_id(root, species_id):
    with connect(root) as db:
        row = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
    if row is None:
        raise CatalogError('not_found')
    return names_for(row)


def set_label(root, run_id, segment_id, label):
    with connect(root) as db:
        if not db.execute('SELECT 1 FROM search_hits WHERE run_id=? AND segment_id=?', (run_id, segment_id)).fetchone():
            raise CatalogError('not_found')
        db.execute('DELETE FROM model_labels WHERE run_id=? AND segment_id=?', (run_id, segment_id))
        if label is not None:
            db.execute('INSERT INTO model_labels(run_id,segment_id,label) VALUES(?,?,?)', (run_id, segment_id, label))
        db.execute('DELETE FROM workflow_cache')
    return {'segment_id': segment_id, 'label': label}


def _score_rows(analysis_ids, recording_ids, starts, ends, scores):
    return [{'analysis_id': int(aid), 'recording_id': int(rid), 'start_s': float(start), 'end_s': float(end),
             'score': float(score), 'example_start_s': float(start)}
            for aid, rid, start, end, score in zip(analysis_ids, recording_ids, starts, ends, scores)
            if abs(float(end) - float(start) - 5) < 1e-6]


def create(root, ctx, species_id, cancel=None):
    state = readiness(root, ctx, species_id, limit=0)
    if not state['can_train']:
        raise CatalogError(state['reasons'][0])
    with connect(root) as db:
        run, labels = _materials(db, ctx, species_id)
        analyses = _perch_analyses(db, ctx)
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],))}
    expected = {analysis_metadata(a['metadata'])['model_sha256'] for a in analyses}
    if len(expected) != 1:
        raise CatalogError('custom_perch_mixed' if expected else 'custom_perch_missing')
    zone = ZoneInfo(ctx['site']['timezone'])
    examples, vectors = [], []
    ids = [a['id'] for a in analyses]
    rows, scored = [], []
    for batch, matrix in _vector_batches(root, ids):
        if cancel and cancel.is_set():
            raise CatalogError('cancelled')
        for row, vector in zip(batch, matrix):
            if row['segment_id'] in labels:
                examples.append({**row, **labels[row['segment_id']]})
                vectors.append(vector)
    y = np.array([row['label'] == 'target' for row in examples], dtype=np.float64)
    if y.sum() < MIN_CLASS_EXAMPLES or len(y) - y.sum() < MIN_CLASS_EXAMPLES:
        raise CatalogError('custom_positive_examples_needed' if y.sum() < MIN_CLASS_EXAMPLES else 'custom_negative_examples_needed')
    weights, intercept = _fit(_normalise(np.stack(vectors)), y)
    for batch, matrix in _vector_batches(root, ids):
        if cancel and cancel.is_set():
            raise CatalogError('cancelled')
        for row, score in zip(batch, expit(matrix @ weights + intercept)):
            record = records.get(row['recording_id'])
            if record and window_in_scope(record, row['start_s'], row['end_s'], zone, ctx['start_date'], ctx['end_date']):
                rows.append(row)
                scored.append(float(score))
    snapshot = {'schema': 'custom_model_v2', 'species_id': species_id, 'run_id': run['id'], 'source_fingerprint': ctx['fingerprint'],
        'scope': {'site_id': ctx['site']['id'], 'start_date': ctx['start_date'], 'end_date': ctx['end_date'], 'recording_ids': sorted(records)},
        'feature': {'engine': 'perch', 'model_sha256': expected.pop(), 'spec': FEATURE_SPEC, 'window_s': 5},
        'training': {'positive_5s': int(y.sum()), 'negative_5s': int(len(y) - y.sum()), 'l2': L2,
                     'negative_from_checks': sum(e['origin'] == 'check' and e['label'] == 'other' for e in examples),
                     'positive_from_presence': sum(e['origin'] == 'presence' and e['label'] == 'target' for e in examples),
                     'examples': examples},
        'analysis_ids': ids, 'inference': {'scored_5s': len(scored)}}
    folder = Path(root) / 'custom-models'
    folder.mkdir(parents=True, exist_ok=True)
    temporary = final = None
    try:
        with connect(root) as db:
            model_id = db.execute('''INSERT INTO custom_models(dataset_id,species_id,run_id,snapshot,artifact_path,artifact_sha256,scores_path,scores_sha256)
                VALUES(?,?,?,'{}','','','','')''', (ctx['dataset']['id'], species_id, run['id'])).lastrowid
            temporary = Path(tempfile.mkdtemp(prefix=f'.{model_id}-', dir=folder))
            final = folder / str(model_id)
            np.savez_compressed(temporary / 'model.npz', weights=weights, intercept=np.array(intercept), l2=np.array(L2),
                                feature_spec=np.array(FEATURE_SPEC), perch_model_sha256=np.array(snapshot['feature']['model_sha256']))
            np.savez_compressed(temporary / 'scores.npz', segment_ids=np.asarray([r['segment_id'] for r in rows], dtype=np.int64),
                analysis_ids=np.asarray([r['analysis_id'] for r in rows], dtype=np.int64),
                recording_ids=np.asarray([r['recording_id'] for r in rows], dtype=np.int64),
                start_s=np.asarray([r['start_s'] for r in rows]), end_s=np.asarray([r['end_s'] for r in rows]), scores=np.asarray(scored))
            artifact, scores = _digest(temporary / 'model.npz'), _digest(temporary / 'scores.npz')
            os.replace(temporary, final)
            db.execute('UPDATE custom_models SET snapshot=?,artifact_path=?,artifact_sha256=?,scores_path=?,scores_sha256=? WHERE id=?',
                (json.dumps(snapshot, allow_nan=False), str(final / 'model.npz'), artifact, str(final / 'scores.npz'), scores, model_id))
            db.execute('DELETE FROM workflow_cache')
    except Exception:
        for path in (temporary, final):
            if path and path.exists():
                shutil.rmtree(path)
        raise
    return get_model(root, model_id)


def _public(root, row, current_fingerprint=None):
    snap = json.loads(row['snapshot'])

    def file_ok(path, digest):
        try:
            return Path(path).is_file() and _digest(path) == digest
        except OSError:
            return False
    files_ok = file_ok(row['artifact_path'], row['artifact_sha256']) and file_ok(row['scores_path'], row['scores_sha256'])
    scope_ok = (current_fingerprint or snap['source_fingerprint']) == snap['source_fingerprint']
    return {'id': row['id'], 'created_at': row['created_at'], 'dataset_id': row['dataset_id'], 'run_id': row['run_id'],
        'species': names_for_id(root, row['species_id']), 'scope': snap['scope'], 'feature': snap['feature'],
        'training': {k: v for k, v in snap['training'].items() if k != 'examples'}, 'inference': snap['inference'],
        'analysis_ids': snap['analysis_ids'], 'applicable': scope_ok and files_ok,
        'reason': None if scope_ok and files_ok else 'custom_model_scope_changed' if not scope_ok else 'custom_model_files_missing',
        'artifact_sha256': row['artifact_sha256'], 'scores_sha256': row['scores_sha256']}


def get_model(root, model_id):
    from results_workflow import fingerprint
    with connect(root) as db:
        row = db.execute('SELECT * FROM custom_models WHERE id=?', (model_id,)).fetchone()
    if not row:
        raise CatalogError('not_found')
    return _public(root, row, fingerprint(root, row['dataset_id']))


def training_windows(root, model_id):
    with connect(root) as db:
        row = db.execute('SELECT snapshot FROM custom_models WHERE id=?', (model_id,)).fetchone()
    if not row:
        raise CatalogError('not_found')
    return {(e['recording_id'], float(e['start_s']), float(e['end_s'])) for e in json.loads(row['snapshot'])['training']['examples']}


def list_models(root, ctx, species_id=None, every=False):
    """This site's models for the displayed period, newest first; by default the newest usable one per species."""
    with connect(root) as db:
        rows = db.execute('''SELECT m.* FROM custom_models m JOIN datasets d ON d.id=m.dataset_id WHERE d.site_id=?
            AND (? IS NULL OR m.species_id=?) ORDER BY m.id DESC''', (ctx['site']['id'], species_id, species_id)).fetchall()
    rows = [row for row in rows if [json.loads(row['snapshot'])['scope'][k] for k in ('start_date', 'end_date')] == [ctx['start_date'], ctx['end_date']]]
    models = [_public(root, row, ctx['fingerprint']) for row in rows]
    if every:
        return models
    chosen = {}
    for model in models:
        if model['species']['id'] not in chosen or model['applicable'] and not chosen[model['species']['id']]['applicable']:
            chosen[model['species']['id']] = model
    return sorted(chosen.values(), key=lambda model: model['id'], reverse=True)


def delete_model(root, model_id):
    """Remove what the model produced: its files, its row and its own checks."""
    from validation import delete_sessions
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        if not db.execute('SELECT 1 FROM custom_models WHERE id=?', (model_id,)).fetchone():
            raise CatalogError('not_found')
        sessions = [r[0] for r in db.execute('SELECT id FROM validation_sessions WHERE model=?', (f'custom:{model_id}',))]
        delete_sessions(db, sessions)
        db.execute('DELETE FROM custom_models WHERE id=?', (model_id,))
    # Only our own generated folder is removed, never a path read from the catalogue.
    shutil.rmtree(Path(root) / 'custom-models' / str(model_id), ignore_errors=True)
    return {'id': model_id, 'validations': len(sessions)}


def score_windows(root, model_id):
    with connect(root) as db:
        row = db.execute('SELECT * FROM custom_models WHERE id=?', (model_id,)).fetchone()
    if not row:
        raise CatalogError('not_found')
    path = Path(row['scores_path'])
    if not path.is_file() or _digest(path) != row['scores_sha256']:
        raise CatalogError('custom_model_files_missing')
    with np.load(path, allow_pickle=False) as saved:
        keys = ('segment_ids', 'analysis_ids', 'recording_ids', 'start_s', 'end_s', 'scores')
        if len({len(saved[key]) for key in keys}) != 1 or not all(np.isfinite(saved[key]).all() for key in ('start_s', 'end_s', 'scores')):
            raise CatalogError('invalid_scores')
        return _score_rows(saved['analysis_ids'], saved['recording_ids'], saved['start_s'], saved['end_s'], saved['scores'])


def report_snapshot(root, ctx, model_id, threshold):
    model = get_model(root, model_id)
    if not model['applicable']:
        raise CatalogError(model['reason'])
    windows = score_windows(root, model_id)
    zone = ZoneInfo(ctx['site']['timezone'])
    record_ids = set(model['scope']['recording_ids'])
    with connect(root) as db:
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],)) if r['id'] in record_ids}
    cells = calendar_cells(list(records.values()), zone, date.fromisoformat(ctx['start_date']), date.fromisoformat(ctx['end_date']))
    eligible = found = 0
    days, candidate_days, evidence = set(), set(), []
    for record in records.values():
        checked_source(record)
        for key, duration in effort(record, zone):
            if key in cells:
                cells[key]['analysed_seconds'] += duration
    for row in windows:
        record = records.get(row['recording_id'])
        if not record or not window_in_scope(record, row['start_s'], row['end_s'], zone, ctx['start_date'], ctx['end_date']):
            continue
        key, local = window_key(record, row['start_s'], zone)
        eligible += 1
        days.add(key[0])
        cells[key]['eligible_windows'] += 1
        cells[key]['effective_seconds'] += 5
        if threshold is not None and row['score'] >= threshold:
            found += 1
            candidate_days.add(key[0])
            cells[key]['candidate_windows'] += 1
            evidence.append({**row, 'path': record['path'], 'seconds': record['seconds'], 'recorded_at': record['recorded_at'], 'timezone': record['timezone'],
                'start_s': row['example_start_s'], 'end_s': row['example_start_s'] + 5, 'window_start_s': row['start_s'], 'window_end_s': row['end_s'],
                'local_time': window_key(record, row['example_start_s'], zone)[1], 'window_local_time': local, 'date': key[0], 'hour': key[1], 'status': 'ai_candidate'})
    counted = threshold is not None
    summary = {'species': model['species'], 'eligible_windows': eligible, 'candidate_windows': found if counted else None,
        'candidate_rate': found / eligible if eligible and counted else None, 'recorded_days': len(days),
        'candidate_days': len(candidate_days) if counted else None, 'quality': 'not_evaluated'}
    return {'kind': 'detection', 'dataset': ctx['dataset'], 'site': ctx['site'], 'engine': 'custom', 'model_sha256': model['artifact_sha256'], 'model_id': model_id,
        'custom_model': {'id': model_id, 'perch_model_sha256': model['feature']['model_sha256'], 'feature_spec': model['feature']['spec'],
            'training_window_s': 5, 'evaluation_window_s': 5, 'training': model['training'], 'run_id': model['run_id']},
        'analysis_ids': sorted({row['analysis_id'] for row in windows}), 'missing_region': [], 'threshold': threshold,
        'start_date': ctx['start_date'], 'end_date': ctx['end_date'], 'window_s': 5, 'species': model['species'], 'summaries': [summary],
        'cells': finalize_cells(cells, threshold), 'evidence': evidence, 'quality': 'not_evaluated', 'complete_windows_only': True,
        'recording_ids': sorted(records), 'base_report_id': None}
