"""Versioned human validation of complete 15-second windows.

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

from reports import analysis_metadata, classification_batches, window_key
from species import names_for
from storage import CatalogError, checked_source, connect

CALIBRATION_PER_BAND = 20
EVALUATION_PER_STRATUM = 60
TARGET_PRECISION = .90
METHOD = 'stratified_finite_window_validation_v1'


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
                if not ctx['start_date']<=key[0]<=ctx['end_date']:
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
        for item in rng.sample(items,count):
            chosen.append({**item,'stratum':key})
    rng.shuffle(chosen)
    for i,item in enumerate(chosen):
        db.execute('INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,stratum,scores) VALUES(?,?,?,?,?,?,?)',
            (session_id,phase,i,item['recording_id'],item['start_s'],item['stratum'],json.dumps(item['scores'])))
    return strata


def start_session(root,body):
    from results_workflow import context
    ctx=context(root,body.dataset_id,body.start_date.isoformat(),body.end_date.isoformat())
    if ctx.get('location_stale'):
        raise CatalogError('location_reanalysis')
    with connect(root) as db:
        existing=db.execute('SELECT id,snapshot FROM validation_sessions WHERE dataset_id=? AND species_id=? AND fingerprint=? ORDER BY id DESC',
            (body.dataset_id,body.species_id,ctx['fingerprint'])).fetchall()
        for row in existing:
            snap=json.loads(row['snapshot'])
            if (snap['start_date'],snap['end_date'])==(ctx['start_date'],ctx['end_date']):
                return view_session(root,row['id'],ctx=ctx)
    items,models,analyses=population(root,ctx,body.species_id)
    seed=secrets.randbits(63);rng=random.Random(seed)
    groups=grouped(items,lambda i:str(int(max(i['scores'].values())>=.1)+int(max(i['scores'].values())>=.5)))
    snapshot={'method':METHOD,'stage':'calibration','seed':str(seed),'models':models,'analysis_ids':analyses,
        'start_date':ctx['start_date'],'end_date':ctx['end_date'],'site':ctx['site'],'population':items,
        'target_precision':TARGET_PRECISION,'window_s':15,'thresholds':{},'results':{},'reference':'user_annotations',
        'evaluation_scope':'complete_saved_windows_excluding_calibration_intervals','uncertainty':'simultaneous_exact_hypergeometric_95',
        'sampling_unit':'non_overlapping_15_second_interval','calibration_per_band':CALIBRATION_PER_BAND,
        'evaluation_per_stratum':EVALUATION_PER_STRATUM}
    with connect(root) as db:
        sid=db.execute('INSERT INTO validation_sessions(dataset_id,species_id,fingerprint,snapshot) VALUES(?,?,?,?)',
            (body.dataset_id,body.species_id,ctx['fingerprint'],'{}')).lastrowid
        snapshot['calibration_strata']=add_items(db,sid,'calibration',groups,rng,CALIBRATION_PER_BAND)
        if not sum(s['sample'] for s in snapshot['calibration_strata'].values()):
            raise CatalogError('validation_too_few')
        db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',(json.dumps(snapshot),sid))
    return view_session(root,sid,ctx=ctx)


def answered_items(db,sid,phase=None):
    return [dict(r) for r in db.execute('''SELECT i.*,a.verdict,a.id AS answer_id,a.created_at AS answered_at FROM validation_items i
        LEFT JOIN validation_answers a ON a.id=(SELECT MAX(id) FROM validation_answers WHERE item_id=i.id)
        WHERE i.session_id=? AND (? IS NULL OR i.phase=?) ORDER BY i.phase,i.position''',(sid,phase,phase))]


def suggest_thresholds(snapshot,items):
    result={}
    for engine in snapshot['models']:
        best=None
        for cutoff in np.arange(.01,1.001,.01):
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
            if precision>=snapshot['target_precision'] and positives>=3:
                candidate=(recall,precision,-float(cutoff))
                if best is None or candidate>best:
                    best=candidate
        result[engine]={'threshold':round(-best[2],2) if best else .1,'adjusted':best is not None}
    return result


def begin_evaluation(db,sid,snapshot,calibration):
    snapshot['thresholds']=suggest_thresholds(snapshot,calibration)
    used={(r['recording_id'],r['start_s']) for r in calibration}
    remaining=[r for r in snapshot['population'] if (r['recording_id'],r['start_s']) not in used]
    engines=sorted(snapshot['models'])
    def stratum(row):
        return ''.join('x' if engine not in row['scores'] else '1' if row['scores'][engine]>=snapshot['thresholds'][engine]['threshold'] else '0' for engine in engines)
    snapshot['evaluation_engines']=engines
    snapshot['evaluation_strata']=add_items(db,sid,'evaluation',grouped(remaining,stratum),random.Random(int(snapshot['seed'])+1),EVALUATION_PER_STRATUM)
    snapshot['stage']='evaluation'
    snapshot['evaluation_population']=len(remaining)
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


def evaluate(snapshot,items):
    strata=snapshot['evaluation_strata'];bounds={};estimates={};unknowns={}
    alpha=.05/max(1,len(strata))
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
        precision={'value':tp/predicted if resolved and predicted else None,'low':tp_lo/predicted if predicted else None,'high':tp_hi/predicted if predicted else None}
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


def answer(root,sid,item_id,verdict):
    with connect(root) as db:
        row=db.execute('SELECT * FROM validation_sessions WHERE id=?',(sid,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        snap=json.loads(row['snapshot'])
        item=db.execute('SELECT * FROM validation_items WHERE id=? AND session_id=?',(item_id,sid)).fetchone()
        if not item:
            raise CatalogError('not_found')
        if item['phase']!=snap['stage']:
            raise CatalogError('validation_phase_locked')
        recording=db.execute('SELECT * FROM recordings WHERE id=?',(item['recording_id'],)).fetchone()
        checked_source(recording)
        db.execute('INSERT INTO validation_answers(item_id,verdict) VALUES(?,?)',(item_id,verdict))
        rows=answered_items(db,sid,snap['stage'])
        if all(r['verdict'] is not None for r in rows):
            if snap['stage']=='calibration':
                begin_evaluation(db,sid,snap,rows)
            else:
                snap['results']=evaluate(snap,rows);snap['stage']='complete'
            db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',(json.dumps(snap,allow_nan=False),sid))
    return view_session(root,sid)


def view_session(root,sid,ctx=None,include_items=True):
    from results_workflow import context
    with connect(root) as db:
        row=db.execute('SELECT * FROM validation_sessions WHERE id=?',(sid,)).fetchone()
        if not row:
            raise CatalogError('not_found')
        snap=json.loads(row['snapshot']);taxon=names_for(db.execute('SELECT * FROM species WHERE id=?',(row['species_id'],)).fetchone())
        items=answered_items(db,sid)
        counts={phase:{'total':sum(i['phase']==phase for i in items),'answered':sum(i['phase']==phase and i['verdict'] is not None for i in items)} for phase in ['calibration','evaluation']}
        if include_items:
            visible=[i for i in items if i['phase']==snap['stage']]
            next_item=next((i for i in visible if i['verdict'] is None),None)
            question=None
            if next_item:
                rec=dict(db.execute('SELECT * FROM recordings WHERE id=?',(next_item['recording_id'],)).fetchone())
                question={'id':next_item['id'],'position':next_item['position'],'source':{'id':rec['id'],'kind':'recording','path':rec['path'],'seconds':rec['seconds']},
                    'start_s':next_item['start_s'],'end_s':next_item['start_s']+15}
        else:
            question=None
    ctx=ctx or context(root,row['dataset_id'],snap['start_date'],snap['end_date'])
    return {'id':sid,'dataset_id':row['dataset_id'],'species':taxon,'stage':snap['stage'],'counts':counts,'question':question,
        'models':snap['models'],'thresholds':snap['thresholds'] if snap['stage']=='complete' else {},'results':snap['results'],
        'start_date':snap['start_date'],'end_date':snap['end_date'],'target_precision':snap['target_precision'],
        'window_s':15,'population':len(snap['population']),'evaluation_population':snap.get('evaluation_population'),
        'method':snap['method'],'evaluation_scope':snap['evaluation_scope'],'reference':snap['reference'],
        'applicable':not ctx.get('location_stale') and row['fingerprint']==ctx['fingerprint'] and (snap['start_date'],snap['end_date'])==(ctx['start_date'],ctx['end_date']),
        'created_at':row['created_at']}
