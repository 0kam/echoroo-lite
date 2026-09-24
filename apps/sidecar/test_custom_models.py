"""Real fitting and persistence checks for the one-species custom classifier."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from custom_models import _folds, get_model, list_models, report_snapshot, score_windows, validation_population
from results_workflow import context, fingerprint
from server import create_app
from storage import connect, initialize


SPECIES = 'custom-model-species'


@contextmanager
def custom_model_fixture():
    """Small real-vector catalog shared by custom report/API tests."""
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp).resolve();initialize(root)
        files=[]
        for recording_id in range(1,7):
            path=root/f'recording-{recording_id}.wav';path.write_bytes(b'fixture');files.append(path)
        with connect(root) as db:
            db.execute("CREATE TABLE species(id TEXT PRIMARY KEY, scientific_name TEXT NOT NULL UNIQUE, names TEXT NOT NULL DEFAULT '{}')")
            db.execute("INSERT INTO species VALUES(?, 'Test bird', '{}')",(SPECIES,))
            db.execute("INSERT INTO sites VALUES(1,'Site','Asia/Tokyo',35,138)")
            db.execute("INSERT INTO datasets VALUES(1,1,'Dataset',?)",(str(root),))
            for recording_id,path in enumerate(files,1):
                stat=path.stat()
                db.execute("""INSERT INTO recordings(id,dataset_id,path,original_time,timezone,recorded_at,seconds,sample_rate,size,mtime_ns)
                    VALUES(?,?,?,'2024-08-08T00:00:00','Asia/Tokyo',?,30,24000,?,?)""",
                    (recording_id,1,str(path),f'2024-08-07T{14+recording_id:02d}:00:00+00:00',stat.st_size,stat.st_mtime_ns))
            job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',6)").lastrowid
            segment_ids={}
            for recording_id in range(1,7):
                analysis=db.execute("INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,'perch',?,'unused.npz')",
                    (job,recording_id,json.dumps({'model_sha256':'perch-sha'}))).lastrowid
                for offset in range(6):
                    start=offset*5
                    segment=db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,?,?)',(analysis,start,start+5)).lastrowid
                    vector=np.zeros(1536,dtype='<f4')
                    vector[0 if offset in (0,3) else 1]=1
                    vector[2+recording_id]=.05
                    db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)',(segment,vector.tobytes()))
                    segment_ids[recording_id,offset]=segment
            run=db.execute("INSERT INTO search_runs(dataset_id,species_id,snapshot) VALUES(1,?,'{}')",(SPECIES,)).lastrowid
            for recording_id in range(1,5):
                for offset,verdict in ((0,'target'),(1,'other'),(2,'other')):
                    db.execute("INSERT INTO search_judgements(run_id,species_id,segment_id,verdict,note) VALUES(?,?,?,?, '')",
                        (run,SPECIES,segment_ids[recording_id,offset],verdict))
        current=fingerprint(root,1)
        snapshot={'seed':'123','split_unit':'recording','start_date':'2024-08-08','end_date':'2024-08-08',
            'calibration_recording_ids':[1,2,3,4],'evaluation_recording_ids':[5,6],
            'models':[{'engine':'perch','model_sha256':'perch-sha'}]}
        with connect(root) as db:
            annotation=db.execute('INSERT INTO annotation_sessions(dataset_id,fingerprint,snapshot) VALUES(?,?,?)',(1,current,json.dumps(snapshot))).lastrowid
        yield root,annotation,segment_ids,run


class CustomModelCheck(unittest.TestCase):
    def test_fold_assignment_keeps_both_classes_in_each_training_split(self):
        labels={'p1':{'target'},'p2':{'target'},'n1':{'other'},'n2':{'other'}}
        folds,_=_folds(labels,'seed',SPECIES)
        for fold in range(3):
            training={label for group,values in labels.items() if folds[group]!=fold for label in values}
            self.assertEqual(training,{'target','other'})

    def test_search_labels_fit_infer_report_and_staleness(self):
        with custom_model_fixture() as (root,annotation,segments,run):
            headers={'Authorization':'Bearer test'}
            with TestClient(create_app(root,root,'test'),headers=headers) as client:
                ready=client.get(f'/annotations/{annotation}/custom-model-readiness',params={'species_id':SPECIES})
                self.assertEqual(ready.status_code,200,ready.text);ready=ready.json()
                self.assertTrue(ready['can_train']);self.assertEqual(ready['examples']['recordings'],4)
                self.assertEqual((ready['examples']['positive_5s'],ready['examples']['negative_5s']),(4,8))
                with connect(root) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM annotation_items').fetchone()[0],0)
                created=client.post('/custom-models',json={'annotation_session_id':annotation,'species_id':SPECIES})
                self.assertEqual(created.status_code,200,created.text);model=created.json();model_id=model['id']
                self.assertEqual((model['status'],model['training']['recordings'],model['cv']['window_s']),('ready',4,5))
                self.assertTrue(model['applicable']);self.assertIsNotNone(model['cv']['threshold'])
                self.assertEqual(model['inference'],{'status':'complete','scored_5s':36,'complete_15s':12,'missing_5s':0})
                self.assertEqual(len(score_windows(root,model_id)),12)
                ctx=context(root,1,'2024-08-08','2024-08-08')
                population,_=validation_population(root,ctx,model_id)
                self.assertEqual({row['recording_id'] for row in population},{5,6})
                self.assertEqual(len(population),4)
                report=report_snapshot(root,ctx,model_id)
                self.assertEqual((report['kind'],report['engine'],report['window_s']),('detection','custom',15))
                self.assertEqual(report['custom_model']['training']['recordings'],4)
                session=client.post('/validations',json={'model_id':model_id})
                self.assertEqual(session.status_code,200,session.text)
                self.assertEqual((session.json()['kind'],session.json()['model_id']),('custom',model_id))
                detail=client.get(f'/datasets/1/species/{SPECIES}',params={'engine':'custom','model_id':model_id,
                    'start_date':'2024-08-08','end_date':'2024-08-08'})
                self.assertEqual(detail.status_code,200,detail.text)
                with connect(root) as db:
                    snapshot=json.loads(db.execute('SELECT snapshot FROM custom_models WHERE id=?',(model_id,)).fetchone()[0])
                    snapshot['cv']={**snapshot['cv'],'status':'threshold_unavailable','threshold':None}
                    snapshot['inference']={'status':'not_run','scored_5s':0,'complete_15s':0,'missing_5s':0}
                    failed=db.execute("""INSERT INTO custom_models(dataset_id,species_id,annotation_session_id,status,snapshot)
                        VALUES(1,?,?,'threshold_unavailable',?)""",(SPECIES,annotation,json.dumps(snapshot))).lastrowid
                current=list_models(root,context(root,1,'2024-08-08','2024-08-08'),SPECIES)
                self.assertEqual([entry['id'] for entry in current],[model_id])
                self.assertEqual(get_model(root,failed)['status'],'threshold_unavailable')
            with connect(root) as db:
                row=db.execute('SELECT artifact_path FROM custom_models WHERE id=?',(model_id,)).fetchone()
                artifact_path=Path(row['artifact_path']);original=artifact_path.read_bytes()
            artifact_path.write_bytes(original+b'changed')
            self.assertEqual(get_model(root,model_id)['reason'],'custom_model_files_missing')
            artifact_path.write_bytes(original)
            with connect(root) as db:
                db.execute("INSERT INTO search_judgements(run_id,species_id,segment_id,verdict,note) VALUES(?,?,?,'other','')",
                    (run,SPECIES,segments[1,3]))
            self.assertTrue(get_model(root,model_id)['applicable'])
            with connect(root) as db:
                db.execute("INSERT INTO search_judgements(run_id,species_id,segment_id,verdict,note) VALUES(?,?,?,'other','')",
                    (run,SPECIES,segments[1,0]))
            stale=get_model(root,model_id)
            self.assertFalse(stale['applicable']);self.assertEqual(stale['reason'],'custom_model_labels_changed')


if __name__=='__main__':
    unittest.main()
