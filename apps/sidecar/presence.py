"""Is the species here? A person listened and said "this is its call" at least once.

Evidence is a marked sound from the candidate list, or a "heard" answer in ② or ③.
① answers are not evidence: those sounds were chosen for their AI scores on purpose.
"""
import json
from zoneinfo import ZoneInfo

from reports import window_in_scope, window_key
from species import names_for
from storage import CatalogError, connect

LIST_SIZE = 100


def candidates(root, dataset_id, species_id, source, first=None, last=None):
    """The sounds most likely to be this species, from the top down."""
    from custom_models import list_models
    from results_workflow import context
    from search_runs import site_run, top_hits
    from validation import model_windows
    ctx = context(root, dataset_id, first, last)
    zone = ZoneInfo(ctx['site']['timezone'])
    with connect(root) as db:
        taxon = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
        if taxon is None:
            raise CatalogError('not_found')
        run = site_run(db, ctx['site']['id'], species_id)
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (dataset_id,))}
        hits = top_hits(db, run['id'], LIST_SIZE) if run and source == 'search' else []
    rows = {}
    if source == 'search':
        for hit in hits:
            rows[hit['recording_id'], hit['start_s'], hit['end_s']] = {'score': hit['similarity'], 'models': [], 'run_id': run['id']}
    else:
        lists = []
        for model in ctx['models']:
            windows = model_windows(root, ctx, species_id, model['engine'])[0]
            lists.append((model['engine'], sorted(windows, key=lambda w: -w['score'])[:LIST_SIZE]))
        for model in list_models(root, ctx, species_id):
            if model['applicable']:
                windows = model_windows(root, ctx, species_id, 'custom', model['id'])[0]
                lists.append(('custom', sorted(windows, key=lambda w: -w['score'])[:LIST_SIZE]))
        # Interleave each model's ranking so no single AI decides which sounds come first.
        for rank in range(LIST_SIZE):
            for name, windows in lists:
                if rank < len(windows):
                    w = windows[rank]
                    row = rows.setdefault((w['recording_id'], w['start_s'], w['start_s'] + 15), {'score': None, 'models': [], 'run_id': None})
                    row['models'].append({'engine': name, 'rank': rank + 1, 'score': w['score']})
    summary = summaries(root, ctx, species_id).get(species_id, empty())
    marked = {(e['recording_id'], e['start_s'], e['end_s']) for e in summary['evidence'] if e['origin'] == 'mark'}
    result = []
    for (rid, start, end), row in list(rows.items())[:LIST_SIZE]:
        record = records[rid]
        result.append({**row, 'recording_id': rid, 'start_s': start, 'end_s': end, 'marked': (rid, start, end) in marked,
                       'source': {'id': rid, 'kind': 'recording', 'path': record['path'], 'seconds': record['seconds']},
                       'local_time': window_key(record, start, zone)[1]})
    return {'species': names_for(taxon), 'source': source, 'run_id': run['id'] if run else None, 'rows': result,
            'has_ai': bool(ctx['models']), 'start_date': ctx['start_date'], 'end_date': ctx['end_date'],
            'site': ctx['site'], 'presence': summary}


def set_mark(root, body):
    with connect(root) as db:
        record = db.execute('SELECT * FROM site_recordings WHERE scope_id=? AND id=?', (body.dataset_id, body.recording_id)).fetchone()
        if record is None or not 0 <= body.start_s < body.end_s <= record['seconds'] or round(body.end_s - body.start_s) not in (5, 15):
            raise CatalogError('invalid_interval')
        if body.run_id is not None and not db.execute('''SELECT 1 FROM search_hits h JOIN segments s ON s.id=h.segment_id
                JOIN search_runs r ON r.id=h.run_id WHERE h.run_id=? AND r.species_id=? AND s.start_s=? AND s.end_s=?''',
                (body.run_id, body.species_id, body.start_s, body.end_s)).fetchone():
            raise CatalogError('not_found')
        if body.marked:
            db.execute('''INSERT OR IGNORE INTO presence_marks(dataset_id,species_id,recording_id,start_s,end_s,run_id)
                VALUES(?,?,?,?,?,?)''', (body.dataset_id, body.species_id, body.recording_id, body.start_s, body.end_s, body.run_id))
        else:
            db.execute('DELETE FROM presence_marks WHERE species_id=? AND recording_id=? AND start_s=? AND end_s=?',
                       (body.species_id, body.recording_id, body.start_s, body.end_s))
    return {'marked': body.marked}


def record_miss(root, body):
    """Keep "listened to the top N without confirming" so the report can say so."""
    if body.listened < 1:
        raise CatalogError('invalid_input')
    with connect(root) as db:
        if not db.execute('SELECT 1 FROM datasets WHERE id=?', (body.dataset_id,)).fetchone():
            raise CatalogError('not_found')
        db.execute('INSERT INTO presence_misses(dataset_id,species_id,source,listened,snapshot) VALUES(?,?,?,?,?)',
            (body.dataset_id, body.species_id, body.source, body.listened,
             json.dumps({'start_date': body.start_date.isoformat(), 'end_date': body.end_date.isoformat()})))
    return {'listened': body.listened}


def empty():
    return {'status': 'unchecked', 'evidence': [], 'miss': None}


def summaries(root, ctx, species_id=None):
    """Per species at this site and period: evidence sounds, the latest not-found record, and a status."""
    zone = ZoneInfo(ctx['site']['timezone'])
    result = {}
    with connect(root) as db:
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],))}
        marks = db.execute('''SELECT m.species_id,m.recording_id,m.start_s,m.end_s,m.id,m.created_at,m.run_id FROM presence_marks m
            JOIN datasets d ON d.id=m.dataset_id WHERE d.site_id=? AND (? IS NULL OR m.species_id=?) ORDER BY m.id''',
            (ctx['site']['id'], species_id, species_id)).fetchall()
        heard = db.execute('''SELECT DISTINCT v.species_id,i.recording_id,i.start_s,i.start_s+15 AS end_s,a.id,a.created_at,NULL AS run_id
            FROM validation_items i JOIN validation_sessions v ON v.id=i.session_id JOIN datasets d ON d.id=v.dataset_id
            JOIN window_answers a ON a.id=(SELECT MAX(id) FROM window_answers w
                WHERE w.species_id=v.species_id AND w.recording_id=i.recording_id AND w.start_s=i.start_s)
            WHERE d.site_id=? AND (? IS NULL OR v.species_id=?) AND i.phase='evaluation' AND a.verdict='target' ORDER BY a.id''',
            (ctx['site']['id'], species_id, species_id)).fetchall()
        misses = db.execute('''SELECT m.* FROM presence_misses m JOIN datasets d ON d.id=m.dataset_id
            WHERE d.site_id=? AND (? IS NULL OR m.species_id=?) ORDER BY m.id''', (ctx['site']['id'], species_id, species_id)).fetchall()
    for origin, rows in (('mark', marks), ('check', heard)):
        for row in rows:
            record = records.get(row['recording_id'])
            if not record or not window_in_scope(record, row['start_s'], row['end_s'], zone, ctx['start_date'], ctx['end_date']):
                continue
            item = result.setdefault(row['species_id'], empty())
            if any((e['recording_id'], e['start_s'], e['end_s']) == (row['recording_id'], row['start_s'], row['end_s']) for e in item['evidence']):
                continue
            item['evidence'].append({'recording_id': row['recording_id'], 'start_s': row['start_s'], 'end_s': row['end_s'], 'origin': origin,
                'id': row['id'], 'created_at': row['created_at'], 'run_id': row['run_id'], 'source_path': record['path'],
                'seconds': record['seconds'], 'local_time': window_key(record, row['start_s'], zone)[1]})
    for row in misses:
        result.setdefault(row['species_id'], empty())['miss'] = {'listened': row['listened'], 'source': row['source'], 'created_at': row['created_at']}
    for item in result.values():
        item['status'] = 'confirmed' if item['evidence'] else 'not_found' if item['miss'] else 'unchecked'
    return result
