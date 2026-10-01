"""Freeze one report scope, then publish its document and portable audio/data suite."""
import hashlib
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime,timezone,timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
from contextlib import ExitStack

from reports import csv_bytes,save_report,scope_records,effort,window_key,window_in_scope,classification_batches
from storage import CatalogError,checked_source,connect
from results_workflow import attach_validation,context,current_validations,detection_snapshot,custom_snapshot,threshold_for
from report_document import create_html,messages,quality_result,status,criterion,method
from report_figures import DATA,spiral,recording_schedule


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def quality_export(validation,result):
    """Flatten the one-stage check for CSV without hiding 90% intervals."""
    row={}
    for metric in ('density','count','precision','recall','auprc','multiplier'):
        value=((result or {}).get(metric) or {})
        row.update({f'{metric}_{part}':value.get(part) for part in ('value','low','high')})
    row.update({
        'status':result.get('status') if result else None,'recall_status':result.get('recall_status') if result else None,
        'unknown':result.get('unknown') if result else None,'disagreement':result.get('disagreement') if result else None,
        'answers_per_bin':result.get('answers_per_bin') if result else None,
    })
    if validation:
        row.update(validation_method=validation.get('method'),evaluation_scope=validation.get('evaluation_scope'),
            sampling_unit=validation.get('sampling_unit'),human_reference=validation.get('reference'))
    return row


def save_audio(folder,record,start,end):
    import soundfile as sf
    from audio import read_interval
    key=f"{record['id']}_{start:.6f}_{end:.6f}"
    target=folder/'audio'/f'{key}.wav'
    if not target.exists():
        # Preserve the source rate for playback without importing a resampler.
        samples,interval=read_interval(record,start,end,native_rate=True)
        target.parent.mkdir(exist_ok=True)
        sf.write(target,samples,interval['sample_rate'],subtype='FLOAT')
        write_json(target.with_suffix('.json'),{'recording_id':record['id'],**interval,'sha256':digest(target)})
    return target.relative_to(folder).as_posix()


def score_tables(root,db,folder,snapshots,records,cancel=None):
    """Export all eligible scores, including below-threshold unreviewed intervals."""
    (folder/'scores').mkdir()
    fields=['recording_id','analysis_id','start_s','end_s','score','example_start_s']
    with ExitStack() as stack:
        writers={}
        for index,snap in enumerate(snapshots):
            snap['figure_id']=f'F{index+1:03d}';snap['score_file']=f"scores/{snap['figure_id']}.csv"
            stream=stack.enter_context((folder/snap['score_file']).open('w',encoding='utf-8-sig',newline=''))
            writers[snap['figure_id']]=csv.writer(stream);writers[snap['figure_id']].writerow(fields)
        for engine in ('birdnet','perch'):
            group=[s for s in snapshots if s.get('engine')==engine]
            if not group:continue
            ids=sorted({aid for s in group for aid in s['analysis_ids']})
            analyses=[dict(db.execute('SELECT * FROM analyses WHERE id=?',(aid,)).fetchone()) for aid in ids]
            zone=ZoneInfo(group[0]['site']['timezone'])
            for analysis,_,arrays in classification_batches(root,db,analyses,records):
                if cancel and cancel.is_set():raise CatalogError('cancelled')
                labels,starts,scores,best=arrays;columns={str(label):i for i,label in enumerate(labels)}
                record=records[analysis['recording_id']]
                width=3 if engine=='birdnet' else 5
                indices=[i for i,start in enumerate(starts) if window_in_scope(record,float(start),float(start)+width,zone,group[0]['start_date'],group[0]['end_date'])]
                for snap in group:
                    column=columns.get(snap['species']['scientific_name'])
                    if column is None or analysis['id'] not in snap['analysis_ids']:continue
                    writers[snap['figure_id']].writerows([record['id'],analysis['id'],float(starts[i]),float(starts[i])+width,float(scores[i,column]),float(best[i,column])] for i in indices)
        for snap in snapshots:
            if cancel and cancel.is_set():raise CatalogError('cancelled')
            if snap.get('engine')=='custom':
                from custom_models import score_windows
                writers[snap['figure_id']].writerows([row[k] for k in fields] for row in score_windows(root,snap['model_id']))


def export(root,body,cancel=None):
    from custom_models import list_models
    from presence import empty,summaries
    from quiz import report_results
    from validation import answered_items
    ctx=context(root,body.dataset_id,body.start_date.isoformat(),body.end_date.isoformat())
    if ctx.get('location_stale'):raise CatalogError('location_reanalysis')
    if ctx['revision']!=body.revision:raise CatalogError('workflow_changed')
    parent=Path(body.folder).expanduser().resolve()
    if not parent.is_dir():raise CatalogError('folder_missing')
    quality=current_validations(root,ctx);snapshots=[];t=messages(body.locale)
    custom=list_models(root,ctx);found=summaries(root,ctx)
    wanted=list(dict.fromkeys(body.species_ids));presence={sid:found.get(sid,empty()) for sid in wanted}
    for sid in wanted:
        if cancel and cancel.is_set():raise CatalogError('cancelled')
        for model in ctx['models']:
            # Unchecked, undecided and goal-missed models are kept; the report states their status.
            v=quality.get((sid,model['engine']))
            snap=detection_snapshot(root,ctx,model,sid,threshold_for(v,model))
            snap['summaries']=[s for s in snap['summaries'] if s['species']['id']==sid]
            if not snap['summaries']:continue
            snapshots.append(attach_validation(snap,v))
        for model in custom:
            if model['species']['id']==sid and model['applicable']:
                snapshots.append(custom_snapshot(root,ctx,model))
    if not snapshots and not any(p['evidence'] for p in presence.values()):raise CatalogError('no_report_analyses')
    token=uuid4().hex[:10];package_id=f'echoroo-{body.dataset_id}-{token}'
    destination=parent/package_id;zip_path=parent/(package_id+'.zip')
    zone=ZoneInfo(ctx['site']['timezone']);index=[];species_rows=[];hourly=[];evidence=[];quality_rows=[];presence_rows=[]
    published_folder=published_zip=False
    try:
        with tempfile.TemporaryDirectory(prefix='.echoroo-',dir=parent) as temp:
            folder=Path(temp)/package_id;folder.mkdir();(folder/'figures').mkdir()
            with connect(root) as db:
                records=scope_records([dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?',(body.dataset_id,))],zone,ctx['start_date'],ctx['end_date'])
                records={r['id']:r for r in records}
                for record in records.values():checked_source(record)
                quiz=report_results(db,body.species_ids,include_sessions=True)
                daily={};schedule={}
                for i in range((body.end_date-body.start_date).days+1):
                    day=str(body.start_date+timedelta(days=i));daily[day]=0.;schedule[day]=[0.]*24
                for record in records.values():
                    for (day,hour),seconds in effort(record,zone):
                        if day in daily:daily[day]+=seconds;schedule[day][hour]+=seconds
                scope={'recording_ids':sorted(records),'recorded_seconds':sum(daily.values()),'daily':[{'date':day,'seconds':seconds} for day,seconds in daily.items()],
                    'schedule':[{'date':day,'hours':hours} for day,hours in schedule.items()],
                    'analysed_seconds':{}}
                handled_validations=set()
                score_tables(root,db,folder,snapshots,records,cancel)
                for position,snap in enumerate(snapshots):
                    if cancel and cancel.is_set():raise CatalogError('cancelled')
                    snap['figure_id']=f'F{position+1:03d}';sid=snap['species']['id'];engine=snap['engine']
                    snap['recording_ids']=sorted(records)
                    key=method(snap,t)
                    scope['analysed_seconds'][key]=max(scope['analysed_seconds'].get(key,0),sum(c['analysed_seconds'] for c in snap['cells']))
                    report_id=save_report(db,body.dataset_id,snap['kind'],snap)
                    common={'report_id':report_id,'figure_id':snap['figure_id'],'species_id':sid,'scientific_name':snap['species']['scientific_name'],
                        'common_name_ja':snap['species']['common_names'].get('ja',''),'common_name_en':snap['species']['common_names'].get('en',''),
                        'engine':engine,'model_sha256':snap['model_sha256'],'custom_model_id':snap.get('model_id'),'threshold':snap['threshold'],
                        'start_date':snap['start_date'],'end_date':snap['end_date'],'timezone':snap['site']['timezone'],'window_s':snap['window_s'],'quality':status(snap)}
                    caption=criterion(snap,t).replace('\n',' · ')+' · '+t[status(snap)]
                    png,svg=spiral(snap,body.locale,caption);(folder/'figures'/f"{snap['figure_id']}.png").write_bytes(png);(folder/'figures'/f"{snap['figure_id']}.svg").write_text(svg,encoding='utf-8')
                    species_rows.extend({**common,**{k:v for k,v in row.items() if k not in ('species','quality')}} for row in snap['summaries'])
                    hourly.extend({**common,**cell} for cell in snap['cells'])
                    selected={(e['recording_id'],e['start_s'],e['end_s']) for e in sorted(snap['evidence'],key=lambda e:-e['score'])[:5]}
                    for item in snap['evidence']:
                        key=(item['recording_id'],item['start_s'],item['end_s'])
                        row={**common,**item,'source_path':records[key[0]]['path'],'audio_file':'','audio_role':'','audio_reason':'not_selected_example'}
                        if key in selected:
                            row.update(audio_file=save_audio(folder,records[key[0]],key[1],key[2]),audio_role='ai_example',audio_reason='included')
                        evidence.append(row)
                    v=snap.get('validation');result=quality_result(snap)
                    common.update(validation_method=v.get('method') if v else None,
                        evaluation_scope=v.get('evaluation_scope') if v else None,sampling_unit=v.get('sampling_unit') if v else None,
                        uncertainty_method=v.get('uncertainty') if v else None,human_reference=v.get('reference') if v else None)
                    quality_rows.append({**common,'validation_id':v['id'] if v else None,'validation_applicable':v['applicable'] if v else False,
                        'threshold_reason':(v['thresholds'].get(engine) or {}).get('reason') if v else None,**quality_export(v,result)})
                    if engine=='custom':
                        model=dict(db.execute('SELECT * FROM custom_models WHERE id=?',(snap['model_id'],)).fetchone())
                        model['snapshot']=json.loads(model['snapshot'])
                        model_folder=folder/'models'/str(snap['model_id']);model_folder.mkdir(parents=True,exist_ok=True)
                        for key,filename in (('artifact','model.npz'),('scores','scores.npz')):
                            target=model_folder/filename
                            shutil.copyfile(Path(model[key+'_path']),target)
                            if digest(target)!=model[key+'_sha256']:raise CatalogError('custom_model_files_changed')
                            model[key+'_path']=target.relative_to(folder).as_posix()
                        write_json(model_folder/'provenance.json',model)
                    if v and v['applicable'] and v['id'] not in handled_validations:
                        handled_validations.add(v['id']);saved=db.execute('SELECT * FROM validation_sessions WHERE id=?',(v['id'],)).fetchone()
                        answered=[]
                        for answer in answered_items(db,v['id']):
                            if answer['verdict'] is None:continue
                            record=records[answer['recording_id']];start=answer['start_s'];end=answer['end_s']
                            clip=save_audio(folder,record,start,end)
                            answered.append({**answer,'audio_file':clip})
                            evidence.append({**common,**answer,'validation_id':v['id'],'start_s':start,'end_s':end,'local_time':window_key(record,start,zone)[1],
                                'audio_file':clip,'audio_role':'validation','audio_reason':'included','status':answer['verdict']})
                        history=[dict(r) for r in db.execute('''SELECT a.* FROM window_answers a WHERE a.species_id=? AND EXISTS
                            (SELECT 1 FROM validation_items i WHERE i.session_id=? AND i.recording_id=a.recording_id
                             AND i.start_s=a.start_s AND i.end_s=a.end_s) ORDER BY a.id''',(sid,v['id']))]
                        write_json(folder/'validation'/f"{v['id']}.json",{**dict(saved),'snapshot':json.loads(saved['snapshot']),
                            'items':answered,'answer_history':history})
                    write_json(folder/'results'/f'{report_id}.json',{'id':report_id,**snap})
                    index.append(common)
                taxa={s['species']['id']:s['species'] for s in snapshots}
                from species import names_for
                for sid in presence:
                    if sid not in taxa:taxa[sid]=names_for(db.execute('SELECT * FROM species WHERE id=?',(sid,)).fetchone())
                for sid,item in presence.items():
                    if cancel and cancel.is_set():raise CatalogError('cancelled')
                    for entry in item['evidence']:
                        record=records[entry['recording_id']]
                        evidence.append({'species_id':sid,'scientific_name':taxa[sid]['scientific_name'],**entry,'status':'target',
                            'audio_file':save_audio(folder,record,entry['start_s'],entry['end_s']),'audio_role':'presence','audio_reason':'included'})
                    miss=item['miss'] or {}
                    presence_rows.append({'species_id':sid,'scientific_name':taxa[sid]['scientific_name'],'status':item['status'],
                        'evidence_count':len(item['evidence']),'marked':sum(e['origin']=='mark' for e in item['evidence']),
                        'heard_in_checks':sum(e['origin']=='check' for e in item['evidence']),'listened_without_confirming':miss.get('listened'),
                        'not_found_list':miss.get('source'),'not_found_recorded_at':miss.get('created_at')})
                manifest={**ctx,'schema':'echoroo_recording_report_v2','package_id':package_id,'saved_at':datetime.now(timezone.utc).isoformat(),
                    'format':'html','locale':body.locale,'scope':scope,'results':index,'selected_species':[taxa[sid] for sid in wanted if sid in taxa],
                    'presence':[{**row,'species':taxa[row['species_id']]} for row in presence_rows],
                    'report_file':'index.html','folder_path':str(destination),
                    'file_guide':[['presence.csv',t['presence_files']],['species.csv',t['species_list']],['hourly.csv',t['details']],['quality.csv',t['quality_title']],['scores/',t['all_scores']],
                        ['evidence.csv / audio/',t['evidence_files']],['validation/',t['validation_files']],['quiz-results.json',t['quiz_title']],
                        ['manifest.json / results/',t['methods_title']]]}
                if any(s['engine']=='custom' for s in snapshots):
                    manifest['file_guide'].append(['models/',t['custom_model_files']])
                (folder/'figures'/'recording-schedule.png').write_bytes(recording_schedule(scope))
                for filename,rows in [('presence',presence_rows),('species',species_rows),('hourly',hourly),('evidence',evidence),('quality',quality_rows)]:
                    fields=list(dict.fromkeys(k for row in rows for k in row))
                    values=[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in rows]
                    (folder/(filename+'.csv')).write_bytes(csv_bytes(values,fields or ['report_id']))
                write_json(folder/'recordings.json',list(records.values()));write_json(folder/'quiz-results.json',quiz)
                create_html(folder,manifest,snapshots,evidence,quiz)
                (folder/'README.txt').write_text(t['files_note']+'\n\n'+t['evidence_note'],encoding='utf-8')
                shutil.copyfile(DATA/'echoroo.png',folder/'figures'/'echoroo.png')
                shutil.copyfile(DATA/'fonts'/'OFL.txt',folder/'font-license.txt')
                manifest['files']={p.relative_to(folder).as_posix():{'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(folder.rglob('*')) if p.is_file()}
                write_json(folder/'manifest.json',manifest)
                if cancel and cancel.is_set():raise CatalogError('cancelled')
                if context(root,body.dataset_id,ctx['start_date'],ctx['end_date'])['revision']!=body.revision:raise CatalogError('workflow_changed')
                for record in records.values():checked_source(record)
                staged_zip=Path(temp)/(package_id+'.zip')
                with zipfile.ZipFile(staged_zip,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                    for path in sorted(folder.rglob('*')):
                        if cancel and cancel.is_set():raise CatalogError('cancelled')
                        if path.is_file():archive.write(path,path.relative_to(folder).as_posix())
                if cancel and cancel.is_set():raise CatalogError('cancelled')
                folder.rename(destination);published_folder=True
                staged_zip.rename(zip_path);published_zip=True
                eid=db.execute('INSERT INTO workflow_exports(dataset_id,snapshot,path) VALUES(?,?,?)',(body.dataset_id,json.dumps(manifest,ensure_ascii=False),str(zip_path))).lastrowid
    except Exception:
        if published_folder:shutil.rmtree(destination,ignore_errors=True)
        if published_zip:zip_path.unlink(missing_ok=True)
        raise
    return {'id':eid,'path':str(zip_path),'folder_path':str(destination),'report_path':str(destination/manifest['report_file'])}


def open_export(root,export_id,kind):
    with connect(root) as db:row=db.execute('SELECT * FROM workflow_exports WHERE id=?',(export_id,)).fetchone()
    if not row:raise CatalogError('not_found')
    snap=json.loads(row['snapshot'])
    if not snap.get('folder_path'):raise CatalogError('report_export_missing')
    folder=Path(snap['folder_path']);path=folder/snap['report_file'] if kind=='document' else folder
    if not path.exists():raise CatalogError('report_export_missing')
    try:
        if sys.platform=='win32':os.startfile(str(path))
        else:subprocess.Popen(['open' if sys.platform=='darwin' else 'xdg-open',str(path)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    except OSError:raise CatalogError('report_open_failed') from None
    return {'opened':True}
