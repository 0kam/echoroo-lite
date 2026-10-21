"""Read-only result views; only exports freeze reports into permanent history."""
import hashlib
import json
import zlib
from datetime import date
from functools import lru_cache
from types import SimpleNamespace

from reports import detection, options, snapshot_view
from species import canonical_scientific_name, names_for
from storage import CatalogError, checked_source, connect


def fingerprint(root, dataset_id):
    with connect(root) as db:
        for record in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (dataset_id,)):
            checked_source(record)
        revision = db.execute('SELECT version FROM result_revision WHERE id=1').fetchone()[0]
    return cached_fingerprint(root, dataset_id, revision)


@lru_cache(maxsize=8)
def cached_fingerprint(root, dataset_id, revision):
    return cached(root, ['fingerprint-v1', dataset_id, revision], lambda: build_fingerprint(root, dataset_id))


def build_fingerprint(root, dataset_id):
    with connect(root) as db:
        dataset = db.execute('SELECT d.*,s.timezone,s.latitude,s.longitude FROM datasets d JOIN sites s ON s.id=d.site_id WHERE d.id=?', (dataset_id,)).fetchone()
        if dataset is None:
            raise CatalogError('not_found')
        record_rows=db.execute('SELECT * FROM site_recordings WHERE scope_id=? ORDER BY id',(dataset_id,)).fetchall()
        records=[{k:r[k] for k in r.keys() if k!='scope_id'} for r in record_rows]
        analyses = [tuple(r) for r in db.execute('''SELECT a.id,a.recording_id,a.engine,a.metadata FROM analyses a
            JOIN site_recordings r ON r.id=a.recording_id WHERE r.scope_id=? AND a.id IN
            (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine) ORDER BY a.id''', (dataset_id,))]
    return hashlib.sha256(json.dumps([{k:dataset[k] for k in ('site_id','timezone','latitude','longitude')}, records, analyses], sort_keys=True).encode()).hexdigest()


@lru_cache(maxsize=8)
def cached_options(root, dataset_id, revision):
    def build():
        from management import location_status
        result = options(root, dataset_id)
        with connect(root) as db:
            site = dict(result['site'])
            site['location_changed'] = bool(db.execute('SELECT 1 FROM changed_locations WHERE site_id=?',(site['id'],)).fetchone())
            complete, stale = location_status(db, site)
        return {**result, 'analysed_recordings':len(complete), 'location_stale':stale}
    return cached(root, ['options-v2', dataset_id, revision], build)


def context(root, dataset_id, first=None, last=None):
    signature = fingerprint(root, dataset_id)
    with connect(root) as db:
        data_revision = db.execute('SELECT version FROM result_revision WHERE id=1').fetchone()[0]
    result = dict(cached_options(root, dataset_id, data_revision))
    result['mixed_models']=len(result['models'])>len({m['engine'] for m in result['models']})
    result['models']=list({m['engine']:m for m in sorted(result['models'],key=lambda m:m['latest_analysis_id'])}.values())
    result['start_date'] = first or result['first_date']
    result['end_date'] = last or result['last_date']
    if date.fromisoformat(result['start_date']) > date.fromisoformat(result['end_date']):
        raise CatalogError('report_range')
    if (date.fromisoformat(result['end_date']) - date.fromisoformat(result['start_date'])).days >= 366:
        if first or last:
            raise CatalogError('report_range')
        result['start_date'] = date(date.fromisoformat(result['end_date']).year,1,1).isoformat()
    result['fingerprint'] = signature
    with connect(root) as db:
        # MAX and COUNT together notice new rows and deleted ones (undone marks, deleted checks).
        revision=[tuple(db.execute(f'SELECT COALESCE(MAX(id),0),COUNT(*) FROM {table}').fetchone()) for table in
                  ('search_runs','window_answers','validation_sessions','presence_marks','presence_misses','model_labels','custom_models')]
        revision.append([tuple(r) for r in db.execute("SELECT id,json_extract(snapshot,'$.stage') FROM validation_sessions ORDER BY id")])
        revision.append([tuple(r) for r in db.execute('SELECT id,names FROM species ORDER BY id')])
    result['names_revision']=hashlib.sha256(json.dumps(revision[-1]).encode()).hexdigest()
    with connect(root) as db:
        revision.append([tuple(r) for r in db.execute('SELECT id,answers FROM quiz_sessions ORDER BY id')])
    result['revision']=hashlib.sha256(json.dumps([result['fingerprint'],data_revision,revision]).encode()).hexdigest()
    return result


def region_statuses(root, ctx, scientific_names, birdnet=True):
    """Compare app species with the latest saved BirdNET range list in this period."""
    from zoneinfo import ZoneInfo
    from reports import analysis_metadata, scope_records
    from species import birdnet_species_mapping
    names = tuple(scientific_names)
    if not birdnet:
        return dict.fromkeys(names)
    with connect(root) as db:
        records = scope_records([dict(r) for r in db.execute(
            'SELECT * FROM site_recordings WHERE scope_id=?', (ctx['dataset']['id'],))],
            ZoneInfo(ctx['site']['timezone']), ctx['start_date'], ctx['end_date'])
        recording_ids = {record['id'] for record in records}
        analyses = db.execute('''SELECT a.recording_id,a.metadata FROM analyses a
            JOIN site_recordings r ON r.id=a.recording_id
            WHERE r.scope_id=? AND a.engine='birdnet' ORDER BY a.id DESC''', (ctx['dataset']['id'],))
        analysis = next((row for row in analyses if row['recording_id'] in recording_ids), None)
    labels = analysis_metadata(analysis['metadata']).get('species') if analysis else None
    if labels is None:
        return dict.fromkeys(names)
    usual = {str(label).partition('_')[0] for label in labels}
    app_to_birdnet = {app: birdnet for birdnet, app in birdnet_species_mapping().items()}
    return {name: None if name not in app_to_birdnet else
            'usual' if app_to_birdnet[name] in usual else 'unusual' for name in names}


def cached(root, key, build):
    key = hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()
    with connect(root) as db:
        row = db.execute('SELECT snapshot FROM workflow_cache WHERE cache_key=?', (key,)).fetchone()
    if row:
        return json.loads(zlib.decompress(row[0]))
    snapshot = build()
    with connect(root) as db:
        db.execute('INSERT OR REPLACE INTO workflow_cache VALUES(?,?)',
                   (key, zlib.compress(json.dumps(snapshot, ensure_ascii=False, allow_nan=False).encode())))
    return snapshot


def detection_snapshot(root, ctx, model, species_id=None, threshold=.1):
    body = SimpleNamespace(dataset_id=ctx['dataset']['id'], engine=model['engine'], model_sha256=model['model_sha256'],
        threshold=threshold, start_date=date.fromisoformat(ctx['start_date']), end_date=date.fromisoformat(ctx['end_date']),
        species_id=species_id, base_report_id=None)
    return cached(root, ['detection-site-v7', ctx['fingerprint'], ctx['names_revision'], ctx['site']['name'], ctx['dataset']['name'], vars(body)], lambda: detection(root, body, persist=False))


def search_for(root, ctx, species_id):
    from search_runs import site_run
    with connect(root) as db:
        run = site_run(db, ctx['site']['id'], species_id)
    return {'id': run['id'], 'created_at': run['created_at']} if run else None


def custom_snapshot(root, ctx, model):
    from custom_models import report_snapshot
    quality = current_validations(root, ctx).get((model['species']['id'], f"custom:{model['id']}"))
    threshold = threshold_for(quality, {'engine': 'custom', 'model_sha256': model['artifact_sha256']})

    def build():
        result = report_snapshot(root, ctx, model['id'], threshold)
        result['created_at'] = model['created_at']
        return attach_validation(result, quality)
    return cached(root, ['custom-report-v3', ctx['revision'], ctx['start_date'], ctx['end_date'], model['id']], build)


def current_validations(root, ctx):
    """The applicable check per (species, model key), else the latest one."""
    from validation import view_session
    with connect(root) as db:
        rows = db.execute('SELECT v.id,v.species_id,v.model,v.snapshot,v.fingerprint FROM validation_sessions v '
                          'JOIN datasets d ON d.id=v.dataset_id WHERE d.site_id=? ORDER BY v.id DESC', (ctx['site']['id'],)).fetchall()
    chosen = {}
    for row in rows:
        key = (row['species_id'], row['model'])
        if key in chosen and chosen[key]['applicable']:
            continue
        snap = json.loads(row['snapshot'])
        applicable = (not ctx.get('location_stale') and row['fingerprint'] == ctx['fingerprint']
                      and (snap['start_date'], snap['end_date']) == (ctx['start_date'], ctx['end_date']))
        if key not in chosen or applicable:
            chosen[key] = view_session(root, row['id'], ctx=ctx, include_items=False)
    return chosen


def threshold_for(quality, model):
    """Use a decided threshold, otherwise the documented model default."""
    from validation import default_threshold
    if quality and quality['applicable'] and quality['models'].get(model['engine']) == model['model_sha256']:
        chosen = quality['thresholds'].get(model['engine'])
        if chosen and chosen['threshold'] is not None:
            return chosen['threshold']
    return default_threshold(model['engine'])


def attach_validation(snapshot, validation):
    snapshot['validation'] = validation
    result = (validation or {}).get('results', {}).get(snapshot['engine']) if validation else None
    for summary in snapshot['summaries']:
        summary.update(validation_status=validation['status'] if validation else 'not_started',
                       threshold_state='decided' if result and result.get('threshold') is not None else 'default',
                       engine=snapshot['engine'], model_sha256=snapshot['model_sha256'], window_s=snapshot['window_s'],
                       threshold=snapshot['threshold'],
                       density=(result or {}).get('density'), count=(result or {}).get('count'),
                       precision=(result or {}).get('precision'), recall=(result or {}).get('recall'),
                       recall_status=(result or {}).get('recall_status'), auprc=(result or {}).get('auprc'),
                       multiplier=(result or {}).get('multiplier'), answers_per_bin=(result or {}).get('answers_per_bin'),
                       disagreement=(result or {}).get('disagreement'), validation_unknown=(result or {}).get('unknown'))
    return snapshot


def counting_status(validations):
    """One species is countable as soon as any applicable AI check decides how to count it."""
    states = [value['status'] for value in validations if value and value.get('applicable')]
    if 'decided' in states:
        return 'decided'
    if 'in_progress' in states:
        return 'in_progress'
    if states and all(state == 'undecided' for state in states):
        return 'undecided'
    return 'not_started'


def overview(root, dataset_id, first=None, last=None, birdnet=True):
    ctx = context(root, dataset_id, first, last)
    return cached(root, ['overview-v12', dataset_id, ctx['revision'], ctx['start_date'], ctx['end_date'], birdnet],
                  lambda: build_overview(root, dataset_id, ctx, birdnet))


def build_overview(root, dataset_id, ctx, birdnet=True):
    from zoneinfo import ZoneInfo
    from custom_models import list_models
    from presence import empty, summaries
    from reports import effort, scope_records
    quality = current_validations(root, ctx)
    presence = summaries(root, ctx)
    with connect(root) as db:
        records = [dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?', (dataset_id,))]
        named = {r['id']: names_for(r) for r in db.execute('SELECT * FROM species')}
        searched = {r['species_id']: {'id': r['id'], 'created_at': r['created_at']} for r in db.execute(
            'SELECT r.* FROM search_runs r JOIN datasets d ON d.id=r.dataset_id WHERE d.site_id=? ORDER BY r.id', (ctx['site']['id'],))}
    zone = ZoneInfo(ctx['site']['timezone'])
    records = scope_records(records, zone, ctx['start_date'], ctx['end_date'])
    rows = {}

    def add(taxon):
        sid = taxon['id']
        if sid not in rows:
            rows[sid] = {'species': named.get(sid, taxon), 'models': [], 'search': searched.get(sid), 'presence': presence.get(sid, empty())}
        return rows[sid]
    for model in ctx['models']:
        found = {s['species']['id']: s for s in detection_snapshot(root, ctx, model)['summaries']}
        for sid, key in quality:
            if key != model['engine']:
                continue
            threshold = threshold_for(quality[sid, key], model)
            if sid in found and threshold == .1:
                continue
            summaries = detection_snapshot(root, ctx, model, sid, threshold)['summaries']
            summary = next((s for s in summaries if s['species']['id'] == sid), None)
            if summary is None and sid in named:
                summary = {'species': named[sid], 'eligible_windows': 0, 'candidate_windows': 0,
                           'candidate_rate': None, 'recorded_days': 0, 'candidate_days': 0,
                           'quality': 'not_evaluated'}
            if summary is not None:
                found[sid] = summary
        for summary in found.values():
            sid = summary['species']['id']
            validation=quality.get((sid, model['engine']))
            add(summary['species'])['models'].append({**summary, 'species': None, **model, 'key': model['engine'], 'model_id': None,
                'threshold': threshold_for(validation, model), 'validation': validation,
                'validation_status': validation['status'] if validation else 'not_started'})
    for model in list_models(root, ctx):
        entry = {'engine': 'custom', 'key': f"custom:{model['id']}", 'model_id': model['id'], 'created_at': model['created_at'],
                 'applicable': model['applicable'], 'reason': model['reason'],
                 'validation': quality.get((model['species']['id'], f"custom:{model['id']}"))}
        entry['validation_status']=entry['validation']['status'] if entry['validation'] else 'not_started'
        if model['applicable']:
            snap = custom_snapshot(root, ctx, model)
            entry.update({k: v for k, v in snap['summaries'][0].items() if k != 'species'}, threshold=snap['threshold'])
        add(model['species'])['models'].append(entry)
    for sid in [*searched, *presence, *(sid for sid, _ in quality)]:
        if sid in named:
            add(named[sid])
    regions = region_statuses(root, ctx, (row['species']['scientific_name'] for row in rows.values()), birdnet)
    for row in rows.values():
        row['counting_status'] = counting_status(model.get('validation') for model in row['models'])
        row['region'] = regions[row['species']['scientific_name']]
    seconds = sum(value for r in records for key, value in effort(r, zone) if ctx['start_date'] <= key[0] <= ctx['end_date'])
    return {**ctx, 'records': len(records), 'recorded_seconds': seconds,
            'species': sorted(rows.values(), key=lambda r: (-max([m.get('candidate_windows') or 0 for m in r['models']] or [0]),
                                                           -len(r['presence']['evidence']), r['species']['scientific_name']))}


def species_result(root, dataset_id, species_id, first=None, last=None, engine='auto', cell_date=None, hour=None, model_id=None, birdnet=True):
    """One species: presence, each model's check, and the time chart of the chosen model."""
    from custom_models import list_models
    from presence import empty, summaries
    ctx = context(root, dataset_id, first, last)
    with connect(root) as db:
        row = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        taxon = names_for(row)
    from reports import candidate_list
    quality = current_validations(root, ctx)
    custom = [m for m in list_models(root, ctx, species_id) if m['applicable']]
    with connect(root) as db:
        # Only models whose species list includes this species can count it.
        listed = [m for m in ctx['models'] if any(canonical_scientific_name(m['engine'], label) == taxon['scientific_name'] for label in
                  candidate_list(db, db.execute('SELECT * FROM analyses WHERE id=?', (m['latest_analysis_id'],)).fetchone()) or [])]
    checks = [{'engine': m['engine'], 'key': m['engine'], 'model_id': None, 'model_sha256': m['model_sha256'],
               'validation': quality.get((species_id, m['engine']))} for m in listed]
    checks += [{'engine': 'custom', 'key': f"custom:{m['id']}", 'model_id': m['id'], 'model_sha256': m['artifact_sha256'],
                'created_at': m['created_at'], 'validation': quality.get((species_id, f"custom:{m['id']}"))} for m in custom]
    for check in checks:
        check['status']=check['validation']['status'] if check['validation'] else 'not_started'
    overall_status = counting_status(check.get('validation') for check in checks)
    if engine == 'auto':
        engine = next((m['engine'] for m in listed if any(
            s['species']['id'] == species_id and s['candidate_windows'] for s in detection_snapshot(root, ctx, m)['summaries'])), None)
        if engine is None and custom and model_id is None:
            engine, model_id = 'custom', custom[0]['id']
        engine = engine or next((m['engine'] for m in listed), None)
    selected = next((c for c in checks if c['engine'] == engine and (engine != 'custom' or c['model_id'] == model_id)), None)
    snap = None
    if selected and engine == 'custom':
        snap = custom_snapshot(root, ctx, next(m for m in custom if m['id'] == model_id))
    elif selected:
        model = next(m for m in ctx['models'] if m['engine'] == engine)
        snap = detection_snapshot(root, ctx, model, species_id, threshold_for(selected['validation'], model))
        attach_validation(snap, selected['validation'])
    with connect(root) as db:
        report = snapshot_view(db, snap, cell_date=cell_date, hour=hour) if snap else None
    region = region_statuses(root, ctx, [taxon['scientific_name']], birdnet)[taxon['scientific_name']]
    return {**ctx, 'species': taxon, 'checks': checks, 'counting_status': overall_status,
            'selected': selected['key'] if selected else None, 'report': report,
            'search': search_for(root, ctx, species_id), 'presence': summaries(root, ctx, species_id).get(species_id, empty()),
            'region': region}
