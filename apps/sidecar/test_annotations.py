"""Sampling, explicit labels, recording separation, corrections and portable evidence."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from annotations import partition_records
from sampling import draw_plan, strata_for
from server import create_app
from storage import connect


class AnnotationCheck(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for hour in ('060000','070000','080000','090000'):
            sf.write(self.root/f'20240808_{hour}.wav', np.zeros(180000), 1000)
        self.client = TestClient(create_app(self.root,self.root,'test'),headers={'Authorization':'Bearer test'})
        self.client.__enter__();self.addCleanup(self.client.__exit__,None,None,None)
        self.post('/imports',dict(folder=str(self.root),site_name='Site',dataset_name='Data',timezone='Asia/Tokyo',latitude=35,longitude=138))
        self.taxon = self.client.get('/species?q=ウグイス').json()[0]
        self.other = self.post('/species',dict(scientific_name='Example unmodelled',ja='テスト未対応種',en='Unmodelled test'))
        label='Horornis diphone_Japanese Bush Warbler'
        with connect(self.root) as db:
            job=db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',4)").lastrowid
            for rid in range(1,5):
                path=self.root/f'bird-{rid}.npz';scores=np.repeat(np.tile([.02,.2,.8],4),5)
                np.savez(path,labels=np.repeat(label,60),scores=scores,start_s=np.arange(60)*3,end_s=np.arange(1,61)*3)
                meta={'model_sha256':'birdnet','species':[label]}
                db.execute('INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,?,?,?)',(job,rid,'birdnet',json.dumps(meta),str(path)))
        self.scope={'dataset_id':1,'start_date':'2024-08-08','end_date':'2024-08-08'}
        with patch('annotations.secrets.randbits',return_value=20):
            self.sid=self.post('/annotations',self.scope)['id']
        with connect(self.root) as db:
            self.items=[dict(r) for r in db.execute('SELECT id,start_s FROM annotation_items WHERE session_id=? ORDER BY position',(self.sid,))]

    def post(self,path,body=None):
        response=self.client.post(path,json=body)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def mark(self,item,ranges,complete=True):
        """Legacy site-wide call marks; the listening page that wrote them is gone."""
        with connect(self.root) as db:
            db.execute("INSERT INTO annotation_answers(item_id,ranges,complete,quiz_snapshot) VALUES(?,?,?,'{}')",(item['id'],json.dumps(ranges),int(complete)))

    def fill(self,count=10):
        for index,item in enumerate(self.items[:count]):
            start=item['start_s']
            ranges=[dict(species_id=self.taxon['id'],start_s=start+1,end_s=start+2)] if index<3 else []
            if index==0:ranges += [dict(species_id=self.other['id'],start_s=start+1.5,end_s=start+3),dict(species_id=None,start_s=start+8,end_s=start+9)]
            self.mark(item,ranges)

    def test_only_latest_complete_answer_becomes_evidence(self):
        first=self.items[0];start=first['start_s']
        ranges=[dict(species_id=self.taxon['id'],start_s=start+1,end_s=start+2),
                dict(species_id=self.other['id'],start_s=start+2,end_s=start+3)]
        self.mark(first,ranges,complete=False)
        self.assertEqual(self.client.get(f'/datasets/1/species/{self.taxon["id"]}').json()['annotations'],[])
        self.mark(first,ranges)
        self.assertEqual(len(self.client.get(f'/datasets/1/species/{self.taxon["id"]}').json()['annotations']),1)
        self.mark(first,ranges,complete=False)
        from results_workflow import cached,context
        ctx=context(self.root,1)
        cached(self.root,['overview-v3',1,ctx['revision'],ctx['start_date'],ctx['end_date']],
               lambda:{'revision':ctx['revision'],'species':[{'species':self.taxon,'annotated':99}]})
        overview=self.client.get('/datasets/1/overview').json()
        self.assertEqual(next(r for r in overview['species'] if r['species']['id']==self.taxon['id'])['annotated'],0)
        self.assertEqual(self.client.get(f'/datasets/1/species/{self.taxon["id"]}').json()['annotations'],[])
        exported=self.post('/workflow-exports',{**self.scope,'revision':overview['revision'],'species_ids':[self.taxon['id']],
                                               'folder':str(self.root),'locale':'ja'})
        with zipfile.ZipFile(exported['path']) as archive:
            self.assertEqual(json.loads(archive.read('annotations/ranges.json')),[])

    def test_species_check_starts_without_site_listening(self):
        body={**self.scope,'species_id':self.taxon['id']}
        validation=self.post('/validations',body)
        self.assertEqual(validation['annotation_session_id'],self.sid)
        self.assertEqual(validation['counts']['calibration'],dict(total=10,answered=0))
        self.assertEqual(self.post('/validations',body)['id'],validation['id'])
        with connect(self.root) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM annotation_sessions').fetchone()[0],1)
            # Former "evaluate together" members open as ordinary single-species checks.
            db.execute("UPDATE validation_sessions SET snapshot=json_set(snapshot,'$.group_ids',json('[1,2]'))")
        legacy=self.client.get(f'/validations/{validation["id"]}').json()
        self.assertEqual((legacy['species']['id'],legacy['question']['id']),(self.taxon['id'],validation['question']['id']))
        self.assertNotIn('members',legacy)

    def test_reuse_explicit_positives_and_reserve_whole_recordings(self):
        self.fill();sid=self.sid
        body={**self.scope,'species_id':self.taxon['id']}
        validation=self.post('/validations',body);vid=validation['id']
        self.assertEqual(validation['counts']['calibration'],dict(total=10,answered=3))
        self.assertEqual(self.post('/validations',body)['id'],vid)
        while validation['question']:
            q=validation['question']
            validation=self.post(f'/validations/{vid}/answers',dict(item_id=q['id'],verdict='other',version=q['version']))
        validation=self.post(f'/validations/{vid}/evaluation')
        with connect(self.root) as db:
            groups={phase:{r[0] for r in db.execute('SELECT recording_id FROM validation_items WHERE session_id=? AND phase=?',(vid,phase))} for phase in ('calibration','evaluation')}
            self.assertFalse(groups['calibration'] & groups['evaluation'])
            snap=json.loads(db.execute('SELECT snapshot FROM validation_sessions WHERE id=?',(vid,)).fetchone()[0])
            self.assertFalse(set(snap['excluded_recording_ids']) & groups['evaluation'])
            self.assertEqual(sum(s['population'] for s in snap['calibration_strata'].values()),24)
        while validation['question']:
            q=validation['question'];verdict='target' if int(q['start_s']/15)%3 else 'other'
            validation=self.post(f'/validations/{vid}/answers',dict(item_id=q['id'],verdict=verdict,ranges=[],version=q['version']))
        self.assertEqual(validation['stage'],'complete')
        self.assertEqual(validation['evaluation_population'],24)
        # Later source corrections invalidate live quality, without changing frozen answers.
        self.mark(self.items[0],[])
        stale=self.client.get(f'/validations/{vid}').json()
        self.assertFalse(stale['applicable']);self.assertFalse(stale['can_start_recall'])
        self.assertEqual(self.client.post(f'/validations/{vid}/recall').json()['detail'],'workflow_changed')

    def test_heard_answers_wait_for_marks_that_can_be_added_later(self):
        v=self.post('/validations',{**self.scope,'species_id':self.taxon['id']});vid=v['id']
        while v['question']:
            v=self.post(f'/validations/{vid}/answers',dict(item_id=v['question']['id'],verdict='target',version=v['question']['version']))
        self.post(f'/validations/{vid}/evaluation')
        url=f'/annotations/{self.sid}/custom-model-readiness?species_id={self.taxon["id"]}'
        waiting=self.client.get(url).json()['needs_range']
        self.assertEqual(len(waiting),10)
        item=waiting[0];mark=[dict(species_id=self.taxon['id'],start_s=item['start_s']+1,end_s=item['start_s']+2)]
        locked=self.client.post(f'/validations/{vid}/answers',json=dict(item_id=item['item_id'],verdict='other',version=item['version']))
        self.assertEqual(locked.json()['detail'],'validation_phase_locked')
        self.post(f'/validations/{vid}/answers',dict(item_id=item['item_id'],verdict='target',ranges=mark,version=item['version']))
        ready=self.client.get(url).json()
        self.assertEqual((len(ready['needs_range']),ready['answers']['target']),(9,10))
        with connect(self.root) as db:  # New marks never rewrite the site's shared marks.
            self.assertEqual(db.execute('SELECT COUNT(*) FROM annotation_answers').fetchone()[0],0)

    def test_quality_correction_updates_shared_ranges_and_keeps_history(self):
        self.fill()
        v=self.post('/validations',{**self.scope,'species_id':self.taxon['id']})
        q=self.client.get(f'/validations/{v["id"]}?item_id={v["history"][0]["id"]}').json()['question']
        conflict=self.client.post(f'/validations/{v["id"]}/answers',json=dict(item_id=q['id'],verdict='other',ranges=q['ranges']))
        self.assertEqual(conflict.json()['detail'],'annotation_conflict')
        # Without explicit ranges, "not heard" withdraws this species' reused marks and keeps the others.
        corrected=self.post(f'/validations/{v["id"]}/answers',dict(item_id=q['id'],verdict='other',version=q['version']))
        self.assertTrue(corrected['applicable'])
        with connect(self.root) as db:
            latest=json.loads(db.execute('SELECT ranges FROM annotation_answers WHERE item_id=? ORDER BY id DESC',(self.items[0]['id'],)).fetchone()[0])
        self.assertEqual({r['species_id'] for r in latest},{self.other['id'],None})
        self.assertEqual(len(self.client.get(f'/datasets/1/species/{self.taxon["id"]}').json()['annotations']),2)

    def test_unmodelled_ranges_in_results_and_portable_report(self):
        self.fill()
        data=self.client.get('/datasets/1/overview').json()
        row=next(r for r in data['species'] if r['species']['id']==self.other['id'])
        self.assertEqual(row['annotated'],1);self.assertEqual(row['models'],[])
        detail=self.client.get('/datasets/1/species/'+self.other['id']).json()
        self.assertEqual(len(detail['annotations']),1)
        result=self.post('/workflow-exports',{**self.scope,'revision':data['revision'],'species_ids':[self.other['id']], 'folder':str(self.root),'locale':'ja'})
        original=Path(result['path']).read_bytes()
        with zipfile.ZipFile(result['path']) as archive:
            manifest=json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['selected_species'][0]['id'],self.other['id'])
            self.assertEqual(manifest['manual_species'][0]['intervals'],1)
            ranges=json.loads(archive.read('annotations/ranges.json'))
            self.assertEqual(len(ranges),2)  # Named and unknown, no fabricated negative.
            self.assertTrue(all(r['audio_file'] in archive.namelist() and r['spectrogram'] in archive.namelist() for r in ranges))
            self.assertIn('テスト未対応種',archive.read('index.html').decode())
            self.assertIn('頻度・季節性は推定できません',archive.read('index.html').decode())
        self.fill(1)
        self.assertEqual(Path(result['path']).read_bytes(),original)

    def test_delete_accounts_for_annotations_and_stale_confirmation(self):
        self.fill(1)
        from management import preview_delete, delete
        plan=preview_delete(self.root,1)
        self.assertEqual(plan['annotations'],1)
        self.fill(1)
        with self.assertRaisesRegex(Exception,'management_changed'):delete(self.root,1,None,plan['revision'])
        plan=preview_delete(self.root,1);delete(self.root,1,None,plan['revision'])
        with connect(self.root) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM annotation_answers').fetchone()[0],0)
            self.assertFalse(db.execute('PRAGMA foreign_key_check').fetchall())
        self.assertTrue((self.root/'20240808_060000.wav').exists())

    def test_sampling_weights_prefix_and_missing_embeddings(self):
        vectors=np.array([[1.,0.]]*20+[[0.,1.]]*80,dtype=np.float32);vectors[-1]=0
        strata,_=strata_for(np.array(['low']*20+['difference']*80),vectors,10)
        order,rows=draw_plan(strata,30,11)
        self.assertEqual(strata[-1],'missing-embedding')
        self.assertEqual(order[:10],draw_plan(strata,10,11)[0])
        self.assertEqual(sum(r['weight']*r['sample'] for r in rows),100)
        self.assertEqual(len(set(order)),30)
        self.assertEqual(set(strata[order[:len(rows)]]),set(strata))

    def test_previous_calibration_and_recording_days_stay_out_of_holdout(self):
        from zoneinfo import ZoneInfo
        records={i:{'id':i,'recorded_at':f'2024-08-{8+i//2:02d}T00:00:00+00:00','timezone':'Asia/Tokyo'} for i in range(6)}
        a,b,unit=partition_records(records,ZoneInfo('Asia/Tokyo'),20,[0])
        self.assertEqual(unit,'local_day');self.assertTrue({0,1}.issubset(a));self.assertFalse(set(a)&set(b))
        for pair in [(0,1),(2,3),(4,5)]:self.assertTrue(set(pair).issubset(a) or set(pair).issubset(b))


if __name__=='__main__':unittest.main()
