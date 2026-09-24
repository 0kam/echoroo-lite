"""Deletion must preserve other imports, source files, and independent learning data."""
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from server import create_app
from storage import connect, initialize


class ManagementCheck(unittest.TestCase):
    def populate(self, root, client):
        for index in range(4):
            folder = root / f'original-{index}'
            folder.mkdir()
            sf.write(folder/'20240808_060000.wav', np.zeros(15000), 1000)
            body = dict(folder=str(folder),site_name=f'Site {index//2}',dataset_name=f'Import {index}',
                        timezone='Asia/Tokyo',latitude=35,longitude=138)
            if index%2:
                body['site_id'] = index//2+1
            response = client.post('/imports',json=body)
            self.assertEqual(response.status_code,200,response.text)
        species = client.get('/species?q=ウグイス').json()[0]['id']
        with connect(root) as db:
            for dataset in (1,3):
                job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(?,'done',4)",(dataset,)).lastrowid
                for rid in (dataset,dataset+1):
                    for engine in ('birdnet','perch'):
                        folder=root/'analyses'/str(job);folder.mkdir(parents=True,exist_ok=True)
                        path=folder/f'{rid}-{engine}.npz'
                        label='Horornis diphone_Japanese Bush Warbler'
                        if engine=='birdnet':
                            np.savez(path,labels=np.repeat(label,5),scores=np.repeat(.5,5),start_s=np.arange(5)*3,end_s=np.arange(1,6)*3)
                        else:
                            np.savez(path,labels=['Horornis diphone','Other'],logits=np.log(np.full((3,2),.5)))
                        meta={'model_sha256':engine,'species':[label],'latitude':35,'longitude':138,
                              'geo_filter':{'species':[label],'latitude':35,'longitude':138}}
                        aid=db.execute('INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,?,?,?)',
                                       (job,rid,engine,json.dumps(meta),str(path))).lastrowid
                        if engine=='perch':
                            segment=db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,0,5)',(aid,)).lastrowid
                            db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)',(segment,np.ones(1536,dtype=np.float32).tobytes()))
                    # All histories deliberately use the earliest import as their entry point.
                    snap=json.dumps({'recording_ids':[rid],'analysis_ids':[aid]})
                    run=db.execute('INSERT INTO search_runs(dataset_id,species_id,snapshot) VALUES(?,?,?)',(dataset,species,snap)).lastrowid
                    db.execute('INSERT INTO search_hits VALUES(?,?,.8,0,0)',(run,segment))
                    sample=db.execute('INSERT INTO search_samples(run_id,low,high,population,requested_count,seed) VALUES(?,0,1,1,1,42)',(run,)).lastrowid
                    db.execute('INSERT INTO search_sample_items VALUES(?,?,0)',(sample,segment))
                    db.execute("INSERT INTO search_judgements(run_id,species_id,segment_id,verdict,note) VALUES(?,?,?,'target','')",(run,species,segment))
                    validation=db.execute("INSERT INTO validation_sessions(dataset_id,species_id,fingerprint,snapshot) VALUES(?,?,'test',?)",(dataset,species,snap)).lastrowid
                    item=db.execute("INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,stratum,scores) VALUES(?,'calibration',0,?,0,'0','{}')",(validation,rid)).lastrowid
                    db.execute("INSERT INTO validation_answers(item_id,verdict) VALUES(?,'target')",(item,))
                    report=db.execute("INSERT INTO reports(dataset_id,kind,snapshot) VALUES(?,'detection',?)",(dataset,snap)).lastrowid
                    db.execute("INSERT INTO reports(dataset_id,kind,snapshot) VALUES(?,'detection',?)",(dataset,json.dumps({'base_report_id':report})))
                    exported=root/f'export-{rid}.zip';exported.write_bytes(b'original export')
                    db.execute('INSERT INTO workflow_exports(dataset_id,snapshot,path) VALUES(?,?,?)',(dataset,json.dumps({'results':[{'report_id':report}]}),str(exported)))
            db.execute("INSERT INTO quiz_sessions(snapshot,answers) VALUES('{}','[1]')")
        return species

    def test_migration_scoped_deletion_and_non_reused_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            # Start with the exact pre-management table definitions, including populated FKs and vectors.
            with patch('storage.monotonic_ids'):
                old_app=create_app(root,root,'test')
            with TestClient(old_app,headers={'Authorization':'Bearer test'}) as client:
                species=self.populate(root,client)
                with connect(root) as db:
                    for recording in (1,2):
                        snap=json.dumps({'recording_ids':[recording]})
                        annotation=db.execute("INSERT INTO annotation_sessions(dataset_id,fingerprint,snapshot) VALUES(1,'test',?)",(snap,)).lastrowid
                        model=db.execute("""INSERT INTO custom_models(dataset_id,species_id,annotation_session_id,status,snapshot)
                            VALUES(1,?,?,'ready',?)""",(species,annotation,json.dumps({'recording_ids':[recording],'annotation_session_id':annotation}))).lastrowid
                        folder=root/'custom-models'/str(model);folder.mkdir(parents=True)
                        (folder/'model.npz').write_bytes(b'generated model')
            originals={p:p.read_bytes() for p in [*root.glob('original-*/*.wav'),*root.glob('export-*.zip')]}
            initialize(root)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                preview=client.get('/sites/1/delete-preview?dataset_id=1').json()
                self.assertEqual({k:preview[k] for k in ('recordings','analyses','searches','validations','reports','exports')},
                                 dict(recordings=1,analyses=2,searches=1,validations=1,reports=2,exports=1))
                self.assertEqual(preview['custom_models'],1)
                # Reading the confirmation or cancelling it changes nothing.
                self.assertEqual(client.get('/sites/1/delete-preview?dataset_id=1').json(),preview)
                response=client.post('/sites/1/delete',json={'dataset_id':1,'revision':preview['revision']})
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['remaining_dataset_id'],2)
                with connect(root) as db:
                    self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
                    self.assertEqual([r[0] for r in db.execute('SELECT id FROM site_recordings WHERE scope_id=2')],[2])
                    self.assertEqual(db.execute('SELECT dataset_id FROM jobs WHERE id=1').fetchone()[0],2)
                    for table in ('search_runs','validation_sessions','custom_models','reports','workflow_exports'):
                        self.assertEqual(db.execute(f'SELECT COUNT(*) FROM {table} WHERE dataset_id=1').fetchone()[0],0)
                        self.assertGreater(db.execute(f'SELECT COUNT(*) FROM {table} WHERE dataset_id=2').fetchone()[0],0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0],3)
                    self.assertEqual(db.execute('SELECT answers FROM quiz_sessions').fetchone()[0],'[1]')
                self.assertFalse((root/'analyses/1/1-birdnet.npz').exists())
                self.assertTrue((root/'analyses/1/2-birdnet.npz').exists())
                self.assertFalse((root/'custom-models/1').exists())
                self.assertTrue((root/'custom-models/2/model.npz').exists())
                self.assertEqual(client.get('/sites/1/delete-preview?dataset_id=3').json()['detail'],'not_found')
                # Delete the last import; the now-empty site can still accept recordings.
                preview=client.get('/sites/1/delete-preview?dataset_id=2').json()
                self.assertEqual(client.post('/sites/1/delete',json={'dataset_id':2,'revision':preview['revision']}).status_code,200)
                self.assertEqual(client.get('/sites/1/management').json()['datasets'],[])
                # Delete the highest IDs, restart, and re-import the same absolute path.
                preview=client.get('/sites/2/delete-preview').json()
                self.assertEqual(client.post('/sites/2/delete',json={'revision':preview['revision']}).status_code,200)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                response=client.post('/imports',json=dict(folder=str(root/'original-3'),site_name='New site',dataset_name='New import',timezone='Asia/Tokyo',latitude=35,longitude=138))
                self.assertEqual(response.status_code,200,response.text)
                with connect(root) as db:
                    self.assertGreater(db.execute('SELECT MAX(id) FROM sites').fetchone()[0],2)
                    self.assertGreater(db.execute('SELECT MAX(id) FROM recordings').fetchone()[0],4)
                    self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
                self.assertEqual(client.get('/sites/2/management').json()['detail'],'not_found')
            self.assertTrue(all(p.read_bytes()==content for p,content in originals.items()))

    def test_edit_reanalysis_and_busy_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                species=self.populate(root,client)
                # Synthetic history is not a real validation session; remove it before building real overviews.
                with connect(root) as db:
                    for table in ('validation_answers','validation_items','validation_sessions','search_sample_items','search_samples','search_judgements','search_hits','search_runs'):
                        db.execute(f'DELETE FROM {table}')
                    original_times=[tuple(r) for r in db.execute('SELECT original_time,timezone,recorded_at FROM recordings')]
                    saved_reports=[tuple(r) for r in db.execute('SELECT * FROM reports')]
                before=client.get('/datasets/1/overview').json()
                response=client.post('/sites/1/edit',json={'name':'Renamed','latitude':35,'longitude':138})
                self.assertEqual(response.status_code,200,response.text)
                renamed=client.get('/datasets/1/overview').json()
                self.assertEqual(renamed['fingerprint'],before['fingerprint'])
                self.assertEqual(renamed['analysed_recordings'],2)
                self.assertEqual(renamed['site']['name'],'Renamed')
                self.assertEqual(client.post('/jobs',json={'dataset_id':1}).json()['detail'],'no_audio')
                client.post('/sites/1/edit',json={'name':'Renamed','latitude':36,'longitude':138})
                changed=client.get('/datasets/1/overview').json()
                self.assertTrue(changed['location_stale'])
                self.assertEqual(changed['analysed_recordings'],0)
                self.assertNotEqual(changed['fingerprint'],before['fingerprint'])
                response=client.post('/validations',json={'dataset_id':1,'species_id':species,'start_date':'2024-08-08','end_date':'2024-08-08'})
                self.assertEqual(response.json()['detail'],'location_reanalysis')
                response=client.post('/workflow-exports',json={'dataset_id':1,'revision':changed['revision'],'species_ids':[species],
                    'start_date':'2024-08-08','end_date':'2024-08-08','folder':str(root),'locale':'ja','figures':[]})
                self.assertEqual(response.json()['detail'],'location_reanalysis')
                preview=client.get('/sites/1/delete-preview').json()
                entered,release=threading.Event(),threading.Event()
                def run(root,models,job,records,site,cancel):
                    self.assertEqual([r['id'] for r in records],[1,2]);self.assertEqual(site['latitude'],36)
                    entered.set();release.wait(10)
                with patch('analysis.run',side_effect=run):
                    response=client.post('/jobs',json={'dataset_id':1})
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertTrue(entered.wait(2))
                    try:
                        self.assertEqual(client.post('/sites/1/delete',json={'revision':preview['revision']}).json()['detail'],'management_busy')
                    finally:
                        release.set()
                with connect(root) as db:
                    self.assertEqual([tuple(r) for r in db.execute('SELECT original_time,timezone,recorded_at FROM recordings')],original_times)
                    self.assertEqual([tuple(r) for r in db.execute('SELECT * FROM reports')],saved_reports)

    def test_stale_confirmation_cross_site_reference_and_active_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                species=self.populate(root,client)
                preview=client.get('/sites/1/delete-preview').json()
                with connect(root) as db:
                    db.execute("INSERT INTO validation_answers(item_id,verdict) VALUES(1,'other')")
                self.assertEqual(client.post('/sites/1/delete',json={'revision':preview['revision']}).json()['detail'],'management_changed')
                with connect(root) as db:
                    db.execute('INSERT INTO search_runs(dataset_id,species_id,snapshot) VALUES(3,?,?)',
                               (species,json.dumps({'references':[{'source':{'kind':'recording','id':1}}]})))
                self.assertEqual(client.get('/sites/1/delete-preview').json()['detail'],'management_shared_reference')
                self.assertEqual(len(client.get('/sites/1/management').json()['datasets']),2)
                entered,release=threading.Event(),threading.Event()
                def read(*args):
                    entered.set();release.wait(10);return {}
                with patch('management.site_details',side_effect=read),ThreadPoolExecutor() as pool:
                    pending=pool.submit(client.get,'/sites/1/management')
                    self.assertTrue(entered.wait(2))
                    try:
                        response=client.post('/sites/1/edit',json={'name':'Blocked','latitude':35,'longitude':138})
                        self.assertEqual(response.json()['detail'],'management_busy')
                    finally:
                        release.set()
                    self.assertEqual(pending.result().status_code,200)
                self.assertEqual(client.get('/sites/1/management').json()['site']['name'],'Site 0')


if __name__=='__main__':
    unittest.main()
