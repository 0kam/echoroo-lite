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
from validation import exact_count_interval,evaluate
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
                save(root,job,record,'perch',{'model_sha256':'perch','geo_filter':{'species':labels}},dict(
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
                            meta={'model_sha256':engine,'model':engine,'species':[label],'geo_filter':{'species':[label]}}
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
                self.assertNotIn('scores',session['question'])
                first_question=session['question']['id']
                phases=set()
                while session['stage']!='complete':
                    phases.add(session['stage']);question=session['question']
                    target=int(question['start_s']/15)%3!=0
                    response=client.post(f'/validations/{sid}/answers',json={'item_id':question['id'],'verdict':'target' if target else 'other'})
                    self.assertEqual(response.status_code,200,response.text);session=response.json()
                self.assertEqual(phases,{'calibration','evaluation'})
                for result in session['results'].values():
                    self.assertEqual(result['precision'],dict(value=1,low=1,high=1))
                    self.assertEqual(result['recall'],dict(value=1,low=1,high=1))
                    self.assertTrue(result['goal_met']);self.assertTrue(result['adjusted'])
                self.assertEqual(client.post(f'/validations/{sid}/answers',json={'item_id':first_question,'verdict':'other'}).json()['detail'],'validation_phase_locked')
                detail=client.get(f'/datasets/1/species/{taxon["id"]}').json()
                self.assertEqual(detail['validation']['stage'],'complete')
                self.assertLess(detail['report']['threshold'],.1)
                with connect(root) as db:
                    groups={p:{(r['recording_id'],r['start_s']) for r in db.execute('SELECT * FROM validation_items WHERE phase=?',(p,))} for p in phases}
                    self.assertFalse(groups['calibration']&groups['evaluation'])
                export_body={**body,'revision':detail['revision'],'species_ids':[taxon['id']],'folder':str(root),'locale':'ja',
                    'figures':[{'species_id':taxon['id'],'engine':engine,'svg':'<svg xmlns="http://www.w3.org/2000/svg"/>'} for engine in ('birdnet','perch')]}
                response=client.post('/workflow-exports',json=export_body)
                self.assertEqual(response.status_code,200,response.text)
                with zipfile.ZipFile(response.json()['path']) as archive:
                    self.assertIn('quality.csv',archive.namelist());self.assertIn(f'validation/{sid}.json',archive.namelist())
                    manifest=json.loads(archive.read('manifest.json'));self.assertEqual(len(manifest['results']),2)
                    self.assertTrue(any(n.endswith('.wav') for n in archive.namelist()))
                history=client.get('/datasets/1/exports').json()
                self.assertEqual(len(history[0]['results']),2)
                self.assertNotIn('snapshot',history[0])
                with connect(root) as db:
                    old=db.execute('SELECT * FROM analyses WHERE engine="birdnet" LIMIT 1').fetchone()
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',1)").lastrowid
                    db.execute('INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,?,?,?)',(job,old['recording_id'],'birdnet',old['metadata'],old['scores_path']))
                self.assertFalse(client.get(f'/validations/{sid}').json()['applicable'])
                self.assertEqual(client.post('/workflow-exports',json=export_body).json()['detail'],'workflow_changed')

    def test_finite_bounds_and_unknowns(self):
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


if __name__=='__main__':unittest.main()
