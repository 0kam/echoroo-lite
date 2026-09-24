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
from datetime import datetime,timezone,date,timedelta
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
from contextlib import ExitStack

from reports import csv_bytes,save_report,scope_records,effort,window_key,window_in_scope,classification_batches
from storage import CatalogError,checked_source,connect
from results_workflow import context,current_validations,detection_snapshot,searches,search_snapshot,custom_snapshot,threshold_for
from report_document import create_html,messages,quality_result,status,criterion,method,fixed_precision
from report_figures import DATA,spiral,spectrum,recording_effort


def write_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def digest(path):
    with path.open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def quality_export(validation,result):
    """Flatten saved validation evidence without conflating decision and descriptive intervals."""
    row={}
    for metric in ('precision','recall','estimated_window_rate'):
        complete=(not fixed_precision(validation) or
            (result or {}).get('precision_complete' if metric=='precision' else 'recall_complete'))
        value=((result or {}).get(metric) or {}) if complete else {}
        row.update({f'{metric}_{part}':value.get(part) for part in ('value','low','high')})
    precision_complete=bool(result and result.get('precision_complete'))
    row.update({
        'goal_met':result.get('goal_met') if result and (not fixed_precision(validation) or precision_complete) else None,
        'precision_lower_95':result.get('precision_lower_95') if precision_complete else None,
        'precision_complete':result.get('precision_complete') if result else None,
        'recall_complete':result.get('recall_complete') if result else None,
        'evaluation_eligible_windows':result.get('eligible_windows') if result else None,
        'evaluation_reviewed':result.get('reviewed') if result else None,
        'evaluation_unknown':result.get('unknown') if result else None,
    })
    for metric in ('precision','recall'):
        sample=(result or {}).get(metric+'_sample') or {}
        row.update({f'{metric}_sample_{part}':sample.get(part) for part in ('total','answered','target','unknown','population')})
    if validation:
        row.update(validation_method=validation.get('method'),target_precision=validation.get('target_precision'),
            calibration_target_precision=validation.get('calibration_target_precision'),evaluation_scope=validation.get('evaluation_scope'),
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


def references_for(folder,snap):
    import soundfile as sf
    from audio import read_interval
    refs=[]
    for index,reference in enumerate(snap['search_snapshot']['references']):
        row=reference['source'];query=reference['query'];key=f"R{snap['run_id']}-{index+1}"
        record={'id':key,'run_id':snap['run_id'],'species_id':snap['species']['id'],
            'reference_index':index,'source':row,'query':query,'attribution':row.get('attribution'),
            'status':'included','original_file':None,'clip_file':None,'spectrogram':None}
        try:
            original=checked_source(row);sha=digest(original)
            destination=folder/'references'/f'{sha}{original.suffix.lower()}'
            destination.parent.mkdir(exist_ok=True)
            if not destination.exists():shutil.copyfile(original,destination)
            if digest(destination)!=sha:raise CatalogError('source_changed')
            samples,interval=read_interval(row,query['start_s'],query['end_s'],native_rate=True)
            clip=folder/'references'/f'{key}.wav';sf.write(clip,samples,interval['sample_rate'],subtype='FLOAT')
            png,display=spectrum(row,query['start_s'],query['end_s'])
            figure=folder/'references'/f'{key}.png';figure.write_bytes(png)
            checked_source(row)
            record.update(original_file=destination.relative_to(folder).as_posix(),clip_file=clip.relative_to(folder).as_posix(),
                spectrogram=figure.relative_to(folder).as_posix(),original_sha256=sha,clip_sha256=digest(clip),interval=interval,display=display)
        except CatalogError as error:
            if str(error) not in ('source_missing','source_changed','unreadable_audio'):raise
            record.update(status='unavailable',reason=str(error))
        refs.append(record)
    return refs


def score_tables(root,db,folder,snapshots,records):
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
                labels,starts,scores,best=arrays;columns={str(label):i for i,label in enumerate(labels)}
                record=records[analysis['recording_id']]
                indices=[i for i,start in enumerate(starts) if window_in_scope(record,float(start),float(start)+15,zone,group[0]['start_date'],group[0]['end_date'])]
                for snap in group:
                    column=columns.get(snap['species']['scientific_name'])
                    if column is None or analysis['id'] not in snap['analysis_ids']:continue
                    writers[snap['figure_id']].writerows([record['id'],analysis['id'],float(starts[i]),float(starts[i])+15,float(scores[i,column]),float(best[i,column])] for i in indices)
        for snap in snapshots:
            if snap.get('engine')=='custom':
                from custom_models import score_windows
                writers[snap['figure_id']].writerows([row[k] for k in fields] for row in score_windows(root,snap['model_id']))
                continue
            if snap['kind']!='search':continue
            zone=ZoneInfo(snap['site']['timezone'])
            rows=db.execute('''SELECT a.recording_id,s.analysis_id,s.start_s,s.end_s,h.similarity FROM search_hits h
                JOIN segments s ON s.id=h.segment_id JOIN analyses a ON a.id=s.analysis_id WHERE h.run_id=? ORDER BY h.segment_id''',(snap['run_id'],))
            for row in rows:
                record=records.get(row['recording_id'])
                if record and row['end_s']<=record['seconds'] and abs(row['end_s']-row['start_s']-5)<1e-6 and window_in_scope(record,row['start_s'],row['end_s'],zone,snap['start_date'],snap['end_date']):
                    writers[snap['figure_id']].writerow([*row,''])


def export(root,body):
    from quiz import report_results
    from validation import answered_items
    ctx=context(root,body.dataset_id,body.start_date.isoformat(),body.end_date.isoformat())
    if ctx.get('location_stale'):raise CatalogError('location_reanalysis')
    if ctx['revision']!=body.revision:raise CatalogError('workflow_changed')
    parent=Path(body.folder).expanduser().resolve()
    if not parent.is_dir():raise CatalogError('folder_missing')
    quality=current_validations(root,ctx);snapshots=[];t=messages(body.locale)
    from custom_models import list_models
    custom=list_models(root,ctx)
    from annotations import evidence as annotation_evidence, latest_answers
    annotations=[r for r in annotation_evidence(root,ctx) if r['species_id'] is None or r['species_id'] in body.species_ids]
    for sid in dict.fromkeys(body.species_ids):
        for model in ctx['models']:
            snap=detection_snapshot(root,ctx,model,sid,threshold_for(quality.get(sid),model))
            snap['summaries']=[s for s in snap['summaries'] if s['species']['id']==sid]
            if not snap['summaries']:continue
            snap['validation']=quality.get(sid);snapshots.append(snap)
        for run in searches(root,body.dataset_id,sid):
            snap=search_snapshot(root,ctx,run['id'])
            if snap['summaries'][0]['eligible_windows']:snapshots.append(snap)
        for model in custom:
            if model['species']['id']==sid and model['applicable'] and model['status']=='ready':
                snapshots.append(custom_snapshot(root,ctx,model['id']))
    if not snapshots and not any(r['species_id'] for r in annotations):raise CatalogError('no_report_analyses')
    token=uuid4().hex[:10];package_id=f'echoroo-{body.dataset_id}-{token}'
    destination=parent/package_id;zip_path=parent/(package_id+'.zip')
    zone=ZoneInfo(ctx['site']['timezone']);index=[];species_rows=[];hourly=[];evidence=[];quality_rows=[];references=[]
    published_folder=published_zip=False
    try:
        with tempfile.TemporaryDirectory(prefix='.echoroo-',dir=parent) as temp:
            folder=Path(temp)/package_id;folder.mkdir();(folder/'figures').mkdir()
            with connect(root) as db:
                records=scope_records([dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?',(body.dataset_id,))],zone,ctx['start_date'],ctx['end_date'])
                records={r['id']:r for r in records}
                for record in records.values():checked_source(record)
                quiz=report_results(db,body.species_ids,include_sessions=True)
                daily={}
                for i in range((body.end_date-body.start_date).days+1):daily[str(body.start_date+timedelta(days=i))]=0.
                for record in records.values():
                    for (day,_),seconds in effort(record,zone):
                        if day in daily:daily[day]+=seconds
                scope={'recording_ids':sorted(records),'recorded_seconds':sum(daily.values()),'daily':[{'date':day,'seconds':seconds} for day,seconds in daily.items()],
                    'analysed_seconds':{}}
                handled_validations=set()
                score_tables(root,db,folder,snapshots,records)
                for position,snap in enumerate(snapshots):
                    snap['figure_id']=f'F{position+1:03d}';sid=snap['species']['id'];engine=snap.get('engine','search')
                    snap['recording_ids']=sorted(records)
                    if engine=='search':
                        refs=references_for(folder,snap);references.extend(refs);snap['reference_ids']=[r['id'] for r in refs]
                    key=method(snap,t)
                    scope['analysed_seconds'][key]=max(scope['analysed_seconds'].get(key,0),sum(c['analysed_seconds'] for c in snap['cells']))
                    report_id=save_report(db,body.dataset_id,snap['kind'],snap)
                    common={'report_id':report_id,'figure_id':snap['figure_id'],'species_id':sid,'scientific_name':snap['species']['scientific_name'],
                        'common_name_ja':snap['species']['common_names'].get('ja',''),'common_name_en':snap['species']['common_names'].get('en',''),
                        'engine':engine,'model_sha256':snap['model_sha256'],'search_run_id':snap.get('run_id'),'custom_model_id':snap.get('model_id'),'threshold':snap['threshold'],
                        'start_date':snap['start_date'],'end_date':snap['end_date'],'timezone':snap['site']['timezone'],'window_s':snap['window_s'],'quality':status(snap)}
                    caption=criterion(snap,t).replace('\n',' · ')+' · '+t[status(snap)]
                    png,svg=spiral(snap,body.locale,caption);(folder/'figures'/f"{snap['figure_id']}.png").write_bytes(png);(folder/'figures'/f"{snap['figure_id']}.svg").write_text(svg,encoding='utf-8')
                    species_rows.extend({**common,**{k:v for k,v in row.items() if k not in ('species','quality')}} for row in snap['summaries'])
                    hourly.extend({**common,**cell} for cell in snap['cells'])
                    ranked=sorted(snap['evidence'],key=lambda e:-e['score'])
                    selected={(e['recording_id'],e['start_s'],e['end_s']) for e in ranked[:5]} if engine!='search' else {(e['recording_id'],e['start_s'],e['end_s']) for e in ranked if e['status']=='target'}
                    for item in snap['evidence']:
                        key=(item['recording_id'],item['start_s'],item['end_s'])
                        row={**common,**item,'source_path':records[key[0]]['path'],'audio_file':'','audio_role':'','audio_reason':'not_selected_example'}
                        if key in selected:
                            row.update(audio_file=save_audio(folder,records[key[0]],key[1],key[2]),audio_role='search_positive' if engine=='search' else 'ai_example',audio_reason='included')
                        if item.get('judgement_id'):
                            judgement=db.execute('SELECT quiz_snapshot,examples_snapshot FROM search_judgements WHERE id=?',(item['judgement_id'],)).fetchone()
                            row['quiz_at_answer']=json.loads(judgement[0]) if judgement and judgement[0] else None
                            row['examples_at_answer']=json.loads(judgement[1]) if judgement and judgement[1] else None
                        evidence.append(row)
                    v=snap.get('validation');result=quality_result(snap)
                    common.update(validation_method=v.get('method') if v else None,target_precision=v.get('target_precision') if v else None,
                        calibration_target_precision=v.get('calibration_target_precision') if v else None,
                        evaluation_scope=v.get('evaluation_scope') if v else None,sampling_unit=v.get('sampling_unit') if v else None,
                        uncertainty_method=v.get('uncertainty') if v else None,human_reference=v.get('reference') if v else None)
                    quality_rows.append({**common,'validation_id':v['id'] if v else None,'validation_applicable':v['applicable'] if v else False,
                        **quality_export(v,result),'evaluation_checkpoint':v.get('evaluated_count') if v else None})
                    if engine=='custom':
                        model=dict(db.execute('SELECT * FROM custom_models WHERE id=?',(snap['model_id'],)).fetchone())
                        model['snapshot']=json.loads(model['snapshot'])
                        model_folder=folder/'models'/str(snap['model_id']);model_folder.mkdir(parents=True,exist_ok=True)
                        for key,filename in (('artifact','model.npz'),('scores','scores.npz')):
                            source=Path(model[key+'_path'])
                            target=model_folder/filename
                            shutil.copyfile(source,target)
                            if digest(target)!=model[key+'_sha256']:raise CatalogError('custom_model_files_changed')
                            model[key+'_path']=target.relative_to(folder).as_posix()
                        write_json(model_folder/'provenance.json',model)
                    if v and v['applicable'] and v['id'] not in handled_validations:
                        handled_validations.add(v['id']);saved=db.execute('SELECT * FROM validation_sessions WHERE id=?',(v['id'],)).fetchone()
                        answers=answered_items(db,v['id']);answered=[]
                        for answer in answers:
                            if answer['verdict'] is None:continue
                            record=records[answer['recording_id']];start=answer['start_s'];end=start+v['window_s']
                            _,local=window_key(record,start,zone)
                            clip=save_audio(folder,record,start,end)
                            answered.append({**answer,'audio_file':clip})
                            evidence.append({**common,**answer,'validation_id':v['id'],'start_s':start,'end_s':end,'local_time':local,
                                'audio_file':clip,'audio_role':'validation','audio_reason':'included','status':answer['verdict']})
                        history=[dict(r) for r in db.execute('''SELECT a.* FROM validation_answers a JOIN validation_items i ON i.id=a.item_id
                            WHERE i.session_id=? ORDER BY a.id''',(v['id'],))]
                        write_json(folder/'validation'/f"{v['id']}.json",{**dict(saved),'snapshot':json.loads(saved['snapshot']),
                            'items':answered,'answer_history':history})
                    write_json(folder/'results'/f'{report_id}.json',{'id':report_id,**snap})
                    index.append(common)
                taxa={s['species']['id']:s['species'] for s in snapshots}
                manual_species={}
                for entry in annotations:
                    record=records[entry['recording_id']]
                    entry['audio_file']=save_audio(folder,record,entry['start_s'],entry['end_s'])
                    entry['review_audio_file']=save_audio(folder,record,entry['review_start_s'],entry['review_end_s'])
                    png,display=spectrum(record,entry['review_start_s'],entry['review_end_s'],[entry])
                    key=f"A{entry['origin']}-{entry['answer_id']}-{entry['start_s']:.6f}-{entry['end_s']:.6f}"
                    entry['spectrogram']=f'figures/{key}.png';(folder/entry['spectrogram']).write_bytes(png);entry['display']=display
                    evidence.append({**entry,'audio_role':'annotation','audio_reason':'included','status':'target' if entry['species_id'] else 'unknown'})
                    if entry['species_id']:
                        taxa[entry['species_id']]=entry['species']
                        intervals=manual_species.setdefault(entry['species_id'],set())
                        intervals.add((entry['recording_id'],entry['review_start_s'],entry['review_end_s']))
                for sid,intervals in manual_species.items():
                    species_rows.append({'species_id':sid,'scientific_name':taxa[sid]['scientific_name'],'engine':'human_time_ranges',
                        'annotated_intervals':len(intervals),'coverage':'partial_positive_ranges'})
                for sid in {r['session_id'] for r in annotations if r['origin']=='annotation'}:
                    saved=db.execute('SELECT * FROM annotation_sessions WHERE id=?',(sid,)).fetchone()
                    write_json(folder/'annotations'/f'{sid}.json',{**dict(saved),'snapshot':json.loads(saved['snapshot']),
                        'items':latest_answers(db,sid),'answer_history':[dict(r) for r in db.execute('SELECT * FROM annotation_answers WHERE item_id IN (SELECT id FROM annotation_items WHERE session_id=?) ORDER BY id',(sid,))]})
                write_json(folder/'annotations'/'ranges.json',annotations)
                fields=['species_id','recording_id','start_s','end_s','review_start_s','review_end_s','coverage','origin','session_id','answer_id','answered_at','audio_file','review_audio_file']
                (folder/'annotations'/'ranges.csv').write_bytes(csv_bytes(annotations,fields))
                manifest={**ctx,'schema':'echoroo_recording_report_v1','package_id':package_id,'saved_at':datetime.now(timezone.utc).isoformat(),
                    'format':'html','locale':body.locale,'scope':scope,'results':index,'selected_species':list(taxa.values()),
                    'manual_species':[{'species':taxa[sid],'intervals':len(intervals)} for sid,intervals in manual_species.items()],
                    'report_file':'index.html','folder_path':str(destination),
                    'file_guide':[['species.csv',t['species_list']],['hourly.csv',t['details']],['quality.csv',t['quality_title']],['scores/',t['all_scores']],
                        ['evidence.csv / audio/',t['evidence_files']],['validation/',t['validation_files']],['annotations/',t['annotation_files']],['quiz-results.json',t['quiz_title']],
                        ['references.json / references/',t['references']],['manifest.json / results/',t['methods_title']]]}
                if any(s.get('engine')=='custom' for s in snapshots):
                    manifest['file_guide'].append(['models/',t['custom_model_files']])
                (folder/'figures'/'recording-effort.png').write_bytes(recording_effort(scope))
                for filename,rows in [('species',species_rows),('hourly',hourly),('evidence',evidence),('quality',quality_rows)]:
                    fields=list(dict.fromkeys(k for row in rows for k in row))
                    values=[{k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in rows]
                    (folder/(filename+'.csv')).write_bytes(csv_bytes(values,fields or ['report_id']))
                write_json(folder/'recordings.json',list(records.values()));write_json(folder/'references.json',references);write_json(folder/'quiz-results.json',quiz)
                create_html(folder,manifest,snapshots,references,evidence,quiz)
                (folder/'README.txt').write_text(t['files_note']+'\n\n'+t['evidence_note'],encoding='utf-8')
                shutil.copyfile(DATA/'echoroo.png',folder/'figures'/'echoroo.png')
                shutil.copyfile(DATA/'roseus-license.txt',folder/'roseus-license.txt')
                shutil.copyfile(DATA/'fonts'/'OFL.txt',folder/'font-license.txt')
                manifest['files']={p.relative_to(folder).as_posix():{'sha256':digest(p),'bytes':p.stat().st_size} for p in sorted(folder.rglob('*')) if p.is_file()}
                write_json(folder/'manifest.json',manifest)
                if context(root,body.dataset_id,ctx['start_date'],ctx['end_date'])['revision']!=body.revision:raise CatalogError('workflow_changed')
                for record in records.values():checked_source(record)
                staged_zip=Path(temp)/(package_id+'.zip')
                with zipfile.ZipFile(staged_zip,'w',compression=zipfile.ZIP_DEFLATED) as archive:
                    for path in sorted(folder.rglob('*')):
                        if path.is_file():archive.write(path,path.relative_to(folder).as_posix())
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
