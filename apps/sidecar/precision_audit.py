"""Fixed per-model probability samples; precision and recall are separate tasks.

Shared random ranks coordinate overlapping samples without changing the marginal
simple-random sample for any species/model. Membership is frozen before answers.
"""
import hashlib
import json
import secrets

METHOD = 'fixed_precision_audit_v1'
SAMPLE_SIZE = 30
TARGET_PRECISION = .80
UNCERTAINTY = 'fixed_sample_exact_hypergeometric_95_per_model'


def shared_seed(db, fingerprint, snapshot):
    row = db.execute('''SELECT json_extract(snapshot,'$.rank_seed') FROM validation_sessions
        WHERE fingerprint=? AND json_extract(snapshot,'$.method')=?
        AND json_extract(snapshot,'$.site.id')=? AND json_extract(snapshot,'$.start_date')=?
        AND json_extract(snapshot,'$.end_date')=? AND json_extract(snapshot,'$.window_s')=?
        AND json_extract(snapshot,'$.rank_seed') IS NOT NULL ORDER BY id LIMIT 1''',
        (fingerprint, METHOD, snapshot['site']['id'], snapshot['start_date'], snapshot['end_date'], snapshot['window_s'])).fetchone()
    return row[0] if row else str(secrets.randbits(63))


def rank(snapshot, item):
    value = f"{snapshot['rank_seed']}:{item['recording_id']}:{float(item['start_s']):.6f}:{snapshot['window_s']}"
    return hashlib.sha256(value.encode()).digest()


def active_ids(snapshot, step=None):
    field = 'negative_ids' if (step or snapshot.get('audit_step')) == 'recall' else 'positive_ids'
    return {iid for audit in snapshot['evaluation_audits'].values() for iid in audit[field]}


def prepare(db, sid, snapshot, remaining):
    engines = sorted(snapshot['models'])
    ordered = sorted(remaining, key=lambda item: rank(snapshot, item))
    audits, chosen = {}, {}
    for engine in engines:
        threshold = snapshot['thresholds'][engine]['threshold']
        eligible = [r for r in ordered if engine in r['scores'] and threshold is not None]
        positive = [r for r in eligible if r['scores'][engine] >= threshold]
        negative = [r for r in eligible if r['scores'][engine] < threshold]
        audits[engine] = {'population': len(eligible), 'predicted': len(positive), 'negative': len(negative)}
        for name, pool in [('positive', positive), ('negative', negative)]:
            selected = pool[:SAMPLE_SIZE]
            audits[engine][name + '_keys'] = [(r['recording_id'], r['start_s']) for r in selected]
            audits[engine][name + '_inclusion_probability'] = len(selected) / len(pool) if pool else None
            chosen.update(((r['recording_id'], r['start_s']), r) for r in selected)
    ids = {}
    for position, row in enumerate(sorted(chosen.values(), key=lambda item: rank(snapshot, item))):
        bits = ''.join('x' if engine not in row['scores'] or snapshot['thresholds'][engine]['threshold'] is None
                       else '1' if row['scores'][engine] >= snapshot['thresholds'][engine]['threshold'] else '0'
                       for engine in engines)
        ids[row['recording_id'], row['start_s']] = db.execute('''INSERT INTO validation_items
            (session_id,phase,position,recording_id,start_s,stratum,scores) VALUES(?,'evaluation',?,?,?,?,?)''',
            (sid, position, row['recording_id'], row['start_s'], bits, json.dumps(row['scores']))).lastrowid
    for audit in audits.values():
        for name in ('positive', 'negative'):
            audit[name + '_ids'] = [ids[key] for key in audit.pop(name + '_keys')]
    snapshot.update(evaluation_audits=audits, evaluation_engines=engines, evaluation_population=len(remaining),
                    evaluation_per_model=SAMPLE_SIZE, audit_step='precision', recall_started=False,
                    uncertainty=UNCERTAINTY, stage='evaluation')
    update(snapshot, [])


def sample_summary(audit, side, lookup):
    rows = [lookup[iid] for iid in audit[side + '_ids'] if iid in lookup and lookup[iid]['verdict'] is not None]
    return {'total': len(audit[side + '_ids']), 'answered': len(rows),
            'target': sum(r['verdict'] == 'target' for r in rows),
            'unknown': sum(r['verdict'] == 'unknown' for r in rows),
            'population': audit['predicted' if side == 'positive' else 'negative']}


def count_bounds(sample, alpha):
    from validation import exact_count_interval
    n, N = sample['total'], sample['population']
    if sample['answered'] != n:
        return 0, N
    return (exact_count_interval(N, n, sample['target'], alpha)[0],
            exact_count_interval(N, n, sample['target'] + sample['unknown'], alpha)[1])


def evaluate(snapshot, rows):
    lookup = {r['id']: r for r in rows}
    results = {}
    for engine, audit in snapshot['evaluation_audits'].items():
        pos = sample_summary(audit, 'positive', lookup)
        neg = sample_summary(audit, 'negative', lookup)
        pc, nc = pos['answered'] == pos['total'], neg['answered'] == neg['total']
        predicted, N = audit['predicted'], audit['population']
        precision = dict(value=None, low=None, high=None)
        lower = None
        if pc and predicted:
            lo, hi = count_bounds(pos, .05)
            precision = {'value': pos['target'] / pos['total'] if not pos['unknown'] else None,
                         'low': lo / predicted, 'high': hi / predicted}
            # exact_count_interval splits alpha across two tails; .10 gives a .05 lower tail.
            lower = count_bounds(pos, .10)[0] / predicted
        recall, rate = (dict(value=None, low=None, high=None) for _ in range(2))
        if pc and nc and N:
            # Two strata jointly cover TP and FN at least 95%; dependence is allowed.
            tp_lo, tp_hi = count_bounds(pos, .025)
            fn_lo, fn_hi = count_bounds(neg, .025)
            tp = predicted * pos['target'] / pos['total'] if pos['total'] else 0
            fn = audit['negative'] * neg['target'] / neg['total'] if neg['total'] else 0
            resolved = not (pos['unknown'] or neg['unknown'])
            if pos['target'] + neg['target']:
                recall = {'value': tp / (tp + fn) if resolved and tp + fn else None,
                          'low': tp_lo / (tp_lo + fn_hi) if tp_lo + fn_hi else None,
                          'high': tp_hi / (tp_hi + fn_lo) if tp_hi + fn_lo else None}
            rate = {'value': (tp + fn) / N if resolved else None,
                    'low': (tp_lo + fn_lo) / N, 'high': (tp_hi + fn_hi) / N}
        ids = set(audit['positive_ids']) | set(audit['negative_ids'])
        answered = [lookup[iid] for iid in ids if iid in lookup and lookup[iid]['verdict'] is not None]
        results[engine] = {
            'model_sha256': snapshot['models'][engine], **snapshot['thresholds'][engine],
            'precision': precision, 'precision_lower_95': lower,
            'recall': recall, 'estimated_window_rate': rate,
            'precision_complete': pc, 'recall_complete': pc and nc,
            'precision_sample': pos, 'recall_sample': neg, 'eligible_windows': N,
            'reviewed': len(answered), 'unknown': sum(r['verdict'] == 'unknown' for r in answered),
            'unknown_rate': sum(s['population'] * s['unknown'] / s['total'] for s in (pos, neg) if s['total']) / N if pc and nc and N else None,
            'goal_met': lower is not None and lower >= snapshot['target_precision'],
            'status': 'evaluated' if pc and predicted else 'limited' if pc else 'pending',
        }
    return results


def update(snapshot, rows):
    snapshot['results'] = evaluate(snapshot, rows)
    done = {r['id'] for r in rows if r['verdict'] is not None}
    ids = active_ids(snapshot)
    snapshot['stage'] = 'complete' if ids.issubset(done) else 'evaluation'
    snapshot['evaluated_count'] = len({iid for engine, result in snapshot['results'].items()
                                     if result['precision_complete']
                                     for iid in snapshot['evaluation_audits'][engine]['positive_ids']})
