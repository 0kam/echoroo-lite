"""Exercise native windows and the one-stage validation workflow."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from scipy.special import expit

from server import create_app
from storage import connect


class WorkflowCheck(unittest.TestCase):
    def test_overview_orders_by_native_detection_intervals(self):
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
                rows=client.get('/datasets/1/overview').json()['species']
                self.assertEqual([r['species']['scientific_name'] for r in rows],['Parus minor','Horornis diphone','Accipiter gentilis'])
                self.assertEqual([{m['engine']:m['candidate_windows'] for m in r['models']} for r in rows],
                                 [{'birdnet':15,'perch':9},{'birdnet':5,'perch':12},{'birdnet':5}])

    def test_analysis_prepares_native_windows(self):
        from analysis import save
        from reports import cached_windows, classification_windows
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);sf.write(root/'20240808_060000.wav',np.zeros(15000),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                with connect(root) as db:
                    record=dict(db.execute('SELECT * FROM recordings').fetchone())
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'running',1)").lastrowid
                label='Horornis diphone_Japanese Bush Warbler'
                save(root,job,record,'birdnet',{'species':[label]},dict(labels=np.repeat(label,5),start_s=np.arange(5)*3,
                     end_s=np.arange(1,6)*3,scores=np.array([.1,.8,.2,.3,.4])),[])
                with connect(root) as db:analysis=dict(db.execute('SELECT * FROM analyses').fetchone())
                cached_windows.cache_clear()
                with patch('reports.read_classification_windows',side_effect=AssertionError('cache was not prepared')):
                    _,starts,scores,evidence=classification_windows(analysis,15,[label])
                self.assertEqual((starts.tolist(),scores.ravel().tolist(),evidence.ravel().tolist()),
                                 ([0,3,6,9,12],[.1,.8,.2,.3,.4],[0,3,6,9,12]))

    def test_one_stage_check_hides_interim_results_and_adds_presence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);sf.write(root/'20240808_060000.wav',np.zeros(300000),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                sid=client.get('/species?q=ウグイス').json()[0]['id'];label='Horornis diphone_Japanese Bush Warbler'
                scores=expit((np.arange(100)-70)/8)
                path=root/'bird.npz';np.savez(path,labels=np.repeat(label,100),scores=scores,start_s=np.arange(100)*3,end_s=np.arange(1,101)*3)
                with connect(root) as db:
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',1)").lastrowid
                    db.execute("INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,1,'birdnet',?,?)",
                               (job,json.dumps({'model_sha256':'birdnet','species':[label]}),str(path)))
                scope={'dataset_id':1,'species_id':sid,'start_date':'2024-08-08','end_date':'2024-08-08','model':'birdnet'}
                check=client.post('/validations',json=scope).json()
                self.assertEqual((check['status'],check['counts']['total'],check['window_s']),('in_progress',43,3))
                self.assertEqual(check['results'],{})
                with connect(root) as db: second=db.execute('SELECT id FROM validation_items WHERE session_id=? ORDER BY position LIMIT 1 OFFSET 1',(check['id'],)).fetchone()[0]
                self.assertEqual(client.post(f"/validations/{check['id']}/answers",json={'item_id':second,'verdict':'target'}).json()['detail'],'validation_answer_order')
                with patch('validation.extension_needed',return_value=True):
                    while check['question']:
                        q=check['question']; verdict='target' if q['start_s']>=210 else 'other'
                        check=client.post(f"/validations/{check['id']}/answers",json={'item_id':q['id'],'verdict':verdict,'version':q['version']}).json()
                        if check['question']: self.assertEqual(check['results'],{})
                self.assertIn(check['status'],('decided','undecided'))
                self.assertEqual(sum(b['answered'] for b in check['counts']['bins']),43)
                self.assertTrue(check['can_extend'])
                check=client.post(f"/validations/{check['id']}/extend").json()
                self.assertEqual((check['status'],check['results']),('in_progress',{}))
                self.assertGreater(check['counts']['total'],43)
                self.assertEqual(client.post(f"/validations/{check['id']}/extend").json()['detail'],'validation_extension_unavailable')
                while check['question']:
                    q=check['question']; verdict='target' if q['start_s']>=210 else 'other'
                    check=client.post(f"/validations/{check['id']}/answers",json={'item_id':q['id'],'verdict':verdict}).json()
                self.assertTrue(check['extended']);self.assertFalse(check['can_extend'])
                revised=client.get(f"/validations/{check['id']}",params={'item_id':check['history'][0]['id']}).json()
                q=revised['question'];revised=client.post(f"/validations/{check['id']}/answers",json={
                    'item_id':q['id'],'verdict':'unknown','version':q['version']}).json()
                self.assertEqual(revised['counts']['unknown'],1)
                self.assertGreater(len(client.get(f'/datasets/1/species/{sid}').json()['presence']['evidence']),0)
                self.assertEqual(client.post(f"/validations/{check['id']}/evaluation").status_code,404)
                self.assertEqual(client.post(f"/validations/{check['id']}/recall").status_code,404)


if __name__ == '__main__':
    unittest.main()
