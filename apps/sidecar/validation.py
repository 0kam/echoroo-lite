"""Versioned human validation of complete detection or search windows.

Calibration and evaluation samples never overlap. Evaluation uses fixed strata,
known finite populations, and exact hypergeometric limits. Unknown answers are
retained as unresolved outcomes, never silently converted into negatives.
"""
import json
from math import comb
import random
import secrets
from collections import defaultdict
from functools import lru_cache
from itertools import chain
from zoneinfo import ZoneInfo

import numpy as np
import precision_audit as audit

from reports import analysis_metadata, classification_batches, window_key, window_in_scope
from species import names_for
from storage import CatalogError, checked_source, connect

CALIBRATION_PER_BAND = 10
EVALUATION_PER_STRATUM = 60
EVALUATION_BATCH = 10
TARGET_PRECISION = .90
METHOD = 'stratified_finite_window_validation_v2'
THRESHOLD_POLICY = 'max_recall_then_precision_then_highest_cutoff_v2'


def population(root,ctx,species_id):
    with connect(root) as db:
        taxon=db.execute('SELECT * FROM species WHERE id=?',(species_id,)).fetchone()
        if taxon is None:
            raise CatalogError('not_found')
        rows=db.execute('''SELECT a.*,r.seconds,r.path,r.size,r.mtime_ns,r.recorded_at,r.timezone FROM analyses a
            JOIN site_recordings r ON r.id=a.recording_id WHERE r.scope_id=? AND a.id IN
            (SELECT MAX(id) FROM analyses GROUP BY recording_id,engine) ORDER BY a.engine,a.recording_id''',(ctx['dataset']['id'],)).fetchall()
        windows={};models={};analyses=[]
        zone=ZoneInfo(ctx['site']['timezone'])
        records = {row['recording_id']: row for row in rows}
        batches = (classification_batches(root, db, [row for row in rows if row['engine']==engine], records)
                   for engine in sorted({row['engine'] for row in rows}))
        for row, allowed, arrays in chain.from_iterable(batches):
            checked_source(row)
            if allowed is None:
                continue
            labels,starts,scores,_=arrays
            matches=np.flatnonzero(labels==taxon['scientific_name'])
            if not len(matches):
                continue
            sha=analysis_metadata(row['metadata'])['model_sha256']
            if row['engine'] in models and models[row['engine']]!=sha:
                raise CatalogError('validation_mixed_models')
            models[row['engine']]=sha;analyses.append(row['id'])
            for i,start in enumerate(starts):
                key,_=window_key(row,float(start),zone)
                if not window_in_scope(row,float(start),float(start)+15,zone,ctx['start_date'],ctx['end_date']):
                    continue
                item=windows.setdefault((row['recording_id'],float(start)),{'recording_id':row['recording_id'],'start_s':float(start),'scores':{}})
                item['scores'][row['engine']]=float(scores[i,matches[0]])
            checked_source(row)
    if not windows:
        raise CatalogError('validation_no_windows')
    return list(windows.values()),models,analyses


def grouped(items,group):
    result=defaultdict(list)
    for item in items:
        result[group(item)].append(item)
    return result


def add_items(db,session_id,phase,groups,rng,limit):
    chosen=[];strata={}
    for key,items in sorted(groups.items()):
        # Leave at least half of each calibration band for independent evaluation.
        count=min(limit,len(items)//2) if phase=='calibration' else min(limit,len(items))
        strata[key]={'population':len(items),'sample':count}
        for draw,item in enumerate(rng.sample(items,count)):
            chosen.append({**item,'stratum':key,'draw':draw})
    rng.shuffle(chosen)
    # Visit every nonempty stratum early, in a label-independent random order.
    if phase=='evaluation':chosen.sort(key=lambda item:item['draw'])
    for i,item in enumerate(chosen):
        db.execute('INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,stratum,scores) VALUES(?,?,?,?,?,?,?)',
            (session_id,phase,i,item['recording_id'],item['start_s'],item['stratum'],json.dumps(item['scores'])))
    return strata


def start_custom_session(root, model_id):
    from custom_models import get_model, validation_population
    from results_workflow import context
    model = get_model(root, model_id)
    first, last = model['scope']['start_date'], model['scope']['end_date']
    ctx = context(root, model['dataset_id'], first, last)
    items, model = validation_population(root, ctx, model_id)
    species_id = model['species']['id']
    with connect(root) as db:
        for row in db.execute('''SELECT id,snapshot FROM validation_sessions
            WHERE dataset_id=? AND species_id=? AND fingerprint=? ORDER BY id DESC''',
            (model['dataset_id'], species_id, ctx['fingerprint'])):
            if json.loads(row['snapshot']).get('model_id') == model_id:
                # One audit per immutable model version; never offer fresh samples until it passes.
                return view_session(root, row['id'], ctx=ctx)
    threshold = model['cv']['threshold']
    seed = secrets.randbits(63)
    snapshot = {
        'method': audit.METHOD, 'stage': 'evaluation', 'seed': str(seed),
        'models': {'custom': model['artifact_sha256']}, 'analysis_ids': [],
        'start_date': first, 'end_date': last, 'site': ctx['site'], 'population': items,
        'target_precision': audit.TARGET_PRECISION, 'calibration_target_precision': TARGET_PRECISION, 'window_s': 15,
        'thresholds': {'custom': {'threshold': threshold, 'adjusted': False}}, 'results': {},
        'reference': 'user_annotations', 'evaluation_scope': 'complete_15_second_windows_in_reserved_recordings',
        'sampling_unit': 'non_overlapping_15_second_interval',
        'evaluation_per_stratum': EVALUATION_PER_STRATUM, 'kind': 'custom', 'model_id': model_id,
        'calibration_recording_ids': model['split']['calibration_recording_ids'],
        'evaluation_recording_ids': model['split']['evaluation_recording_ids'],
        'excluded_recording_ids': model['split']['calibration_recording_ids'],
        'split_unit': model['split']['unit'], 'calibration_strata': {},
        'evaluation_engines': ['custom'], 'uncertainty': 'simultaneous_exact_hypergeometric_95',
    }
    with connect(root) as db:
        sid = db.execute('INSERT INTO validation_sessions(dataset_id,species_id,fingerprint,snapshot) VALUES(?,?,?,?)',
            (model['dataset_id'], species_id, ctx['fingerprint'], '{}')).lastrowid
        snapshot['rank_seed'] = audit.shared_seed(db, ctx['fingerprint'], snapshot)
        audit.prepare(db, sid, snapshot, items)
        db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',
            (json.dumps(snapshot, allow_nan=False), sid))
    return view_session(root, sid, ctx=ctx)


def start_session(root,body,_group=True):
    if _group and getattr(body, 'species_ids', None):
        from validation_groups import start_group
        return start_group(root, body)
    model_id = getattr(body, 'model_id', None)
    if model_id is not None:
        return start_custom_session(root, model_id)
    from results_workflow import context
    ctx=context(root,body.dataset_id,body.start_date.isoformat(),body.end_date.isoformat())
    if ctx.get('location_stale'):
        raise CatalogError('location_reanalysis')
    run_id=getattr(body,'run_id',None)
    shared = shared_calibration(root, body, ctx) if getattr(body, 'annotation_session_id', None) and run_id is None else None
    with connect(root) as db:
        existing=db.execute('''SELECT id,snapshot FROM validation_sessions WHERE dataset_id IN
            (SELECT id FROM datasets WHERE site_id=(SELECT site_id FROM datasets WHERE id=?))
            AND species_id=? AND fingerprint=? ORDER BY id DESC''',
            (body.dataset_id,body.species_id,ctx['fingerprint'])).fetchall()
        for row in existing:
            snap=json.loads(row['snapshot'])
            if (snap.get('model_id') is None and snap.get('run_id')==run_id and (snap['start_date'],snap['end_date'])==(ctx['start_date'],ctx['end_date'])
                and snap.get('annotation_source') == (shared['source'] if shared else None)):
                return view_session(root,row['id'],ctx=ctx,_group=_group)
    if run_id is None:
        items,models,analyses=population(root,ctx,body.species_id)
        extra={}
        if shared and not any(i['recording_id'] not in set(shared['excluded_recording_ids']) for i in items):
            raise CatalogError('annotation_no_holdout')
    else:
        items,models,analyses,extra=search_population(root,ctx,body.species_id,run_id)
    seed=secrets.randbits(63);rng=random.Random(seed)
    bands=np.quantile([i['scores']['search'] for i in items],[1/3,2/3]) if run_id else [.1,.5]
    groups=grouped(items,lambda i:str(sum(max(i['scores'].values())>=edge for edge in bands)))
    snapshot={'method':audit.METHOD,'stage':'calibration','seed':str(seed),'models':models,'analysis_ids':analyses,
        'start_date':ctx['start_date'],'end_date':ctx['end_date'],'site':ctx['site'],'population':items,
        'target_precision':audit.TARGET_PRECISION,'calibration_target_precision':TARGET_PRECISION,'window_s':15,'thresholds':{},'results':{},'reference':'user_annotations',
        'evaluation_scope':'complete_saved_windows_excluding_calibration_intervals','uncertainty':'simultaneous_exact_hypergeometric_95',
        'sampling_unit':'non_overlapping_15_second_interval','calibration_per_band':CALIBRATION_PER_BAND,
        'evaluation_per_stratum':EVALUATION_PER_STRATUM, 'kind':'search' if run_id else 'detection',
        'run_id':run_id, 'calibration_band_edges':[float(b) for b in bands],
        'calibration_threshold_policy':THRESHOLD_POLICY, **extra}
    with connect(root) as db:
        sid=db.execute('INSERT INTO validation_sessions(dataset_id,species_id,fingerprint,snapshot) VALUES(?,?,?,?)',
            (body.dataset_id,body.species_id,ctx['fingerprint'],'{}')).lastrowid
        if shared:
            snapshot.update(annotation_source=shared['source'], palette=shared['palette'],
                excluded_recording_ids=shared['excluded_recording_ids'], split_unit=shared['split_unit'],
                calibration_method='shared_calibration_recording_holdout_v1', evaluation_scope='complete_windows_in_reserved_recordings',
                calibration_strata=shared['strata'], shared_ranges={})
            lookup={(i['recording_id'],i['start_s']): i for i in items}
            for position, item in enumerate(shared['items']):
                scores=lookup.get((item['recording_id'],item['start_s']),{}).get('scores',{})
                iid=db.execute('INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,stratum,scores) VALUES(?,?,?,?,?,?,?)',
                    (sid,'calibration',position,item['recording_id'],item['start_s'],item['stratum'],json.dumps(scores))).lastrowid
                snapshot['shared_ranges'][str(iid)]=json.loads(item['ranges'])
                if any(r['species_id']==body.species_id for r in json.loads(item['ranges'])):
                    db.execute('INSERT INTO validation_answers(item_id,verdict,quiz_snapshot,ranges) VALUES(?,?,?,?)',
                        (iid,'target',item['quiz_snapshot'],item['ranges']))
        else:
            snapshot['calibration_strata']=add_items(db,sid,'calibration',groups,rng,CALIBRATION_PER_BAND)
        snapshot['rank_seed'] = audit.shared_seed(db, ctx['fingerprint'], snapshot)
        snapshot['uncertainty'] = audit.UNCERTAINTY
        if not sum(s['sample'] for s in snapshot['calibration_strata'].values()):
            raise CatalogError('validation_too_few')
        db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',(json.dumps(snapshot),sid))
    return view_session(root,sid,ctx=ctx,_group=_group)


def shared_calibration(root, body, ctx):
    from annotations import latest_answers, session_row, palette_for
    from sampling import draw_plan
    with connect(root) as db:
        row, snap = session_row(db, body.annotation_session_id)
        if (row['fingerprint'] != ctx['fingerprint'] or snap['site']['id'] != ctx['site']['id']
            or (snap['start_date'], snap['end_date']) != (ctx['start_date'], ctx['end_date'])):
            raise CatalogError('workflow_changed')
        if not snap['evaluation_recording_ids']:
            raise CatalogError('annotation_no_holdout')
        rows = latest_answers(db, row['id'])
        complete = []
        for item in rows:
            if not item['complete']: break
            complete.append(item)
        count = len(complete) if len(complete)==len(rows) else len(complete)//10*10
        if count < min(10, len(rows)):
            raise CatalogError('annotation_need_batch')
        complete = complete[:count]
        _, strata = draw_plan(np.array([i['stratum'] for i in snap['population']]), count, int(snap['seed'])+2)
        return {'source': {'session_id': row['id'], 'answers': {str(i['id']): i['answer_id'] for i in complete}},
                'items': complete, 'strata': {r['stratum']:r for r in strata}, 'palette': palette_for(db, snap, rows),
                'excluded_recording_ids': snap['calibration_recording_ids'], 'split_unit': snap['split_unit']}


def annotation_source_current(db, snap):
    source = snap.get('annotation_source')
    def current(item_id, answer_id):
        row = db.execute('''SELECT id,complete FROM annotation_answers
            WHERE id=(SELECT MAX(id) FROM annotation_answers WHERE item_id=?)''', (item_id,)).fetchone()
        return bool(row and row['id'] == answer_id and row['complete'])
    return not source or all(current(iid, aid) for iid, aid in source['answers'].items())


def custom_model_status(root, snapshot):
    model_id = snapshot.get('model_id')
    if model_id is None:
        return True, None
    from custom_models import get_model
    model = get_model(root, model_id)
    return model['applicable'], model['reason']


def reviewed_windows(db,species_id):
    return {tuple(row) for row in db.execute('''SELECT DISTINCT a.recording_id,s.start_s FROM search_judgements j
        JOIN segments s ON s.id=j.segment_id JOIN analyses a ON a.id=s.analysis_id WHERE j.species_id=?''',(species_id,))}


def search_population(root,ctx,species_id,run_id):
    with connect(root) as db:
        run=db.execute('SELECT * FROM search_runs WHERE id=?',(run_id,)).fetchone()
        if not run or run['species_id']!=species_id:
            raise CatalogError('not_found')
        snap=json.loads(run['snapshot'])
        if snap['site']['id']!=ctx['site']['id']:
            raise CatalogError('not_found')
        records={r['id']:dict(r) for r in db.execute('SELECT * FROM site_recordings WHERE scope_id=?',(ctx['dataset']['id'],))}
        zone=ZoneInfo(ctx['site']['timezone']);items=[];seen=[]
        rows=db.execute('''SELECT h.*,s.start_s,s.end_s,a.recording_id FROM search_hits h
            JOIN segments s ON s.id=h.segment_id JOIN analyses a ON a.id=s.analysis_id WHERE h.run_id=? ORDER BY h.segment_id''',(run_id,))
        reviewed=reviewed_windows(db,species_id)
        for row in rows:
            record=records.get(row['recording_id'])
            if not record or row['end_s']>record['seconds'] or abs(row['end_s']-row['start_s']-5)>1e-6:
                continue
            key,_=window_key(record,row['start_s'],zone)
            if not window_in_scope(record,row['start_s'],row['end_s'],zone,ctx['start_date'],ctx['end_date']):
                continue
            items.append({'recording_id':row['recording_id'],'start_s':row['start_s'],'scores':{'search':row['similarity']}})
            if (row['recording_id'],row['start_s']) in reviewed:
                seen.append([row['recording_id'],row['start_s']])
        for rid in {i['recording_id'] for i in items}:checked_source(records[rid])
    if not items:raise CatalogError('validation_no_windows')
    return items,{'search':snap['model_sha256']},snap['analysis_ids'],{
        'window_s':5,'sampling_unit':'non_overlapping_5_second_search_interval',
        'search_snapshot':snap,'previously_reviewed':seen,
        'evaluation_scope':'complete_saved_search_windows_excluding_calibration_and_previous_judgements'}


def answered_items(db,sid,phase=None):
    return [dict(r) for r in db.execute('''SELECT i.*,a.verdict,a.id AS answer_id,a.created_at AS answered_at,a.quiz_snapshot,a.ranges FROM validation_items i
        LEFT JOIN validation_answers a ON a.id=(SELECT MAX(id) FROM validation_answers WHERE item_id=i.id)
        WHERE i.session_id=? AND (? IS NULL OR i.phase=?) ORDER BY i.phase,i.position''',(sid,phase,phase))]


def suggest_thresholds(snapshot,items):
    result={}
    for engine in snapshot['models']:
        best=None
        for cutoff in np.round(np.arange(-1 if engine=='search' else .01,1.001,.01),2):
            tp=fp=fn=0.; positives=0
            for row in items:
                score=json.loads(row['scores']).get(engine)
                if score is None:
                    continue
                stratum=snapshot['calibration_strata'][row['stratum']]
                weight=stratum['population']/stratum['sample']
                detected=score>=cutoff;target=row['verdict']=='target'
                if detected:
                    tp+=weight*target;fp+=weight*(not target);positives+=int(target)
                elif target:
                    fn+=weight
            precision=tp/(tp+fp) if tp+fp else 0
            recall=tp/(tp+fn) if tp+fn else 0
            if precision>=snapshot.get('calibration_target_precision', snapshot['target_precision']) and positives>=3:
                candidate=(recall,precision,float(cutoff))
                if best is None or candidate>best:
                    best=candidate
        result[engine]={'threshold':round(best[2],2) if best else (None if engine=='search' else .1),'adjusted':best is not None}
    return result


def begin_evaluation(db,sid,snapshot,calibration):
    snapshot['thresholds']=suggest_thresholds(snapshot,calibration)
    snapshot['calibration_threshold_policy']=THRESHOLD_POLICY
    snapshot['calibration_answer_ids']=[r['answer_id'] for r in calibration]
    if snapshot.get('kind')=='search':
        reviewed=reviewed_windows(db,snapshot['search_snapshot']['species']['id'])
        snapshot['previously_reviewed']=[[r['recording_id'],r['start_s']] for r in snapshot['population'] if (r['recording_id'],r['start_s']) in reviewed]
    used={(r['recording_id'],r['start_s']) for r in calibration}
    used.update(tuple(r) for r in snapshot.get('previously_reviewed',[]))
    excluded=set(snapshot.get('excluded_recording_ids',[]))
    remaining=[r for r in snapshot['population'] if (r['recording_id'],r['start_s']) not in used and r['recording_id'] not in excluded]
    if snapshot['method'] == audit.METHOD:
        audit.prepare(db, sid, snapshot, remaining)
        return
    engines=sorted(snapshot['models'])
    def stratum(row):
        return ''.join('x' if engine not in row['scores'] or snapshot['thresholds'][engine]['threshold'] is None else '1' if row['scores'][engine]>=snapshot['thresholds'][engine]['threshold'] else '0' for engine in engines)
    snapshot['evaluation_engines']=engines
    snapshot['evaluation_strata']=add_items(db,sid,'evaluation',grouped(remaining,stratum),random.Random(int(snapshot['seed'])+1),EVALUATION_PER_STRATUM)
    snapshot['stage']='evaluation'
    snapshot['evaluation_population']=len(remaining)
    plan_checkpoints(snapshot)
    if not remaining or all(t['threshold'] is None for t in snapshot['thresholds'].values()):
        snapshot['results']=evaluate(snapshot,[]);snapshot['stage']='complete'
    # The sampling population remains in the saved snapshot for exact reproducibility.


@lru_cache(maxsize=4096)
def exact_count_interval(N,n,k,alpha):
    """Invert hypergeometric tails for the unknown finite-population positive count."""
    if not n:
        return 0,N
    if n==N:
        return k,k
    # Each stratum samples at most 60 windows. Sum exact combination counts
    # directly, avoiding a large scientific-library import on the final answer.
    denominator = comb(N,n)
    def tail(first,last,positive):
        return sum(comb(positive,x)*comb(N-positive,n-x)
                   for x in range(max(0,n-N+positive,first),min(n,positive,last)+1))/denominator
    left,right=k,N-n+k
    lo,hi=left,right
    while lo<hi:
        mid=(lo+hi)//2
        if tail(k,n,mid)>=alpha/2:
            hi=mid
        else:
            lo=mid+1
    lower=lo
    lo,hi=left,right
    while lo<hi:
        mid=(lo+hi+1)//2
        if tail(0,k,mid)>=alpha/2:
            lo=mid
        else:
            hi=mid-1
    return lower,lo


def plan_checkpoints(snapshot):
    """Fix all looks before evaluation; reserve half the error budget for the last."""
    snapshot['method']=METHOD
    total=sum(s['sample'] for s in snapshot['evaluation_strata'].values())
    snapshot['evaluation_checkpoints']=list(range(EVALUATION_BATCH,total,EVALUATION_BATCH))+([total] if total else [])
    snapshot['uncertainty']='simultaneous_exact_hypergeometric_95_across_strata_and_checkpoints'


def checkpoint_alpha(checkpoints,count):
    return .05 if len(checkpoints)==1 else .025 if count==checkpoints[-1] else .025/(len(checkpoints)-1)


def update_results(snapshot,rows):
    if snapshot['method'] == audit.METHOD:
        audit.update(snapshot, rows)
        return
    # Legacy completed sessions retain their original fixed-final-sample method.
    if 'evaluation_checkpoints' not in snapshot and snapshot['stage']!='complete':
        plan_checkpoints(snapshot)
    checkpoints=snapshot.get('evaluation_checkpoints',[len(rows)])
    answered=next((i for i,r in enumerate(rows) if r['verdict'] is None),len(rows))
    count=max((n for n in checkpoints if n<=answered),default=0)
    snapshot['evaluated_count']=count
    snapshot['results']=evaluate(snapshot,rows[:count],checkpoint_alpha(checkpoints,count)) if count else {}
    if answered==len(rows):snapshot['stage']='complete'


def evaluate(snapshot,items,alpha=.05):
    strata=snapshot['evaluation_strata'];bounds={};estimates={};unknowns={}
    alpha/=max(1,len(strata))
    for key,s in strata.items():
        rows=[r for r in items if r['stratum']==key]
        n=len(rows);yes=sum(r['verdict']=='target' for r in rows);unknown=sum(r['verdict']=='unknown' for r in rows)
        low=exact_count_interval(s['population'],n,yes,alpha)[0]
        high=exact_count_interval(s['population'],n,yes+unknown,alpha)[1]
        bounds[key]=(low,high)
        estimates[key]=s['population']*yes/n if n and not unknown else None
        unknowns[key]=s['population']*unknown/n if n else s['population']
    result={}
    for index,engine in enumerate(snapshot['evaluation_engines']):
        positive=[k for k in strata if k[index]=='1'];negative=[k for k in strata if k[index]=='0'];eligible=positive+negative
        predicted=sum(strata[k]['population'] for k in positive);N=sum(strata[k]['population'] for k in eligible)
        tp_lo=sum(bounds[k][0] for k in positive);tp_hi=sum(bounds[k][1] for k in positive)
        fn_lo=sum(bounds[k][0] for k in negative);fn_hi=sum(bounds[k][1] for k in negative)
        resolved=all(estimates[k] is not None for k in eligible)
        tp=sum(estimates[k] or 0 for k in positive);fn=sum(estimates[k] or 0 for k in negative)
        precision={'value':tp/predicted if all(estimates[k] is not None for k in positive) and predicted else None,'low':tp_lo/predicted if predicted else None,'high':tp_hi/predicted if predicted else None}
        recall={'value':tp/(tp+fn) if resolved and tp+fn else None,
                'low':tp_lo/(tp_lo+fn_hi) if tp_lo+fn_hi else None,
                'high':tp_hi/(tp_hi+fn_lo) if tp_hi+fn_lo else None}
        rate={'value':(tp+fn)/N if resolved and N else None,'low':(tp_lo+fn_lo)/N if N else None,'high':(tp_hi+fn_hi)/N if N else None}
        # No true positives in the evaluation sample cannot establish recall.
        if not any(r['verdict']=='target' and r['stratum'] in eligible for r in items):
            recall={'value':None,'low':None,'high':None}
        result[engine]={'model_sha256':snapshot['models'][engine],**snapshot['thresholds'][engine],
            'precision':precision,'recall':recall,'estimated_window_rate':rate,'eligible_windows':N,
            'reviewed':sum(r['stratum'] in eligible for r in items),'unknown':sum(r['verdict']=='unknown' and r['stratum'] in eligible for r in items),
            'unknown_rate':sum(unknowns[k] for k in eligible)/N if N else None,
            'goal_met':precision['low'] is not None and precision['low']>=snapshot['target_precision'],
            'status':'evaluated' if precision['value'] is not None or recall['value'] is not None else 'limited'}
    return result


def answer(root,sid,item_id,verdict,ranges=None,version=None,reviews=None):
    with connect(root) as db:
        row=db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(sid,)).fetchone()
        if not row:raise CatalogError('not_found')
        grouped_session=bool(json.loads(row['snapshot']).get('group_ids'))
    if grouped_session:
        from validation_groups import answer_group
        if not reviews:raise CatalogError('validation_group_answers_required')
        return answer_group(root,sid,reviews,ranges)
    with connect(root) as db:
        db.execute('BEGIN IMMEDIATE')
        _write_answer(root,db,sid,item_id,verdict,ranges,version)
    return view_session(root,sid)


def _write_answer(root,db,sid,item_id,verdict,ranges,version,allow_out_of_order=False):
    row=db.execute('SELECT * FROM validation_sessions WHERE id=?',(sid,)).fetchone()
    if not row:
        raise CatalogError('not_found')
    snap=json.loads(row['snapshot'])
    model_current, model_reason = custom_model_status(root, snap)
    if not model_current:
        raise CatalogError(model_reason)
    item=db.execute('SELECT * FROM validation_items WHERE id=? AND session_id=?',(item_id,sid)).fetchone()
    if not item:
        raise CatalogError('not_found')
    phase='evaluation' if snap['stage']=='complete' else snap['stage']
    if item['phase']!=phase:
        raise CatalogError('validation_phase_locked')
    if not annotation_source_current(db,snap):raise CatalogError('annotation_source_changed')
    rows=answered_items(db,sid,phase)
    current=next(r for r in rows if r['id']==item_id)
    if version is not None and (current['answer_id'] or 0)!=version:raise CatalogError('annotation_changed')
    pending = rows
    if phase == 'evaluation' and snap['method'] == audit.METHOD:
        pending = [r for r in rows if r['id'] in audit.active_ids(snap)]
    if not allow_out_of_order and current['verdict'] is None and item_id!=next((r['id'] for r in pending if r['verdict'] is None),None):
        raise CatalogError('validation_answer_order')
    recording=db.execute('SELECT * FROM recordings WHERE id=?',(item['recording_id'],)).fetchone()
    checked_source(recording)
    from annotations import clean_ranges
    ranges=clean_ranges(db,ranges,item['start_s'],item['start_s']+snap['window_s']) if ranges is not None else json.loads(current['ranges'] or 'null')
    if ranges and verdict!='target' and any(r['species_id']==row['species_id'] for r in ranges):
        raise CatalogError('annotation_conflict')
    from quiz import report_results
    quiz=report_results(db,[row['species_id']])
    if phase=='calibration' and snap.get('annotation_source') and ranges is not None:
        original=db.execute('SELECT id FROM annotation_items WHERE session_id=? AND recording_id=? AND start_s=?',
            (snap['annotation_source']['session_id'],item['recording_id'],item['start_s'])).fetchone()
        previous=db.execute('SELECT * FROM annotation_answers WHERE id=?',(snap['annotation_source']['answers'][str(original['id'])],)).fetchone()
        if json.loads(previous['ranges'])!=ranges:
            aid=db.execute('INSERT INTO annotation_answers(item_id,ranges,complete,quiz_snapshot,palette_ids) VALUES(?,?,?,?,?)',
                (original['id'],json.dumps(ranges),previous['complete'],json.dumps(quiz),previous['palette_ids'])).lastrowid
            snap['annotation_source']['answers'][str(original['id'])]=aid
            for peer_id in snap.get('group_ids', []):
                if peer_id == sid:continue
                peer=json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(peer_id,)).fetchone()[0])
                source=peer.get('annotation_source',{})
                if peer['stage']=='calibration' and source.get('answers',{}).get(str(original['id']))==previous['id']:
                    source['answers'][str(original['id'])]=aid
                    for value in db.execute('SELECT id FROM validation_items WHERE session_id=? AND recording_id=? AND start_s=?',
                                            (peer_id,item['recording_id'],item['start_s'])):
                        peer.setdefault('shared_ranges',{})[str(value['id'])]=ranges
                    db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',(json.dumps(peer,allow_nan=False),peer_id))
    db.execute('INSERT INTO validation_answers(item_id,verdict,quiz_snapshot,ranges) VALUES(?,?,?,?)',(item_id,verdict,json.dumps(quiz),json.dumps(ranges) if ranges is not None else None))
    if phase=='evaluation':update_results(snap,answered_items(db,sid,phase))
    db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',(json.dumps(snap,allow_nan=False),sid))


def finish_calibration(root,sid,_group=True):
    with connect(root) as db:
        row=db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(sid,)).fetchone()
        if not row:raise CatalogError('not_found')
        snap=json.loads(row['snapshot'])
        if _group and snap.get('group_ids'):
            from validation_groups import finish_group
            return finish_group(root,sid)
        model_current, model_reason = custom_model_status(root, snap)
        if not model_current:
            raise CatalogError(model_reason)
        if not annotation_source_current(db,snap):raise CatalogError('annotation_source_changed')
        if snap['stage']=='calibration':
            rows=answered_items(db,sid,'calibration')
            if not rows or any(r['verdict'] is None for r in rows):raise CatalogError('validation_calibration_incomplete')
            begin_evaluation(db,sid,snap,rows)
            db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',(json.dumps(snap,allow_nan=False),sid))
    return view_session(root,sid,_group=_group)


def start_recall(root,sid,_group=True):
    current = view_session(root, sid, include_items=False, _group=False)
    if not current['applicable']:
        raise CatalogError('workflow_changed')
    with connect(root) as db:
        row=db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(sid,)).fetchone()
        if not row:raise CatalogError('not_found')
        snap=json.loads(row['snapshot'])
        if _group and snap.get('group_ids'):
            from validation_groups import start_recall_group
            return start_recall_group(root,sid)
        if snap['method'] != audit.METHOD or snap['stage']=='calibration':
            raise CatalogError('validation_precision_incomplete')
        rows=answered_items(db,sid,'evaluation')
        done={r['id'] for r in rows if r['verdict'] is not None}
        if not audit.active_ids(snap,'precision').issubset(done):
            raise CatalogError('validation_precision_incomplete')
        snap.update(audit_step='recall',recall_started=True)
        audit.update(snap,rows)
        db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',(json.dumps(snap,allow_nan=False),sid))
    return view_session(root,sid,_group=_group)


def view_session(root,sid,ctx=None,include_items=True,item_id=None,_group=True):
    from results_workflow import context
    with connect(root) as db:
        row=db.execute('SELECT * FROM validation_sessions WHERE id=?',(sid,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        snap=json.loads(row['snapshot']);taxon=names_for(db.execute('SELECT * FROM species WHERE id=?',(row['species_id'],)).fetchone())
        if include_items and _group and snap.get('group_ids'):
            from validation_groups import view_group
            return view_group(root,sid,ctx=ctx,item_id=item_id)
        source_current=annotation_source_current(db,snap)
        items=answered_items(db,sid)
        counts={phase:{'total':sum(i['phase']==phase for i in items),'answered':sum(i['phase']==phase and i['verdict'] is not None for i in items)} for phase in ['calibration','evaluation']}
        fixed=snap['method']==audit.METHOD
        if fixed:
            for step, name in [('precision','evaluation'),('recall','recall')]:
                ids=audit.active_ids(snap,step) if 'evaluation_audits' in snap else set()
                counts[name]={'total':len(ids),'answered':sum(i['id'] in ids and i['verdict'] is not None for i in items)}
        if include_items:
            phase='evaluation' if snap['stage']=='complete' else snap['stage']
            visible=[i for i in items if i['phase']==phase]
            if fixed and phase=='evaluation':
                ids=audit.active_ids(snap)
                visible=[i for i in visible if i['id'] in ids or i['verdict'] is not None]
            next_item=next((i for i in visible if i['verdict'] is None),None)
            if item_id is not None:
                selected=next((i for i in visible if i['id']==item_id),None)
                if not selected or (selected['verdict'] is None and selected!=next_item):raise CatalogError('not_found')
                next_item=selected
            history=[{'id':i['id'],'position':i['position'],'verdict':i['verdict']} for i in visible if i['verdict'] is not None]
            question=None
            if next_item:
                rec=dict(db.execute('SELECT * FROM recordings WHERE id=?',(next_item['recording_id'],)).fetchone())
                question={'id':next_item['id'],'position':next_item['position'],'verdict':next_item['verdict'],'version':next_item['answer_id'] or 0,'source':{'id':rec['id'],'kind':'recording','path':rec['path'],'seconds':rec['seconds']},
                    'start_s':next_item['start_s'],'end_s':next_item['start_s']+snap['window_s'],
                    'ranges':json.loads(next_item['ranges']) if next_item['ranges'] else snap.get('shared_ranges',{}).get(str(next_item['id']),[])}
        else:
            question=None;history=[]
    ctx=ctx or context(root,row['dataset_id'],snap['start_date'],snap['end_date'])
    model_current, model_reason = custom_model_status(root, snap)
    applicable = (source_current and model_current and not ctx.get('location_stale')
                  and row['fingerprint']==ctx['fingerprint']
                  and (snap['start_date'],snap['end_date'])==(ctx['start_date'],ctx['end_date']))
    return {'id':sid,'dataset_id':row['dataset_id'],'species':taxon,'stage':snap['stage'],'counts':counts,'question':question,
        'kind':snap.get('kind','detection'),'run_id':snap.get('run_id'),'model_id':snap.get('model_id'),'history':history,
        'group_ids':snap.get('group_ids',[]),
        'annotation_session_id':snap.get('annotation_source',{}).get('session_id'), 'palette':snap.get('palette',[taxon]),
        'annotation_source_current':source_current,'split_unit':snap.get('split_unit'),
        'excluded_recording_ids':snap.get('excluded_recording_ids',[]),
        'evaluated_count':snap.get('evaluated_count',counts['evaluation']['answered'] if snap['stage']=='complete' else 0),
        'uncertainty':snap.get('uncertainty','simultaneous_exact_hypergeometric_95'),
        'models':snap['models'],'thresholds':snap['thresholds'],'results':snap['results'],
        'audit_step':snap.get('audit_step'),'recall_started':snap.get('recall_started',False),
        'can_start_recall':applicable and fixed and snap['stage']=='complete' and not snap.get('recall_started') and counts.get('recall',{}).get('answered',0)<counts.get('recall',{}).get('total',0),
        'calibration_target_precision':snap.get('calibration_target_precision',snap['target_precision']),
        'start_date':snap['start_date'],'end_date':snap['end_date'],'target_precision':snap['target_precision'],
        'window_s':snap['window_s'],'population':len(snap['population']),'evaluation_population':snap.get('evaluation_population'),
        'method':snap['method'],'evaluation_scope':snap['evaluation_scope'],'reference':snap['reference'],
        'applicable':applicable,'model_reason':model_reason,
        'created_at':row['created_at']}
