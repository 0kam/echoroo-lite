"""Exercise the result-to-validation-to-export workflow without downloading models."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from server import create_app
from storage import connect
from validation import exact_count_interval,evaluate,plan_checkpoints,checkpoint_alpha,suggest_thresholds
from results_workflow import threshold_for
from scipy.stats import hypergeom


class WorkflowCheck(unittest.TestCase):
    def test_overview_orders_by_detection_intervals_not_days_or_model_sum(self):
        from analysis import save
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);sf.write(root/'20240808_060000.wav',np.zeros(60000),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                with connect(root) as db:
                    record=dict(db.execute('SELECT * FROM recordings').fetchone())
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',2)").lastrowid
                labels=['Horornis diphone_Japanese Bush Warbler','Parus minor_Japanese Tit','Accipiter gentilis_Northern Goshawk']
                bird=np.repeat([[.2,.2,.1],[.099,.2,.099],[.099,.2,.099],[.099,.099,.099]],5,axis=0)
                save(root,job,record,'birdnet',{'model_sha256':'birdnet','species':labels},dict(
                    labels=np.tile(labels,20),scores=bird.ravel(),start_s=np.repeat(np.arange(20)*3,3),end_s=np.repeat(np.arange(1,21)*3,3)),[])
                probs=np.repeat([[.2,.2,.01],[.2,.2,.01],[.2,.2,.01],[.2,.05,.01]],3,axis=0)
                save(root,job,record,'perch',{'model_sha256':'perch','candidate_filter':{'species':labels}},dict(
                    labels=[s.partition('_')[0] for s in labels]+['Other'],logits=np.log(np.column_stack([probs,1-probs.sum(axis=1)]))),[])
                response=client.get('/datasets/1/overview')
                self.assertEqual(response.status_code,200,response.text)
                rows=response.json()['species']
                self.assertEqual([r['species']['scientific_name'] for r in rows],['Horornis diphone','Parus minor','Accipiter gentilis'])
                self.assertEqual([{m['engine']:m['candidate_windows'] for m in r['models']} for r in rows],
                                 [{'birdnet':1,'perch':4},{'birdnet':3,'perch':3},{'birdnet':1}])
                self.assertTrue(all(m['candidate_days']==1 for r in rows for m in r['models']))

    def test_analysis_prepares_shared_display_windows(self):
        from analysis import save
        from reports import cached_windows, classification_windows
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);sf.write(root/'20240808_060000.wav',np.zeros(15000),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                with connect(root) as db:
                    record=dict(db.execute('SELECT * FROM recordings').fetchone())
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'running',2)").lastrowid
                label='Horornis diphone_Japanese Bush Warbler'
                save(root,job,record,'birdnet',{'species':[label]},dict(labels=np.repeat(label,5),start_s=np.arange(5)*3,end_s=np.arange(1,6)*3,scores=np.array([.1,.8,.2,.3,.4])),[])
                with connect(root) as db:analysis=dict(db.execute('SELECT * FROM analyses').fetchone())
                cached_windows.cache_clear()
                with patch('reports.read_classification_windows',side_effect=AssertionError('display was not prepared')):
                    labels,starts,scores,evidence=classification_windows(analysis,15,[label])
                    self.assertEqual((starts.tolist(),scores.tolist(),evidence.tolist()),([0],[[.8]],[[3]]))

    def test_independent_validation_and_frozen_export(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            audio=np.zeros(450*1000,dtype=np.float32)
            for hour in ('060000','070000'):
                sf.write(root/f'20240808_{hour}.wav',audio,1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                response=client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                self.assertEqual(response.status_code,200,response.text)
                taxon=client.get('/species?q=ウグイス').json()[0]
                label='Horornis diphone_Japanese Bush Warbler'
                with connect(root) as db:
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',4)").lastrowid
                    for rid in (1,2):
                        native=np.repeat(np.tile([.02,.2,.8],10),5)
                        bird=root/f'bird-{rid}.npz'
                        np.savez(bird,labels=np.repeat(label,len(native)),scores=native,start_s=np.arange(150)*3,end_s=np.arange(1,151)*3)
                        probs=np.repeat(np.tile([.02,.2,.8],10),3)
                        perch=root/f'perch-{rid}.npz'
                        np.savez(perch,labels=['Horornis diphone','Other'],logits=np.log(np.column_stack([probs,1-probs])))
                        for engine,path in [('birdnet',bird),('perch',perch)]:
                            meta={'model_sha256':engine,'model':engine,'species':[label],'candidate_filter':{'species':[label]}}
                            db.execute('INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,?,?,?)',(job,rid,engine,json.dumps(meta),str(path)))
                with connect(root) as db:
                    db.execute("INSERT INTO datasets VALUES(2,1,'Second collection','/second')")
                    db.execute('UPDATE recordings SET dataset_id=2 WHERE id=2')
                # Both collections belong to one site, including validation and export.
                overview=client.get('/datasets/1/overview')
                self.assertEqual(overview.status_code,200,overview.text)
                self.assertEqual(overview.json()['species'][0]['models'][0]['candidate_windows'],40)
                self.assertEqual(overview.json()['records'],2)
                self.assertEqual(client.get('/datasets/2/overview').json()['fingerprint'],overview.json()['fingerprint'])
                from reports import cached_windows
                cached_windows.cache_clear()
                with patch('reports.read_classification_windows',side_effect=AssertionError('score archive reread')):
                    detail=client.get(f'/datasets/1/species/{taxon["id"]}?engine=auto')
                    self.assertEqual(detail.status_code,200,detail.text)
                    self.assertGreater(detail.json()['report']['evidence_count'],0)
                again=client.get('/datasets/1/overview').json()
                self.assertEqual(again['revision'],overview.json()['revision'])
                with connect(root) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM reports').fetchone()[0],0)
                body={'dataset_id':1,'species_id':taxon['id'],'start_date':'2024-08-08','end_date':'2024-08-08'}
                session=client.post('/validations',json=body)
                self.assertEqual(session.status_code,200,session.text)
                session=session.json();sid=session['id']
                self.assertEqual(client.post('/validations',json=body).json()['id'],sid)
                self.assertEqual(client.post('/validations',json={**body,'dataset_id':2}).json()['id'],sid)
                self.assertNotIn('scores',session['question'])
                self.assertLessEqual(session['counts']['calibration']['total'],30)
                self.assertEqual(client.post(f'/validations/{sid}/evaluation').json()['detail'],'validation_calibration_incomplete')
                with connect(root) as db:
                    snapshot=json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(sid,)).fetchone()[0])
                    self.assertEqual(snapshot['calibration_threshold_policy'],'max_recall_then_precision_then_highest_cutoff_v2')
                    future=db.execute('SELECT id FROM validation_items WHERE session_id=? AND position=1',(sid,)).fetchone()[0]
                self.assertEqual(client.get(f'/validations/{sid}?item_id={future}').status_code,400)
                self.assertEqual(client.post(f'/validations/{sid}/answers',json={'item_id':future,'verdict':'target'}).json()['detail'],'validation_answer_order')
                first_question=session['question']['id']
                phases=set()
                interim=None
                while session['stage']!='complete':
                    phases.add(session['stage']);question=session['question']
                    if not question:
                        # The final calibration answer remains correctable until an explicit transition.
                        self.assertEqual(session['stage'],'calibration')
                        last=session['history'][-1]
                        self.assertEqual(client.get(f'/validations/{sid}?item_id={last["id"]}').json()['question']['verdict'],last['verdict'])
                        client.post(f'/validations/{sid}/answers',json={'item_id':last['id'],'verdict':last['verdict']})
                        revision=client.get('/datasets/1/overview').json()['revision']
                        session=client.post(f'/validations/{sid}/evaluation').json()
                        self.assertNotEqual(client.get('/datasets/1/overview').json()['revision'],revision)
                        with connect(root) as db:
                            audit=json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(sid,)).fetchone()[0])['evaluation_audits']
                        self.assertTrue(all(len(a['positive_ids'])<=30 and len(a['negative_ids'])<=30 for a in audit.values()))
                        continue
                    target=int(question['start_s']/15)%3!=0
                    response=client.post(f'/validations/{sid}/answers',json={'item_id':question['id'],'verdict':'target' if target else 'other',
                        'examples_snapshot':[{'kind':'named','origin':'search-reference','id':'R1-1'}]})
                    self.assertEqual(response.status_code,200,response.text);session=response.json()
                    if session['stage']=='evaluation' and session['counts']['evaluation']['answered']==10:
                        self.assertEqual(session['evaluated_count'],0)
                        self.assertTrue(session['results']);interim=session['results']
                        for result in interim.values():
                            self.assertFalse(result['precision_complete'])
                            self.assertIsNone(result['precision_lower_95'])
                            self.assertFalse(result['goal_met'])
                        detail=client.get(f'/datasets/1/species/{taxon["id"]}').json()
                        self.assertEqual(detail['validation']['results'],interim)
                    if session['stage']=='evaluation' and session['counts']['evaluation']['answered']==11:
                        self.assertEqual(session['evaluated_count'],0)
                        before={key:value['precision_sample']['answered'] for key,value in interim.items()}
                        after={key:value['precision_sample']['answered'] for key,value in session['results'].items()}
                        self.assertTrue(all(after[key]>=count for key,count in before.items()))
                        self.assertTrue(any(after[key]>count for key,count in before.items()))
                self.assertEqual(phases,{'calibration','evaluation'})
                self.assertEqual(session['counts']['evaluation']['answered'], session['counts']['evaluation']['total'])
                self.assertEqual(session['counts']['recall']['answered'], 0)
                self.assertTrue(session['can_start_recall'])
                for result in session['results'].values():
                    self.assertEqual(result['precision'],dict(value=1,low=1,high=1))
                    self.assertEqual(result['recall'],dict(value=None,low=None,high=None))
                    self.assertTrue(result['precision_complete']);self.assertFalse(result['recall_complete'])
                    self.assertTrue(result['goal_met']);self.assertTrue(result['adjusted'])
                self.assertEqual(client.post(f'/validations/{sid}/answers',json={'item_id':first_question,'verdict':'other'}).json()['detail'],'validation_phase_locked')
                # Completed evaluation answers can be corrected, with history retained.
                last=session['history'][-1]
                corrected=client.post(f'/validations/{sid}/answers',json={'item_id':last['id'],'verdict':'other' if last['verdict']=='target' else 'target'}).json()
                self.assertEqual(corrected['stage'],'complete')
                self.assertNotEqual(corrected['results'],session['results'])
                restored=client.post(f'/validations/{sid}/answers',json={'item_id':last['id'],'verdict':last['verdict']}).json()
                self.assertEqual(restored['results'],session['results'])
                with connect(root) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM validation_answers WHERE item_id=?',(last['id'],)).fetchone()[0],3)
                    future=db.execute("SELECT id FROM validation_items WHERE session_id=? AND phase='calibration' ORDER BY position LIMIT 1",(sid,)).fetchone()[0]
                self.assertEqual(client.get(f'/validations/{sid}?item_id={future}').status_code,400)
                detail=client.get(f'/datasets/1/species/{taxon["id"]}').json()
                self.assertEqual(detail['validation']['stage'],'complete')
                self.assertEqual(detail['report']['threshold'],.2)
                with connect(root) as db:
                    groups={p:{(r['recording_id'],r['start_s']) for r in db.execute('SELECT * FROM validation_items WHERE phase=?',(p,))} for p in phases}
                    self.assertFalse(groups['calibration']&groups['evaluation'])
                export_body={**body,'revision':detail['revision'],'species_ids':[taxon['id']],'folder':str(root),'locale':'ja',
                    'figures':[{'species_id':taxon['id'],'engine':engine,'svg':'<svg xmlns="http://www.w3.org/2000/svg"/>'} for engine in ('birdnet','perch')]}
                response=client.post('/workflow-exports',json=export_body)
                self.assertEqual(response.status_code,200,response.text)
                with zipfile.ZipFile(response.json()['path']) as archive:
                    self.assertIn('quality.csv',archive.namelist());self.assertIn(f'validation/{sid}.json',archive.namelist())
                    saved_validation=json.loads(archive.read(f'validation/{sid}.json'))
                    self.assertEqual(json.loads(saved_validation['items'][0]['examples_snapshot']),[{'kind':'named','origin':'search-reference','id':'R1-1'}])
                    self.assertIn('examples_snapshot',saved_validation['answer_history'][0])
                    manifest=json.loads(archive.read('manifest.json'));self.assertEqual(len(manifest['results']),2)
                    self.assertTrue(any(n.endswith('.wav') for n in archive.namelist()))
                saved_bytes=Path(response.json()['path']).read_bytes()
                client.post(f'/validations/{sid}/answers',json={'item_id':last['id'],'verdict':'unknown'})
                self.assertEqual(Path(response.json()['path']).read_bytes(),saved_bytes)
                history=client.get('/datasets/1/exports').json()
                self.assertEqual(len(history[0]['results']),2)
                self.assertNotIn('snapshot',history[0])
                with connect(root) as db:
                    old=db.execute('SELECT * FROM analyses WHERE engine="birdnet" LIMIT 1').fetchone()
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',1)").lastrowid
                    db.execute('INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,?,?,?)',(job,old['recording_id'],'birdnet',old['metadata'],old['scores_path']))
                stale=client.get(f'/validations/{sid}').json()
                self.assertFalse(stale['applicable']);self.assertFalse(stale['can_start_recall'])
                self.assertEqual(client.post(f'/validations/{sid}/recall').json()['detail'],'workflow_changed')
                self.assertEqual(client.post('/workflow-exports',json=export_body).json()['detail'],'workflow_changed')

    def test_finite_bounds_and_unknowns(self):
        from scipy.stats import hypergeom
        for N,n in [(5,2),(10,1),(10,6),(1000,60),(70080,60),(10000000,60)]:
            for alpha in (.05,.0125,.005):
                for k in sorted({0,1,n//2,n-1,n}):
                    low,high=exact_count_interval(N,n,k,alpha)
                    self.assertGreaterEqual(hypergeom.sf(k-1,N,low,n),alpha/2)
                    self.assertGreaterEqual(hypergeom.cdf(k,N,high,n),alpha/2)
                    if low>k:
                        self.assertLess(hypergeom.sf(k-1,N,low-1,n),alpha/2)
                    if high<N-n+k:
                        self.assertLess(hypergeom.cdf(k,N,high+1,n),alpha/2)
        for N in (8,15):
            for n in range(1,N+1):
                limits=[exact_count_interval(N,n,k,.05) for k in range(n+1)]
                for K in range(N+1):
                    coverage=sum(hypergeom.pmf(k,N,K,n) for k,(lo,hi) in enumerate(limits) if lo<=K<=hi)
                    self.assertGreaterEqual(coverage, .95-1e-12)
        self.assertEqual(threshold_for({'applicable':True,'stage':'complete','results':{'birdnet':{'model_sha256':'x','threshold':.4,'goal_met':False}}},{'engine':'birdnet','model_sha256':'x'}),.4)
        self.assertEqual(exact_count_interval(10,10,0,.05),(0,0))
        self.assertEqual(exact_count_interval(10,10,10,.05),(10,10))
        lo,hi=exact_count_interval(10000,60,60,.05)
        self.assertGreater(lo/10000,.9);self.assertEqual(hi,10000)
        snapshot={'evaluation_strata':{'1':{'population':10},'0':{'population':10}},'evaluation_engines':['birdnet'],
            'models':{'birdnet':'test'},'thresholds':{'birdnet':{'threshold':.5,'adjusted':True}},'target_precision':.9}
        items=[{'stratum':'1','verdict':'target'} for _ in range(9)]+[{'stratum':'1','verdict':'unknown'}]+[{'stratum':'0','verdict':'other'} for _ in range(10)]
        result=evaluate(snapshot,items)['birdnet']
        self.assertIsNone(result['precision']['value']);self.assertEqual(result['precision']['low'],.9)
        self.assertEqual(result['precision']['high'],1);self.assertEqual(result['unknown'],1)
        result=evaluate(snapshot,[{**i,'verdict':'other'} for i in items])['birdnet']
        self.assertIsNone(result['recall']['value']);self.assertFalse(result['goal_met'])

    def test_threshold_ties_prefer_highest_cutoff(self):
        snapshot={'models':{'birdnet':'test','search':'test'},'target_precision':.9,
            'calibration_strata':{'0':{'population':4,'sample':4}}}
        items=[{'scores':json.dumps({'birdnet':score,'search':score}),'stratum':'0','verdict':verdict}
            for score,verdict in [(0.9,'target')]*3+[(0.1,'other')]]
        thresholds=suggest_thresholds(snapshot,items)
        self.assertEqual(thresholds['birdnet'],{'threshold':.9,'adjusted':True})
        self.assertEqual(thresholds['search'],{'threshold':.9,'adjusted':True})

    def test_checkpoint_intervals_cover_all_planned_looks(self):
        # Exact path probabilities, including stopping at the first failed interval.
        snapshot={'evaluation_strata':{'1':{'sample':30,'population':40}}}
        plan_checkpoints(snapshot);checkpoints=snapshot['evaluation_checkpoints']
        self.assertEqual(checkpoints,[10,20,30])
        self.assertAlmostEqual(sum(checkpoint_alpha(checkpoints,n) for n in checkpoints),.05)
        N=40
        for K in range(N+1):
            alive={0:1.}
            for n in range(1,31):
                next_alive={}
                for k,p in alive.items():
                    for label,left in [(1,K-k),(0,N-K-(n-1-k))]:
                        if left<=0:continue
                        successes=k+label
                        if n in checkpoints:
                            lo,hi=exact_count_interval(N,n,successes,checkpoint_alpha(checkpoints,n))
                            if not lo<=K<=hi:continue
                        next_alive[successes]=next_alive.get(successes,0)+p*left/(N-n+1)
                alive=next_alive
            self.assertGreaterEqual(sum(alive.values()),.95-1e-12)
        # Four strata and repeated looks still permit the target at the final cap.
        self.assertGreater(exact_count_interval(10000,60,60,.025/4)[0]/10000,.9)
        self.assertEqual(checkpoint_alpha([7],7),.05)


if __name__=='__main__':unittest.main()
