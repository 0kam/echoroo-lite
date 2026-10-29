"""End-to-end checks for portable reports, presence evidence audio and frozen practice evidence."""
import csv
import hashlib
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from server import WorkflowExportRequest, create_app
from storage import CatalogError, connect
from workflow_export import export


class HtmlReportCheck(unittest.TestCase):
    def test_presence_report_and_immutable_audio_suite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);audio=root/'input';audio.mkdir();output=root/'exports';output.mkdir()
            for day in ('20240808','20240809'):
                sf.write(audio/f'{day}_060000.wav',np.zeros(300*1000),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                response=client.post('/imports',json=dict(folder=str(audio),site_name='<script>unexpected()</script>',dataset_name='Day',timezone='Asia/Tokyo',latitude=35,longitude=138))
                self.assertEqual(response.status_code,200,response.text)
                species=client.get('/species?q=ウグイス').json()[0]
                with connect(root) as db:
                    db.execute("INSERT INTO datasets VALUES(2,1,'Another import','unused')")
                    db.execute('UPDATE recordings SET dataset_id=2 WHERE id=2')
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',2)").lastrowid
                    for rid in (1,2):
                        path=root/f'perch-{rid}.npz';probs=np.tile([.02,.2,.8],20)
                        np.savez(path,labels=['Horornis diphone','Other'],logits=np.log(np.column_stack([probs,1-probs])))
                        meta={'model_sha256':'perch','candidate_filter':{'species':['Horornis diphone']}}
                        db.execute("INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,'perch',?,?)",(job,rid,json.dumps(meta),str(path)))
                scope={'dataset_id':1,'species_id':species['id'],'start_date':'2024-08-08','end_date':'2024-08-08'}
                listed=client.get(f"/datasets/1/species/{species['id']}/presence",params={'start_date':'2024-08-08','end_date':'2024-08-08'}).json()
                self.assertEqual(len(listed['rows']),40)
                self.assertTrue(all(model['score'] >= .1 for row in listed['rows'] for model in row['models']))
                for row in listed['rows'][:2]:
                    client.post('/presence/marks',json={'dataset_id':1,'species_id':species['id'],'recording_id':row['recording_id'],
                        'start_s':row['start_s'],'end_s':row['end_s'],'marked':True})
                # Practice is supplementary, including if completed after field answers.
                for taxon in (species, client.get('/species?q=Parus%20minor').json()[0]):
                    added=client.post('/quiz/clips',json={'species_id':taxon['id'],'source_kind':'recording',
                        'source_id':1,'start_s':10,'end_s':15})
                    self.assertEqual(added.status_code,200,added.text)
                library=client.get('/quiz/library').json()
                clip=next(c for c in library['clips'] if c['species_id']==species['id'])
                other=next(c for c in library['clips'] if c['species_id']!=species['id'])
                practice=client.post('/quiz/sessions',json={'clip_ids':[clip['id'],other['id']],'count':2}).json()
                with connect(root) as db:questions=json.loads(db.execute('SELECT snapshot FROM quiz_sessions WHERE id=?',(practice['id'],)).fetchone()[0])['questions']
                for index,q in enumerate(questions):
                    client.post(f"/quiz/sessions/{practice['id']}/answers",json={'position':index,'species_id':q['clip']['species_id']})
                detail=client.get(f"/datasets/1/species/{species['id']}",params={'start_date':'2024-08-08','end_date':'2024-08-08'}).json()
                self.assertEqual(detail['presence']['status'],'confirmed')
                body={**scope,'species_ids':[species['id']],'revision':detail['revision'],'folder':str(output),'locale':'ja'}
                stopped=threading.Event();stopped.set()
                with self.assertRaisesRegex(CatalogError,'cancelled'):
                    export(root,WorkflowExportRequest(**body),stopped)
                self.assertEqual(list(output.iterdir()),[])
                with patch('audio.resample',side_effect=AssertionError('Report playback does not need resampling')):
                    response=client.post('/workflow-exports',json=body)
                self.assertEqual(response.status_code,200,response.text)
                path=Path(response.json()['path']);saved_hash=hashlib.sha256(path.read_bytes()).hexdigest()
                with zipfile.ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    html=archive.read('index.html').decode();self.assertIn('&lt;script&gt;unexpected()',html)
                    self.assertNotIn('<script>unexpected()',html);self.assertNotIn('<audio ',html)
                    self.assertIn('width="1920" height="2240"',html)
                    self.assertIn('聞いて確認した（根拠の音 2）',html);self.assertIn('既定のスコアの区切り（人の確認で決めていない）',html)
                    self.assertIn('0.08 時間',html);self.assertNotIn('基準',html)
                    manifest=json.loads(archive.read('manifest.json'))
                    self.assertEqual(manifest['format'],'html');self.assertEqual(manifest['scope']['recording_ids'],[1])
                    self.assertEqual(manifest['scope']['recorded_seconds'],300)
                    self.assertEqual(manifest['scope']['schedule'][0]['hours'][6],300)
                    self.assertEqual([r['id'] for r in json.loads(archive.read('recordings.json'))],[1])
                    for filename,entry in manifest['files'].items():self.assertEqual(hashlib.sha256(archive.read(filename)).hexdigest(),entry['sha256'])
                    evidence=list(csv.DictReader(io.StringIO(archive.read('evidence.csv').decode('utf-8-sig'))))
                    presence=[row for row in evidence if row['audio_role']=='presence']
                    self.assertEqual(len(presence),2)
                    for row in presence:
                        with sf.SoundFile(io.BytesIO(archive.read(row['audio_file']))) as sound:self.assertEqual((sound.frames,sound.samplerate),(5000,1000))
                    rows=list(csv.DictReader(io.StringIO(archive.read('presence.csv').decode('utf-8-sig'))))
                    self.assertEqual([(r['status'],r['marked'],r['heard_in_checks']) for r in rows],[('confirmed','2','0')])
                    self.assertEqual(json.loads(archive.read('quiz-results.json'))['per_species'][0]['correct'],1)
                    for result in manifest['results']:
                        summary=next(r for r in csv.DictReader(io.StringIO(archive.read('species.csv').decode('utf-8-sig'))) if int(r['report_id'])==result['report_id'])
                        scores=list(csv.DictReader(io.StringIO(archive.read(f"scores/{result['figure_id']}.csv").decode('utf-8-sig'))))
                        self.assertEqual(len(scores),int(summary['eligible_windows']))
                        self.assertEqual(sum(float(s['score'])>=result['threshold'] for s in scores),int(summary['candidate_windows']))
                english=client.post('/workflow-exports',json={**body,'locale':'en'})
                self.assertEqual(english.status_code,200,english.text)
                with zipfile.ZipFile(english.json()['path']) as archive:
                    self.assertIn('lang="en"',archive.read('index.html').decode())
                    self.assertIn('Confirmed by listening (2 evidence sounds)',archive.read('index.html').decode())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),saved_hash)
                count=len(list(output.iterdir()))
                with patch('workflow_export.create_html',side_effect=OSError('disk full')):
                    self.assertEqual(client.post('/workflow-exports',json=body).json()['detail'],'report_write_failed')
                self.assertEqual(len(list(output.iterdir())),count)
                self.assertEqual(len(client.get('/datasets/1/exports').json()),2)

    def test_period_edges_and_cell_colours(self):
        from zoneinfo import ZoneInfo
        from reports import scope_records,window_in_scope
        from report_figures import cell_color
        zone=ZoneInfo('Asia/Tokyo')
        recording={'id':1,'recorded_at':'2024-08-08T14:59:55+00:00','timezone':'Asia/Tokyo','seconds':20}
        self.assertEqual(scope_records([recording],zone,'2024-08-09','2024-08-09'),[recording])
        self.assertTrue(window_in_scope(recording,0,5,zone,'2024-08-08','2024-08-08'))
        self.assertFalse(window_in_scope(recording,0,15,zone,'2024-08-08','2024-08-08'))
        self.assertTrue(window_in_scope(recording,5,20,zone,'2024-08-09','2024-08-09'))
        self.assertEqual(cell_color({'status':'analysed','candidate_rate':1},{}),'#a0504c')
        self.assertEqual(cell_color({'status':'analysed','candidate_rate':0},{}),'#ffffff')
        self.assertEqual(cell_color({'status':'analysed','candidate_rate':None},{}),'#f1f0f2')


if __name__=='__main__':unittest.main()
