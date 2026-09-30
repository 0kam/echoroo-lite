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
from scipy.special import expit


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

    def test_per_model_checks_share_answers_and_feed_presence_and_report(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            for day in ('08','09','10','11'):
                sf.write(root/f'202408{day}_060000.wav',np.zeros(450*1000,dtype=np.float32),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                response=client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                self.assertEqual(response.status_code,200,response.text)
                taxon=client.get('/species?q=ウグイス').json()[0];sid=taxon['id']
                label='Horornis diphone_Japanese Bush Warbler'
                window=np.arange(30)
                with connect(root) as db:
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',8)").lastrowid
                    for rid in (1,2,3,4):
                        bird=root/f'bird-{rid}.npz';native=np.repeat(expit((window-15)/2),5)
                        np.savez(bird,labels=np.repeat(label,150),scores=native,start_s=np.arange(150)*3,end_s=np.arange(1,151)*3)
                        perch=root/f'perch-{rid}.npz';probs=np.repeat(expit((window-14)/3),3)
                        np.savez(perch,labels=['Horornis diphone','Other'],logits=np.log(np.column_stack([probs,1-probs])))
                        for engine,path in [('birdnet',bird),('perch',perch)]:
                            meta={'model_sha256':engine,'model':engine,'species':[label],'candidate_filter':{'species':[label]}}
                            db.execute('INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,?,?,?)',(job,rid,engine,json.dumps(meta),str(path)))
                heard=lambda question:'target' if question['start_s']>=225 else 'other'
                scope={'dataset_id':1,'species_id':sid,'start_date':'2024-08-08','end_date':'2024-08-11'}
                detail=client.get(f'/datasets/1/species/{sid}',params=scope).json()
                self.assertEqual([(c['key'],c['validation']) for c in detail['checks']],[('birdnet',None),('perch',None)])
                self.assertEqual(detail['presence']['status'],'unchecked')
                self.assertEqual(detail['report']['threshold'],.1)
                def run(session,phase='calibration',until=None):
                    while session['question'] and session['question']['phase']==phase and (until is None or session['counts'][until]['answered']<session['counts'][until]['total']):
                        response=client.post(f"/validations/{session['id']}/answers",json={'item_id':session['question']['id'],'verdict':heard(session['question']),'version':session['question']['version']})
                        self.assertEqual(response.status_code,200,response.text);session=response.json()
                    return session
                bird=client.post('/validations',json={**scope,'model':'birdnet'}).json()
                self.assertEqual(client.post('/validations',json={**scope,'model':'birdnet'}).json()['id'],bird['id'])
                self.assertEqual(client.post('/validations',json={**scope,'model':'custom'}).json()['detail'],'invalid_input')
                self.assertEqual((bird['stage'],bird['counts']['calibration']['total'],bird['status']),('calibration',20,'calibrating'))
                with connect(root) as db:
                    snap=json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(bird['id'],)).fetchone()[0])
                    first=[tuple(r) for r in db.execute('SELECT recording_id,start_s FROM validation_items WHERE session_id=?',(bird['id'],))]
                    later=db.execute('SELECT id FROM validation_items WHERE session_id=? AND position=1',(bird['id'],)).fetchone()[0]
                calibration_days=set(snap['split']['calibration_recording_ids'])
                self.assertEqual((snap['split']['unit'],len(calibration_days)),('local_day',2))
                self.assertTrue(all(r in calibration_days for r,_ in first))
                self.assertEqual(client.post(f"/validations/{bird['id']}/answers",json={'item_id':later,'verdict':'target'}).json()['detail'],'validation_answer_order')
                self.assertEqual(client.post(f"/validations/{bird['id']}/evaluation").json()['detail'],'validation_calibration_incomplete')
                bird=run(bird)
                self.assertIsNone(bird['question'])
                bird=client.post(f"/validations/{bird['id']}/evaluation").json()
                self.assertEqual(bird['stage'],'evaluation')
                threshold=bird['thresholds']['birdnet']['threshold']
                self.assertTrue(.4<threshold<1,threshold)
                self.assertEqual(bird['calibration']['curve']['answers'],20)
                with connect(root) as db:
                    evaluation={tuple(r) for r in db.execute("SELECT recording_id,start_s FROM validation_items WHERE session_id=? AND phase='evaluation'",(bird['id'],))}
                self.assertTrue(evaluation and all(r not in calibration_days for r,_ in evaluation))
                self.assertEqual(client.post(f"/validations/{bird['id']}/answers",json={'item_id':later,'verdict':'other'}).json()['detail'],'validation_phase_locked')
                # Perch's ① reuses every BirdNET answer about the same 15-second window.
                perch=client.post('/validations',json={**scope,'model':'perch'}).json()
                with connect(root) as db:
                    shared=len(set(first)&{tuple(r) for r in db.execute('SELECT recording_id,start_s FROM validation_items WHERE session_id=?',(perch['id'],))})
                self.assertEqual(perch['counts']['calibration']['answered'],shared)
                bird=run(bird,'evaluation')
                self.assertEqual((bird['stage'],bird['status']),('complete','goal_met'))
                self.assertTrue(bird['can_start_recall'])
                perch=client.post(f"/validations/{run(perch)['id']}/evaluation").json()
                with connect(root) as db:
                    bird_evaluation={tuple(r) for r in db.execute("SELECT recording_id,start_s FROM validation_items WHERE session_id=? AND phase='evaluation'",(bird['id'],))}
                    perch_positive=json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(perch['id'],)).fetchone()[0])['evaluation_audits']['perch']['positive_ids']
                    windows={r['id']:(r['recording_id'],r['start_s']) for r in db.execute('SELECT * FROM validation_items WHERE session_id=?',(perch['id'],))}
                reused=sum(windows[i] in bird_evaluation for i in perch_positive)
                self.assertGreater(reused,0)
                self.assertEqual(perch['counts']['evaluation']['answered'],reused)
                # Presence: heard answers from ②③ count; ① answers do not.
                detail=client.get(f'/datasets/1/species/{sid}',params=scope).json()
                evidence=detail['presence']['evidence']
                self.assertEqual(detail['presence']['status'],'confirmed')
                self.assertTrue(evidence and all(e['origin']=='check' and e['recording_id'] not in calibration_days for e in evidence))
                self.assertEqual(len(evidence),sum(start>=225 for _,start in bird_evaluation))
                self.assertEqual(detail['report']['threshold'],threshold)
                checks={c['key']:c['validation']['status'] for c in detail['checks']}
                self.assertEqual(checks,{'birdnet':'goal_met','perch':'checking'})
                overview=client.get('/datasets/1/overview',params={'start_date':'2024-08-08','end_date':'2024-08-11'}).json()
                row=next(r for r in overview['species'] if r['species']['id']==sid)
                self.assertEqual(len(row['presence']['evidence']),len(evidence))
                self.assertEqual({m['key']:m['threshold'] for m in row['models']}['birdnet'],threshold)
                # The report states presence and which models give reliable time charts.
                export=client.post('/workflow-exports',json={**scope,'revision':overview['revision'],'species_ids':[sid],'folder':str(root),'locale':'ja'})
                self.assertEqual(export.status_code,200,export.text)
                with zipfile.ZipFile(export.json()['path']) as archive:
                    html=archive.read('index.html').decode()
                    self.assertIn(f'聞いて確認した（根拠の音 {len(evidence)}）',html)
                    self.assertIn('確かな図あり（BirdNET）',html)
                    self.assertIn('確認中・まだ判定していません',html)
                    presence=archive.read('presence.csv').decode('utf-8-sig')
                    self.assertIn('confirmed',presence)
                    self.assertIn(f"validation/{bird['id']}.json",archive.namelist())
                    self.assertTrue(any(n.endswith('.wav') for n in archive.namelist()))
                # Deleting BirdNET's check keeps the answers Perch still uses.
                with connect(root) as db:
                    before=db.execute('SELECT COUNT(*) FROM window_answers').fetchone()[0]
                    perch_windows={tuple(r) for r in db.execute('SELECT recording_id,start_s FROM validation_items WHERE session_id=?',(perch['id'],))}
                deleted=client.post(f"/validations/{bird['id']}/delete")
                self.assertEqual(deleted.json(),{'deleted':1})
                self.assertEqual(client.get(f"/validations/{bird['id']}").json()['detail'],'not_found')
                with connect(root) as db:
                    kept={tuple(r) for r in db.execute('SELECT recording_id,start_s FROM window_answers')}
                    self.assertEqual(kept,{w for w in perch_windows if w in kept})
                    self.assertTrue(kept and len(kept)<before)
                self.assertEqual(client.get(f'/validations/{perch["id"]}').json()['counts']['evaluation']['answered'],reused)
                self.assertEqual(client.get(f'/datasets/1/species/{sid}',params=scope).json()['report']['threshold'],.1)
                # When ① hears nothing, no threshold is set and nothing is counted: no default is used.
                bird=client.post('/validations',json={**scope,'model':'birdnet'}).json()
                with connect(root) as db:
                    items=[r[0] for r in db.execute('SELECT id FROM validation_items WHERE session_id=? ORDER BY position',(bird['id'],))]
                for item in items:
                    self.assertEqual(client.post(f"/validations/{bird['id']}/answers",json={'item_id':item,'verdict':'other'}).status_code,200)
                bird=client.post(f"/validations/{bird['id']}/evaluation").json()
                self.assertEqual((bird['stage'],bird['status'],bird['thresholds']['birdnet']),('complete','undecided',{'threshold':None,'adjusted':False,'reason':'no_target'}))
                report=client.get(f'/datasets/1/species/{sid}',params={**scope,'engine':'birdnet'}).json()['report']
                self.assertIsNone(report['threshold'])
                self.assertIsNone(report['summaries'][0]['candidate_windows'])
                self.assertEqual(report['evidence'],[])


if __name__=='__main__':unittest.main()
