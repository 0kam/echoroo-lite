"""End-to-end checks for calibrated search, portable reports and frozen practice evidence."""
import csv
import hashlib
import io
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


class HtmlReportCheck(unittest.TestCase):
    def test_search_validation_report_and_immutable_audio_suite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);audio=root/'input';audio.mkdir();output=root/'exports';output.mkdir()
            for day in ('20240808','20240809'):
                sf.write(audio/f'{day}_060000.wav',np.zeros(300*1000),1000)
            with TestClient(create_app(root,root,'test'),headers={'Authorization':'Bearer test'}) as client:
                response=client.post('/imports',json=dict(folder=str(audio),site_name='<script>unexpected()</script>',dataset_name='Day',timezone='Asia/Tokyo',latitude=35,longitude=138))
                self.assertEqual(response.status_code,200,response.text)
                species=client.get('/species?q=ウグイス').json()[0]
                ref_path=root/'reference.wav';sf.write(ref_path,np.sin(np.arange(12000)*.08),1000)
                ref=client.post('/references',json={'path':str(ref_path)}).json()
                with connect(root) as db:
                    db.execute("INSERT INTO datasets VALUES(2,1,'Another import','unused')")
                    db.execute('UPDATE recordings SET dataset_id=2 WHERE id=2')
                    job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',2)").lastrowid
                    for rid in (1,2):
                        path=root/f'perch-{rid}.npz';probs=np.tile([.02,.2,.8],20)
                        np.savez(path,labels=['Horornis diphone','Other'],logits=np.log(np.column_stack([probs,1-probs])))
                        meta={'model_sha256':'perch','geo_filter':{'species':['Horornis diphone_Japanese Bush Warbler']}}
                        aid=db.execute("INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,'perch',?,?)",(job,rid,json.dumps(meta),str(path))).lastrowid
                        if rid==1:
                            segments=[db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,?,?)',(aid,i*5,(i+1)*5)).lastrowid for i in range(60)]
                    snapshot={'species':species,'dataset':dict(db.execute('SELECT * FROM datasets WHERE id=1').fetchone()),
                        'site':dict(db.execute('SELECT * FROM sites').fetchone()),'analysis_ids':[1],'model_sha256':'perch',
                        'references':[{'source':ref,'query':{'start_s':start,'end_s':start+5}} for start in (0,5)]}
                    run=db.execute('INSERT INTO search_runs(dataset_id,species_id,snapshot) VALUES(1,?,?)',(species['id'],json.dumps(snapshot))).lastrowid
                    db.executemany('INSERT INTO search_hits VALUES(?,?,?,0,0)',[(run,segment,float(probs[i])) for i,segment in enumerate(segments)])
                scope={'dataset_id':1,'species_id':species['id'],'start_date':'2024-08-08','end_date':'2024-08-08','run_id':run}
                url=f"/datasets/1/species/{species['id']}?run_id={run}&start_date=2024-08-08&end_date=2024-08-08"
                # More than twenty saved positives must not be silently truncated.
                for i in [i for i in range(60) if i%3][:21]:
                    client.post(f'/search-runs/{run}/judgements',json={'segment_id':segments[i],'verdict':'target'})
                for i in (0,3,6):client.post(f'/search-runs/{run}/judgements',json={'segment_id':segments[i],'verdict':'other'})
                self.assertIsNone(client.get(url).json()['report']['threshold'])
                session=client.post('/validations',json=scope).json();sid=session['id']
                # Judgements added during calibration are also excluded from evaluation.
                client.post(f'/search-runs/{run}/judgements',json={'segment_id':segments[57],'verdict':'other'})
                self.assertEqual(session['window_s'],5)
                self.assertIsNone(client.get(f"/datasets/1/species/{species['id']}").json()['validation'])
                while session['stage']=='calibration':
                    if session['question'] is None:
                        session=client.post(f'/validations/{sid}/evaluation').json()
                        break
                    q=session['question'];verdict='target' if int(q['start_s']/5)%3 else 'other'
                    response=client.post(f'/validations/{sid}/answers',json={'item_id':q['id'],'verdict':verdict})
                    self.assertEqual(response.status_code,200,response.text);session=response.json()
                self.assertEqual(session['stage'],'evaluation')
                calibrated=session['thresholds']['search']['threshold']
                self.assertIsNotNone(calibrated)
                self.assertEqual(client.get(url).json()['report']['threshold'],calibrated)
                with connect(root) as db:
                    phases={phase:{r[0] for r in db.execute('SELECT start_s FROM validation_items WHERE session_id=? AND phase=?',(sid,phase))} for phase in ('calibration','evaluation')}
                    previously={i*5 for i in [i for i in range(60) if i%3][:21]}|{0,15,30,285}
                    self.assertFalse(phases['calibration']&phases['evaluation'])
                    self.assertFalse(previously&phases['evaluation'])
                while session['stage']!='complete':
                    q=session['question'];session=client.post(f'/validations/{sid}/answers',json={'item_id':q['id'],'verdict':'target' if int(q['start_s']/5)%3 else 'other'}).json()
                self.assertTrue(session['results']['search']['goal_met'])
                last=session['history'][-1]
                changed='other' if last['verdict']=='target' else 'target'
                session=client.post(f'/validations/{sid}/answers',json={'item_id':last['id'],'verdict':changed}).json()
                session=client.post(f'/validations/{sid}/answers',json={'item_id':last['id'],'verdict':last['verdict']}).json()
                with connect(root) as db:
                    answer_history=[dict(r) for r in db.execute('''SELECT a.* FROM validation_answers a JOIN validation_items i ON i.id=a.item_id
                        WHERE i.session_id=? ORDER BY a.id''',(sid,))]
                # Practice is supplementary, including if completed after field answers.
                library=client.get('/quiz/library').json()
                clip=next(c for c in library['clips'] if c['species_id']==species['id'])
                other=next(c for c in library['clips'] if c['species_id']!=species['id'])
                practice=client.post('/quiz/sessions',json={'clip_ids':[clip['id'],other['id']],'count':2}).json()
                with connect(root) as db:questions=json.loads(db.execute('SELECT snapshot FROM quiz_sessions WHERE id=?',(practice['id'],)).fetchone()[0])['questions']
                for index,q in enumerate(questions):
                    client.post(f"/quiz/sessions/{practice['id']}/answers",json={'position':index,'species_id':q['clip']['species_id']})
                detail=client.get(url).json()
                body={**scope,'species_ids':[species['id']],'revision':detail['revision'],'folder':str(output),'locale':'ja'}
                with patch('audio.resample',side_effect=AssertionError('Report playback does not need resampling')):
                    response=client.post('/workflow-exports',json=body)
                self.assertEqual(response.status_code,200,response.text)
                path=Path(response.json()['path']);saved_hash=hashlib.sha256(path.read_bytes()).hexdigest()
                with zipfile.ZipFile(path) as archive:
                    self.assertIsNone(archive.testzip())
                    html=archive.read('index.html').decode();self.assertIn('&lt;script&gt;unexpected()',html)
                    self.assertNotIn('<script>unexpected()',html);self.assertNotIn('<audio ',html)
                    self.assertIn('width="1920" height="2240"',html)
                    self.assertIn('href="#R1-1"',html)
                    manifest=json.loads(archive.read('manifest.json'))
                    self.assertEqual(manifest['format'],'html');self.assertEqual(manifest['scope']['recording_ids'],[1])
                    self.assertEqual(manifest['scope']['recorded_seconds'],300)
                    self.assertEqual([r['id'] for r in json.loads(archive.read('recordings.json'))],[1])
                    for filename,entry in manifest['files'].items():self.assertEqual(hashlib.sha256(archive.read(filename)).hexdigest(),entry['sha256'])
                    refs=json.loads(archive.read('references.json'));self.assertEqual(len(refs),2)
                    self.assertEqual(refs[0]['original_file'],refs[1]['original_file'])
                    for reference in refs:
                        with sf.SoundFile(io.BytesIO(archive.read(reference['clip_file']))) as sound:self.assertEqual((sound.frames,sound.samplerate),(5000,1000))
                    evidence=list(csv.DictReader(io.StringIO(archive.read('evidence.csv').decode('utf-8-sig'))))
                    self.assertEqual(sum(row['audio_role']=='search_positive' for row in evidence),21)
                    self.assertTrue(all(row['audio_file'] for row in evidence if row['audio_role']=='validation'))
                    self.assertEqual(json.loads(archive.read('quiz-results.json'))['per_species'][0]['correct'],1)
                    validation=json.loads(archive.read(f'validation/{sid}.json'))
                    self.assertTrue(all(json.loads(i['quiz_snapshot'])['per_species']==[] for i in validation['items']))
                    self.assertEqual(validation['answer_history'],answer_history)
                    for result in manifest['results']:
                        summary=next(r for r in csv.DictReader(io.StringIO(archive.read('species.csv').decode('utf-8-sig'))) if int(r['report_id'])==result['report_id'])
                        scores=list(csv.DictReader(io.StringIO(archive.read(f"scores/{result['figure_id']}.csv").decode('utf-8-sig'))))
                        self.assertEqual(len(scores),int(summary['eligible_windows']))
                        self.assertEqual(sum(float(s['score'])>=result['threshold'] for s in scores),int(summary['candidate_windows']))
                ref_path.unlink()
                missing=client.post('/workflow-exports',json={**body,'locale':'en'})
                self.assertEqual(missing.status_code,200,missing.text)
                with zipfile.ZipFile(missing.json()['path']) as archive:
                    self.assertTrue(all(r['status']=='unavailable' for r in json.loads(archive.read('references.json'))))
                    self.assertIn('lang="en"',archive.read('index.html').decode())
                    self.assertIn('Copy text and tables',archive.read('index.html').decode())
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),saved_hash)
                count=len(list(output.iterdir()))
                with patch('workflow_export.create_html',side_effect=OSError('disk full')):
                    self.assertEqual(client.post('/workflow-exports',json=body).json()['detail'],'report_write_failed')
                self.assertEqual(len(list(output.iterdir())),count)
                self.assertEqual(len(client.get('/datasets/1/exports').json()),2)

    def test_period_edges_and_unset_search(self):
        from zoneinfo import ZoneInfo
        from reports import scope_records,window_in_scope
        from validation import suggest_thresholds
        from report_figures import cell_color
        zone=ZoneInfo('Asia/Tokyo')
        recording={'id':1,'recorded_at':'2024-08-08T14:59:55+00:00','timezone':'Asia/Tokyo','seconds':20}
        self.assertEqual(scope_records([recording],zone,'2024-08-09','2024-08-09'),[recording])
        self.assertTrue(window_in_scope(recording,0,5,zone,'2024-08-08','2024-08-08'))
        self.assertFalse(window_in_scope(recording,0,15,zone,'2024-08-08','2024-08-08'))
        self.assertTrue(window_in_scope(recording,5,20,zone,'2024-08-09','2024-08-09'))
        snap={'models':{'search':'model'},'target_precision':.9,'calibration_strata':{'0':{'population':6,'sample':3}}}
        rows=[{'scores':json.dumps({'search':-.3}),'stratum':'0','verdict':'unknown'} for _ in range(3)]
        self.assertIsNone(suggest_thresholds(snap,rows)['search']['threshold'])
        for row in rows:row['verdict']='target'
        self.assertLessEqual(suggest_thresholds(snap,rows)['search']['threshold'],-.3)
        cell={'status':'analysed','max_similarity':1}
        self.assertEqual(cell_color(cell,{'kind':'search','threshold':1}),'#a0504c')


if __name__=='__main__':unittest.main()
