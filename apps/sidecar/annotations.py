"""Shared, versioned time ranges; omitted taxa always remain unassessed.

Common samples are for discovery and threshold calibration. Whole recordings
(whole days when available) are reserved before sampling for final evaluation.
"""
import json
import math
import secrets
import tempfile
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from reports import analysis_metadata, classification_batches, scope_records, window_in_scope, window_key
from sampling import draw_plan, strata_for
from species import names_for
from storage import CatalogError, checked_source, connect

SAMPLE_SIZE = 30


def partition_records(records, zone, seed, previously_used=()):
    """Reserve unseen recordings before any human labels, grouping by local day."""
    days = defaultdict(list)
    for record in records.values():
        days[window_key(record, 0, zone)[0][0]].append(record['id'])
    groups = list(days.values()) if len(days) > 1 else [[rid] for rid in records]
    used=set(previously_used)
    forced={i for i,group in enumerate(groups) if used.intersection(group)}
    order=np.random.default_rng(seed).permutation([i for i in range(len(groups)) if i not in forced]).tolist()
    split=max(0,max(1,len(groups)//2)-len(forced))
    calibration = sorted(rid for i in [*forced,*order[:split]] for rid in groups[i])
    evaluation = sorted(rid for i in order[split:] for rid in groups[i])
    return calibration, evaluation, 'local_day' if len(days) > 1 else 'recording'


def build_plan(root, ctx, seed):
    from results_workflow import current_validations, threshold_for
    quality = current_validations(root, ctx)
    zone = ZoneInfo(ctx['site']['timezone'])
    with connect(root) as db, tempfile.TemporaryDirectory(prefix='echoroo-sampling-') as temp:
        records = {r['id']: dict(r) for r in scope_records(
            [dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=? ORDER BY recorded_at,id', (ctx['dataset']['id'],))],
            zone, ctx['start_date'], ctx['end_date']) if r['seconds'] >= 15}
        items = [{'recording_id': rid, 'start_s': float(start), 'predictions': {}}
                 for rid, record in records.items() for start in range(0, int(record['seconds']) - 14, 15)
                 if window_in_scope(record, start, start + 15, zone, ctx['start_date'], ctx['end_date'])]
        if not items:
            raise CatalogError('annotation_no_intervals')
        used_ids = {i['recording_id'] for i in items}
        records = {rid: r for rid, r in records.items() if rid in used_ids}
        for record in records.values():
            checked_source(record)
        previously_used={r[0] for r in db.execute('''SELECT DISTINCT i.recording_id FROM validation_items i
            JOIN validation_answers a ON a.item_id=i.id WHERE i.phase='calibration'
            UNION SELECT DISTINCT i.recording_id FROM annotation_items i JOIN annotation_answers a ON a.item_id=i.id
            UNION SELECT DISTINCT a.recording_id FROM search_judgements j JOIN segments s ON s.id=j.segment_id JOIN analyses a ON a.id=s.analysis_id''')}
        calibration, evaluation, split_unit = partition_records(records, zone, seed, previously_used)
        calibration_ids = set(calibration)
        sampled_population = [i for i in items if i['recording_id'] in calibration_ids]
        lookup = {(i['recording_id'], i['start_s']): i for i in items}
        vector_rows = {(i['recording_id'], i['start_s']): j for j, i in enumerate(sampled_population)}
        # A recording-year needs about 215 MB here; a disk-backed array bounds RAM.
        vectors = np.memmap(Path(temp)/'vectors.f32', dtype='float32', mode='w+', shape=(len(sampled_population), 1536))
        analyses = [dict(r) for r in db.execute('''SELECT a.* FROM analyses a JOIN site_recordings r ON r.id=a.recording_id
            WHERE r.scope_id=? AND a.id IN (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)
            ORDER BY a.recording_id,a.engine''', (ctx['dataset']['id'],)) if r['recording_id'] in records]
        taxa = {r['scientific_name']: names_for(r) for r in db.execute('SELECT * FROM species ORDER BY scientific_name')}
        palette, conditions = set(), {}
        for engine in ('birdnet', 'perch'):
            rows = [a for a in analyses if a['engine'] == engine]
            for analysis, allowed, arrays in classification_batches(root, db, rows, records):
                if allowed is None:
                    continue
                labels, starts, scores, _ = arrays
                model = {'engine': engine, 'model_sha256': analysis_metadata(analysis['metadata'])['model_sha256']}
                thresholds = np.array([threshold_for(quality.get(taxa[str(label)]['id']), model) if str(label) in taxa else .1 for label in labels])
                conditions[engine] = {str(label): float(value) for label, value in zip(labels, thresholds)}
                for start, score in zip(starts, scores):
                    item = lookup.get((analysis['recording_id'], float(start)))
                    if item is None:
                        continue
                    predicted = frozenset(str(label) for label in labels[score >= thresholds])
                    item['predictions'][engine] = predicted
                    palette.update(predicted)
        for analysis in analyses:
            rid = analysis['recording_id']
            if analysis['engine'] != 'perch' or rid not in calibration_ids:
                continue
            segments = {float(r['start_s']): np.frombuffer(r['embedding'], dtype='<f4') for r in db.execute(
                '''SELECT s.start_s,v.embedding FROM segments s JOIN vectors v ON v.rowid=s.id
                   WHERE s.analysis_id=? AND ABS(s.end_s-s.start_s-5)<0.000001''', (analysis['id'],))}
            for start in range(0, int(records[rid]['seconds']) - 14, 15):
                index = vector_rows.get((rid, float(start)))
                if index is None or not all(start + offset in segments for offset in (0, 5, 10)):
                    continue
                matrix = np.stack([segments[start + offset] for offset in (0, 5, 10)])
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                if not np.isfinite(matrix).all() or np.any(norms == 0):
                    continue
                mean = (matrix / norms).mean(axis=0)
                norm = np.linalg.norm(mean)
                if norm > 0:
                    vectors[index] = mean / norm
        for item in sampled_population:
            a, b = item['predictions'].get('birdnet'), item['predictions'].get('perch')
            item['group'] = 'unavailable' if a is None or b is None else 'low' if not a and not b else 'agreement' if a == b else 'difference'
        strata, fits = strata_for(np.array([i['group'] for i in sampled_population]), vectors, seed + 1)
        del vectors
        order, totals = draw_plan(strata, SAMPLE_SIZE, seed + 2)
        population = [{k: v for k, v in item.items() if k != 'predictions'} | {'stratum': str(strata[i])} for i, item in enumerate(sampled_population)]
        return {'method': 'shared_acoustic_calibration_v1', 'seed': str(seed), 'window_s': 15,
                'site': ctx['site'], 'start_date': ctx['start_date'], 'end_date': ctx['end_date'],
                'recording_ids': sorted(records), 'recordings': list(records.values()),
                'calibration_recording_ids': calibration, 'evaluation_recording_ids': evaluation, 'split_unit': split_unit,
                'previously_used_recording_ids': sorted(previously_used.intersection(records)),
                'analysis_ids': [a['id'] for a in analyses], 'models': ctx['models'],
                'palette': [taxa[s] for s in sorted(palette) if s in taxa], 'palette_conditions': conditions,
                'population': population, 'strata': totals, 'fits': fits, 'sample_indexes': order}


def start_session(root, body):
    from results_workflow import context
    ctx = context(root, body.dataset_id, body.start_date.isoformat(), body.end_date.isoformat())
    if ctx['location_stale']:
        raise CatalogError('location_reanalysis')
    if ctx['mixed_models']:
        raise CatalogError('validation_mixed_models')
    with connect(root) as db:
        for row in db.execute('''SELECT a.* FROM annotation_sessions a JOIN datasets d ON d.id=a.dataset_id
            WHERE d.site_id=? AND a.fingerprint=? ORDER BY a.id DESC''', (ctx['site']['id'], ctx['fingerprint'])):
            snap = json.loads(row['snapshot'])
            if (snap['start_date'], snap['end_date']) == (ctx['start_date'], ctx['end_date']):
                return view_session(root, row['id'])
    snap = build_plan(root, ctx, secrets.randbits(63))
    if context(root, body.dataset_id, ctx['start_date'], ctx['end_date'])['revision'] != ctx['revision']:
        raise CatalogError('workflow_changed')
    with connect(root) as db:
        sid = db.execute('INSERT INTO annotation_sessions(dataset_id,fingerprint,snapshot) VALUES(?,?,?)',
                         (body.dataset_id, ctx['fingerprint'], json.dumps(snap))).lastrowid
        for position, index in enumerate(snap['sample_indexes']):
            item = snap['population'][index]
            db.execute('INSERT INTO annotation_items(session_id,position,recording_id,start_s,end_s,stratum) VALUES(?,?,?,?,?,?)',
                       (sid, position, item['recording_id'], item['start_s'], item['start_s'] + 15, item['stratum']))
    return view_session(root, sid)


def latest_answers(db, sid):
    return [dict(r) for r in db.execute('''SELECT i.*,a.id AS answer_id,a.ranges,a.complete,a.palette_ids,a.quiz_snapshot,a.created_at AS answered_at
        FROM annotation_items i LEFT JOIN annotation_answers a ON a.id=(SELECT MAX(id) FROM annotation_answers WHERE item_id=i.id)
        WHERE i.session_id=? ORDER BY i.position''', (sid,))]


def session_row(db, sid):
    row = db.execute('SELECT * FROM annotation_sessions WHERE id=?', (sid,)).fetchone()
    if not row:
        raise CatalogError('not_found')
    return row, json.loads(row['snapshot'])


def palette_for(db, snapshot, rows):
    ids = {s['id'] for s in snapshot['palette']}
    for row in rows:
        ids.update(json.loads(row['palette_ids'] or '[]'))
        ids.update(r['species_id'] for r in json.loads(row['ranges'] or '[]') if r['species_id'])
    return [names_for(r) for r in db.execute('SELECT * FROM species ORDER BY scientific_name') if r['id'] in ids]


def view_session(root, sid, item_id=None):
    from results_workflow import fingerprint
    with connect(root) as db:
        row, snap = session_row(db, sid)
        rows = latest_answers(db, sid)
        palette = palette_for(db, snap, rows)
        current = next((r for r in rows if not r['complete']), None)
        question = next((r for r in rows if r['id'] == item_id), None) if item_id is not None else current
        if item_id is not None and (not question or (not question['complete'] and question != current)):
            raise CatalogError('validation_answer_order')
        if question:
            record = dict(db.execute('SELECT * FROM recordings WHERE id=?', (question['recording_id'],)).fetchone())
            question = {k: question[k] for k in ('id', 'position', 'start_s', 'end_s', 'complete')} | {
                'version': question['answer_id'] or 0, 'ranges': json.loads(question['ranges'] or '[]'),
                'source': {'id': record['id'], 'kind': 'recording', 'path': record['path'], 'seconds': record['seconds']},
                'local_time': window_key(record, question['start_s'], ZoneInfo(snap['site']['timezone']))[1]}
        heard = defaultdict(int)
        for item in rows:
            if not item['complete']:
                continue
            for taxon in {r['species_id'] for r in json.loads(item['ranges'] or '[]') if r['species_id']}:
                heard[taxon] += 1
        return {'id': sid, 'dataset_id': row['dataset_id'], 'site': snap['site'], 'start_date': snap['start_date'], 'end_date': snap['end_date'],
                'total': len(rows), 'completed': sum(bool(r['complete']) for r in rows), 'question': question,
                'palette': palette, 'heard': [{'species': s, 'intervals': heard[s['id']]} for s in palette if heard[s['id']]],
                'history': [{'id': r['id'], 'position': r['position']} for r in rows if r['complete']],
                'can_evaluate': bool(snap['evaluation_recording_ids']) and sum(bool(r['complete']) for r in rows) >= min(10, len(rows)),
                'has_holdout': bool(snap['evaluation_recording_ids']),
                'applicable': row['fingerprint'] == fingerprint(root, row['dataset_id'])}


def clean_ranges(db, ranges, start, end):
    result = []
    for value in ranges:
        a, b, sid = value['start_s'], value['end_s'], value['species_id']
        if not (math.isfinite(a) and math.isfinite(b) and start <= a < b <= end):
            raise CatalogError('invalid_interval')
        if sid is not None and not db.execute('SELECT 1 FROM species WHERE id=?', (sid,)).fetchone():
            raise CatalogError('not_found')
        result.append({'species_id': sid, 'start_s': float(a), 'end_s': float(b)})
    return sorted(result, key=lambda r: (r['start_s'], r['end_s'], r['species_id'] or ''))


def save_answer(root, sid, body):
    from quiz import report_results
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        _, snapshot = session_row(db, sid)
        rows = latest_answers(db, sid)
        row = next((r for r in rows if r['id'] == body.item_id), None)
        if not row or (not row['complete'] and row != next((r for r in rows if not r['complete']), None)):
            raise CatalogError('validation_answer_order')
        if (row['answer_id'] or 0) != body.version:
            raise CatalogError('annotation_changed')
        checked_source(db.execute('SELECT * FROM recordings WHERE id=?', (row['recording_id'],)).fetchone())
        ranges = clean_ranges(db, [r.model_dump() for r in body.ranges], row['start_s'], row['end_s'])
        ids = sorted({r['species_id'] for r in ranges if r['species_id']})
        palette_ids = set(body.palette_ids) | {s['id'] for s in palette_for(db, snapshot, rows)} | set(ids)
        if not palette_ids.issubset({r[0] for r in db.execute('SELECT id FROM species')}):
            raise CatalogError('not_found')
        additions = sorted(palette_ids - {s['id'] for s in snapshot['palette']})
        quiz = report_results(db, ids, include_sessions=True)
        db.execute('INSERT INTO annotation_answers(item_id,ranges,complete,quiz_snapshot,palette_ids) VALUES(?,?,?,?,?)',
                   (row['id'], json.dumps(ranges), int(body.complete), json.dumps(quiz), json.dumps(additions)))
    return view_session(root, sid)


def evidence(root, ctx, species_id=None):
    """Exact human ranges, including AI-unlisted taxa. No population estimates."""
    zone = ZoneInfo(ctx['site']['timezone'])
    result = []
    with connect(root) as db:
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],))}
        taxa = {r['id']: names_for(r) for r in db.execute('SELECT * FROM species')}
        queries = [
            ('''SELECT i.recording_id,i.start_s,i.end_s,a.ranges,a.id AS answer_id,a.created_at,a.quiz_snapshot,i.session_id
                FROM annotation_items i JOIN annotation_answers a ON a.id=(SELECT MAX(id) FROM annotation_answers WHERE item_id=i.id)
                WHERE a.complete=1''', 'annotation'),
            ('''SELECT i.recording_id,i.start_s,i.start_s+json_extract(v.snapshot,'$.window_s') AS end_s,
                a.ranges,a.id AS answer_id,a.created_at,a.quiz_snapshot,i.session_id,v.snapshot FROM validation_items i
                JOIN validation_sessions v ON v.id=i.session_id JOIN validation_answers a ON a.id=(SELECT MAX(id) FROM validation_answers WHERE item_id=i.id)
                WHERE a.ranges IS NOT NULL''', 'validation')]
        seen = set()
        for query, origin in queries:
            for row in db.execute(query):
                if origin=='validation':
                    from validation import annotation_source_current
                    if not annotation_source_current(db,json.loads(row['snapshot'])):continue
                record = records.get(row['recording_id'])
                if not record or not window_in_scope(record, row['start_s'], row['end_s'], zone, ctx['start_date'], ctx['end_date']):
                    continue
                for interval in json.loads(row['ranges'] or '[]'):
                    sid = interval['species_id']
                    if species_id is not None and sid != species_id:
                        continue
                    key = (record['id'], sid, interval['start_s'], interval['end_s'])
                    if key in seen:
                        continue
                    seen.add(key)
                    taxon = taxa.get(sid)
                    result.append({**interval, 'species': taxon, 'recording_id': record['id'], 'source_path': record['path'],
                                   'seconds': record['seconds'], 'local_time': window_key(record, interval['start_s'], zone)[1],
                                   'review_start_s': row['start_s'], 'review_end_s': row['end_s'], 'coverage': 'partial_positive_ranges',
                                   'origin': origin, 'session_id': row['session_id'], 'answer_id': row['answer_id'],
                                   'answered_at': row['created_at'], 'quiz_at_answer': json.loads(row['quiz_snapshot'] or 'null')})
    return result
