"""Exercise native windows and the one-stage validation workflow."""
import json
import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient
from scipy.special import expit

from server import create_app
from storage import connect


class WorkflowCheck(unittest.TestCase):
    def test_species_counting_status_needs_one_decided_ai(self):
        from results_workflow import counting_status
        validation=lambda state: {'applicable':True,'status':state}
        self.assertEqual(counting_status([validation('undecided'),validation('decided')]),'decided')
        self.assertEqual(counting_status([validation('undecided'),None]),'undecided')
        self.assertEqual(counting_status([validation('undecided'),validation('in_progress')]),'in_progress')
        self.assertEqual(counting_status([{'applicable':False,'status':'decided'},None]),'not_started')

    def test_overview_orders_by_native_detection_intervals(self):
        from analysis import save
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'models.json').write_text(json.dumps({'main':'perch','extra':['birdnet'],'confirmed':True}))
            sf.write(root/'20240808_060000.wav',np.zeros(60000),1000)
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
                perch_labels=['Horornis diphone','Parus cinereus','Astur gentilis']
                save(root,job,record,'perch',{'model_sha256':'perch','candidate_filter':{'species':perch_labels}},dict(
                    labels=perch_labels+['Other'],logits=np.log(np.column_stack([probs,1-probs.sum(axis=1)]))),[])
                first=client.get('/datasets/1/overview').json()
                rows=first['species']
                self.assertEqual([r['species']['scientific_name'] for r in rows],['Parus cinereus','Horornis diphone','Astur gentilis'])
                self.assertEqual([{m['engine']:m['candidate_windows'] for m in r['models']} for r in rows],
                                 [{'birdnet':15,'perch':9},{'birdnet':5,'perch':12},{'birdnet':5}])
                with connect(root) as db: db.execute('DELETE FROM workflow_cache')
                with patch('reports.classification_batches',side_effect=AssertionError('summary cache missed')):
                    self.assertEqual(client.get('/datasets/1/overview').json(),first)
                with connect(root) as db:
                    db.execute("UPDATE analyses SET metadata=json_set(metadata,'$.model_sha256','birdnet-new') WHERE engine='birdnet'")
                    db.execute('DELETE FROM workflow_cache')
                from reports import classification_batches
                with patch('reports.classification_batches',wraps=classification_batches) as batches:
                    self.assertEqual(client.get('/datasets/1/overview').status_code,200)
                    self.assertTrue(batches.called)

    @patch('results_workflow.predict_species', return_value=frozenset({'Horornis diphone'}))
    def test_region_status_uses_geo_for_all_payloads_without_birdnet(self, predict):
        from analysis import save
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'models.json').write_text(json.dumps({'main':'perch','extra':[],'confirmed':True}))
            sf.write(root/'20240808_060000.wav',np.zeros(15000),1000)
            geo=root/'birdnet/geo-models/v2.4/tf/model-fp32.tflite'
            geo.parent.mkdir(parents=True);geo.touch()
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                usual=client.get('/species?q=Horornis diphone').json()[0]
                unusual=client.get('/species?q=Parus cinereus').json()[0]
                unmapped=client.post('/species',json={'scientific_name':'Example unmapped bird'}).json()
                with connect(root) as db:
                    record=dict(db.execute('SELECT * FROM recordings').fetchone())
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',1)").lastrowid
                labels=[taxon['scientific_name'] for taxon in (usual,unusual,unmapped)]
                save(root,job,record,'perch',{'model_sha256':'perch','candidate_filter':{'species':labels}},dict(
                    labels=labels+['Other'],logits=np.log(np.tile([.4,.3,.2,.1],(3,1)))),[])
                with connect(root) as db:
                    db.executemany('''INSERT INTO presence_marks(dataset_id,species_id,recording_id,start_s,end_s)
                        VALUES(1,?,1,0,3)''',((taxon['id'],) for taxon in (usual,unusual,unmapped)))
                expected={usual['id']:'usual',unusual['id']:'unusual',unmapped['id']:None}
                payload=client.get('/datasets/1/overview').json()
                overview={row['species']['id']:row['region'] for row in payload['species']}
                self.assertEqual({sid:overview[sid] for sid in expected},expected)
                for taxon in (usual,unusual,unmapped):
                    detail=client.get(f"/datasets/1/species/{taxon['id']}").json()
                    listed=client.get(f"/datasets/1/species/{taxon['id']}/presence",params={'source':'search'}).json()
                    self.assertEqual((detail['region'],listed['region']),(expected[taxon['id']],)*2)
                exported=client.post('/workflow-exports',json={'dataset_id':1,'start_date':'2024-08-08',
                    'end_date':'2024-08-08','species_ids':list(expected),'revision':payload['revision'],
                    'folder':str(root),'locale':'en'})
                self.assertEqual(exported.status_code,200,exported.text)
                with zipfile.ZipFile(exported.json()['path']) as archive:
                    manifest=json.loads(archive.read('manifest.json'))
                self.assertEqual({row['species_id']:row['region'] for row in manifest['presence']},expected)
                geo.unlink()
                self.assertIsNone(client.get(f"/datasets/1/species/{unusual['id']}").json()['region'])

    def test_region_status_is_null_without_geo_model(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'models.json').write_text(json.dumps({'main':'perch','extra':['birdnet'],'confirmed':True}))
            sf.write(root/'20240808_060000.wav',np.zeros(15000),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                species=client.get('/species?q=Horornis diphone').json()[0]
                with connect(root) as db:
                    db.execute('INSERT INTO presence_marks(dataset_id,species_id,recording_id,start_s,end_s) VALUES(1,?,1,0,3)',(species['id'],))
                row=next(row for row in client.get('/datasets/1/overview').json()['species'] if row['species']['id']==species['id'])
                detail=client.get(f"/datasets/1/species/{species['id']}").json()
                listed=client.get(f"/datasets/1/species/{species['id']}/presence",params={'source':'search'}).json()
                self.assertEqual((row['region'],detail['region'],listed['region']),(None,None,None))

    def test_overview_keeps_checked_model_with_zero_candidates(self):
        from results_workflow import build_overview
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'models.json').write_text(json.dumps({'main':'perch','extra':['birdnet'],'confirmed':True}))
            sf.write(root/'20240808_060000.wav',np.zeros(15000),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                species=client.get('/species?q=ウグイス').json()[0]
                with connect(root) as db:
                    site=dict(db.execute('SELECT * FROM sites').fetchone())
                    dataset=dict(db.execute('SELECT * FROM datasets').fetchone())
                model={'engine':'perch','model_sha256':'perch','name':'Perch','latest_analysis_id':1,'recordings':1}
                quality={(species['id'],'perch'):{'applicable':True,'models':{'perch':'perch'},
                    'thresholds':{'perch':{'threshold':.897}},'status':'decided'}}
                ctx={'site':site,'dataset':dataset,'models':[model],'start_date':'2024-08-08','end_date':'2024-08-08'}
                with patch('results_workflow.current_validations',return_value=quality), \
                     patch('results_workflow.detection_snapshot',return_value={'summaries':[]}):
                    row=next(r for r in build_overview(root,1,ctx)['species'] if r['species']['id']==species['id'])
                self.assertEqual([(m['engine'],m['candidate_windows'],m['threshold']) for m in row['models']],
                                 [('perch',0,.897)])
                self.assertEqual(row['counting_status'],'decided')

    def test_overview_prepares_native_windows_once_after_pair_commit(self):
        from analysis import save
        from reports import cached_windows, classification_windows, read_classification_windows
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'models.json').write_text(json.dumps({'main':'perch','extra':['birdnet'],'confirmed':True}))
            sf.write(root/'20240808_060000.wav',np.zeros(15000),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                client.post('/imports',json=dict(folder=str(root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
                with connect(root) as db:
                    record=dict(db.execute('SELECT * FROM recordings').fetchone())
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'running',1)").lastrowid
                label='Horornis diphone_Japanese Bush Warbler'
                save(root,job,record,'birdnet',{'species':[label],'model_sha256':'test-birdnet'},dict(labels=np.repeat(label,5),start_s=np.arange(5)*3,
                     end_s=np.arange(1,6)*3,scores=np.array([.1,.8,.2,.3,.4])),[])
                with connect(root) as db:analysis=dict(db.execute('SELECT * FROM analyses').fetchone())
                self.assertEqual(list((root/'analyses').rglob('*.windows.npz')), [])
                with patch('reports.read_classification_windows',wraps=read_classification_windows) as prepare:
                    response=client.get('/datasets/1/overview')
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertEqual(prepare.call_count,1)
                cached_windows.cache_clear()
                with patch('reports.read_classification_windows',side_effect=AssertionError('cache was not prepared')):
                    _,starts,scores,evidence=classification_windows(analysis,15,[label])
                self.assertEqual((starts.tolist(),scores.ravel().tolist(),evidence.ravel().tolist()),
                                 ([0,3,6,9,12],[.1,.8,.2,.3,.4],[0,3,6,9,12]))

    def test_one_stage_check_hides_interim_results_and_adds_presence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'models.json').write_text(json.dumps({'main':'perch','extra':['birdnet'],'confirmed':True}))
            sf.write(root/'20240808_060000.wav',np.zeros(300000),1000)
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
                with connect(root) as db:
                    second=db.execute('SELECT id FROM validation_items WHERE session_id=? ORDER BY position LIMIT 1 OFFSET 1',(check['id'],)).fetchone()[0]
                    db.execute("INSERT INTO workflow_cache VALUES('keep-after-answer',X'00')")
                self.assertEqual(client.post(f"/validations/{check['id']}/answers",json={'item_id':second,'verdict':'target'}).json()['detail'],'validation_answer_order')
                with patch('validation.extension_needed',return_value=True) as extension_check:
                    while check['question']:
                        q=check['question']; verdict='target' if q['start_s']>=210 else 'other'
                        check=client.post(f"/validations/{check['id']}/answers",json={'item_id':q['id'],'verdict':verdict,'version':q['version']}).json()
                        if check['question']: self.assertEqual(check['results'],{})
                extension_check.assert_not_called()
                self.assertIn(check['status'],('decided','undecided'))
                with connect(root) as db:self.assertTrue(db.execute("SELECT 1 FROM workflow_cache WHERE cache_key='keep-after-answer'").fetchone())
                self.assertEqual(sum(b['answered'] for b in check['counts']['bins']),43)
                self.assertEqual(check['method'],'detection_top_bin_v1')
                self.assertFalse(check['can_extend'])
                self.assertFalse(check['extended'])
                self.assertNotIn('multiplier',check['results']['birdnet'])
                self.assertEqual(client.post(f"/validations/{check['id']}/extend").json()['detail'],'validation_extension_unavailable')
                revised=client.get(f"/validations/{check['id']}",params={'item_id':check['history'][0]['id']}).json()
                q=revised['question'];revised=client.post(f"/validations/{check['id']}/answers",json={
                    'item_id':q['id'],'verdict':'unknown','version':q['version']}).json()
                self.assertEqual(revised['counts']['unknown'],1)
                self.assertGreater(len(client.get(f'/datasets/1/species/{sid}').json()['presence']['evidence']),0)
                self.assertEqual(client.post(f"/validations/{check['id']}/evaluation").status_code,404)
                self.assertEqual(client.post(f"/validations/{check['id']}/recall").status_code,404)
                detail=client.get(f'/datasets/1/species/{sid}').json()
                # Reproduce a saved legacy result that still contains the retired field.
                with connect(root) as db:
                    snap=json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(check['id'],)).fetchone()[0])
                    snap['can_extend']=True
                    db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',(json.dumps(snap),check['id']))
                self.assertFalse(client.get(f"/validations/{check['id']}").json()['can_extend'])
                self.assertEqual(client.post(f"/validations/{check['id']}/extend").json()['detail'],'validation_extension_unavailable')
                with connect(root) as db:
                    snap['method']='stratified_quantile6_v1'
                    snap['results']['birdnet']['multiplier']={'value':123,'low':100,'high':150}
                    db.execute('UPDATE validation_sessions SET snapshot=? WHERE id=?',(json.dumps(snap),check['id']))
                legacy=client.get(f"/validations/{check['id']}").json()
                self.assertNotIn('multiplier',legacy['results']['birdnet'])
                overview=client.get('/datasets/1/overview').json()
                self.assertNotEqual(detail['revision'],overview['revision'])
                self.assertEqual(detail['fingerprint'],overview['fingerprint'])
                detail=client.get(f'/datasets/1/species/{sid}').json()
                self.assertNotIn('multiplier',json.dumps(detail))
                self.assertNotIn('multiplier',json.dumps(overview))
                exported=client.post('/workflow-exports',json={**scope,'species_ids':[sid],
                    'revision':overview['revision'],'folder':str(root),'locale':'en'})
                self.assertEqual(exported.status_code,200,exported.text)
                with zipfile.ZipFile(exported.json()['path']) as archive:
                    for filename in ('quality.csv','species.csv'):
                        fields=next(csv.reader(io.StringIO(archive.read(filename).decode('utf-8-sig'))))
                        self.assertFalse(any('multiplier' in field for field in fields))
                    for filename in archive.namelist():
                        if filename.endswith('.json'):
                            self.assertNotIn('multiplier',archive.read(filename).decode(),filename)
                    self.assertNotIn('Detection count correction',archive.read('index.html').decode())


if __name__ == '__main__':
    unittest.main()
