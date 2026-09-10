"""Read-only result views; only exports freeze reports into permanent history."""
import hashlib
import json
import zlib
from datetime import date
from types import SimpleNamespace

from reports import detection, options, search_report, snapshot_view
from species import names_for
from storage import CatalogError, checked_source, connect


def fingerprint(root, dataset_id):
    with connect(root) as db:
        dataset = db.execute('SELECT d.*,s.timezone,s.latitude,s.longitude FROM datasets d JOIN sites s ON s.id=d.site_id WHERE d.id=?', (dataset_id,)).fetchone()
        if dataset is None:
            raise CatalogError('not_found')
        record_rows=db.execute('SELECT * FROM site_recordings WHERE scope_id=? ORDER BY id',(dataset_id,)).fetchall()
        for record in record_rows:
            checked_source(record)
        records=[{k:r[k] for k in r.keys() if k!='scope_id'} for r in record_rows]
        analyses = [tuple(r) for r in db.execute('''SELECT a.id,a.recording_id,a.engine,a.metadata FROM analyses a
            JOIN site_recordings r ON r.id=a.recording_id WHERE r.scope_id=? AND a.id IN
            (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine) ORDER BY a.id''', (dataset_id,))]
    return hashlib.sha256(json.dumps([{k:dataset[k] for k in ('site_id','timezone','latitude','longitude')}, records, analyses], sort_keys=True).encode()).hexdigest()


def context(root, dataset_id, first=None, last=None):
    result = options(root, dataset_id)
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
    result['fingerprint'] = fingerprint(root, dataset_id)
    with connect(root) as db:
        revision=[db.execute(f'SELECT COALESCE(MAX(id),0) FROM {table}').fetchone()[0] for table in
                  ('search_runs','search_judgements','validation_answers','validation_sessions')]
        revision.append([tuple(r) for r in db.execute('SELECT id,names FROM species ORDER BY id')])
    result['names_revision']=hashlib.sha256(json.dumps(revision[-1]).encode()).hexdigest()
    result['revision']=hashlib.sha256(json.dumps([result['fingerprint'],revision]).encode()).hexdigest()
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
    # ponytail: cache per species; consolidate saved window matrices if annual first-view latency dominates.
    body = SimpleNamespace(dataset_id=ctx['dataset']['id'], engine=model['engine'], model_sha256=model['model_sha256'],
        threshold=threshold, start_date=date.fromisoformat(ctx['start_date']), end_date=date.fromisoformat(ctx['end_date']),
        species_id=species_id, base_report_id=None)
    return cached(root, ['detection-site-v3', ctx['fingerprint'], ctx['names_revision'], vars(body)], lambda: detection(root, body, persist=False))


def searches(root, dataset_id, species_id=None):
    with connect(root) as db:
        rows = db.execute('''SELECT r.id,r.species_id,r.created_at FROM search_runs r WHERE r.dataset_id IN (SELECT id FROM datasets WHERE site_id=(SELECT site_id FROM datasets WHERE id=?))
            AND (? IS NULL OR r.species_id=?) ORDER BY r.id DESC''', (dataset_id,species_id,species_id)).fetchall()
        return [{**dict(r), 'species': names_for(db.execute('SELECT * FROM species WHERE id=?',(r['species_id'],)).fetchone())} for r in rows]


def search_snapshot(root, ctx, run_id):
    with connect(root) as db:
        run = db.execute('SELECT dataset_id FROM search_runs WHERE id=?',(run_id,)).fetchone()
        if not run or not db.execute('SELECT 1 FROM datasets WHERE id=? AND site_id=?',(run[0],ctx['site']['id'])).fetchone():
            raise CatalogError('not_found')
        cutoff = db.execute('SELECT COALESCE(MAX(id),0) FROM search_judgements').fetchone()[0]
    def build():
        body = SimpleNamespace(run_id=run_id, start_date=date.fromisoformat(ctx['start_date']), end_date=date.fromisoformat(ctx['end_date']), threshold=None)
        result = search_report(root, body, persist=False)
        if result['threshold_suggestion']:
            body.threshold = result['threshold_suggestion']['threshold']
            result = search_report(root, body, persist=False)
        return result
    return cached(root, ['search-site-v3',ctx['fingerprint'],ctx['names_revision'],run_id,ctx['start_date'],ctx['end_date'],cutoff], build)


def current_validations(root, ctx):
    from validation import view_session
    with connect(root) as db:
        rows = db.execute('SELECT id,species_id,snapshot,fingerprint FROM validation_sessions WHERE dataset_id IN (SELECT id FROM datasets WHERE site_id=(SELECT site_id FROM datasets WHERE id=?)) ORDER BY id DESC', (ctx['dataset']['id'],)).fetchall()
    chosen={}
    for row in rows:
        sid=row['species_id']
        if sid in chosen and chosen[sid]['applicable']:
            continue
        snap=json.loads(row['snapshot'])
        applicable=row['fingerprint']==ctx['fingerprint'] and (snap['start_date'],snap['end_date'])==(ctx['start_date'],ctx['end_date'])
        if sid not in chosen or applicable:
            chosen[sid]=view_session(root,row['id'],ctx=ctx,include_items=False)
    return chosen


def threshold_for(quality, model):
    if quality and quality['applicable'] and quality['stage']=='complete':
        result = quality['results'].get(model['engine'])
        if result and result['model_sha256']==model['model_sha256']:
            return result['threshold']
    return .1


def overview(root, dataset_id, first=None, last=None):
    ctx = context(root,dataset_id,first,last)
    quality = current_validations(root,ctx)
    with connect(root) as db:
        records = [dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?',(dataset_id,))]
        named = {r['id']: names_for(r) for r in db.execute('SELECT * FROM species')}
        complete = db.execute('''SELECT COUNT(*) FROM (SELECT a.recording_id FROM analyses a JOIN site_recordings r ON r.id=a.recording_id
            WHERE r.scope_id=? GROUP BY a.recording_id HAVING COUNT(DISTINCT a.engine)=2)''',(dataset_id,)).fetchone()[0]
    rows = {}
    def add(taxon):
        sid = taxon['id']
        if sid not in rows:
            rows[sid]={'species':named.get(sid,taxon),'models':[],'searches':[],'confirmed':0,'validation':quality.get(sid)}
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
    return {**ctx,'records':len(records),'recorded_seconds':sum(r['seconds'] for r in records),'analysed_recordings':complete,
            'species': sorted(rows.values(),key=lambda r:(-max([m['candidate_windows'] for m in r['models']] or [0]),-r['confirmed'],r['species']['scientific_name']))}


def species_result(root,dataset_id,species_id,first=None,last=None,engine='auto',run_id=None,cell_date=None,hour=None):
    ctx=context(root,dataset_id,first,last)
    with connect(root) as db:
        row=db.execute('SELECT * FROM species WHERE id=?',(species_id,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        taxon=names_for(row)
    quality=current_validations(root,ctx).get(species_id)
    runs=searches(root,dataset_id,species_id)
    if engine == 'auto':
        engine = next((m['engine'] for m in ctx['models'] if any(
            row['species']['id'] == species_id and row['candidate_windows'] > 0
            for row in detection_snapshot(root,ctx,m)['summaries'])), 'birdnet')
    model=next((m for m in ctx['models'] if m['engine']==engine),None)
    snap=None
    if run_id is not None:
        if run_id not in [r['id'] for r in runs]:
            raise CatalogError('not_found')
        snap=search_snapshot(root,ctx,run_id)
    elif model:
        snap=detection_snapshot(root,ctx,model,species_id,threshold_for(quality,model))
        snap['validation']=quality
    with connect(root) as db:
        report=snapshot_view(db,snap,cell_date=cell_date,hour=hour) if snap else None
    return {**ctx,'species':taxon,'validation':quality,'searches':runs,'report':report}
