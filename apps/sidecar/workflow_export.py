"""One immutable export package containing the selected species and their evidence."""
import base64
import io
import json
import zipfile
from pathlib import Path
from uuid import uuid4

from reports import csv_bytes,save_report
from storage import CatalogError,checked_source,connect
from results_workflow import context,current_validations,detection_snapshot,searches,search_snapshot,threshold_for


def export(root,body):
    ctx=context(root,body.dataset_id,body.start_date.isoformat(),body.end_date.isoformat())
    if ctx['revision']!=body.revision:
        raise CatalogError('workflow_changed')
    folder=Path(body.folder).expanduser().resolve()
    if not folder.is_dir():
        raise CatalogError('folder_missing')
    quality=current_validations(root,ctx)
    figures={(f.species_id,f.engine,f.run_id):f for f in body.figures}
    snapshots=[]
    for sid in body.species_ids:
        for model in ctx['models']:
            snap=detection_snapshot(root,ctx,model,sid,threshold_for(quality.get(sid),model))
            snap['summaries']=[s for s in snap['summaries'] if s['species']['id']==sid]
            snap['validation']=quality.get(sid)
            snapshots.append(snap)
        for run in searches(root,body.dataset_id,sid):
            snap=search_snapshot(root,ctx,run['id']);snap['validation']=None;snapshots.append(snap)
    expected={(s['species']['id'],s.get('engine','search'),s.get('run_id')) for s in snapshots}
    if set(figures)!=expected:
        raise CatalogError('workflow_changed')
    path=folder/f'echoroo-{body.dataset_id}-{uuid4().hex[:10]}.zip'
    index=[];species_rows=[];hourly=[];evidence_rows=[];quality_rows=[]
    with connect(root) as db:
        records={r['id']:dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?',(body.dataset_id,))}
        for record in records.values():checked_source(record)
        with path.open('xb') as stream:
            try:
                with zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                    for snap in snapshots:
                        sid=snap['species']['id'];engine=snap.get('engine','search');run_id=snap.get('run_id')
                        report_id=save_report(db,body.dataset_id,snap['kind'],snap)
                        prefix=f'results/{report_id}'
                        common={'report_id':report_id,'species_id':sid,'scientific_name':snap['species']['scientific_name'],
                            'common_name_ja':snap['species']['common_names'].get('ja',''),'common_name_en':snap['species']['common_names'].get('en',''),
                            'engine':engine,'model_sha256':snap['model_sha256'],'search_run_id':run_id,'threshold':snap['threshold'],
                            'start_date':snap['start_date'],'end_date':snap['end_date'],'timezone':snap['site']['timezone']}
                        figure=figures[sid,engine,run_id]
                        if figure.svg:
                            archive.writestr(prefix+'/spiral.svg',figure.svg)
                        if figure.png:
                            try:png=base64.b64decode(figure.png,validate=True)
                            except ValueError:raise CatalogError('invalid_input') from None
                            if not png.startswith(b'\x89PNG\r\n\x1a\n'):raise CatalogError('invalid_input')
                            archive.writestr(prefix+'/spiral.png',png)
                        species_rows.extend({**common,**{k:v for k,v in row.items() if k!='species'}} for row in snap['summaries'])
                        hourly.extend({**common,**c} for c in snap['cells'])
                        # Five strongest examples per model and up to twenty confirmed search sounds.
                        ranked=sorted(snap['evidence'],key=lambda e:(e['status']!='target',-e['score']))
                        clips=ranked[:5] if engine!='search' else [e for e in ranked if e['status']=='target'][:20]
                        audio_files={}
                        for e in clips:
                            import soundfile as sf
                            from audio import read_interval
                            key=(e['recording_id'],e['start_s'],e['end_s'])
                            filename=f'{prefix}/audio/{key[0]}_{key[1]:.3f}_{key[2]:.3f}.wav'
                            if key in audio_files:continue
                            samples,interval=read_interval(records[key[0]],key[1],key[2]);buffer=io.BytesIO()
                            sf.write(buffer,samples,32000,format='WAV',subtype='FLOAT')
                            archive.writestr(filename,buffer.getvalue());archive.writestr(filename+'.json',json.dumps(interval))
                            audio_files[key]=filename
                        evidence_rows.extend({**common,**e,'path':records[e['recording_id']]['path'],
                            'audio_file':audio_files.get((e['recording_id'],e['start_s'],e['end_s']),'')} for e in snap['evidence'])
                        validation=snap.get('validation');result=validation['results'].get(engine) if validation and validation['applicable'] else None
                        q={**common,'validation_id':validation['id'] if validation else None,'quality':result['status'] if result else 'not_evaluated'}
                        for metric in ('precision','recall','estimated_window_rate'):
                            q.update({metric+'_'+k:result[metric][k] if result else None for k in ('value','low','high')})
                        if result:q.update({k:result[k] for k in ('unknown','unknown_rate','reviewed','eligible_windows','goal_met')})
                        quality_rows.append(q)
                        archive.writestr(prefix+'/report.json',json.dumps(snap,ensure_ascii=False,indent=2))
                        index.append({**common,'folder':prefix,'audio_examples':len(audio_files)})
                    for name,rows in [('species',species_rows),('hourly',hourly),('evidence',evidence_rows),('quality',quality_rows)]:
                        fields=list(dict.fromkeys(['species_id','scientific_name',*(k for r in rows for k in r)]))
                        archive.writestr(name+'.csv',csv_bytes(rows,fields))
                    for sid in body.species_ids:
                        value=quality.get(sid)
                        if value:
                            session=db.execute('SELECT * FROM validation_sessions WHERE id=?',(value['id'],)).fetchone()
                            from validation import answered_items
                            archive.writestr(f'validation/{value["id"]}.json',json.dumps({**dict(session),'snapshot':json.loads(session['snapshot']),
                                'items':answered_items(db,value['id'])},ensure_ascii=False,indent=2))
                    manifest={**ctx,'species_ids':body.species_ids,'results':index,'locale':body.locale,
                        'audio_selection':'five_strongest_per_model_and_twenty_confirmed_per_search',
                        'interpretation':'AI detections, search confirmations and validation estimates are separate; no absence or abundance claim.'}
                    archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
                    archive.writestr('recordings.json',json.dumps(list(records.values()),ensure_ascii=False,indent=2))
                if context(root,body.dataset_id,ctx['start_date'],ctx['end_date'])['revision']!=body.revision:
                    raise CatalogError('workflow_changed')
                eid=db.execute('INSERT INTO workflow_exports(dataset_id,snapshot,path) VALUES(?,?,?)',
                    (body.dataset_id,json.dumps(manifest,ensure_ascii=False),str(path))).lastrowid
            except Exception:
                stream.close();path.unlink(missing_ok=True);raise
    return {'id':eid,'path':str(path)}
