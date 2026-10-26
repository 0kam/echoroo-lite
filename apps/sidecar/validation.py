"""One-stage, stratified checks of a species detector on native model windows."""
import json
import secrets
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
from scipy.special import expit, logit

from reports import analysis_metadata, classification_batches, classification_species_windows, window_in_scope
from species import names_for
from storage import CatalogError, checked_source, connect

METHOD = 'stratified_quantile6_v1'
QUANTILES = np.array([0, .5, .75, .875, .9375, .96875, 1.0])
SAMPLE_PER_BIN, EXTENSION_PER_BIN = 10, 3
TARGET_PRECISION, SCORE_FLOOR = .90, 1e-9
CURVE_FLOOR, DRAWS, DENSITY_DRAWS = float(expit(-11)), 1000, 10_000
GRID_A = np.linspace(-15, 15, 121)
GRID_B = np.linspace(.005, 20, 100)


def _population_path(root, sid):
    return Path(root) / 'validations' / f'{sid}.npz'


def _write_population(root, sid, windows, strata):
    path = _population_path(root, sid)
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        with temporary.open('wb') as stream:
            np.savez_compressed(stream,
                recording_id=np.fromiter((w['recording_id'] for w in windows), dtype=np.int64, count=len(windows)),
                start_s=np.fromiter((w['start_s'] for w in windows), dtype=np.float64, count=len(windows)),
                end_s=np.fromiter((w['end_s'] for w in windows), dtype=np.float64, count=len(windows)),
                score=np.fromiter((w['score'] for w in windows), dtype=np.float64, count=len(windows)),
                bin=np.asarray(strata, dtype=np.int8))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def _load_population(root, sid):
    path = _population_path(root, sid)
    if not path.is_file():
        raise CatalogError('validation_population_missing')
    try:
        with np.load(path, allow_pickle=False) as saved:
            population = {key: saved[key] for key in ('recording_id', 'start_s', 'end_s', 'score', 'bin')}
    except (OSError, ValueError, KeyError):
        raise CatalogError('validation_population_invalid') from None
    if len({len(values) for values in population.values()}) != 1:
        raise CatalogError('validation_population_invalid')
    return population


def _delete_population_files(db, ids):
    root = Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
    for sid in ids:
        try:
            _population_path(root, sid).unlink(missing_ok=True)
        except OSError:
            pass


def model_key(engine, model_id=None):
    return f'custom:{model_id}' if engine == 'custom' else engine


def native_window(engine):
    from models import window_s
    return window_s(engine)


def default_threshold(engine):
    return .5 if engine == 'custom' else .1


def logit_scores(scores):
    return logit(np.clip(np.asarray(scores, dtype=np.float64), SCORE_FLOOR, 1 - SCORE_FLOOR))


def quantile_strata(scores, seed):
    """Assign rank quantile bins, using a seeded permutation only to break ties."""
    x = logit_scores(scores)
    rng = np.random.default_rng(seed)
    order = np.lexsort((rng.permutation(len(x)), x))
    edges = np.floor(QUANTILES * len(x)).astype(int)
    strata = np.empty(len(x), dtype=np.int8)
    bounds = []
    for b, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
        members = order[lo:hi]
        strata[members] = b
        bounds.append({'low_x': float(x[members].min()) if len(members) else None,
                       'high_x': float(x[members].max()) if len(members) else None})
    return strata, edges, bounds


def sample_indices(strata, seed, per_bin, excluded=()):
    rng, excluded, chosen = np.random.default_rng(seed), set(excluded), []
    for b in range(6):
        members = np.array([int(i) for i in np.flatnonzero(strata == b) if int(i) not in excluded])
        if len(members):
            chosen.extend((int(i), b) for i in rng.choice(members, min(per_bin, len(members)), replace=False))
    rng.shuffle(chosen)
    return chosen


def _interval(values):
    return {'value': float(np.median(values)), 'low': float(np.quantile(values, .05)),
            'high': float(np.quantile(values, .95))}


def _groups(x, known, heard, limit=2000):
    order = np.argsort(x, kind='stable')
    values, first, counts = np.unique(x[order], return_index=True, return_counts=True)
    value_chunks = np.array_split(np.arange(len(values)), min(limit, len(values)))
    chunks = [order[first[v[0]]:first[v[-1]] + counts[v[-1]]] for v in value_chunks]
    return (np.array([x[c].mean() for c in chunks]), np.array([len(c) for c in chunks], dtype=np.int64),
            np.array([heard[c].sum() for c in chunks], dtype=np.int64),
            np.array([len(c) - known[c].sum() for c in chunks], dtype=np.int64),
            np.array([x[c].min() for c in chunks]))


def estimate(scores, strata, sampled, verdicts, x0, seed, draws=DRAWS):
    """Bin-Beta density plus a fixed-floor monotone curve and finite-population draws."""
    scores, strata = np.asarray(scores, dtype=np.float64), np.asarray(strata)
    x, sampled, verdicts = logit_scores(scores), np.asarray(sampled, dtype=np.int64), np.asarray(verdicts)
    known_sample, y_sample = verdicts != 'unknown', verdicts == 'target'
    known, heard = np.zeros(len(x), dtype=bool), np.zeros(len(x), dtype=bool)
    known[sampled[known_sample]] = True
    heard[sampled[known_sample]] = y_sample[known_sample]
    rng = np.random.default_rng(seed)
    bin_rows, density_draws, point = [], np.zeros(DENSITY_DRAWS), 0.0
    for b in range(6):
        members, verdict = strata == b, verdicts[strata[sampled] == b]
        pos, neg, unknown_count = (int(np.sum(verdict == value)) for value in ('target', 'other', 'unknown'))
        weight = float(members.mean())
        point += weight * (pos / (pos + neg) if pos + neg else .5)
        density_draws += weight * rng.beta(pos + .1, neg + .1, DENSITY_DRAWS)
        bin_rows.append({'bin': b, 'population': int(members.sum()), 'weight': weight,
                         'heard': pos, 'not_heard': neg, 'unknown': unknown_count})
    density = _interval(density_draws)
    density['value'] = float(point)
    count = {k: float(v * len(x)) for k, v in density.items()}
    base = {'density': density, 'count': count, 'answers_per_bin': bin_rows,
            'unknown': int(np.sum(verdicts == 'unknown')),
            'draws': {'density': DENSITY_DRAWS, 'posterior_predictive': draws}}
    rare = point < .01
    if not y_sample[known_sample].any():
        return {**base, 'threshold': None, 'reason': 'no_target', 'precision': None, 'recall': None,
                'auprc': None, 'multiplier': None, 'curve_density': None, 'disagreement': False,
                'recall_status': 'not_estimable_rare' if rare else 'not_estimable'}

    A, B = (v.ravel() for v in np.meshgrid(GRID_A, GRID_B, indexing='ij'))
    p = CURVE_FLOOR + (1 - CURVE_FLOOR) * expit(A[:, None] + B[:, None] * (x[sampled[known_sample]][None, :] - x0))
    p = np.clip(p, 1e-12, 1 - 1e-12)
    y = y_sample[known_sample].astype(float)
    log_post = (y * np.log(p) + (1 - y) * np.log1p(-p)).sum(axis=1)
    log_post += -.5 * (A / 2.5) ** 2 - .5 * (B / 1.5) ** 2
    weights = np.exp(log_post - log_post.max()); weights /= weights.sum()
    edge_mass = {'a_low': float(weights[A == GRID_A[0]].sum()), 'a_high': float(weights[A == GRID_A[-1]].sum()),
                 'b_low': float(weights[B == GRID_B[0]].sum()), 'b_high': float(weights[B == GRID_B[-1]].sum())}
    picked = rng.choice(len(weights), draws, p=weights)
    gx, totals, fixed, unobserved, thresholds = _groups(x, known, heard)
    probabilities = CURVE_FLOOR + (1 - CURVE_FLOOR) * expit(A[picked, None] + B[picked, None] * (gx[None, :] - x0))
    positives = rng.binomial(unobserved, probabilities) + fixed
    mean_probability = np.empty(len(gx))
    for start in range(0, len(gx), 128):
        values = CURVE_FLOOR + (1 - CURVE_FLOOR) * expit(
            A[:, None] + B[:, None] * (gx[None, start:start + 128] - x0))
        mean_probability[start:start + 128] = weights @ values
    expected = mean_probability * unobserved + fixed
    tail_total = np.cumsum(totals[::-1])[::-1]
    candidates = np.flatnonzero(np.cumsum(expected[::-1])[::-1] / tail_total >= TARGET_PRECISION)
    if len(candidates):
        chosen = int(candidates[0])
        threshold, reason = float(expit(thresholds[chosen])), None
        tp, all_positive = positives[:, chosen:].sum(axis=1), positives.sum(axis=1)
        precision_draws = tp / tail_total[chosen]
        recall_draws = tp / np.maximum(all_positive, 1)
        precision, recall = _interval(precision_draws), _interval(recall_draws)
        multiplier = _interval(precision_draws / np.maximum(recall_draws, 1e-12))
    else:
        threshold, reason = None, 'target_unreachable'
        precision = recall = multiplier = None
    descending = positives[:, ::-1]
    cumulative = np.cumsum(descending, axis=1)
    total_positive = np.maximum(positives.sum(axis=1), 1)
    auprc = _interval(((cumulative / np.cumsum(totals[::-1])) * descending).sum(axis=1) / total_positive)
    curve_density = _interval(positives.sum(axis=1) / len(x))
    return {**base, 'threshold': threshold, 'reason': reason, 'precision': precision,
            'recall': None if rare else recall, 'recall_status': 'not_estimable_rare' if rare else 'estimated',
            'auprc': auprc, 'multiplier': None if rare else multiplier, 'curve_density': curve_density,
            'disagreement': not density['low'] <= curve_density['value'] <= density['high'],
            'posterior_edge_mass': edge_mass,
            'grid_edge_ok': max(edge_mass[k] for k in ('a_low', 'a_high', 'b_high')) < .01}


def extension_needed(result):
    if result.get('recall') is None or result['recall']['high'] - result['recall']['low'] <= .30:
        return False
    return any(row['population'] > row['heard'] + row['not_heard'] + row['unknown'] for row in result['answers_per_bin'])


def model_windows(root, ctx, species_id, engine, model_id=None):
    from models import require_selected
    if engine != 'custom':
        require_selected(root, engine)
    zone = ZoneInfo(ctx['site']['timezone'])
    if engine == 'custom':
        from custom_models import get_model, score_windows
        model = get_model(root, model_id)
        if model['species']['id'] != species_id or model['scope']['site_id'] != ctx['site']['id']:
            raise CatalogError('not_found')
        if not model['applicable']:
            raise CatalogError(model['reason'])
        with connect(root) as db:
            records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],))}
        windows = [dict(w) for w in score_windows(root, model_id) if w['recording_id'] in records and
                   window_in_scope(records[w['recording_id']], w['start_s'], w['end_s'], zone, ctx['start_date'], ctx['end_date'])]
        return windows, {'custom': model['artifact_sha256']}, model['analysis_ids']
    with connect(root) as db:
        taxon = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
        if taxon is None: raise CatalogError('not_found')
        rows = db.execute('''SELECT a.*,r.seconds,r.path,r.size,r.mtime_ns,r.recorded_at,r.timezone FROM analyses a
            JOIN site_recordings r ON r.id=a.recording_id WHERE r.scope_id=? AND a.engine=? AND a.id IN
            (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine) ORDER BY a.recording_id''', (ctx['dataset']['id'], engine)).fetchall()
        records, windows, models, analyses = {row['recording_id']: row for row in rows}, [], {}, []
        width = native_window(engine)
        for row in rows:
            checked_source(row)
        indexed = classification_species_windows(root, db, rows, records, taxon['scientific_name'])
        if indexed is not None:
            analysis_ids, recording_ids, starts, scores = indexed
            by_id = {row['id']: row for row in rows}
            for aid in dict.fromkeys(int(value) for value in analysis_ids):
                row = by_id[aid]
                sha = analysis_metadata(row['metadata'])['model_sha256']
                if models.setdefault(engine, sha) != sha: raise CatalogError('validation_mixed_models')
                analyses.append(aid)
            for rid, start, score in zip(recording_ids, starts, scores):
                end = float(start) + width
                record = records[int(rid)]
                if window_in_scope(record, float(start), end, zone, ctx['start_date'], ctx['end_date']):
                    windows.append({'recording_id': int(rid), 'start_s': float(start), 'end_s': end, 'score': float(score)})
            return windows, models, analyses
        for row, allowed, arrays in classification_batches(root, db, rows, records, False, True):
            if allowed is None: continue
            labels, starts, scores, _ = arrays
            matches = np.flatnonzero(labels == taxon['scientific_name'])
            if not len(matches): continue
            sha = analysis_metadata(row['metadata'])['model_sha256']
            if models.setdefault(engine, sha) != sha: raise CatalogError('validation_mixed_models')
            analyses.append(row['id'])
            for i, start in enumerate(starts):
                end = float(start) + width
                if window_in_scope(row, float(start), end, zone, ctx['start_date'], ctx['end_date']):
                    windows.append({'recording_id': row['recording_id'], 'start_s': float(start), 'end_s': end,
                                    'score': float(scores[i, matches[0]])})
    return windows, models, analyses


def session_rows(db, site_id, species_id=None, model=None):
    return db.execute('''SELECT v.* FROM validation_sessions v JOIN datasets d ON d.id=v.dataset_id
        WHERE d.site_id=? AND (? IS NULL OR v.species_id=?) AND (? IS NULL OR v.model=?) ORDER BY v.id DESC''',
        (site_id, species_id, species_id, model, model)).fetchall()


def start_session(root, body):
    from results_workflow import context
    ctx = context(root, body.dataset_id, body.start_date.isoformat(), body.end_date.isoformat())
    if ctx.get('location_stale'): raise CatalogError('location_reanalysis')
    engine, key = body.model, model_key(body.model, body.model_id)
    with connect(root) as db:
        for row in session_rows(db, ctx['site']['id'], body.species_id, key):
            snap = json.loads(row['snapshot'])
            if row['fingerprint'] == ctx['fingerprint'] and (snap['start_date'], snap['end_date']) == (ctx['start_date'], ctx['end_date']):
                return view_session(root, row['id'], ctx=ctx)
    windows, models, analyses = model_windows(root, ctx, body.species_id, engine, body.model_id)
    if engine == 'custom':
        from custom_models import training_windows
        used = training_windows(root, body.model_id)
        windows = [w for w in windows if (w['recording_id'], w['start_s'], w['end_s']) not in used]
    if not windows: raise CatalogError('validation_no_windows')
    seed = secrets.randbits(63)
    strata, edges, bounds = quantile_strata([w['score'] for w in windows], seed)
    chosen = sample_indices(strata, seed ^ 0x5A17, SAMPLE_PER_BIN)
    top = np.flatnonzero(strata == 5)
    x0 = float(logit_scores([windows[int(i)]['score'] for i in top]).min())
    snapshot = {'method': METHOD, 'stage': 'sample', 'seed': str(seed), 'sample_seed': str(seed ^ 0x5A17),
        'draw_seed': str(seed ^ 0xC0FFEE), 'extension_seed': str(seed ^ 0xE17E),
        'model': key, 'engine': engine, 'model_id': body.model_id,
        'models': models, 'analysis_ids': analyses, 'start_date': ctx['start_date'], 'end_date': ctx['end_date'],
        'site': ctx['site'],
        'N': len(windows), 'window_s': native_window(engine), 'quantile_bounds': QUANTILES.tolist(),
        'rank_edges': edges.tolist(), 'bin_bounds': bounds, 'bin_weights': [float(np.mean(strata == b)) for b in range(6)],
        'x0': x0, 'priors': {'a': {'distribution': 'normal', 'mean': 0, 'sd': 2.5},
        'b': {'distribution': 'half_normal', 'sd': 1.5}, 'floor': CURVE_FLOOR},
        'grid': {'a': [float(GRID_A[0]), float(GRID_A[-1]), len(GRID_A)],
        'b': [float(GRID_B[0]), float(GRID_B[-1]), len(GRID_B)]},
        'draws': {'density': DENSITY_DRAWS, 'posterior_predictive': DRAWS},
        'thresholds': {}, 'results': {}, 'extended': False, 'reference': 'user_answers',
        'sampling_unit': 'native_model_window', 'evaluation_scope': 'all_complete_native_windows_in_period'}
    population_path = None
    try:
        with connect(root) as db:
            sid = db.execute('INSERT INTO validation_sessions(dataset_id,species_id,model,fingerprint,snapshot) VALUES(?,?,?,?,?)',
                (body.dataset_id, body.species_id, key, ctx['fingerprint'], '{}')).lastrowid
            population_path = _write_population(root, sid, windows, strata)
            for position, (index, b) in enumerate(chosen):
                w = windows[index]
                db.execute('''INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,end_s,stratum,scores)
                    VALUES(?,?,?,?,?,?,?,?)''', (sid, 'sample', position, w['recording_id'], w['start_s'], w['end_s'],
                                                 f'bin-{b}', json.dumps({engine: w['score']})))
            _finish(root, db, sid, snapshot)
            db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?', (json.dumps(snapshot, allow_nan=False), sid))
    except Exception:
        if population_path is not None:
            population_path.unlink(missing_ok=True)
        raise
    return view_session(root, sid, ctx=ctx)


def answered_items(db, sid, phase=None):
    return [dict(r) for r in db.execute('''SELECT i.*,a.verdict,a.id AS answer_id,a.created_at AS answered_at,
        a.quiz_snapshot,a.examples_snapshot FROM validation_items i JOIN validation_sessions v ON v.id=i.session_id
        LEFT JOIN window_answers a ON a.id=(SELECT MAX(id) FROM window_answers w WHERE w.species_id=v.species_id
            AND w.recording_id=i.recording_id AND w.start_s=i.start_s AND w.end_s=i.end_s)
        WHERE i.session_id=? AND (? IS NULL OR i.phase=?) ORDER BY i.position''', (sid, phase, phase))]


def require_visible(root, snapshot):
    if snapshot['engine'] == 'custom':
        from custom_models import get_model
        from models import main_engine
        if get_model(root, snapshot['model_id'])['feature']['engine'] != main_engine(root):
            raise CatalogError('not_found')
    else:
        from models import require_selected
        require_selected(root, snapshot['engine'])


def custom_model_status(root, snapshot):
    if snapshot.get('engine') != 'custom': return True, None
    from custom_models import get_model
    try: model = get_model(root, snapshot['model_id'])
    except CatalogError: return False, 'custom_model_files_missing'
    return model['applicable'], model['reason']


def _finish(root, db, sid, snap):
    rows = answered_items(db, sid)
    if not rows or any(r['verdict'] is None for r in rows): return False
    population = _load_population(root, sid)
    lookup = {(int(rid), float(start), float(end)): i for i, (rid, start, end) in enumerate(zip(
        population['recording_id'], population['start_s'], population['end_s']))}
    sampled = [lookup[r['recording_id'], r['start_s'], r['end_s']] for r in rows]
    result = estimate(population['score'], population['bin'], sampled,
                      [r['verdict'] for r in rows], snap['x0'], int(snap['draw_seed']))
    result.update(model_sha256=snap['models'][snap['engine']], status='decided' if result['threshold'] is not None else 'undecided')
    snap['results'] = {snap['engine']: result}
    snap['thresholds'] = {snap['engine']: {'threshold': result['threshold'], 'adjusted': result['threshold'] is not None,
                                           'reason': result['reason']}}
    snap['stage'] = 'complete'
    snap['can_extend'] = not snap['extended'] and extension_needed(result)
    return True


def answer(root, sid, item_id, verdict, version=None, examples=None):
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM validation_sessions WHERE id=?', (sid,)).fetchone()
        if not row: raise CatalogError('not_found')
        snap = json.loads(row['snapshot'])
        require_visible(root, snap)
        current_model, reason = custom_model_status(root, snap)
        if not current_model: raise CatalogError(reason)
        item = db.execute('SELECT * FROM validation_items WHERE id=? AND session_id=?', (item_id, sid)).fetchone()
        if not item: raise CatalogError('not_found')
        rows, current = answered_items(db, sid), None
        current = next(r for r in rows if r['id'] == item_id)
        if version is not None and (current['answer_id'] or 0) != version: raise CatalogError('annotation_changed')
        if current['verdict'] is None and item_id != next((r['id'] for r in rows if r['verdict'] is None), None):
            raise CatalogError('validation_answer_order')
        checked_source(db.execute('SELECT * FROM recordings WHERE id=?', (item['recording_id'],)).fetchone())
        from quiz import report_results
        db.execute('''INSERT INTO window_answers(species_id,recording_id,start_s,end_s,verdict,session_id,quiz_snapshot,examples_snapshot)
            VALUES(?,?,?,?,?,?,?,?)''', (row['species_id'], item['recording_id'], item['start_s'], item['end_s'], verdict, sid,
            json.dumps(report_results(db, [row['species_id']])), json.dumps(examples) if examples is not None else None))
        others = db.execute('''SELECT DISTINCT v.id,v.snapshot FROM validation_sessions v JOIN validation_items i ON i.session_id=v.id
            WHERE v.species_id=? AND i.recording_id=? AND i.start_s=? AND i.end_s=?''',
            (row['species_id'], item['recording_id'], item['start_s'], item['end_s'])).fetchall()
        for other in others:
            other_snap = json.loads(other['snapshot']); _finish(root, db, other['id'], other_snap)
            db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?', (json.dumps(other_snap, allow_nan=False), other['id']))
    return view_session(root, sid)


def extend(root, sid):
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT snapshot FROM validation_sessions WHERE id=?', (sid,)).fetchone()
        if not row: raise CatalogError('not_found')
        snap = json.loads(row['snapshot'])
        require_visible(root, snap)
        if snap['stage'] != 'complete' or not snap.get('can_extend') or snap.get('extended'):
            raise CatalogError('validation_extension_unavailable')
        population = _load_population(root, sid)
        existing_rows = answered_items(db, sid)
        lookup = {(int(rid), float(start), float(end)): i for i, (rid, start, end) in enumerate(zip(
            population['recording_id'], population['start_s'], population['end_s']))}
        existing = [lookup[r['recording_id'], r['start_s'], r['end_s']] for r in existing_rows]
        chosen = sample_indices(population['bin'], int(snap['extension_seed']),
                                EXTENSION_PER_BIN, existing)
        for offset, (index, b) in enumerate(chosen, start=len(existing_rows)):
            db.execute('''INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,end_s,stratum,scores)
                VALUES(?,?,?,?,?,?,?,?)''', (sid, 'extension', offset, int(population['recording_id'][index]),
                    float(population['start_s'][index]), float(population['end_s'][index]), f'bin-{b}',
                    json.dumps({snap['engine']: float(population['score'][index])})))
        snap.update(stage='extension', extended=True, can_extend=False, results={}, thresholds={})
        _finish(root, db, sid, snap)
        db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?', (json.dumps(snap, allow_nan=False), sid))
        db.execute('DELETE FROM workflow_cache')
    return view_session(root, sid)


def delete_sessions(db, ids):
    if not ids: return
    encoded = json.dumps(ids)
    windows = db.execute('''SELECT DISTINCT v.species_id,i.recording_id,i.start_s,i.end_s FROM validation_items i
        JOIN validation_sessions v ON v.id=i.session_id WHERE v.id IN (SELECT value FROM json_each(?))''', (encoded,)).fetchall()
    db.execute('DELETE FROM validation_items WHERE session_id IN (SELECT value FROM json_each(?))', (encoded,))
    db.execute('UPDATE window_answers SET session_id=NULL WHERE session_id IN (SELECT value FROM json_each(?))', (encoded,))
    db.execute('DELETE FROM validation_sessions WHERE id IN (SELECT value FROM json_each(?))', (encoded,))
    for species_id, recording_id, start_s, end_s in windows:
        if not db.execute('''SELECT 1 FROM validation_items i JOIN validation_sessions v ON v.id=i.session_id
                WHERE v.species_id=? AND i.recording_id=? AND i.start_s=? AND i.end_s=?''',
                (species_id, recording_id, start_s, end_s)).fetchone():
            db.execute('DELETE FROM window_answers WHERE species_id=? AND recording_id=? AND start_s=? AND end_s=?',
                       (species_id, recording_id, start_s, end_s))
    db.execute('DELETE FROM workflow_cache')
    _delete_population_files(db, ids)


def delete_check(root, sid):
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT v.species_id,v.model,d.site_id FROM validation_sessions v JOIN datasets d ON d.id=v.dataset_id WHERE v.id=?', (sid,)).fetchone()
        if not row: raise CatalogError('not_found')
        ids = [r['id'] for r in session_rows(db, row['site_id'], row['species_id'], row['model'])]
        delete_sessions(db, ids)
    return {'deleted': len(ids)}


def status(view):
    if not view: return 'not_started'
    if not view['applicable']: return 'stale'
    if view['stage'] != 'complete': return 'in_progress'
    return (view['results'].get(view['engine']) or {}).get('status', 'undecided')


def view_session(root, sid, ctx=None, include_items=True, item_id=None):
    from results_workflow import context
    with connect(root) as db:
        row = db.execute('SELECT * FROM validation_sessions WHERE id=?', (sid,)).fetchone()
        if not row: raise CatalogError('not_found')
        snap = json.loads(row['snapshot'])
        require_visible(root, snap)
        taxon = names_for(db.execute('SELECT * FROM species WHERE id=?', (row['species_id'],)).fetchone())
        items = answered_items(db, sid)
        counts = {'total': len(items), 'answered': sum(i['verdict'] is not None for i in items),
                  'unknown': sum(i['verdict'] == 'unknown' for i in items), 'bins': []}
        for b in range(6):
            rows = [i for i in items if i['stratum'] == f'bin-{b}']
            counts['bins'].append({'bin': b, 'total': len(rows), 'answered': sum(i['verdict'] is not None for i in rows),
                'heard': sum(i['verdict'] == 'target' for i in rows), 'not_heard': sum(i['verdict'] == 'other' for i in rows),
                'unknown': sum(i['verdict'] == 'unknown' for i in rows)})
        question, history = None, []
        if include_items:
            next_item = next((i for i in items if i['verdict'] is None), None)
            if item_id is not None:
                selected = next((i for i in items if i['id'] == item_id), None)
                if not selected or (selected['verdict'] is None and selected != next_item): raise CatalogError('not_found')
                next_item = selected
            history = [{'id': i['id'], 'position': i['position'], 'phase': i['phase'], 'verdict': i['verdict']}
                       for i in items if i['verdict'] is not None]
            if next_item:
                rec = dict(db.execute('SELECT * FROM recordings WHERE id=?', (next_item['recording_id'],)).fetchone())
                question = {'id': next_item['id'], 'position': next_item['position'], 'phase': next_item['phase'],
                    'verdict': next_item['verdict'], 'version': next_item['answer_id'] or 0,
                    'source': {'id': rec['id'], 'kind': 'recording', 'path': rec['path'], 'seconds': rec['seconds']},
                    'start_s': next_item['start_s'], 'end_s': next_item['end_s'],
                    'review_position': items.index(next_item) + 1, 'review_total': len(items)}
    ctx = ctx or context(root, row['dataset_id'], snap['start_date'], snap['end_date'])
    current_model, reason = custom_model_status(root, snap)
    applicable = current_model and not ctx.get('location_stale') and row['fingerprint'] == ctx['fingerprint'] and \
        (snap['start_date'], snap['end_date']) == (ctx['start_date'], ctx['end_date'])
    view = {'id': sid, 'dataset_id': row['dataset_id'], 'species': taxon, 'stage': snap['stage'], 'counts': counts,
        'question': question, 'model': row['model'], 'engine': snap['engine'], 'model_id': snap.get('model_id'),
        'history': history, 'models': snap['models'], 'thresholds': snap['thresholds'], 'results': snap['results'],
        'can_extend': applicable and bool(snap.get('can_extend')), 'extended': snap.get('extended', False),
        'start_date': snap['start_date'], 'end_date': snap['end_date'], 'window_s': snap['window_s'],
        'population': snap['N'], 'method': snap['method'], 'evaluation_scope': snap['evaluation_scope'],
        'reference': snap['reference'], 'sampling_unit': snap['sampling_unit'], 'quantile_bounds': snap['quantile_bounds'],
        'bin_bounds': snap['bin_bounds'], 'bin_weights': snap['bin_weights'],
        'seeds': {'rank': snap['seed'], 'sample': snap['sample_seed'], 'draw': snap['draw_seed'],
                  'extension': snap['extension_seed']},
        'priors': snap['priors'], 'grid': snap['grid'], 'draws': snap['draws'], 'applicable': applicable,
        'model_reason': reason, 'created_at': row['created_at']}
    view['status'] = status(view)
    return view
