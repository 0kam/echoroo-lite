"""Explicit site management; original recordings and exported files are never removed."""
import hashlib
import json
import shutil
from pathlib import Path

from storage import CatalogError, connect


def site_details(root, site_id):
    with connect(root) as db:
        site = db.execute('SELECT * FROM sites WHERE id=?', (site_id,)).fetchone()
        if site is None:
            raise CatalogError('not_found')
        datasets = [dict(r) for r in db.execute('''SELECT d.*, COUNT(r.id) AS count,
            COALESCE(SUM(r.seconds),0) AS seconds, MIN(r.recorded_at) AS first, MAX(r.recorded_at) AS last
            FROM datasets d LEFT JOIN recordings r ON r.dataset_id=d.id WHERE d.site_id=? GROUP BY d.id ORDER BY d.id''', (site_id,))]
        return {'site': dict(site), 'datasets': datasets}


def location_status(db, site):
    """Return completed recordings at this location and whether older locations remain."""
    from reports import analysis_metadata
    engines = {}
    stale = False
    rows = db.execute('''SELECT a.recording_id,a.engine,a.metadata FROM analyses a JOIN recordings r ON r.id=a.recording_id
        JOIN datasets d ON d.id=r.dataset_id WHERE d.site_id=? AND a.id IN
        (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)''', (site['id'],))
    for row in rows:
        meta = analysis_metadata(row['metadata'])
        geo = meta if row['engine']=='birdnet' else (meta.get('geo_filter') or {})
        matches = all(geo.get(key)==site[key] for key in ('latitude','longitude'))
        # Legacy metadata without a location is not declared wrong until the user changes it.
        if not any(key in geo for key in ('latitude','longitude')) and not site.get('location_changed', False):
            matches = True
        stale |= not matches
        if matches:
            engines.setdefault(row['recording_id'], set()).add(row['engine'])
    complete = {rid for rid, values in engines.items() if len(values)==2}
    return complete, stale


def edit_site(root, site_id, body):
    with connect(root) as db:
        old = db.execute('SELECT * FROM sites WHERE id=?', (site_id,)).fetchone()
        if old is None:
            raise CatalogError('not_found')
        changed = (old['latitude'],old['longitude']) != (body.latitude,body.longitude)
        db.execute('UPDATE sites SET name=?,latitude=?,longitude=? WHERE id=?', (body.name,body.latitude,body.longitude,site_id))
        if changed:
            db.execute('INSERT OR IGNORE INTO changed_locations VALUES(?)', (site_id,))
    return site_details(root, site_id)


def edit_dataset(root, site_id, dataset_id, name):
    with connect(root) as db:
        if not db.execute('UPDATE datasets SET name=? WHERE id=? AND site_id=?', (name,dataset_id,site_id)).rowcount:
            raise CatalogError('not_found')
    return site_details(root, site_id)


def deletion_plan(db, site_id, dataset_id=None):
    site = db.execute('SELECT * FROM sites WHERE id=?', (site_id,)).fetchone()
    if site is None:
        raise CatalogError('not_found')
    datasets = [dict(r) for r in db.execute('SELECT * FROM datasets WHERE site_id=? ORDER BY id', (site_id,))]
    selected = [d for d in datasets if dataset_id is None or d['id']==dataset_id]
    if dataset_id is not None and not selected:
        raise CatalogError('not_found')
    ids = [d['id'] for d in selected]
    remaining = [d['id'] for d in datasets if d['id'] not in ids]
    encoded = json.dumps(ids)
    recordings = [r[0] for r in db.execute('SELECT id FROM recordings WHERE dataset_id IN (SELECT value FROM json_each(?)) ORDER BY id', (encoded,))]
    analyses = [dict(r) for r in db.execute('SELECT id,scores_path FROM analyses WHERE recording_id IN (SELECT value FROM json_each(?)) ORDER BY id', (json.dumps(recordings),))]
    rids, aids = set(recordings), {a['id'] for a in analyses}
    runs = [r[0] for r in db.execute('''SELECT DISTINCT h.run_id FROM search_hits h JOIN segments s ON s.id=h.segment_id
        WHERE s.analysis_id IN (SELECT value FROM json_each(?))''',(json.dumps(sorted(aids)),))]
    validations = [r[0] for r in db.execute('SELECT DISTINCT session_id FROM validation_items WHERE recording_id IN (SELECT value FROM json_each(?))',(json.dumps(recordings),))]
    annotations = [r[0] for r in db.execute('SELECT DISTINCT session_id FROM annotation_items WHERE recording_id IN (SELECT value FROM json_each(?))',(json.dumps(recordings),))]
    custom_models, reports, exports = [], [], []
    def depends(value):
        if isinstance(value, list):
            return any(depends(v) for v in value)
        if not isinstance(value, dict):
            return False
        return (bool(set(value.get('analysis_ids', [])) & aids) or bool(set(value.get('recording_ids', [])) & rids)
                or value.get('recording_id') in rids or value.get('run_id') in runs
                or value.get('model_id') in custom_models
                or value.get('base_report_id') in reports or value.get('report_id') in reports
                or value.get('validation_id') in validations
                or value.get('annotation_session_id') in annotations
                or value.get('annotation_source',{}).get('session_id') in annotations
                or (value.get('kind')=='recording' and value.get('id') in rids)
                or any(depends(v) for v in value.values() if isinstance(v,(dict,list))))
    for table, chosen in [('search_runs',runs), ('annotation_sessions',annotations), ('custom_models',custom_models), ('validation_sessions',validations), ('reports',reports), ('workflow_exports',exports)]:
        for row in db.execute(f'SELECT t.*,d.site_id FROM {table} t JOIN datasets d ON d.id=t.dataset_id ORDER BY t.id'):
            related = depends(json.loads(row['snapshot']))
            if row['site_id'] != site_id:
                if related:
                    raise CatalogError('management_shared_reference')
                continue
            if not remaining or related:
                if row['id'] not in chosen:
                    chosen.append(row['id'])
    plan = {'site_id':site_id,'dataset_id':dataset_id,'name':site['name'] if dataset_id is None else selected[0]['name'],
            'datasets':ids,'recordings':recordings,'analyses':analyses,'searches':runs,'validations':validations,'annotations':annotations,'custom_models':custom_models,'reports':reports,'exports':exports,
            'remaining_dataset_id':remaining[0] if remaining else None}
    # A stale confirmation must not silently delete newly added answers or records.
    revision = [db.execute('SELECT version FROM result_revision WHERE id=1').fetchone()[0],
                *[db.execute(f'SELECT COALESCE(MAX(id),0) FROM {table}').fetchone()[0] for table in
                  ('search_judgements','validation_answers','annotation_answers','search_samples','workflow_exports','custom_models')]]
    plan['revision'] = hashlib.sha256(json.dumps([plan,revision],sort_keys=True).encode()).hexdigest()
    return plan


def preview_delete(root, site_id, dataset_id=None):
    with connect(root) as db:
        plan = deletion_plan(db,site_id,dataset_id)
    return {k: (len(v) if isinstance(v,list) else v) for k,v in plan.items() if k!='analyses'} | {'analyses':len(plan['analyses'])}


def delete(root, site_id, dataset_id, revision):
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        plan = deletion_plan(db,site_id,dataset_id)
        if plan['revision'] != revision:
            raise CatalogError('management_changed')
        def remove(table, column, ids):
            db.execute(f'DELETE FROM {table} WHERE {column} IN (SELECT value FROM json_each(?))', (json.dumps(ids),))
        aids = [a['id'] for a in plan['analyses']]
        segments = 'SELECT id FROM segments WHERE analysis_id IN (SELECT value FROM json_each(?))'
        db.execute(f'DELETE FROM vectors WHERE rowid IN ({segments})', (json.dumps(aids),))
        db.execute('DELETE FROM search_sample_items WHERE sample_id IN (SELECT id FROM search_samples WHERE run_id IN (SELECT value FROM json_each(?)))',(json.dumps(plan['searches']),))
        for table in ('search_samples','search_judgements','search_hits'):
            remove(table,'run_id',plan['searches'])
        remove('search_runs','id',plan['searches'])
        db.execute('DELETE FROM validation_answers WHERE item_id IN (SELECT id FROM validation_items WHERE session_id IN (SELECT value FROM json_each(?)))',(json.dumps(plan['validations']),))
        remove('validation_items','session_id',plan['validations'])
        remove('validation_sessions','id',plan['validations'])
        remove('custom_models','id',plan['custom_models'])
        db.execute('DELETE FROM annotation_answers WHERE item_id IN (SELECT id FROM annotation_items WHERE session_id IN (SELECT value FROM json_each(?)))',(json.dumps(plan['annotations']),))
        remove('annotation_items','session_id',plan['annotations'])
        remove('annotation_sessions','id',plan['annotations'])
        remove('reports','id',plan['reports'])
        remove('workflow_exports','id',plan['exports'])
        for table in ('candidates','segments'):
            remove(table,'analysis_id',aids)
        remove('analyses','id',aids)
        remove('recordings','id',plan['recordings'])
        # A job anchored to the first import can also contain surviving recordings.
        db.execute('DELETE FROM jobs WHERE dataset_id IN (SELECT value FROM json_each(?)) AND id NOT IN (SELECT job_id FROM analyses)',(json.dumps(plan['datasets']),))
        if plan['remaining_dataset_id'] is not None:
            for table in ('jobs','search_runs','validation_sessions','annotation_sessions','custom_models','reports','workflow_exports'):
                db.execute(f'UPDATE {table} SET dataset_id=? WHERE dataset_id IN (SELECT value FROM json_each(?))',(plan['remaining_dataset_id'],json.dumps(plan['datasets'])))
        remove('datasets','id',plan['datasets'])
        if dataset_id is None:
            db.execute('DELETE FROM changed_locations WHERE site_id=?',(site_id,))
            db.execute('DELETE FROM sites WHERE id=?',(site_id,))
        db.execute('DELETE FROM workflow_cache')
        if db.execute('PRAGMA foreign_key_check').fetchone():
            raise CatalogError('management_changed')
    # These files are generated inside our own catalogue; never follow arbitrary source paths.
    folder = (Path(root)/'analyses').resolve()
    for analysis in plan['analyses']:
        path = Path(analysis['scores_path'])
        if path.resolve().is_relative_to(folder):
            for generated in (path, *path.parent.glob(path.name+'.*.windows.npz')):
                try:
                    generated.unlink(missing_ok=True)
                except OSError:
                    pass
    for generated in (Path(root)/'window-batches').glob('*.npz'):
        try:
            generated.unlink()
        except OSError:
            pass
    model_folder = (Path(root)/'custom-models').resolve()
    for model_id in plan['custom_models']:
        generated = model_folder/str(model_id)
        if generated.resolve().is_relative_to(model_folder):
            shutil.rmtree(generated, ignore_errors=True)
    from results_workflow import cached_fingerprint, cached_options
    from reports import cached_windows
    cached_fingerprint.cache_clear(); cached_options.cache_clear(); cached_windows.cache_clear()
    return {'remaining_dataset_id':plan['remaining_dataset_id']}
