"""Read-only result views; only exports freeze reports into permanent history."""
import hashlib
import json
import zlib
from datetime import date
from functools import lru_cache
from types import SimpleNamespace

from reports import detection, options, search_report, snapshot_view
from species import names_for
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
        revision=[db.execute(f'SELECT COALESCE(MAX(id),0) FROM {table}').fetchone()[0] for table in
                  ('search_runs','search_judgements','validation_answers','validation_sessions','annotation_answers','annotation_sessions','custom_models')]
        revision.append([tuple(r) for r in db.execute("SELECT id,json_extract(snapshot,'$.stage') FROM validation_sessions ORDER BY id")])
        revision.append([tuple(r) for r in db.execute('SELECT id,names FROM species ORDER BY id')])
    result['names_revision']=hashlib.sha256(json.dumps(revision[-1]).encode()).hexdigest()
    with connect(root) as db:
        revision.append([tuple(r) for r in db.execute('SELECT id,answers FROM quiz_sessions ORDER BY id')])
    result['revision']=hashlib.sha256(json.dumps([result['fingerprint'],data_revision,revision]).encode()).hexdigest()
    return result


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
    return cached(root, ['detection-site-v5', ctx['fingerprint'], ctx['names_revision'], ctx['site']['name'], ctx['dataset']['name'], vars(body)], lambda: detection(root, body, persist=False))


def searches(root, dataset_id, species_id=None):
    with connect(root) as db:
        rows = db.execute('''SELECT r.id,r.species_id,r.created_at FROM search_runs r WHERE r.dataset_id IN (SELECT id FROM datasets WHERE site_id=(SELECT site_id FROM datasets WHERE id=?))
            AND (? IS NULL OR r.species_id=?) ORDER BY r.id DESC''', (dataset_id,species_id,species_id)).fetchall()
        return [{**dict(r), 'species': names_for(db.execute('SELECT * FROM species WHERE id=?',(r['species_id'],)).fetchone())} for r in rows]


def search_snapshot(root, ctx, run_id):
    with connect(root) as db:
        run = db.execute('SELECT dataset_id,species_id FROM search_runs WHERE id=?',(run_id,)).fetchone()
        if not run or not db.execute('SELECT 1 FROM datasets WHERE id=? AND site_id=?',(run[0],ctx['site']['id'])).fetchone():
            raise CatalogError('not_found')
        cutoff = db.execute('SELECT COALESCE(MAX(id),0) FROM search_judgements').fetchone()[0]
    quality=current_validations(root,ctx,run_id=run_id).get(run['species_id'])
    def build():
        threshold=threshold_for(quality,{'engine':'search','model_sha256':quality['models']['search']}) if quality else None
        body = SimpleNamespace(run_id=run_id, start_date=date.fromisoformat(ctx['start_date']), end_date=date.fromisoformat(ctx['end_date']), threshold=threshold)
        result = search_report(root, body, persist=False)
        result['validation']=quality
        return result
    return cached(root, ['search-site-v5',ctx['revision'],run_id,ctx['start_date'],ctx['end_date'],cutoff], build)


def custom_snapshot(root, ctx, model_id):
    from custom_models import report_snapshot
    def build():
        result = report_snapshot(root, ctx, model_id)
        result['validation'] = current_validations(root, ctx, model_id=model_id).get(result['species']['id'])
        return result
    return cached(root, ['custom-report-v1', ctx['revision'], ctx['start_date'], ctx['end_date'], model_id], build)


def current_validations(root, ctx, run_id=None, model_id=None):
    from validation import view_session
    with connect(root) as db:
        rows = db.execute('SELECT id,species_id,snapshot,fingerprint FROM validation_sessions WHERE dataset_id IN (SELECT id FROM datasets WHERE site_id=(SELECT site_id FROM datasets WHERE id=?)) ORDER BY id DESC', (ctx['dataset']['id'],)).fetchall()
    chosen={}
    for row in rows:
        snap=json.loads(row['snapshot'])
        if snap.get('run_id')!=run_id or snap.get('model_id')!=model_id:continue
        sid=row['species_id']
        if sid in chosen and chosen[sid]['applicable']:
            continue
        applicable=not ctx.get('location_stale') and row['fingerprint']==ctx['fingerprint'] and (snap['start_date'],snap['end_date'])==(ctx['start_date'],ctx['end_date'])
        if sid not in chosen or applicable:
            chosen[sid]=view_session(root,row['id'],ctx=ctx,include_items=False)
    return chosen


def threshold_for(quality, model):
    if quality and quality['applicable']:
        result = quality.get('results',{}).get(model['engine'])
        if result and result['model_sha256']==model['model_sha256']:
            return result['threshold']
        if quality.get('models',{}).get(model['engine'])==model['model_sha256']:
            adjusted=quality.get('thresholds',{}).get(model['engine'])
            if adjusted:return adjusted['threshold']
    return None if model['engine']=='search' else .1


def overview(root, dataset_id, first=None, last=None):
    ctx = context(root,dataset_id,first,last)
    return cached(root, ['overview-v7', dataset_id, ctx['revision'], ctx['start_date'], ctx['end_date']],
                  lambda: build_overview(root, dataset_id, ctx))


def build_overview(root, dataset_id, ctx):
    quality = current_validations(root,ctx)
    with connect(root) as db:
        records = [dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?',(dataset_id,))]
        named = {r['id']: names_for(r) for r in db.execute('SELECT * FROM species')}
    from reports import scope_records,effort
    from zoneinfo import ZoneInfo
    zone=ZoneInfo(ctx['site']['timezone'])
    records=scope_records(records,zone,ctx['start_date'],ctx['end_date'])
    rows = {}
    def add(taxon):
        sid = taxon['id']
        if sid not in rows:
            rows[sid]={'species':named.get(sid,taxon),'models':[],'custom_models':[],'searches':[],'confirmed':0,'annotated':0,'validation':quality.get(sid)}
        return rows[sid]
    for model in ctx['models']:
        snap = detection_snapshot(root,ctx,model)
        summaries={s['species']['id']:s for s in snap['summaries']}
        for sid in quality:
            threshold=threshold_for(quality[sid],model)
            if threshold != .1:
                revised=detection_snapshot(root,ctx,model,sid,threshold)
                summaries.update({s['species']['id']:s for s in revised['summaries'] if s['species']['id']==sid})
        for summary in summaries.values():
            item = add(summary['species'])
            threshold = threshold_for(quality.get(summary['species']['id']),model)
            item['models'].append({**summary,'species':None,**model,'threshold':threshold})
    from custom_models import list_models
    for model in list_models(root, ctx):
        item = add(model['species'])
        entry = dict(model)
        entry['validation'] = current_validations(root, ctx, model_id=model['id']).get(model['species']['id'])
        if model['applicable'] and model['status']=='ready':
            snap = custom_snapshot(root, ctx, model['id'])
            if snap['summaries']:
                entry.update({k:v for k,v in snap['summaries'][0].items() if k!='species'})
        item['custom_models'].append(entry)
    seen = {}
    for run in searches(root,dataset_id):
        item = add(run['species']); item['searches'].append(run)
        snap = search_snapshot(root,ctx,run['id'])
        confirmed=seen.setdefault(run['species_id'],set())
        confirmed.update((e['recording_id'],e['start_s'],e['end_s']) for e in snap['evidence'] if e['status']=='target')
        item['confirmed']=len(confirmed)
    for sid in quality:
        if sid in named:
            add(named[sid])
    from annotations import evidence
    manual = {}
    for entry in evidence(root,ctx):
        if entry['species']:
            item=add(entry['species'])
            seen=manual.setdefault(entry['species_id'],set())
            seen.add((entry['recording_id'],entry['review_start_s'],entry['review_end_s']))
            item['annotated']=len(seen)
    return {**ctx,'records':len(records),'recorded_seconds':sum(seconds for r in records for key,seconds in effort(r,zone) if ctx['start_date']<=key[0]<=ctx['end_date']),
            'species': sorted(rows.values(),key=lambda r:(-max([m.get('candidate_windows') or 0 for m in [*r['models'],*r['custom_models']]] or [0]),-r['confirmed'],r['species']['scientific_name']))}


def species_result(root,dataset_id,species_id,first=None,last=None,engine='auto',run_id=None,cell_date=None,hour=None,model_id=None):
    ctx=context(root,dataset_id,first,last)
    with connect(root) as db:
        row=db.execute('SELECT * FROM species WHERE id=?',(species_id,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        taxon=names_for(row)
    quality=current_validations(root,ctx).get(species_id)
    runs=searches(root,dataset_id,species_id)
    from custom_models import list_models
    custom = list_models(root, ctx, species_id)
    if engine == 'auto':
        engine = next((m['engine'] for m in ctx['models'] if any(
            row['species']['id'] == species_id and row['candidate_windows'] > 0
            for row in detection_snapshot(root,ctx,m)['summaries'])), None)
        if engine is None and model_id is None and run_id is None:
            selected=next((m for m in custom if m['applicable'] and m['status']=='ready'),None)
            if selected:model_id=selected['id'];engine='custom'
        engine=engine or 'birdnet'
    model=next((m for m in ctx['models'] if m['engine']==engine),None)
    snap=None
    if model_id is not None:
        selected = next((m for m in custom if m['id']==model_id),None)
        if not selected:
            from custom_models import get_model
            selected=get_model(root,model_id)
            if selected['species']['id']!=species_id or selected['scope']['site_id']!=ctx['site']['id']:
                raise CatalogError('not_found')
            if (selected['scope']['start_date'],selected['scope']['end_date'])!=(ctx['start_date'],ctx['end_date']):
                selected={**selected,'applicable':False,'reason':'custom_model_scope_changed'}
            custom.append(selected)
        quality=current_validations(root,ctx,model_id=model_id).get(species_id)
        if selected['applicable'] and selected['status']=='ready':
            snap=custom_snapshot(root,ctx,model_id)
    elif run_id is not None:
        if run_id not in [r['id'] for r in runs]:
            raise CatalogError('not_found')
        snap=search_snapshot(root,ctx,run_id)
        quality=snap.get('validation')
    elif model:
        snap=detection_snapshot(root,ctx,model,species_id,threshold_for(quality,model))
        snap['validation']=quality
    with connect(root) as db:
        report=snapshot_view(db,snap,cell_date=cell_date,hour=hour) if snap else None
    from annotations import evidence
    return {**ctx,'species':taxon,'validation':quality,'searches':runs,'custom_models':custom,'report':report,'annotations':evidence(root,ctx,species_id)}
