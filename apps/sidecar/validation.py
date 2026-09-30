"""Per-species, per-model checks of how an AI counts a species.

Recordings are split by local day into calibration and evaluation days (by
recording when the site has only one day). ① asks about 20 calibration-day
windows spread evenly over the model's logit-score range and fixes a threshold
from a fitted curve. ② and ③ are fixed 30-window samples from the other days
(precision_audit). Answers belong to a species and a 15-second window, so the
same window is asked only once for all of that species' models. Unknown answers
stay unresolved outcomes, never negatives.
"""
import hashlib
import json
import secrets
from collections import defaultdict
from functools import lru_cache
from math import comb
from zoneinfo import ZoneInfo

import numpy as np
import precision_audit as audit
from scipy.optimize import minimize
from scipy.special import expit, logit

from reports import analysis_metadata, classification_batches, scope_records, window_key, window_in_scope
from species import names_for
from storage import CatalogError, checked_source, connect

CALIBRATION_SIZE = 20
TARGET_PRECISION = .90
CALIBRATION_METHOD = 'logit_bins_20_fitted_curve_v1'
SCORE_FLOOR = 1e-9  # ponytail: scores below this share the lowest logit; widen if models report finer tails.
DEFAULT_THRESHOLD = {'custom': .5}


def model_key(engine, model_id=None):
    return f'custom:{model_id}' if engine == 'custom' else engine


def default_threshold(engine):
    return DEFAULT_THRESHOLD.get(engine, .1)


def logit_scores(scores):
    return logit(np.clip(np.asarray(scores, dtype=np.float64), SCORE_FLOOR, 1 - SCORE_FLOOR))


def split_recordings(records, zone, site_id):
    """Label-independent, reproducible halves: local days, or recordings when there is one day."""
    groups = defaultdict(list)
    days = {window_key(record, 0, zone)[0][0] for record in records.values()}
    unit = 'local_day' if len(days) > 1 else 'recording'
    for record in records.values():
        groups[window_key(record, 0, zone)[0][0] if unit == 'local_day' else str(record['id'])].append(record['id'])
    order = sorted(groups, key=lambda key: hashlib.sha256(f'{site_id}:{key}'.encode()).hexdigest())
    count = max(1, len(order) // 2)
    calibration = sorted(rid for key in order[:count] for rid in groups[key])
    evaluation = sorted(rid for key in order[count:] for rid in groups[key])
    return {'unit': unit, 'calibration_recording_ids': calibration, 'evaluation_recording_ids': evaluation}


def calibration_sample(scores, rng, count=CALIBRATION_SIZE):
    """One random window per equal-width logit bin; bins left empty are filled by further rounds over the others."""
    x = logit_scores(scores)
    if not len(x):
        return []
    low, high = float(x.min()), float(x.max())
    bins = np.zeros(len(x), dtype=int) if high == low else np.minimum(((x - low) / (high - low) * count).astype(int), count - 1)
    members = [[int(i) for i in rng.permutation(np.flatnonzero(bins == b))] for b in range(count)]
    chosen = []
    while len(chosen) < min(count, len(x)):
        for b in rng.permutation(count):
            if members[b] and len(chosen) < count:
                chosen.append((members[b].pop(), int(b)))
    return chosen


def fit_threshold(scores, verdicts, population):
    """Lowest threshold whose fitted share of calls among population windows at or above it is >= 90%."""
    known = [(s, v == 'target') for s, v in zip(scores, verdicts) if v in ('target', 'other')]
    if not any(heard for _, heard in known):
        return {'threshold': None, 'reason': 'no_target'}
    x = logit_scores([s for s, _ in known])
    y = np.array([heard for _, heard in known], dtype=np.float64)
    if y.all():
        return {'threshold': float(np.min(population)), 'reason': 'all_target', 'estimated_precision': 1.0,
                'curve': {'answers': len(known), 'heard': len(known)}}
    if np.ptp(x) == 0:
        return {'threshold': None, 'reason': 'no_relation'}
    design = np.column_stack([np.ones_like(x), x])

    def loss(beta):
        # Firth's penalty keeps the curve finite when heard and not-heard sounds separate completely.
        eta = design @ beta
        weight = expit(eta) * expit(-eta)
        sign, logdet = np.linalg.slogdet(design.T @ (design * weight[:, None]))
        return np.inf if sign <= 0 else -(y @ eta - np.logaddexp(0, eta).sum() + .5 * logdet)
    beta = minimize(loss, np.zeros(2), method='Nelder-Mead', options={'xatol': 1e-8, 'fatol': 1e-10, 'maxiter': 5000}).x
    curve = {'intercept': float(beta[0]), 'slope': float(beta[1]), 'answers': len(known), 'heard': int(y.sum())}
    if not np.isfinite(beta).all() or beta[1] <= 0:
        return {'threshold': None, 'reason': 'no_relation', 'curve': curve}
    values, counts = np.unique(np.asarray(population, dtype=np.float64), return_counts=True)
    fitted = expit(beta[0] + beta[1] * logit_scores(values)) * counts
    share = np.cumsum(fitted[::-1])[::-1] / np.cumsum(counts[::-1])[::-1]
    reached = np.flatnonzero(share >= TARGET_PRECISION)
    if not len(reached):
        return {'threshold': None, 'reason': 'target_unreachable', 'curve': curve}
    return {'threshold': float(values[reached[0]]), 'reason': None, 'curve': curve,
            'estimated_precision': float(share[reached[0]])}


def scope(root, ctx):
    """In-period site recordings with a complete 15-second window, and their day split."""
    zone = ZoneInfo(ctx['site']['timezone'])
    with connect(root) as db:
        records = {r['id']: dict(r) for r in scope_records(
            [dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],))],
            zone, ctx['start_date'], ctx['end_date']) if r['seconds'] >= 15}
    return records, split_recordings(records, zone, ctx['site']['id']) if records else None


def model_windows(root, ctx, species_id, engine, model_id=None):
    """Every in-period 15-second window of one model for one species, with its score."""
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
        windows = [{'recording_id': w['recording_id'], 'start_s': w['start_s'], 'score': w['score']} for w in score_windows(root, model_id)
                   if w['recording_id'] in records and window_in_scope(records[w['recording_id']], w['start_s'], w['end_s'], zone, ctx['start_date'], ctx['end_date'])]
        return windows, {'custom': model['artifact_sha256']}, model['analysis_ids']
    with connect(root) as db:
        taxon = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
        if taxon is None:
            raise CatalogError('not_found')
        rows = db.execute('''SELECT a.*,r.seconds,r.path,r.size,r.mtime_ns,r.recorded_at,r.timezone FROM analyses a
            JOIN site_recordings r ON r.id=a.recording_id WHERE r.scope_id=? AND a.engine=? AND a.id IN
            (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine) ORDER BY a.recording_id''', (ctx['dataset']['id'], engine)).fetchall()
        records = {row['recording_id']: row for row in rows}
        windows, models, analyses = [], {}, []
        for row, allowed, arrays in classification_batches(root, db, rows, records):
            checked_source(row)
            if allowed is None:
                continue
            labels, starts, scores, _ = arrays
            matches = np.flatnonzero(labels == taxon['scientific_name'])
            if not len(matches):
                continue
            sha = analysis_metadata(row['metadata'])['model_sha256']
            if models.setdefault(engine, sha) != sha:
                raise CatalogError('validation_mixed_models')
            analyses.append(row['id'])
            for i, start in enumerate(starts):
                if window_in_scope(row, float(start), float(start) + 15, zone, ctx['start_date'], ctx['end_date']):
                    windows.append({'recording_id': row['recording_id'], 'start_s': float(start), 'score': float(scores[i, matches[0]])})
    return windows, models, analyses


def session_rows(db, site_id, species_id=None, model=None):
    return db.execute('''SELECT v.* FROM validation_sessions v JOIN datasets d ON d.id=v.dataset_id
        WHERE d.site_id=? AND (? IS NULL OR v.species_id=?) AND (? IS NULL OR v.model=?) ORDER BY v.id DESC''',
        (site_id, species_id, species_id, model, model)).fetchall()


def start_session(root, body):
    from results_workflow import context
    ctx = context(root, body.dataset_id, body.start_date.isoformat(), body.end_date.isoformat())
    if ctx.get('location_stale'):
        raise CatalogError('location_reanalysis')
    engine, key = body.model, model_key(body.model, body.model_id)
    with connect(root) as db:
        for row in session_rows(db, ctx['site']['id'], body.species_id, key):
            snap = json.loads(row['snapshot'])
            if row['fingerprint'] == ctx['fingerprint'] and (snap['start_date'], snap['end_date']) == (ctx['start_date'], ctx['end_date']):
                return view_session(root, row['id'], ctx=ctx)
    windows, models, analyses = model_windows(root, ctx, body.species_id, engine, body.model_id)
    records, split = scope(root, ctx)
    if engine == 'custom':
        from custom_models import training_windows
        used = training_windows(root, body.model_id)
        # Windows the model learned from would flatter its own check.
        windows = [w for w in windows if (w['recording_id'], w['start_s']) not in used]
    calibration_ids = set(split['calibration_recording_ids']) if split else set()
    evaluation_ids = set(split['evaluation_recording_ids']) if split else set()
    calibration = [w for w in windows if w['recording_id'] in calibration_ids]
    if not windows or not calibration:
        raise CatalogError('validation_no_windows')
    if not any(w['recording_id'] in evaluation_ids for w in windows):
        raise CatalogError('validation_no_holdout')
    seed = secrets.randbits(63)
    chosen = calibration_sample([w['score'] for w in calibration], np.random.default_rng(seed))
    snapshot = {'method': audit.METHOD, 'calibration_method': CALIBRATION_METHOD, 'stage': 'calibration', 'seed': str(seed),
        'model': key, 'engine': engine, 'model_id': body.model_id, 'models': models, 'analysis_ids': analyses,
        'start_date': ctx['start_date'], 'end_date': ctx['end_date'], 'site': ctx['site'], 'split': split,
        'population': [{'recording_id': w['recording_id'], 'start_s': w['start_s'], 'scores': {engine: w['score']}} for w in windows],
        'calibration_population': len(calibration), 'calibration_size': CALIBRATION_SIZE,
        'target_precision': audit.TARGET_PRECISION, 'calibration_target_precision': TARGET_PRECISION, 'window_s': 15,
        'thresholds': {}, 'results': {}, 'reference': 'user_answers', 'sampling_unit': 'non_overlapping_15_second_interval',
        'evaluation_scope': 'complete_windows_in_evaluation_days', 'uncertainty': audit.UNCERTAINTY}
    with connect(root) as db:
        sid = db.execute('INSERT INTO validation_sessions(dataset_id,species_id,model,fingerprint,snapshot) VALUES(?,?,?,?,?)',
            (body.dataset_id, body.species_id, key, ctx['fingerprint'], '{}')).lastrowid
        for position, (index, band) in enumerate(chosen):
            window = calibration[index]
            db.execute('INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,stratum,scores) VALUES(?,?,?,?,?,?,?)',
                (sid, 'calibration', position, window['recording_id'], window['start_s'], f'bin-{band}', json.dumps({engine: window['score']})))
        snapshot['rank_seed'] = audit.shared_seed(db, ctx['fingerprint'], snapshot)
        db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?', (json.dumps(snapshot, allow_nan=False), sid))
    return view_session(root, sid, ctx=ctx)


def answered_items(db, sid, phase=None):
    return [dict(r) for r in db.execute('''SELECT i.*,a.verdict,a.id AS answer_id,a.created_at AS answered_at,a.quiz_snapshot,a.examples_snapshot
        FROM validation_items i JOIN validation_sessions v ON v.id=i.session_id
        LEFT JOIN window_answers a ON a.id=(SELECT MAX(id) FROM window_answers w
            WHERE w.species_id=v.species_id AND w.recording_id=i.recording_id AND w.start_s=i.start_s)
        WHERE i.session_id=? AND (? IS NULL OR i.phase=?) ORDER BY i.phase,i.position''', (sid, phase, phase))]


def custom_model_status(root, snapshot):
    if snapshot.get('engine') != 'custom':
        return True, None
    from custom_models import get_model
    try:
        model = get_model(root, snapshot['model_id'])
    except CatalogError:
        return False, 'custom_model_files_missing'
    return model['applicable'], model['reason']


def begin_evaluation(db, sid, snapshot, calibration):
    engine = snapshot['engine']
    ids = set(snapshot['split']['calibration_recording_ids'])
    fitted = fit_threshold([json.loads(r['scores'])[engine] for r in calibration], [r['verdict'] for r in calibration],
                           [w['scores'][engine] for w in snapshot['population'] if w['recording_id'] in ids])
    snapshot['calibration_answer_ids'] = [r['answer_id'] for r in calibration]
    snapshot['calibration'] = fitted
    snapshot['thresholds'] = {engine: {'threshold': fitted['threshold'], 'adjusted': fitted['threshold'] is not None, 'reason': fitted['reason']}}
    if fitted['threshold'] is None:
        # No default threshold is substituted: the report states that none could be decided.
        snapshot['results'] = {engine: {'model_sha256': snapshot['models'][engine], **snapshot['thresholds'][engine],
            'precision_complete': False, 'recall_complete': False, 'goal_met': False, 'status': 'undecided'}}
        snapshot['stage'] = 'complete'
        return
    held = set(snapshot['split']['evaluation_recording_ids'])
    audit.prepare(db, sid, snapshot, [w for w in snapshot['population'] if w['recording_id'] in held])


@lru_cache(maxsize=4096)
def exact_count_interval(N, n, k, alpha):
    """Invert hypergeometric tails for the unknown finite-population positive count."""
    if not n:
        return 0, N
    if n == N:
        return k, k
    denominator = comb(N, n)

    def tail(first, last, positive):
        return sum(comb(positive, x) * comb(N - positive, n - x)
                   for x in range(max(0, n - N + positive, first), min(n, positive, last) + 1)) / denominator
    left, right = k, N - n + k
    lo, hi = left, right
    while lo < hi:
        mid = (lo + hi) // 2
        if tail(k, n, mid) >= alpha / 2:
            hi = mid
        else:
            lo = mid + 1
    lower = lo
    lo, hi = left, right
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if tail(0, k, mid) >= alpha / 2:
            lo = mid
        else:
            hi = mid - 1
    return lower, lo


def answer(root, sid, item_id, verdict, version=None, examples=None):
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT * FROM validation_sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        snap = json.loads(row['snapshot'])
        current_model, reason = custom_model_status(root, snap)
        if not current_model:
            raise CatalogError(reason)
        item = db.execute('SELECT * FROM validation_items WHERE id=? AND session_id=?', (item_id, sid)).fetchone()
        if not item:
            raise CatalogError('not_found')
        stage = 'evaluation' if snap['stage'] == 'complete' else snap['stage']
        if item['phase'] != stage:
            raise CatalogError('validation_phase_locked')
        rows = answered_items(db, sid, item['phase'])
        current = next(r for r in rows if r['id'] == item_id)
        if version is not None and (current['answer_id'] or 0) != version:
            raise CatalogError('annotation_changed')
        pending = [r for r in rows if r['id'] in audit.active_ids(snap)] if item['phase'] == 'evaluation' else rows
        if current['verdict'] is None and item_id != next((r['id'] for r in pending if r['verdict'] is None), None):
            raise CatalogError('validation_answer_order')
        checked_source(db.execute('SELECT * FROM recordings WHERE id=?', (item['recording_id'],)).fetchone())
        from quiz import report_results
        db.execute('''INSERT INTO window_answers(species_id,recording_id,start_s,verdict,session_id,quiz_snapshot,examples_snapshot)
            VALUES(?,?,?,?,?,?,?)''', (row['species_id'], item['recording_id'], item['start_s'], verdict, sid,
            json.dumps(report_results(db, [row['species_id']])), json.dumps(examples) if examples is not None else None))
        # The same window may sit in this species' other model checks; their saved results follow the new answer.
        for other in db.execute('''SELECT DISTINCT v.id,v.snapshot FROM validation_sessions v JOIN validation_items i ON i.session_id=v.id
                WHERE v.species_id=? AND i.recording_id=? AND i.start_s=? AND i.phase='evaluation' ''',
                (row['species_id'], item['recording_id'], item['start_s'])).fetchall():
            other_snap = json.loads(other['snapshot'])
            if 'evaluation_audits' in other_snap:
                audit.update(other_snap, answered_items(db, other['id'], 'evaluation'))
                db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?', (json.dumps(other_snap, allow_nan=False), other['id']))
    return view_session(root, sid)


def finish_calibration(root, sid):
    with connect(root) as db:
        row = db.execute('SELECT snapshot FROM validation_sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        snap = json.loads(row['snapshot'])
        current_model, reason = custom_model_status(root, snap)
        if not current_model:
            raise CatalogError(reason)
        if snap['stage'] == 'calibration':
            rows = answered_items(db, sid, 'calibration')
            if not rows or any(r['verdict'] is None for r in rows):
                raise CatalogError('validation_calibration_incomplete')
            begin_evaluation(db, sid, snap, rows)
            db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?', (json.dumps(snap, allow_nan=False), sid))
    return view_session(root, sid)


def start_recall(root, sid):
    if not view_session(root, sid, include_items=False)['applicable']:
        raise CatalogError('workflow_changed')
    with connect(root) as db:
        snap = json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?', (sid,)).fetchone()['snapshot'])
        if 'evaluation_audits' not in snap:
            raise CatalogError('validation_precision_incomplete')
        rows = answered_items(db, sid, 'evaluation')
        if not audit.active_ids(snap, 'precision').issubset({r['id'] for r in rows if r['verdict'] is not None}):
            raise CatalogError('validation_precision_incomplete')
        snap.update(audit_step='recall', recall_started=True)
        audit.update(snap, rows)
        db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?', (json.dumps(snap, allow_nan=False), sid))
    return view_session(root, sid)


def delete_sessions(db, ids):
    """Remove checks and the answers no remaining check of the same species still uses."""
    encoded = json.dumps(ids)
    windows = db.execute('''SELECT DISTINCT v.species_id,i.recording_id,i.start_s FROM validation_items i
        JOIN validation_sessions v ON v.id=i.session_id WHERE v.id IN (SELECT value FROM json_each(?))''', (encoded,)).fetchall()
    db.execute('DELETE FROM validation_items WHERE session_id IN (SELECT value FROM json_each(?))', (encoded,))
    db.execute('UPDATE window_answers SET session_id=NULL WHERE session_id IN (SELECT value FROM json_each(?))', (encoded,))
    db.execute('DELETE FROM validation_sessions WHERE id IN (SELECT value FROM json_each(?))', (encoded,))
    for species_id, recording_id, start_s in windows:
        if not db.execute('''SELECT 1 FROM validation_items i JOIN validation_sessions v ON v.id=i.session_id
                WHERE v.species_id=? AND i.recording_id=? AND i.start_s=?''', (species_id, recording_id, start_s)).fetchone():
            db.execute('DELETE FROM window_answers WHERE species_id=? AND recording_id=? AND start_s=?', (species_id, recording_id, start_s))
    db.execute('DELETE FROM workflow_cache')


def delete_check(root, sid):
    """Delete this species' check of one model at the site: every version and its ①–③ answers."""
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT v.species_id,v.model,d.site_id FROM validation_sessions v JOIN datasets d ON d.id=v.dataset_id WHERE v.id=?', (sid,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        ids = [r['id'] for r in session_rows(db, row['site_id'], row['species_id'], row['model'])]
        delete_sessions(db, ids)
    return {'deleted': len(ids)}


def status(view):
    """One word for a model's check: not_started, stale, calibrating, undecided, checking, goal_met or goal_not_met."""
    if not view:
        return 'not_started'
    if not view['applicable']:
        return 'stale'
    result = view['results'].get(view['engine']) or {}
    if view['stage'] == 'calibration':
        return 'calibrating'
    if result.get('status') == 'undecided':
        return 'undecided'
    if not result.get('precision_complete'):
        return 'checking'
    return 'goal_met' if result.get('goal_met') else 'goal_not_met'


def view_session(root, sid, ctx=None, include_items=True, item_id=None):
    from results_workflow import context
    with connect(root) as db:
        row = db.execute('SELECT * FROM validation_sessions WHERE id=?', (sid,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        snap = json.loads(row['snapshot'])
        taxon = names_for(db.execute('SELECT * FROM species WHERE id=?', (row['species_id'],)).fetchone())
        items = answered_items(db, sid)
        counts = {'calibration': {'total': sum(i['phase'] == 'calibration' for i in items),
                                  'answered': sum(i['phase'] == 'calibration' and i['verdict'] is not None for i in items)}}
        audits = snap.get('evaluation_audits', {})

        def membership(iid):
            return [{'engine': engine, 'sample': sample} for engine, value in audits.items()
                    for sample, field in [('precision', 'positive_ids'), ('recall', 'negative_ids')] if iid in value.get(field, [])]
        for step, name in [('precision', 'evaluation'), ('recall', 'recall')]:
            ids = audit.active_ids(snap, step) if audits else set()
            counts[name] = {'total': len(ids), 'answered': sum(i['id'] in ids and i['verdict'] is not None for i in items)}
        question, history = None, []
        if include_items:
            phase = 'evaluation' if snap['stage'] == 'complete' else snap['stage']
            visible = [i for i in items if i['phase'] == phase]
            if phase == 'evaluation':
                ids = audit.active_ids(snap) if audits else set()
                visible = [i for i in visible if i['id'] in ids or i['verdict'] is not None]
            next_item = next((i for i in visible if i['verdict'] is None), None)
            if item_id is not None:
                selected = next((i for i in visible if i['id'] == item_id), None)
                if not selected or (selected['verdict'] is None and selected != next_item):
                    raise CatalogError('not_found')
                next_item = selected
            history = [{'id': i['id'], 'position': i['position'], 'verdict': i['verdict'], 'samples': membership(i['id'])}
                       for i in visible if i['verdict'] is not None]
            if next_item:
                rec = dict(db.execute('SELECT * FROM recordings WHERE id=?', (next_item['recording_id'],)).fetchone())
                question = {'id': next_item['id'], 'position': next_item['position'], 'phase': next_item['phase'],
                    'verdict': next_item['verdict'], 'version': next_item['answer_id'] or 0,
                    'source': {'id': rec['id'], 'kind': 'recording', 'path': rec['path'], 'seconds': rec['seconds']},
                    'start_s': next_item['start_s'], 'end_s': next_item['start_s'] + snap['window_s'],
                    'samples': membership(next_item['id']), 'review_position': visible.index(next_item) + 1, 'review_total': len(visible)}
    ctx = ctx or context(root, row['dataset_id'], snap['start_date'], snap['end_date'])
    current_model, reason = custom_model_status(root, snap)
    applicable = (current_model and not ctx.get('location_stale') and row['fingerprint'] == ctx['fingerprint']
                  and (snap['start_date'], snap['end_date']) == (ctx['start_date'], ctx['end_date']))
    view = {'id': sid, 'dataset_id': row['dataset_id'], 'species': taxon, 'stage': snap['stage'], 'counts': counts, 'question': question,
        'model': row['model'], 'engine': snap['engine'], 'model_id': snap.get('model_id'), 'history': history,
        'split_unit': snap['split']['unit'], 'excluded_recording_ids': snap['split']['calibration_recording_ids'],
        'uncertainty': snap['uncertainty'], 'models': snap['models'], 'thresholds': snap['thresholds'], 'results': snap['results'],
        'calibration': snap.get('calibration'), 'audit_step': snap.get('audit_step'), 'recall_started': snap.get('recall_started', False),
        'can_start_recall': applicable and snap['stage'] == 'complete' and bool(audits) and not snap.get('recall_started')
            and counts['recall']['answered'] < counts['recall']['total'],
        'calibration_target_precision': snap['calibration_target_precision'], 'target_precision': snap['target_precision'],
        'start_date': snap['start_date'], 'end_date': snap['end_date'], 'window_s': snap['window_s'], 'population': len(snap['population']),
        'evaluation_population': snap.get('evaluation_population'), 'method': snap['method'], 'calibration_method': snap['calibration_method'],
        'evaluation_scope': snap['evaluation_scope'], 'reference': snap['reference'], 'sampling_unit': snap['sampling_unit'],
        'applicable': applicable, 'model_reason': reason, 'created_at': row['created_at']}
    view['status'] = status(view)
    return view
