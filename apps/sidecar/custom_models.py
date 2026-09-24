"""Small one-species classifiers over fixed Perch embeddings."""
import hashlib
import json
import os
import shutil
import tempfile
from collections import defaultdict
from datetime import date,datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from scipy.optimize import minimize
from scipy.special import expit

from reports import analysis_metadata, calendar_cells, effort, finalize_cells, window_in_scope, window_key
from species import names_for
from storage import CatalogError, checked_source, connect

CV_FOLDS = 3
MIN_CLASS_EXAMPLES = 3
TARGET_PRECISION = .90
L2 = 1.0
FEATURE_SPEC = 'perch_embedding_1536_l2_v1'


def _digest(path):
    value=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def _group_key(record,unit,zone):
    if unit=='local_day':
        return datetime.fromisoformat(record['recorded_at']).astimezone(zone).date().isoformat()
    return str(record['id'])


def _prior_evaluation_recordings(db,species_id,records,unit,zone):
    used={row[0] for row in db.execute('''SELECT DISTINCT i.recording_id FROM validation_items i
        JOIN validation_sessions v ON v.id=i.session_id JOIN validation_answers a ON a.item_id=i.id
        WHERE v.species_id=? AND i.phase='evaluation' ''',(species_id,)) if row[0] in records}
    groups={_group_key(records[rid],unit,zone) for rid in used}
    expanded=sorted(rid for rid,record in records.items() if _group_key(record,unit,zone) in groups)
    return sorted(used),expanded


def _folds(group_labels,seed,species_id):
    keys=sorted(group_labels)
    digest=hashlib.sha256(f'custom-model-cv-v1\0{seed}\0{species_id}'.encode()).digest()
    rng=np.random.default_rng(int.from_bytes(digest[:8],'big'))
    order=[keys[i] for i in rng.permutation(len(keys))]
    rank={key:index for index,key in enumerate(order)}
    # Spread each class over folds before balancing fold sizes. Labels decide
    # feasibility; scores never influence the split.
    order.sort(key=lambda key:(-len(group_labels[key]),rank[key]))
    counts=[defaultdict(int) for _ in range(CV_FOLDS)];totals=[0]*CV_FOLDS;result={}
    for key in order:
        fold=min(range(CV_FOLDS),key=lambda i:(sum(counts[i][label] for label in group_labels[key]),totals[i],i))
        result[key]=fold;totals[fold]+=1
        for label in group_labels[key]:counts[fold][label]+=1
    return result,digest.hex()


def _sources_current(db,examples,species_id):
    sources={(source['source_kind'],source['source_id'],source['source_answer_id']) for row in examples for source in row['sources']}
    for kind,source_id,answer_id in sources:
        if kind=='annotation_range':
            current=db.execute('SELECT id,complete FROM annotation_answers WHERE id=(SELECT MAX(id) FROM annotation_answers WHERE item_id=?)',(source_id,)).fetchone()
            if not current or current['id']!=answer_id or not current['complete']:return False
        elif kind in ('validation_calibration','search_validation'):
            current=db.execute('''SELECT a.id,v.snapshot FROM validation_items i JOIN validation_sessions v ON v.id=i.session_id
                LEFT JOIN validation_answers a ON a.id=(SELECT MAX(id) FROM validation_answers WHERE item_id=i.id) WHERE i.id=?''',(source_id,)).fetchone()
            if not current or current['id']!=answer_id:return False
            from validation import annotation_source_current
            if not annotation_source_current(db,json.loads(current['snapshot'])):return False
        else:
            current=db.execute('SELECT MAX(id) FROM search_judgements WHERE species_id=? AND segment_id=?',(species_id,source_id)).fetchone()[0]
            if current!=answer_id:return False
    return True


def _vector_batches(root,analysis_ids,segment_ids=None,size=256):
    if not analysis_ids:return
    query='''SELECT s.id AS segment_id,s.analysis_id,a.recording_id,s.start_s,s.end_s,v.embedding
        FROM segments s JOIN analyses a ON a.id=s.analysis_id JOIN vectors v ON v.rowid=s.id
        WHERE s.analysis_id IN (SELECT value FROM json_each(?)) AND ABS(s.end_s-s.start_s-5)<0.000001'''
    params=[json.dumps(analysis_ids)]
    if segment_ids is not None:
        query+=' AND s.id IN (SELECT value FROM json_each(?))';params.append(json.dumps(sorted(segment_ids)))
    query+=' ORDER BY a.recording_id,s.start_s'
    with connect(root) as db:
        cursor=db.execute(query,params)
        while True:
            rows=cursor.fetchmany(size)
            if not rows:return
            valid=[];vectors=[]
            for row in rows:
                vector=np.frombuffer(row['embedding'],dtype='<f4')
                if len(vector)==1536 and np.isfinite(vector).all() and np.linalg.norm(vector)>0:
                    valid.append({k:row[k] for k in ('segment_id','analysis_id','recording_id','start_s','end_s')});vectors.append(vector)
            if valid:yield valid,_normalise(np.stack(vectors))


def _fit(x,y):
    def objective(value):
        weights,intercept=value[:-1],value[-1]
        logits=x@weights+intercept
        loss=np.logaddexp(0,logits)-y*logits
        return float(loss.mean()+L2*np.dot(weights,weights)/(2*len(y)))
    def gradient(value):
        weights,intercept=value[:-1],value[-1]
        residual=expit(x@weights+intercept)-y
        return np.r_[x.T@residual/len(y)+L2*weights/len(y),residual.mean()]
    result=minimize(objective,np.zeros(x.shape[1]+1),jac=gradient,method='L-BFGS-B')
    if not result.success or not np.isfinite(result.x).all():raise CatalogError('custom_model_fit_failed')
    return result.x[:-1].astype(np.float32),float(result.x[-1])


def _normalise(matrix):
    matrix=np.asarray(matrix,dtype=np.float32)
    norms=np.linalg.norm(matrix,axis=1,keepdims=True)
    if matrix.ndim!=2 or matrix.shape[1]!=1536 or not np.isfinite(matrix).all() or np.any(norms==0):
        raise CatalogError('invalid_vectors')
    return matrix/norms


def _prepare(root,annotation_session_id,species_id):
    from results_workflow import fingerprint
    with connect(root) as db:
        annotation=db.execute('SELECT * FROM annotation_sessions WHERE id=?',(annotation_session_id,)).fetchone()
        taxon=db.execute('SELECT * FROM species WHERE id=?',(species_id,)).fetchone()
        if not annotation or not taxon:raise CatalogError('not_found')
        snap=json.loads(annotation['snapshot'])
        dataset=db.execute('SELECT d.*,s.id AS site_id,s.timezone FROM datasets d JOIN sites s ON s.id=d.site_id WHERE d.id=?',(annotation['dataset_id'],)).fetchone()
        if not dataset:raise CatalogError('not_found')
        recording_ids=sorted(set(snap['calibration_recording_ids'])|set(snap['evaluation_recording_ids']))
        records={r['id']:dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?',(annotation['dataset_id'],)) if r['id'] in recording_ids}
        analyses=[dict(r) for r in db.execute('''SELECT a.* FROM analyses a JOIN site_recordings r ON r.id=a.recording_id
            WHERE r.scope_id=? AND a.engine='perch' AND a.id IN (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine)
            ORDER BY a.recording_id''',(annotation['dataset_id'],)) if r['recording_id'] in recording_ids]
        analysis_by_record={row['recording_id']:row for row in analyses}
        expected=next((m['model_sha256'] for m in snap.get('models',[]) if m['engine']=='perch'),None)
        hashes={analysis_metadata(row['metadata'])['model_sha256'] for row in analyses}
        reasons=[]
        current_fingerprint=fingerprint(root,annotation['dataset_id'])
        if annotation['fingerprint']!=current_fingerprint:reasons.append('annotation_source_changed')
        if len(analyses)!=len(recording_ids) or not expected:reasons.append('custom_perch_missing')
        if len(hashes)>1 or hashes and expected not in hashes:reasons.append('custom_perch_mixed')
        unit=snap['split_unit'];zone=ZoneInfo(dataset['timezone'])
        training=set(snap['calibration_recording_ids'])
        prior_evaluation,excluded_evaluation=_prior_evaluation_recordings(db,species_id,records,unit,zone)
        evaluation=set(snap['evaluation_recording_ids'])-set(excluded_evaluation);intervals=[];direct=[];excluded=defaultdict(int)
        rows=db.execute('''SELECT i.id AS source_id,i.recording_id,a.id AS answer_id,a.ranges,a.complete FROM annotation_items i
            LEFT JOIN annotation_answers a ON a.id=(SELECT MAX(id) FROM annotation_answers WHERE item_id=i.id) WHERE i.session_id=?''',(annotation_session_id,))
        for row in rows:
            if row['recording_id'] not in training or not row['complete']:continue
            for value in json.loads(row['ranges'] or '[]'):
                if value['species_id']==species_id:intervals.append((row['recording_id'],value['start_s'],value['end_s'],'target',{'source_kind':'annotation_range','source_id':row['source_id'],'source_answer_id':row['answer_id']}))
        rows=db.execute('''SELECT i.id AS source_id,i.phase,i.recording_id,i.start_s,a.id AS answer_id,a.verdict,a.ranges,v.snapshot
            FROM validation_items i JOIN validation_sessions v ON v.id=i.session_id
            JOIN validation_answers a ON a.id=(SELECT MAX(id) FROM validation_answers WHERE item_id=i.id)
            WHERE v.species_id=? AND i.phase='calibration' ''',(species_id,))
        for row in rows:
            if row['recording_id'] not in training:continue
            if row['verdict']=='unknown':excluded['unknown']+=1;continue
            validation=json.loads(row['snapshot']);window=float(validation.get('window_s',15))
            from validation import annotation_source_current
            if not annotation_source_current(db,validation):continue
            source={'source_kind':'search_validation' if validation.get('run_id') else 'validation_calibration','source_id':row['source_id'],'source_answer_id':row['answer_id']}
            if row['verdict']=='other':intervals.append((row['recording_id'],row['start_s'],row['start_s']+window,'other',source))
            else:
                ranges=[value for value in json.loads(row['ranges'] or '[]') if value['species_id']==species_id]
                if ranges:
                    for value in ranges:intervals.append((row['recording_id'],value['start_s'],value['end_s'],'target',source))
                elif window==5:intervals.append((row['recording_id'],row['start_s'],row['start_s']+5,'target',source))
                else:excluded['target_without_range']+=1
        rows=db.execute('''SELECT j.id AS answer_id,j.segment_id,j.verdict,a.recording_id FROM search_judgements j
            JOIN segments s ON s.id=j.segment_id JOIN analyses a ON a.id=s.analysis_id
            WHERE j.species_id=? AND j.id=(SELECT MAX(id) FROM search_judgements WHERE species_id=j.species_id AND segment_id=j.segment_id)''',(species_id,))
        for row in rows:
            if row['recording_id'] not in training:continue
            if row['verdict']=='unknown':excluded['unknown']+=1;continue
            direct.append((row['segment_id'],row['verdict'],{'source_kind':'search_judgement','source_id':row['segment_id'],'source_answer_id':row['answer_id']}))
        affected={row[0] for row in intervals}|{row['recording_id'] for row in db.execute('''SELECT a.recording_id FROM search_judgements j JOIN segments s ON s.id=j.segment_id JOIN analyses a ON a.id=s.analysis_id
            WHERE j.species_id=?''',(species_id,)) if row['recording_id'] in training}
        affected_analyses=[analysis_by_record[rid]['id'] for rid in affected if rid in analysis_by_record]
        segment_rows=[dict(row) for row in db.execute('''SELECT s.id AS segment_id,s.analysis_id,a.recording_id,s.start_s,s.end_s FROM segments s JOIN analyses a ON a.id=s.analysis_id
            WHERE s.analysis_id IN (SELECT value FROM json_each(?)) AND ABS(s.end_s-s.start_s-5)<0.000001 ORDER BY a.recording_id,s.start_s''',(json.dumps(affected_analyses),))]
        segment_by_id={row['segment_id']:row for row in segment_rows};by_record=defaultdict(list)
        for row in segment_rows:by_record[row['recording_id']].append(row)
        labels=defaultdict(list)
        for recording_id,start,end,label,source in intervals:
            found=False
            for segment in by_record.get(recording_id,[]):
                if min(float(end),segment['end_s'])>max(float(start),segment['start_s']):
                    labels[segment['segment_id']].append({**source,'label':label,'range_start_s':float(start),'range_end_s':float(end)});found=True
            if not found:excluded['missing_embedding']+=1
        for segment_id,label,source in direct:
            segment=segment_by_id.get(segment_id)
            if not segment:excluded['missing_embedding']+=1;continue
            labels[segment_id].append({**source,'label':label,'range_start_s':segment['start_s'],'range_end_s':segment['end_s']})
        conflicts=sum(len({origin['label'] for origin in origins})>1 for origins in labels.values())
        adopted={segment_id for segment_id,origins in labels.items() if len({origin['label'] for origin in origins})==1}
        vectors={}
        for batch,matrix in _vector_batches(root,[a['id'] for a in analyses],adopted):
            for row,vector in zip(batch,matrix):vectors[row['segment_id']]=vector
        examples=[]
        for segment_id,origins in labels.items():
            classes={origin['label'] for origin in origins}
            if len(classes)!=1:continue
            segment=segment_by_id[segment_id];vector=vectors.get(segment_id)
            if vector is None:excluded['missing_embedding']+=1;continue
            record=records[segment['recording_id']]
            if not window_in_scope(record,segment['start_s'],segment['end_s'],zone,snap['start_date'],snap['end_date']):
                excluded['outside_scope']+=1;continue
            examples.append({'segment_id':segment_id,'analysis_id':segment['analysis_id'],'recording_id':segment['recording_id'],
                'start_s':segment['start_s'],'end_s':segment['end_s'],'label':classes.pop(),'group_key':_group_key(record,unit,zone),
                'sources':origins,'vector':vector})
        counts={label:sum(row['label']==label for row in examples) for label in ('target','other')}
        class_groups={label:{row['group_key'] for row in examples if row['label']==label} for label in ('target','other')}
        groups={row['group_key'] for row in examples};group_labels={key:{row['label'] for row in examples if row['group_key']==key} for key in groups}
        folds,fold_seed=_folds(group_labels,snap['seed'],species_id) if groups else ({},'')
        fold_ok=len(groups)>=CV_FOLDS and all(any(row['label']=='target' and folds[row['group_key']]!=fold for row in examples) and
            any(row['label']=='other' and folds[row['group_key']]!=fold for row in examples) for fold in range(CV_FOLDS))
        evaluation_analyses=[analysis_by_record[rid]['id'] for rid in evaluation if rid in analysis_by_record];evaluation_groups=set()
        for row in db.execute('''SELECT a.recording_id,CAST(s.start_s/15 AS INTEGER) AS parent,COUNT(*) AS n FROM segments s
            JOIN analyses a ON a.id=s.analysis_id JOIN vectors v ON v.rowid=s.id WHERE s.analysis_id IN (SELECT value FROM json_each(?))
            AND ABS(s.end_s-s.start_s-5)<0.000001 GROUP BY a.recording_id,parent HAVING n=3''',(json.dumps(evaluation_analyses),)):
            start=float(row['parent']*15);record=records[row['recording_id']]
            if window_in_scope(record,start,start+15,zone,snap['start_date'],snap['end_date']):
                evaluation_groups.add(_group_key(record,unit,zone))
        if counts['target']<MIN_CLASS_EXAMPLES:reasons.append('custom_positive_examples_needed')
        if counts['other']<MIN_CLASS_EXAMPLES:reasons.append('custom_negative_examples_needed')
        if len(groups)<CV_FOLDS:reasons.append('custom_cv_groups_needed')
        elif not fold_ok:reasons.append('custom_cv_fold_missing_class')
        if not evaluation_groups:reasons.append('custom_evaluation_recordings_needed')
        reasons=list(dict.fromkeys(reasons))
        public={'schema':'custom_model_readiness_v1','annotation_session_id':annotation_session_id,'dataset_id':annotation['dataset_id'],
            'species':names_for(taxon),'scope':{'site_id':dataset['site_id'],'start_date':snap['start_date'],'end_date':snap['end_date'],'fingerprint':annotation['fingerprint']},
            'feature':{'engine':'perch','model_sha256':expected,'spec':FEATURE_SPEC,'window_s':5},
            'split':{'unit':unit,'calibration_recording_ids':sorted(training),'evaluation_recording_ids':sorted(evaluation),
                'prior_evaluation_recording_ids':prior_evaluation,'excluded_evaluation_group_recording_ids':excluded_evaluation,'cv_folds':CV_FOLDS,
                'cv_groups':len(groups),'evaluation_groups':len(evaluation_groups),'fold_seed':fold_seed,'fold_by_group':folds},
            'examples':{'positive_5s':counts['target'],'negative_5s':counts['other'],'positive_groups':len(class_groups['target']),
                'negative_groups':len(class_groups['other']),'recordings':len({row['recording_id'] for row in examples}),
                'label_conflicts':conflicts,'missing_embeddings':excluded['missing_embedding'],
                'unknown_excluded':excluded['unknown'],'evaluation_answers_excluded':db.execute('''SELECT COUNT(*) FROM validation_items i JOIN validation_sessions v ON v.id=i.session_id
                    JOIN validation_answers a ON a.id=(SELECT MAX(id) FROM validation_answers WHERE item_id=i.id)
                    WHERE v.species_id=? AND i.phase='evaluation' ''',(species_id,)).fetchone()[0],
                'by_source':dict(sorted((kind,sum(any(source['source_kind']==kind for source in row['sources']) for row in examples)) for kind in {source['source_kind'] for row in examples for source in row['sources']}))},
            'requirements':{'positive_5s':MIN_CLASS_EXAMPLES,'negative_5s':MIN_CLASS_EXAMPLES,'cv_groups':CV_FOLDS,'evaluation_groups':1},
            'can_train':not reasons,'reasons':reasons}
    return public,{'annotation':dict(annotation),'annotation_snapshot':snap,'dataset':dict(dataset),'records':records,'analyses':analyses,
        'examples':examples,'folds':folds,'current_fingerprint':current_fingerprint}


def readiness(root,annotation_session_id,species_id):
    return _prepare(root,annotation_session_id,species_id)[0]


def _cv(examples,folds):
    x=_normalise(np.stack([row['vector'] for row in examples]));y=np.array([row['label']=='target' for row in examples],dtype=np.float64)
    oof=np.empty(len(y),dtype=np.float64)
    for fold in range(CV_FOLDS):
        test=np.array([folds[row['group_key']]==fold for row in examples]);train=~test
        weights,intercept=_fit(x[train],y[train]);oof[test]=expit(x[test]@weights+intercept)
    best=None
    for threshold in sorted(set(map(float,oof))):
        predicted=oof>=threshold;tp=int(np.sum(predicted&(y==1)));fp=int(np.sum(predicted&(y==0)));fn=int(np.sum((~predicted)&(y==1)))
        precision=tp/(tp+fp) if tp+fp else 0.;recall=tp/(tp+fn) if tp+fn else 0.
        if precision>=TARGET_PRECISION:
            candidate=(recall,precision,threshold,tp,fp,fn)
            if best is None or candidate[:3]>best[:3]:best=candidate
    result={'status':'threshold_fixed' if best else 'threshold_unavailable','window_s':5,'folds':CV_FOLDS,'scope_label':'selected_training_labels_only',
        'oof':[{'segment_id':row['segment_id'],'label':row['label'],'score':float(score),'fold':folds[row['group_key']]} for row,score in zip(examples,oof)]}
    if best:
        recall,precision,threshold,tp,fp,fn=best
        result.update(threshold=threshold,reference_precision={'value':precision,'positive':tp,'predicted':tp+fp},reference_recall={'value':recall,'found':tp,'present':tp+fn})
    else:result['threshold']=None
    return result,x,y


def _source_snapshot(examples):
    return [{k:v for k,v in row.items() if k!='vector'} for row in examples]


def _eligible_5s(root,internal):
    analysis_ids=[row['id'] for row in internal['analyses']]
    snap=internal['annotation_snapshot'];zone=ZoneInfo(internal['dataset']['timezone']);total=0
    if not analysis_ids:return 0
    with connect(root) as db:
        rows=db.execute('''SELECT a.recording_id,s.start_s,s.end_s FROM segments s JOIN analyses a ON a.id=s.analysis_id
            WHERE s.analysis_id IN (SELECT value FROM json_each(?)) AND ABS(s.end_s-s.start_s-5)<0.000001''',(json.dumps(analysis_ids),))
        for row in rows:
            record=internal['records'].get(row['recording_id'])
            if record and window_in_scope(record,row['start_s'],row['end_s'],zone,snap['start_date'],snap['end_date']):total+=1
    return total


def _aggregate_arrays(analysis_ids,recording_ids,starts,ends,scores):
    result=[];parts={};current=None
    for analysis_id,recording_id,start,end,score in zip(analysis_ids,recording_ids,starts,ends,scores):
        start=float(start);key=(int(recording_id),float(int(start//15)*15));offset=round(start-key[1])
        if key!=current:
            if current is not None and set(parts)=={0,5,10}:
                best=max(parts.values(),key=lambda row:row['score'])
                result.append({'recording_id':current[0],'analysis_id':best['analysis_id'],'start_s':current[1],'end_s':current[1]+15,
                    'score':best['score'],'example_start_s':best['start_s']})
            current=key;parts={}
        if offset in (0,5,10) and abs(float(end)-start-5)<1e-6:
            parts[offset]={'analysis_id':int(analysis_id),'start_s':start,'score':float(score)}
    if current is not None and set(parts)=={0,5,10}:
        best=max(parts.values(),key=lambda row:row['score'])
        result.append({'recording_id':current[0],'analysis_id':best['analysis_id'],'start_s':current[1],'end_s':current[1]+15,
            'score':best['score'],'example_start_s':best['start_s']})
    return result


def create(root,annotation_session_id,species_id):
    state,internal=_prepare(root,annotation_session_id,species_id)
    if not state['can_train']:raise CatalogError(state['reasons'][0])
    examples=internal['examples'];cv,x,y=_cv(examples,internal['folds'])
    source_examples=_source_snapshot(examples)
    base_snapshot={'schema':'custom_model_v1','dataset_id':state['dataset_id'],'species_id':species_id,'annotation_session_id':annotation_session_id,
        'source_fingerprint':internal['current_fingerprint'],'scope':{**state['scope'],'recording_ids':sorted(internal['records'])},'feature':state['feature'],
        'split':state['split'],'training':{'positive_5s':state['examples']['positive_5s'],'negative_5s':state['examples']['negative_5s'],
            'groups':state['split']['cv_groups'],'recordings':state['examples']['recordings'],'l2':L2,'examples':source_examples,
            'excluded':{k:state['examples'][k] for k in ('label_conflicts','missing_embeddings','unknown_excluded','evaluation_answers_excluded')}},'cv':cv}
    def check_sources():
        from results_workflow import fingerprint
        if fingerprint(root,state['dataset_id'])!=internal['current_fingerprint']:
            raise CatalogError('annotation_source_changed')
        with connect(root) as db:
            if not _sources_current(db,source_examples,species_id):raise CatalogError('custom_model_labels_changed')
    if cv['threshold'] is None:
        snapshot={**base_snapshot,'inference':{'status':'not_run','scored_5s':0,'complete_15s':0,'missing_5s':0},'analysis_ids':[a['id'] for a in internal['analyses']]}
        check_sources()
        with connect(root) as db:
            model_id=db.execute('INSERT INTO custom_models(dataset_id,species_id,annotation_session_id,status,snapshot) VALUES(?,?,?,?,?)',
                (state['dataset_id'],species_id,annotation_session_id,'threshold_unavailable',json.dumps(snapshot,allow_nan=False))).lastrowid
        return get_model(root,model_id)
    weights,intercept=_fit(x,y)
    segment_ids=[];analysis_ids=[];recording_ids=[];starts=[];ends=[];scores=[]
    zone=ZoneInfo(internal['dataset']['timezone']);scope=internal['annotation_snapshot']
    for rows,matrix in _vector_batches(root,[row['id'] for row in internal['analyses']]):
        values=expit(matrix@weights+intercept)
        for row,score in zip(rows,values):
            record=internal['records'].get(row['recording_id'])
            if not record or not window_in_scope(record,row['start_s'],row['end_s'],zone,scope['start_date'],scope['end_date']):continue
            segment_ids.append(row['segment_id']);analysis_ids.append(row['analysis_id']);recording_ids.append(row['recording_id'])
            starts.append(row['start_s']);ends.append(row['end_s']);scores.append(float(score))
    complete=_aggregate_arrays(analysis_ids,recording_ids,starts,ends,scores)
    expected=_eligible_5s(root,internal)
    snapshot={**base_snapshot,'analysis_ids':[a['id'] for a in internal['analyses']],
        'inference':{'status':'complete','scored_5s':len(scores),'complete_15s':len(complete),'missing_5s':max(0,expected-len(scores))}}
    check_sources()
    folder=Path(root)/'custom-models';folder.mkdir(parents=True,exist_ok=True);temporary=final=None
    try:
        with connect(root) as db:
            model_id=db.execute('INSERT INTO custom_models(dataset_id,species_id,annotation_session_id,status,snapshot) VALUES(?,?,?,?,?)',
                (state['dataset_id'],species_id,annotation_session_id,'ready','{}')).lastrowid
            temporary=Path(tempfile.mkdtemp(prefix=f'.{model_id}-',dir=folder));final=folder/str(model_id)
            np.savez_compressed(temporary/'model.npz',weights=weights,intercept=np.array(intercept),l2=np.array(L2),feature_spec=np.array(FEATURE_SPEC),
                perch_model_sha256=np.array(state['feature']['model_sha256']))
            np.savez_compressed(temporary/'scores.npz',segment_ids=np.asarray(segment_ids,dtype=np.int64),analysis_ids=np.asarray(analysis_ids,dtype=np.int64),
                recording_ids=np.asarray(recording_ids,dtype=np.int64),start_s=np.asarray(starts),end_s=np.asarray(ends),scores=np.asarray(scores))
            artifact_hash=_digest(temporary/'model.npz');scores_hash=_digest(temporary/'scores.npz');os.replace(temporary,final)
            db.execute('UPDATE custom_models SET snapshot=?,artifact_path=?,artifact_sha256=?,scores_path=?,scores_sha256=? WHERE id=?',
                (json.dumps(snapshot,allow_nan=False),str(final/'model.npz'),artifact_hash,str(final/'scores.npz'),scores_hash,model_id))
    except Exception:
        if temporary and temporary.exists():shutil.rmtree(temporary)
        if final and final.exists():shutil.rmtree(final)
        raise
    return get_model(root,model_id)


def _evaluation(db,model_id):
    rows=db.execute('SELECT id,snapshot FROM validation_sessions ORDER BY id DESC')
    for row in rows:
        snap=json.loads(row['snapshot'])
        if snap.get('model_id')!=model_id:continue
        counts=db.execute('''SELECT COUNT(*) total,SUM(a.id IS NOT NULL) answered FROM validation_items i
            LEFT JOIN validation_answers a ON a.id=(SELECT MAX(id) FROM validation_answers WHERE item_id=i.id)
            WHERE i.session_id=? AND i.phase='evaluation' ''',(row['id'],)).fetchone()
        return {'validation_id':row['id'],'status':snap['stage'],'window_s':snap['window_s'],'answered':counts['answered'] or 0,'total':counts['total']}
    return {'validation_id':None,'status':'not_started','window_s':15,'answered':0,'total':0}


def _public(root,row,current_fingerprint=None):
    snap=json.loads(row['snapshot'])
    def file_ok(path,digest):
        if not path:return digest is None
        try:return Path(path).is_file() and _digest(path)==digest
        except OSError:return False
    artifact_ok=file_ok(row['artifact_path'],row['artifact_sha256']);scores_ok=file_ok(row['scores_path'],row['scores_sha256'])
    with connect(root) as db:
        taxon=names_for(db.execute('SELECT * FROM species WHERE id=?',(row['species_id'],)).fetchone());evaluation=_evaluation(db,row['id'])
        labels_ok=_sources_current(db,snap['training']['examples'],row['species_id'])
    scope_ok=(current_fingerprint or snap['source_fingerprint'])==snap['source_fingerprint']
    applicable=scope_ok and labels_ok and artifact_ok and scores_ok
    reason=(None if applicable else 'custom_model_scope_changed' if not scope_ok else
            'custom_model_labels_changed' if not labels_ok else 'custom_model_files_missing')
    return {'id':row['id'],'status':row['status'],'created_at':row['created_at'],'dataset_id':row['dataset_id'],'species':taxon,
        'scope':snap['scope'],'feature':snap['feature'],'training':{k:v for k,v in snap['training'].items() if k!='examples'},
        'cv':{k:v for k,v in snap['cv'].items() if k!='oof'},
        'split':snap['split'],'inference':snap['inference'],'evaluation':evaluation,'applicable':applicable,'reason':reason,
        'artifact_sha256':row['artifact_sha256'],'scores_sha256':row['scores_sha256']}


def get_model(root,model_id):
    from results_workflow import fingerprint
    with connect(root) as db:row=db.execute('SELECT * FROM custom_models WHERE id=?',(model_id,)).fetchone()
    if not row:raise CatalogError('not_found')
    return _public(root,row,fingerprint(root,row['dataset_id']))


def list_models(root,ctx,species_id=None):
    with connect(root) as db:
        rows=db.execute('''SELECT m.* FROM custom_models m JOIN datasets d ON d.id=m.dataset_id WHERE d.site_id=?
            AND (? IS NULL OR m.species_id=?) ORDER BY m.id DESC''',(ctx['site']['id'],species_id,species_id)).fetchall()
    candidates=defaultdict(list)
    for row in rows:
        snap=json.loads(row['snapshot'])
        if (snap['scope']['start_date'],snap['scope']['end_date'])==(ctx['start_date'],ctx['end_date']):
            candidates[row['species_id']].append(row)
    chosen=[]
    for group in candidates.values():
        latest=_public(root,group[0],ctx['fingerprint']);selected=None
        for row in group:
            model=latest if row is group[0] else _public(root,row,ctx['fingerprint'])
            if model['applicable'] and model['status']=='ready':selected=model;break
        chosen.append(selected or latest)
    return sorted(chosen,key=lambda model:model['id'],reverse=True)


def score_windows(root,model_id):
    with connect(root) as db:row=db.execute('SELECT * FROM custom_models WHERE id=?',(model_id,)).fetchone()
    if not row:raise CatalogError('not_found')
    if row['status']!='ready' or not row['scores_path']:raise CatalogError('custom_threshold_unavailable')
    path=Path(row['scores_path'])
    if not path.is_file() or _digest(path)!=row['scores_sha256']:raise CatalogError('custom_model_files_missing')
    with np.load(path,allow_pickle=False) as saved:
        keys=('segment_ids','analysis_ids','recording_ids','start_s','end_s','scores');lengths={len(saved[key]) for key in keys}
        if len(lengths)!=1 or not all(np.isfinite(saved[key]).all() for key in ('start_s','end_s','scores')):raise CatalogError('invalid_scores')
        return _aggregate_arrays(saved['analysis_ids'],saved['recording_ids'],saved['start_s'],saved['end_s'],saved['scores'])


def validation_population(root,ctx,model_id):
    model=get_model(root,model_id)
    if not model['applicable']:raise CatalogError(model['reason'])
    if model['cv']['threshold'] is None:raise CatalogError('custom_threshold_unavailable')
    allowed=set(model['split']['evaluation_recording_ids'])
    items=[{'recording_id':row['recording_id'],'start_s':row['start_s'],'scores':{'custom':row['score']}} for row in score_windows(root,model_id) if row['recording_id'] in allowed]
    if not items:raise CatalogError('custom_evaluation_recordings_needed')
    return items,model


def report_snapshot(root,ctx,model_id):
    model=get_model(root,model_id)
    if not model['applicable']:raise CatalogError(model['reason'])
    if (model['scope']['start_date'],model['scope']['end_date'])!=(ctx['start_date'],ctx['end_date']):raise CatalogError('custom_model_scope_changed')
    threshold=model['cv']['threshold']
    if threshold is None:raise CatalogError('custom_threshold_unavailable')
    windows=score_windows(root,model_id);record_ids=set(model['scope']['recording_ids']);zone=ZoneInfo(ctx['site']['timezone'])
    with connect(root) as db:records={r['id']:dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?',(ctx['dataset']['id'],)) if r['id'] in record_ids}
    first=date.fromisoformat(ctx['start_date']);last=date.fromisoformat(ctx['end_date'])
    cells=calendar_cells(list(records.values()),zone,first,last);eligible=found=0;days=set();candidate_days=set();evidence=[]
    for record in records.values():
        checked_source(record)
        for key,duration in effort(record,zone):
            if key in cells:cells[key]['analysed_seconds']+=duration
    for row in windows:
        record=records.get(row['recording_id'])
        if not record or not window_in_scope(record,row['start_s'],row['end_s'],zone,ctx['start_date'],ctx['end_date']):continue
        key,local=window_key(record,row['start_s'],zone);eligible+=1;days.add(key[0]);cell=cells[key];cell['eligible_windows']+=1;cell['effective_seconds']+=15
        if row['score']>=threshold:
            found+=1;candidate_days.add(key[0]);cell['candidate_windows']+=1
            evidence.append({**row,'path':record['path'],'seconds':record['seconds'],'recorded_at':record['recorded_at'],'timezone':record['timezone'],
                'start_s':row['example_start_s'],'end_s':row['example_start_s']+5,'window_start_s':row['start_s'],'window_end_s':row['end_s'],
                'local_time':window_key(record,row['example_start_s'],zone)[1],'window_local_time':local,'date':key[0],'hour':key[1],'status':'ai_candidate'})
    summary={'species':model['species'],'eligible_windows':eligible,'candidate_windows':found,'candidate_rate':found/eligible if eligible else None,
        'recorded_days':len(days),'candidate_days':len(candidate_days),'quality':'not_evaluated'}
    return {'kind':'detection','dataset':ctx['dataset'],'site':ctx['site'],'engine':'custom','model_sha256':model['artifact_sha256'],'model_id':model_id,
        'custom_model':{'id':model_id,'perch_model_sha256':model['feature']['model_sha256'],'feature_spec':model['feature']['spec'],
            'cv_window_s':5,'evaluation_window_s':15,'training':model['training'],'cv':model['cv'],'split':model['split']},
        'analysis_ids':sorted({row['analysis_id'] for row in windows}),'missing_region':[],'threshold':threshold,'start_date':ctx['start_date'],'end_date':ctx['end_date'],
        'window_s':15,'species':model['species'],'summaries':[summary],'cells':finalize_cells(cells,threshold),'evidence':evidence,
        'quality':'not_evaluated','complete_windows_only':True,'recording_ids':sorted(records),'base_report_id':None}
