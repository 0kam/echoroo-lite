"""Is the species here? A person listened and said "this is its call" at least once.

Evidence is any current "present" label of the species at the site: a marked sound from the candidate list, a "heard"
check answer, a "voice" answer on the species-AI page or a species given on the sound map.
"""
import json
from zoneinfo import ZoneInfo

from reports import window_in_scope, window_key
from labels import current, put, ref, remove
from species import names_for
from storage import CatalogError, connect

LIST_SIZE = 100
# Where a "present" answer was given, as the species page names it.
ORIGINS = {'species_mark': 'mark', 'validation': 'check', 'training': 'label', 'sound_map': 'map',
           'rollup_fix': 'rollup'}


def merge_candidates(rows):
    """Collapse overlapping sounds while retaining a native interval that can be marked."""
    groups = []
    for row in rows:
        matches = [group for group in groups if group['recording_id'] == row['recording_id']
                   and row['start_s'] < group['_end'] and group['_start'] < row['end_s']]
        if not matches:
            groups.append({**row, '_start': row['start_s'], '_end': row['end_s'], '_rows': [row]})
            continue
        group = matches[0]
        group['_rows'].append(row)
        group['_start'] = min(group['_start'], row['start_s'])
        group['_end'] = max(group['_end'], row['end_s'])
        for extra in matches[1:]:
            group['_rows'].extend(extra['_rows'])
            group['_start'] = min(group['_start'], extra['_start'])
            group['_end'] = max(group['_end'], extra['_end'])
            groups.remove(extra)
    result = []
    for group in groups:
        rows = group.pop('_rows')
        representative = next((row for row in rows if row['marked']), rows[0])
        models = [model for row in rows for model in row['models']]
        result.append({**representative, 'models': models})
    return result


def candidates(root, dataset_id, species_id, source, first=None, last=None):
    """The sounds most likely to be this species, from the top down."""
    from custom_models import list_models
    from results_workflow import context, current_validations, listed_models, region_statuses, threshold_for
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
        quality = current_validations(root, ctx)
        from models import default_threshold
        from results_workflow import cached

        def listen_from(validation, model):
            # Listening for presence is not counting: an undecided check keeps the model default here.
            threshold = threshold_for(validation, model)
            return default_threshold(model['engine']) if threshold is None else threshold
        for model in ctx['models']:
            threshold = listen_from(quality.get((species_id, model['engine'])), model)
            windows = cached(root, ['presence-top-v1', ctx['fingerprint'], ctx['start_date'], ctx['end_date'],
                                    species_id, model['engine'], model['model_sha256']],
                             lambda model=model: sorted(model_windows(root, ctx, species_id, model['engine'])[0],
                                                        key=lambda w: -w['score'])[:LIST_SIZE])
            lists.append((model['engine'], [w for w in windows if w['score'] >= threshold]))
        for model in list_models(root, ctx, species_id):
            if model['applicable']:
                threshold = listen_from(quality.get((species_id, f"custom:{model['id']}")),
                                        {'engine': 'custom', 'model_sha256': model['artifact_sha256']})
                windows = cached(root, ['presence-top-v1', ctx['fingerprint'], ctx['start_date'], ctx['end_date'],
                                        species_id, 'custom', model['id'], model['artifact_sha256']],
                                 lambda model=model: sorted(model_windows(root, ctx, species_id, 'custom', model['id'])[0],
                                                            key=lambda w: -w['score'])[:LIST_SIZE])
                lists.append(('custom', [w for w in windows if w['score'] >= threshold]))
        # Interleave each model's ranking so no single AI decides which sounds come first.
        for rank in range(LIST_SIZE):
            for name, windows in lists:
                if rank < len(windows):
                    w = windows[rank]
                    row = rows.setdefault((w['recording_id'], w['start_s'], w['end_s']), {'score': None, 'models': [], 'run_id': None})
                    row['models'].append({'engine': name, 'rank': rank + 1, 'score': w['score']})
    summary = summaries(root, ctx, species_id).get(species_id, empty())
    marked = {(e['recording_id'], e['start_s'], e['end_s']) for e in summary['evidence'] if e['origin'] == 'mark'}
    result = []
    for (rid, start, end), row in rows.items():
        record = records[rid]
        result.append({**row, 'recording_id': rid, 'start_s': start, 'end_s': end, 'marked': (rid, start, end) in marked,
                       'source': {'id': rid, 'kind': 'recording', 'path': record['path'], 'seconds': record['seconds']},
                       'local_time': window_key(record, start, zone)[1]})
    result = merge_candidates(result)[:LIST_SIZE]
    region = region_statuses(root, ctx, [taxon['scientific_name']])[taxon['scientific_name']]
    return {'species': names_for(taxon), 'source': source, 'run_id': run['id'] if run else None, 'rows': result,
            'has_ai': bool(ctx['models']),
            # Whether any AI here can recognise this species: a built-in AI that lists it, or an AI made for it.
            'ai_covers': bool(listed_models(root, ctx, taxon['scientific_name'])) or any(m['applicable'] for m in list_models(root, ctx, species_id)),
            'start_date': ctx['start_date'], 'end_date': ctx['end_date'],
            'site': ctx['site'], 'presence': summary, 'region': region}


def set_mark(root, body):
    if body.run_id is not None:
        from search_runs import get_run
        get_run(root, body.run_id)
    with connect(root) as db:
        record = db.execute('SELECT * FROM site_recordings WHERE scope_id=? AND id=?', (body.dataset_id, body.recording_id)).fetchone()
        if record is None or not 0 <= body.start_s < body.end_s <= record['seconds'] or round(body.end_s - body.start_s) not in (3, 5):
            raise CatalogError('invalid_interval')
        if body.run_id is not None and not db.execute('''SELECT 1 FROM search_hits h JOIN segments s ON s.id=h.segment_id
                JOIN search_runs r ON r.id=h.run_id WHERE h.run_id=? AND r.species_id=? AND s.start_s=? AND s.end_s=?''',
                (body.run_id, body.species_id, body.start_s, body.end_s)).fetchone():
            raise CatalogError('not_found')
        if body.marked:
            put(db, body.species_id, body.recording_id, body.start_s, body.end_s, 'present', 'species_mark', {'run_id': body.run_id})
        else:
            remove(db, body.species_id, body.recording_id, body.start_s, body.end_s, 'species_mark')
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


def delete_misses(root, body):
    """Take back "could not confirm" for a species. The status reads every record at the site, so all of them go."""
    with connect(root) as db:
        deleted = db.execute('''DELETE FROM presence_misses WHERE species_id=? AND dataset_id IN
            (SELECT id FROM datasets WHERE site_id=(SELECT site_id FROM datasets WHERE id=?))''', (body.species_id, body.dataset_id)).rowcount
    return {'deleted': deleted}


def empty():
    return {'status': 'unchecked', 'evidence': [], 'miss': None}


def summaries(root, ctx, species_id=None):
    """Per species at this site and period: evidence sounds, the latest not-found record, and a status."""
    zone = ZoneInfo(ctx['site']['timezone'])
    result = {}
    from models import selected_engines
    from custom_models import list_models
    from search_runs import site_run
    visible = set(selected_engines(root)) | {f"custom:{m['id']}" for m in list_models(root, ctx)}
    with connect(root) as db:
        records = {r['id']: dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],))}
        heard = current(db, ctx['dataset']['id'], species_id, 'present')
        models = dict(db.execute('SELECT id,model FROM validation_sessions'))
        runs = {}

        def counts(row):
            data = ref(row)
            if row['source'] == 'species_mark' and data.get('run_id') is not None:
                # A mark on a deleted or outdated search does not count.
                if row['taxon_id'] not in runs:
                    runs[row['taxon_id']] = site_run(db, ctx['site']['id'], row['taxon_id'])
                return bool(runs[row['taxon_id']]) and runs[row['taxon_id']]['id'] == data['run_id']
            # A check answer counts while its AI is shown.
            model = models.get(data.get('session_id')) if row['source'] == 'validation' else None
            return model is None or model in visible
        # An answer that stops at the family ("〇〇科の一種") is not evidence of any species.
        families = {r[0] for r in db.execute("SELECT id FROM species WHERE rank<>'species'")}  # and "no bird sound"
        heard = [row for row in heard if row['taxon_id'] not in families and counts(row)]
        misses = db.execute('''SELECT m.* FROM presence_misses m JOIN datasets d ON d.id=m.dataset_id
            WHERE d.site_id=? AND (? IS NULL OR m.species_id=?) ORDER BY m.id''', (ctx['site']['id'], species_id, species_id)).fetchall()
        misses = [r for r in misses if r['source'] != 'search' or site_run(db, ctx['site']['id'], r['species_id'])]
    for row in heard:
        record = records.get(row['recording_id'])
        if not record or not window_in_scope(record, row['start_s'], row['end_s'], zone, ctx['start_date'], ctx['end_date']):
            continue
        item = result.setdefault(row['taxon_id'], empty())
        item['evidence'].append({'recording_id': row['recording_id'], 'start_s': row['start_s'], 'end_s': row['end_s'],
            'origin': ORIGINS.get(row['source'], 'mark'), 'id': row['id'], 'created_at': row['created_at'],
            'run_id': ref(row).get('run_id'), 'source_path': record['path'],
            'seconds': record['seconds'], 'local_time': window_key(record, row['start_s'], zone)[1]})
    for row in misses:
        result.setdefault(row['species_id'], empty())['miss'] = {'listened': row['listened'], 'source': row['source'], 'created_at': row['created_at']}
    for item in result.values():
        item['status'] = 'confirmed' if item['evidence'] else 'not_found' if item['miss'] else 'unchecked'
    return result
