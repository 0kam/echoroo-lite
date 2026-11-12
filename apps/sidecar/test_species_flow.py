"""Presence marks, one search per species, growing a species AI (with or without a search), and the search deletion cascade."""
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from server import create_app
from storage import connect

E0, E1 = np.eye(2, 1536, dtype='<f4')


class Session:
    def run(self, outputs, feed):
        return [E0[None]]


@contextmanager
def site():
    """Six days of 90-second recordings with Perch embeddings and scores every 5 seconds."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp).resolve()
        audio = root / 'audio'
        audio.mkdir()
        for day in range(1, 7):
            sf.write(audio / f'2024080{day}_060000.wav', np.full(90 * 1000, .1, dtype=np.float32), 1000)
        with TestClient(create_app(root, root, 'test'), headers={'Authorization': 'Bearer test'}) as client:
            client.post('/imports', json=dict(folder=str(audio), site_name='Site', dataset_name='Data', timezone='Asia/Tokyo', latitude=35, longitude=138))
            species = client.get('/species?q=ウグイス').json()[0]
            label = 'Horornis diphone_Japanese Bush Warbler'
            with connect(root) as db:
                job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'done',6)").lastrowid
                for rid in range(1, 7):
                    probs = np.tile([.3, .2, .2], 6)
                    path = root / f'perch-{rid}.npz'
                    np.savez(path, labels=['Horornis diphone', 'Other'], logits=np.log(np.column_stack([probs, 1 - probs])))
                    analysis = db.execute("INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,'perch',?,?)",
                        (job, rid, json.dumps({'model_sha256': 'perch-sha', 'candidate_filter': {'species': [label]}}), str(path))).lastrowid
                    for index in range(18):
                        segment = db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,?,?)', (analysis, index * 5, index * 5 + 5)).lastrowid
                        # Repeated separable examples for the small custom classifier.
                        vector = (E0 if index % 6 == 0 else E1) + np.eye(1, 1536, 10 + rid, dtype='<f4')[0] * .05
                        db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)', (segment, vector.tobytes()))
            yield root, client, species


class SpeciesFlowCheck(unittest.TestCase):
    def test_presence_list_merges_overlapping_native_windows(self):
        from presence import merge_candidates
        source={'id':1,'kind':'recording','path':'sound.wav','seconds':30}
        rows=[
            {'recording_id':1,'start_s':0,'end_s':3,'marked':False,'models':[{'engine':'birdnet'}],'source':source,'local_time':'a','run_id':None,'score':None},
            {'recording_id':1,'start_s':0,'end_s':5,'marked':True,'models':[{'engine':'perch'}],'source':source,'local_time':'a','run_id':None,'score':None},
            {'recording_id':1,'start_s':5,'end_s':10,'marked':False,'models':[{'engine':'perch'}],'source':source,'local_time':'b','run_id':None,'score':None},
        ]
        merged=merge_candidates(rows)
        self.assertEqual([(row['start_s'],row['end_s'],row['marked']) for row in merged],[(0,5,True),(5,10,False)])
        self.assertEqual([model['engine'] for model in merged[0]['models']],['birdnet','perch'])

    def test_custom_feature_engine_and_hiding_incompatible_models(self):
        for engine in ('perch','kinglet-s'):
            with self.subTest(engine=engine), site() as (root,client,species):
                sid=species['id']
                with connect(root) as db:
                    db.execute('UPDATE analyses SET engine=?',(engine,))
                data=json.loads((root/'models.json').read_text())
                data.update(main=engine,extra=[])
                (root/'models.json').write_text(json.dumps(data))
                clip=dict(source_kind='recording',source_id=1,start_s=0,end_s=5)
                with patch('search_runs.load_perch',return_value=(Session(),'perch-sha')), \
                        patch('search_runs.load_onnx',return_value=(Session(),'perch-sha')):
                    run=client.post('/search-runs',json=dict(dataset_id=1,species_id=sid,clips=[clip])).json()
                with connect(root) as db:
                    for label,condition in [('target','>.99'),('other','<.5')]:
                        ids=[r[0] for r in db.execute('SELECT segment_id FROM search_hits WHERE run_id=? AND similarity'+condition+' LIMIT 10',(run['id'],))]
                        for segment in ids:
                            client.post(f'/datasets/1/species/{sid}/labels',json={'segment_id':segment,'label':label})
                scope=dict(dataset_id=1,species_id=sid,start_date='2024-08-01',end_date='2024-08-06')
                response=client.post('/custom-models',json=scope)
                self.assertEqual(response.status_code,200,response.text)
                model=response.json()
                self.assertEqual(model['feature']['engine'],engine)
                self.assertEqual(model['feature']['spec'],f'{engine}_embedding_1536_l2_v1')
                self.assertTrue(model['applicable'])
                data['main']='kinglet-xs'
                (root/'models.json').write_text(json.dumps(data))
                self.assertFalse(client.get(f"/custom-models/{model['id']}").json()['applicable'])
                detail=client.get(f'/datasets/1/species/{sid}',params=scope)
                self.assertEqual(detail.status_code,200,detail.text)
                self.assertFalse(any(c['engine']=='custom' for c in detail.json()['checks']))
                data['main']=engine
                (root/'models.json').write_text(json.dumps(data))
                self.assertTrue(client.get(f"/custom-models/{model['id']}").json()['applicable'])

    def test_presence_search_custom_model_and_deletion(self):
        with site() as (root, client, species):
            sid = species['id']
            scope = {'dataset_id': 1, 'species_id': sid, 'start_date': '2024-08-01', 'end_date': '2024-08-06'}
            clip = {'source_kind': 'recording', 'source_id': 1, 'start_s': 0, 'end_s': 5}
            search = {'dataset_id': 1, 'species_id': sid, 'clips': [clip]}
            with patch('search_runs.load_perch', return_value=(Session(), 'perch-sha')), patch('search.load_perch', return_value=(Session(), 'perch-sha')):
                run = client.post('/search-runs', json=search)
                self.assertEqual(run.status_code, 200, run.text)
                run = run.json()
                self.assertEqual(client.post('/search-runs', json=search).json()['detail'], 'search_exists')
            # The search list: 5-second hits by similarity; its own reference interval is excluded.
            listed = client.get(f'/datasets/1/species/{sid}/presence', params={'source': 'search', **{k: scope[k] for k in ('start_date', 'end_date')}}).json()
            rows = listed['rows']
            self.assertEqual((listed['run_id'], len(rows)), (run['id'], 100))  # 107 hits, capped at the list size
            self.assertEqual([r['score'] for r in rows], sorted((r['score'] for r in rows), reverse=True))
            self.assertTrue(all(r['end_s'] - r['start_s'] == 5 for r in rows))
            mark = lambda row, marked: client.post('/presence/marks', json={'dataset_id': 1, 'species_id': sid, 'recording_id': row['recording_id'],
                'start_s': row['start_s'], 'end_s': row['end_s'], 'run_id': run['id'], 'marked': marked})
            self.assertEqual(mark(rows[0], True).json(), {'marked': True})
            mark(rows[1], True)
            mark(rows[1], False)
            presence = client.get(f'/datasets/1/species/{sid}', params=scope).json()['presence']
            self.assertEqual((presence['status'], len(presence['evidence'])), ('confirmed', 1))
            self.assertEqual(presence['evidence'][0]['origin'], 'mark')
            self.assertEqual(client.post('/presence/marks', json={'dataset_id': 1, 'species_id': sid, 'recording_id': 1, 'start_s': 0, 'end_s': 7,
                'run_id': None, 'marked': True}).json()['detail'], 'invalid_interval')
            mark(rows[0], False)
            self.assertEqual(client.post('/presence/misses', json={**scope, 'source': 'search', 'listened': 8}).json(), {'listened': 8})
            presence = client.get(f'/datasets/1/species/{sid}', params=scope).json()['presence']
            self.assertEqual((presence['status'], presence['miss']['listened']), ('not_found', 8))
            mark(rows[0], True)
            self.assertEqual(client.get(f'/datasets/1/species/{sid}', params=scope).json()['presence']['status'], 'confirmed')
            # The AI list interleaves each model's ranking and says which model flagged a sound.
            ai = client.get(f'/datasets/1/species/{sid}/presence', params={'source': 'ai', **{k: scope[k] for k in ('start_date', 'end_date')}}).json()
            self.assertTrue(ai['rows'] and all(r['end_s'] - r['start_s'] == 5 and r['models'][0]['engine'] == 'perch' for r in ai['rows']))
            # Species-AI material: the "this is its call" mark already counts as voice; 20 answers with three of each are needed.
            url, dates = f'/datasets/1/species/{sid}/custom-model', {k: scope[k] for k in ('start_date', 'end_date')}
            label = lambda segment, value: client.post(f'/datasets/1/species/{sid}/labels', json={'segment_id': segment, 'label': value})
            ready = client.get(url, params=dates).json()
            self.assertEqual((ready['examples']['positive_5s'], ready['examples']['other_answers'], ready['batch'], ready['model']), (1, 1, None, None))
            self.assertEqual((ready['can_train'], ready['needed'], ready['reasons']),
                             (False, 19, ['custom_more_answers_needed', 'custom_positive_examples_needed', 'custom_negative_examples_needed']))
            # Before a species AI: the batch comes from the search ranking, the top unanswered ranks first while voice is short.
            with connect(root) as db:
                ranked = [r[0] for r in db.execute('SELECT segment_id FROM search_hits WHERE run_id=? ORDER BY similarity DESC,segment_id', (run['id'],))]
            first = client.get(url, params={**dates, 'batch': True}).json()
            picked = [row['segment_id'] for row in first['batch']]
            self.assertEqual((first['source'], len(set(picked))), ('search', 10))
            self.assertEqual(picked, [row['segment_id'] for row in client.get(url, params={**dates, 'batch': True}).json()['batch']])
            self.assertEqual(picked[:3], ranked[1:4])  # rank 0 is the marked sound
            self.assertTrue(all(row['label'] is None and row['local_time'] and row['score'] is not None for row in first['batch']))
            self.assertFalse(set(picked) & {row['segment_id'] for row in client.get(url, params={**dates, 'batch': True, 'skip': picked}).json()['batch']})
            self.assertEqual(label(ranked[0] + 10 ** 6, 'target').json()['detail'], 'not_found')
            voice = [s for s in ranked[:6] if s != ranked[0]][:3]
            with connect(root) as db:
                silent = [r[0] for r in db.execute('SELECT h.segment_id FROM search_hits h WHERE h.run_id=? AND h.similarity<.5 ORDER BY h.segment_id LIMIT 3', (run['id'],))]
            for segment in voice:
                label(segment, 'target')
            for segment in silent:
                label(segment, 'other')
            label(silent[0], None)
            self.assertEqual(client.get(url, params=dates).json()['examples']['negative_5s'], 2)
            label(silent[0], 'other')
            label(voice[0], 'other')
            label(voice[0], 'target')  # a changed answer keeps its place in the list
            ready = client.get(url, params={**dates, 'batch': True}).json()
            self.assertEqual((ready['can_train'], ready['examples']['positive_5s'], ready['examples']['negative_5s'], ready['needed']), (False, 4, 3, 13))
            self.assertEqual(ready['reasons'], ['custom_more_answers_needed'])
            self.assertEqual(client.post('/custom-models', json=scope).json()['detail'], 'custom_more_answers_needed')
            self.assertEqual([row['segment_id'] for row in ready['answered']], [silent[0], silent[2], silent[1], voice[2], voice[1], voice[0]])
            self.assertFalse({row['segment_id'] for row in ready['batch']} & {*voice, *silent, ranked[0]})
            with connect(root) as db:
                more = [r[0] for r in db.execute('SELECT segment_id FROM search_hits WHERE run_id=? AND similarity<.5 AND segment_id NOT IN (SELECT value FROM json_each(?)) ORDER BY segment_id LIMIT 13',
                                                (run['id'], json.dumps(silent)))]
            for segment in more:
                label(segment, 'other')
            ready = client.get(url, params=dates).json()
            self.assertEqual((ready['can_train'], ready['needed'], ready['reasons']), (True, 0, []))
            model = client.post('/custom-models', json=scope)
            self.assertEqual(model.status_code, 200, model.text)
            model = model.json()
            self.assertEqual((model['applicable'], model['run_id'], model['round'], model['training']['positive_5s'], model['inference']['scored_5s']), (True, run['id'], 1, 4, 108))
            detail = client.get(f'/datasets/1/species/{sid}', params={**scope, 'engine': 'custom', 'model_id': model['id']}).json()
            self.assertEqual(detail['selected'], f"custom:{model['id']}")
            self.assertEqual(detail['report']['threshold'], .5)
            self.assertEqual(detail['report']['summaries'][0]['eligible_windows'], 108)
            ai = client.get(f'/datasets/1/species/{sid}/presence', params={'source': 'ai', **{k: scope[k] for k in ('start_date', 'end_date')}}).json()
            self.assertEqual({m['engine'] for r in ai['rows'] for m in r['models']}, {'perch', 'custom'})
            # The custom model is checked like any other model, without the windows it learned from.
            check = client.post('/validations', json={**scope, 'model': 'custom', 'model_id': model['id']})
            self.assertEqual(check.status_code, 200, check.text)
            check = check.json()
            with connect(root) as db:
                training = {(e['recording_id'], e['start_s'], e['end_s']) for e in json.loads(db.execute('SELECT snapshot FROM custom_models').fetchone()[0])['training']['examples']}
                items = {tuple(r) for r in db.execute('SELECT recording_id,start_s,end_s FROM validation_items WHERE session_id=?', (check['id'],))}
            self.assertFalse(items & training)
            heard = lambda question: 'target' if question['start_s'] % 30 == 0 else 'other'
            while check['question']:
                check = client.post(f"/validations/{check['id']}/answers", json={'item_id': check['question']['id'], 'verdict': heard(check['question'])}).json()
            self.assertIn(check['status'], ('decided', 'undecided'))
            reported = client.get(f'/datasets/1/species/{sid}', params={**scope, 'engine': 'custom', 'model_id': model['id']}).json()['report']
            summary = reported['summaries'][0]
            self.assertEqual((summary['density'], summary['window_s'], summary['model_sha256']),
                             (check['results']['custom']['density'], 5, model['artifact_sha256']))
            self.assertEqual(summary['validation_status'], check['status'])
            perch_check = client.post('/validations', json={**scope, 'model': 'perch'}).json()
            with connect(root) as db:
                custom_windows = {tuple(r) for r in db.execute('SELECT recording_id,start_s,end_s FROM validation_items WHERE session_id=?',(check['id'],))}
                perch_windows = {tuple(r) for r in db.execute('SELECT recording_id,start_s,end_s FROM validation_items WHERE session_id=?',(perch_check['id'],))}
            self.assertEqual(perch_check['counts']['answered'], len(custom_windows & perch_windows))
            self.assertGreater(perch_check['counts']['answered'], 0)
            client.post(f"/validations/{perch_check['id']}/delete")
            self.assertEqual(client.get(f"/validations/{check['id']}").json()['status'],check['status'])
            # 5-second check answers are material: "heard" as voice, "not heard" as no voice; labels given here win.
            ready = client.get(url, params=dates).json()
            with connect(root) as db:
                pieces = {tuple(r) for r in db.execute('''SELECT s.id,a.verdict FROM segments s JOIN analyses n ON n.id=s.analysis_id JOIN validation_items i
                    ON i.recording_id=n.recording_id AND s.start_s=i.start_s AND s.end_s=i.end_s JOIN window_answers a
                    ON a.recording_id=i.recording_id AND a.start_s=i.start_s AND a.end_s=i.end_s WHERE i.session_id=? AND a.verdict IN ('target','other')
                    AND s.id NOT IN (SELECT segment_id FROM model_labels)''', (check['id'],))}
            self.assertTrue({'target', 'other'} <= {verdict for _, verdict in pieces})
            self.assertEqual(ready['examples']['other_answers'], len({segment for segment, _ in pieces} | {ranked[0]}))
            self.assertEqual(ready['examples']['positive_5s'], 4 + len({segment for segment, verdict in pieces if verdict == 'target'} - {ranked[0]}))
            # Making it again replaces it in place; its finished check no longer applies and a new one starts.
            label(silent[1], 'target')
            again = client.post('/custom-models', json=scope)
            self.assertEqual(again.status_code, 200, again.text)
            again = again.json()
            self.assertEqual((again['id'], again['round'], again['applicable']), (model['id'], 2, True))
            self.assertNotEqual(again['artifact_sha256'], model['artifact_sha256'])
            self.assertEqual(again['training']['positive_from_checks'], len({segment for segment, verdict in pieces if verdict == 'target'} - {ranked[0]}))
            with connect(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM custom_models').fetchone()[0], 1)
            self.assertEqual([p.name for p in (root / 'custom-models').iterdir()], [str(model['id'])])
            stale = client.get(f"/validations/{check['id']}").json()
            self.assertEqual((stale['applicable'], stale['status'], stale['model_reason']), (False, 'stale', 'custom_model_rebuilt'))
            detail = client.get(f'/datasets/1/species/{sid}', params={**scope, 'engine': 'custom', 'model_id': model['id']}).json()
            self.assertEqual(next(c for c in detail['checks'] if c['engine'] == 'custom')['validation']['model_reason'], 'custom_model_rebuilt')
            self.assertIn('check', {e['origin'] for e in detail['presence']['evidence']})  # its "heard" answers still count
            fresh = client.post('/validations', json={**scope, 'model': 'custom', 'model_id': model['id']}).json()
            self.assertNotEqual(fresh['id'], check['id'])
            with connect(root) as db:
                training = {(e['recording_id'], e['start_s'], e['end_s']) for e in json.loads(db.execute('SELECT snapshot FROM custom_models').fetchone()[0])['training']['examples']}
                items = {tuple(r) for r in db.execute('SELECT recording_id,start_s,end_s FROM validation_items WHERE session_id=?', (fresh['id'],))}
            self.assertFalse(items & training)
            with_model = client.get(url, params={**dates, 'batch': True}).json()
            self.assertEqual((with_model['source'], len(with_model['batch'])), ('custom', 10))
            self.assertFalse({row['segment_id'] for row in with_model['batch']} & {*voice, *silent, ranked[0], *(segment for segment, _ in pieces)})
            # Deleting the search removes its marks and the species AI last made with it, with that AI's checks; labels stay.
            preview = client.get(f"/search-runs/{run['id']}/delete-preview").json()
            self.assertEqual({k: preview[k] for k in ('marks', 'custom_models', 'checks')}, {'marks': 1, 'custom_models': 1, 'checks': 2})
            self.assertNotIn('labels', preview)
            self.assertEqual(client.post(f"/search-runs/{run['id']}/delete").status_code, 200)
            with connect(root) as db:
                counts = [db.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in
                          ('search_runs', 'search_hits', 'presence_marks', 'custom_models', 'validation_sessions', 'window_answers', 'presence_misses')]
                self.assertEqual(db.execute('SELECT COUNT(*) FROM model_labels').fetchone()[0], 19)
            self.assertEqual(counts, [0] * 7)
            self.assertFalse((root / 'custom-models' / str(model['id'])).exists())
            self.assertEqual(client.get(f'/datasets/1/species/{sid}', params=scope).json()['presence']['status'], 'unchecked')
            with patch('search_runs.load_perch', return_value=(Session(), 'perch-sha')), patch('search.load_perch', return_value=(Session(), 'perch-sha')):
                self.assertEqual(client.post('/search-runs', json=search).status_code, 200)


class GrowWithoutSearchCheck(unittest.TestCase):
    def test_builtin_batches_materials_and_rebuild(self):
        with site() as (root, client, species):
            sid = species['id']
            dates = {'start_date': '2024-08-01', 'end_date': '2024-08-06'}
            scope = {'dataset_id': 1, 'species_id': sid, **dates}
            url = f'/datasets/1/species/{sid}/custom-model'
            label = lambda segment, value: client.post(f'/datasets/1/species/{sid}/labels', json={'segment_id': segment, 'label': value})
            # No search: the main AI's score ranks the batch; the highest-scored come first while voice is short.
            first = client.get(url, params={**dates, 'batch': True}).json()
            self.assertEqual((first['source'], first['engine'], len(first['batch'])), ('builtin', 'perch', 10))
            self.assertEqual(first['batch'], client.get(url, params={**dates, 'batch': True}).json()['batch'])
            # Without dates in the link, the page still learns the period to make the AI for.
            self.assertEqual({k: client.get(url).json()[k] for k in dates}, dates)
            self.assertTrue(all(abs(row['score'] - .3) < 1e-6 for row in first['batch'][:3]))
            self.assertLess(min(row['score'] for row in first['batch'][3:]), .25)  # boundary picks reach down the ranking
            # A species the built-in AIs do not list, without a search, has nothing to rank by.
            crow = client.get('/species?q=ハシブトガラス').json()[0]['id']
            none = client.get(f'/datasets/1/species/{crow}/custom-model', params={**dates, 'batch': True}).json()
            self.assertEqual((none['source'], none['batch']), (None, []))
            # Materials without a search: a "this is its call" mark and check answers ("heard" = voice).
            self.assertEqual(client.post('/presence/marks', json={'dataset_id': 1, 'species_id': sid, 'recording_id': 2, 'start_s': 0,
                                                                  'end_s': 5, 'run_id': None, 'marked': True}).json(), {'marked': True})
            check = client.post('/validations', json={**scope, 'model': 'perch'}).json()
            while check['question']:
                verdict = 'target' if check['question']['start_s'] % 30 == 0 else 'other'
                check = client.post(f"/validations/{check['id']}/answers", json={'item_id': check['question']['id'], 'verdict': verdict}).json()
            with connect(root) as db:
                heard = {tuple(r) for r in db.execute("SELECT recording_id,start_s FROM window_answers WHERE verdict='target'")} | {(2, 0.0)}
                not_heard = {tuple(r) for r in db.execute("SELECT recording_id,start_s FROM window_answers WHERE verdict='other'")} - heard
            ready = client.get(url, params={**dates, 'batch': True}).json()
            self.assertEqual((ready['examples']['positive_5s'], ready['examples']['negative_5s'], ready['examples']['other_answers']),
                             (len(heard), len(not_heard), len(heard) + len(not_heard)))
            self.assertTrue(ready['can_train'] and not ready['answered'])
            self.assertFalse({(row['recording_id'], row['start_s']) for row in ready['batch']} & (heard | not_heard))
            model = client.post('/custom-models', json=scope)
            self.assertEqual(model.status_code, 200, model.text)
            model = model.json()
            self.assertEqual((model['run_id'], model['round'], model['training']['positive_from_presence']), (None, 1, 1))
            # Nothing new since it was made: making it again is refused, so its round and check stay.
            ready = client.get(url, params=dates).json()
            self.assertEqual((ready['can_train'], ready['reasons'], ready['needed']), (False, ['custom_no_new_answers'], 0))
            self.assertEqual(client.post('/custom-models', json=scope).json()['detail'], 'custom_no_new_answers')
            self.assertEqual(client.get(f"/custom-models/{model['id']}").json()['round'], 1)
            # With a species AI: its scores rank the batch; rounds never repeat and answered sounds never return.
            rounds = [client.get(url, params={**dates, 'batch': True}).json()]
            rounds.append(client.get(url, params={**dates, 'batch': True, 'skip': [row['segment_id'] for row in rounds[0]['batch']]}).json())
            self.assertEqual({r['source'] for r in rounds}, {'custom'})
            picked = [[row['segment_id'] for row in r['batch']] for r in rounds]
            self.assertEqual(len(set(picked[0])), 10)
            self.assertFalse(set(picked[0]) & set(picked[1]))
            label(picked[0][0], 'other')
            again = client.get(url, params={**dates, 'batch': True}).json()
            self.assertNotIn(picked[0][0], [row['segment_id'] for row in again['batch']])
            self.assertEqual([row['segment_id'] for row in again['answered']], [picked[0][0]])
            custom = client.post('/validations', json={**scope, 'model': 'custom', 'model_id': model['id']}).json()
            rebuilt = client.post('/custom-models', json=scope).json()
            self.assertEqual((rebuilt['id'], rebuilt['round']), (model['id'], 2))
            self.assertEqual(client.get(f"/validations/{custom['id']}").json()['model_reason'], 'custom_model_rebuilt')
            self.assertNotEqual(client.post('/validations', json={**scope, 'model': 'custom', 'model_id': model['id']}).json()['id'], custom['id'])
            # A search made later did not make this AI, so deleting it neither warns about the AI nor deletes it.
            with patch('search_runs.load_perch', return_value=(Session(), 'perch-sha')):
                run = client.post('/search-runs', json={'dataset_id': 1, 'species_id': sid,
                    'clips': [{'source_kind': 'recording', 'source_id': 1, 'start_s': 0, 'end_s': 5}]}).json()
            # Nor about marks: the "this is its call" mark above was not made on this search's results.
            preview = client.get(f"/search-runs/{run['id']}/delete-preview").json()
            self.assertEqual((preview['custom_models'], preview['marks']), (0, 0))
            self.assertEqual(client.post(f"/search-runs/{run['id']}/delete").status_code, 200)
            self.assertTrue(client.get(f"/custom-models/{model['id']}").json()['applicable'])
            self.assertEqual(len(client.get(url, params=dates).json()['answered']), 1)

    def test_0_2_0_catalogue_keeps_labels_and_models(self):
        from storage import SCHEMA_VERSION, columns, initialize
        with site() as (root, client, species):
            sid = species['id']
            with patch('search_runs.load_perch', return_value=(Session(), 'perch-sha')):
                run = client.post('/search-runs', json={'dataset_id': 1, 'species_id': sid,
                    'clips': [{'source_kind': 'recording', 'source_id': 1, 'start_s': 0, 'end_s': 5}]}).json()
            with connect(root) as db:
                # The 0.2.0 (schema 6) definitions of the two changed tables.
                db.executescript('''DROP TABLE model_labels; DROP TABLE custom_models;
                    CREATE TABLE model_labels (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, run_id INTEGER NOT NULL REFERENCES search_runs,
                        segment_id INTEGER NOT NULL REFERENCES segments,
                        label TEXT NOT NULL CHECK(label IN ('target','other')), UNIQUE(run_id,segment_id));
                    CREATE TABLE custom_models (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id INTEGER NOT NULL REFERENCES datasets,
                        species_id TEXT NOT NULL REFERENCES species, run_id INTEGER NOT NULL REFERENCES search_runs,
                        snapshot TEXT NOT NULL, artifact_path TEXT NOT NULL, artifact_sha256 TEXT NOT NULL,
                        scores_path TEXT NOT NULL, scores_sha256 TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);''')
                db.executemany('INSERT INTO model_labels(run_id,segment_id,label) VALUES(?,?,?)', [(run['id'], 1, 'target'), (run['id'], 2, 'other')])
                db.execute('''INSERT INTO custom_models(id,dataset_id,species_id,run_id,snapshot,artifact_path,artifact_sha256,scores_path,scores_sha256)
                    VALUES(5,1,?,?,'{"a":1}','m','x','s','y')''', (sid, run['id']))
                db.execute("UPDATE sqlite_sequence SET seq=7 WHERE name='custom_models'")  # models 6 and 7 were deleted
                db.execute('PRAGMA user_version=6')
            self.assertTrue(initialize(root))
            with connect(root) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                self.assertEqual([tuple(r) for r in db.execute('SELECT species_id,segment_id,label FROM model_labels ORDER BY id')],
                                 [(sid, 1, 'target'), (sid, 2, 'other')])
                self.assertEqual(tuple(db.execute('SELECT id,run_id,snapshot,artifact_sha256 FROM custom_models').fetchone()), (5, run['id'], '{"a":1}', 'x'))
                self.assertEqual((columns(db, 'custom_models')['run_id'], columns(db, 'model_labels_v6'), columns(db, 'custom_models_v6')), (0, {}, {}))
                new = db.execute('''INSERT INTO custom_models(dataset_id,species_id,run_id,snapshot,artifact_path,artifact_sha256,scores_path,scores_sha256)
                    VALUES(1,?,NULL,'{}','','','','')''', (sid,)).lastrowid
                self.assertEqual(new, 8)
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertTrue(initialize(root))  # opening again changes nothing
            with connect(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM model_labels').fetchone()[0], 2)
                db.execute('DELETE FROM custom_models')  # the stand-in rows have no real snapshot
            ready = client.get(f'/datasets/1/species/{sid}/custom-model').json()
            self.assertEqual((ready['examples']['positive_5s'], ready['examples']['negative_5s']), (1, 1))


class BatchChoiceCheck(unittest.TestCase):
    def test_species_ai_batch(self):
        from custom_models import boundary_score, model_picks
        from scipy.special import expit
        # Bunched scores as measured on real data (max about 0.75, median about 0.28): 0.5 is not the boundary.
        rng = np.random.default_rng(0)
        scores = np.clip(expit(rng.normal(-1, .35, 5000)), 0, .76)
        scored = [(segment, float(score)) for segment, score in enumerate(scores)]
        by_score = sorted(scored, key=lambda p: -p[1])
        # Answers: "voice" on sounds ranked about 100-150, "no voice" below rank 300, and one mislabelled each way.
        known = {**{by_score[r][0]: 'target' for r in range(100, 150, 5)}, **{by_score[r][0]: 'other' for r in range(300, 4000, 300)},
                 by_score[3000][0]: 'target', by_score[20][0]: 'other', by_score[5][0]: None}
        cut = boundary_score([(dict(scored)[s], label) for s, label in known.items() if label])
        self.assertTrue(by_score[300][1] < cut < by_score[145][1] < .5)  # in the gap, ignoring the two wrong answers
        picks = model_picks(scored, known)
        self.assertEqual(picks, model_picks(reversed(scored), known))
        chosen = {segment for segment, _ in picks}
        self.assertEqual(len(chosen), 10)
        self.assertFalse(chosen & set(known))
        self.assertEqual([score for _, score in picks], sorted((score for _, score in picks), reverse=True))
        free = [p for p in by_score if p[0] not in known]
        # The two highest and two lowest unanswered really are the extremes.
        self.assertEqual(set(picks[:2]), set(free[:2]))
        self.assertEqual(set(picks[-2:]), set(free[-2:]))
        # Six from the 24 ranks centred on the boundary rank, spread out rather than side by side.
        rank = sum(score >= cut for _, score in free[2:-2])
        middle = sorted(picks[2:-2])
        self.assertTrue(set(middle) <= set(free[2:-2][rank - 12:rank + 12]))
        self.assertGreater(middle[-1][0] - middle[0][0], 2500)
        # The next round repeats nothing; a plateau of equal scores is still spread, deterministically.
        self.assertFalse(chosen & {segment for segment, _ in model_picks(scored, {**known, **dict.fromkeys(chosen)})})
        flat = [(segment, .5) for segment in range(1000)]
        self.assertEqual([segment for segment, _ in model_picks(flat, {})], [0, 1, 85, 251, 417, 583, 749, 915, 998, 999])
        self.assertEqual(model_picks(scored[:4], {}), sorted(scored[:4], key=lambda p: -p[1]))

    def test_boundary_score(self):
        from custom_models import boundary_score
        self.assertAlmostEqual(boundary_score([(.6, 'target'), (.7, 'target'), (.2, 'other'), (.3, 'other')]), .45)  # the gap
        self.assertIsNone(boundary_score([(.6, 'target'), (.7, 'target')]))
        # One wrong "no voice" among high scores does not move it; balanced over the kinds.
        self.assertAlmostEqual(boundary_score([(.6, 'target'), (.62, 'target'), (.64, 'target'), (.63, 'other'), (.3, 'other'), (.31, 'other')]), .455)

    def test_batch_before_species_ai(self):
        from custom_models import bracket_ranks
        voice_short = bracket_ranks(1000, {0: 'target'}, True)
        self.assertEqual(voice_short, bracket_ranks(1000, {0: 'target'}, True))
        self.assertEqual((len(set(voice_short)), voice_short[:3]), (10, [1, 2, 3]))
        self.assertEqual(bracket_ranks(1000, {0: 'target', 2: 'other'}, True)[:3], [1, 3, 4])
        known = {0: 'target', 5: 'target', 900: 'other'}
        enough = bracket_ranks(1000, known, False)
        self.assertTrue(len(enough) == 10 and all(5 < r < 900 for r in enough))
        # Sounds shown and passed over are left out like answered ones.
        self.assertFalse(set(enough) & set(bracket_ranks(1000, {**known, **dict.fromkeys(enough)}, False)))
        self.assertEqual(bracket_ranks(1000, {0: 'target', 1: None, 2: None, 3: None}, True)[:3], [4, 5, 6])

    def test_builtin_batch_skips_scores_of_zero(self):
        """With built-in scores most sounds score 0.00; the first batch is chosen among the scored ones."""
        from custom_models import bracket_ranks, scored_span
        scores = [.9 - i * .004 for i in range(200)] + [0.0] * 8000
        span = scored_span(scores)
        self.assertEqual(span, 210)
        first = bracket_ranks(span, {}, True)
        self.assertEqual(first[:3], [0, 1, 2])
        self.assertGreaterEqual(sum(scores[r] > 0 for r in first), 9)
        self.assertEqual(scored_span([.5] * 30), 30)

    def test_first_twenty_answers_reach_both_kinds(self):
        """Two sets of 10 before the first AI: a rare species still gets 3 "voice", a common one 3 "no voice"."""
        from custom_models import MIN_CLASS_EXAMPLES, bracket_ranks
        for name, voice in (('rare', {0, 2, 5, 9, 14}), ('common', set(range(1300)))):
            with self.subTest(name):
                known = {}
                for _ in range(2):
                    need_voice = sum(label == 'target' for label in known.values()) < MIN_CLASS_EXAMPLES
                    for rank in bracket_ranks(8640, known, need_voice):
                        known[rank] = 'target' if rank in voice else 'other'
                labels = list(known.values())
                self.assertEqual(len(labels), 20)
                self.assertGreaterEqual(labels.count('target'), MIN_CLASS_EXAMPLES)
                self.assertGreaterEqual(labels.count('other'), MIN_CLASS_EXAMPLES)


class BoundaryRanksCheck(unittest.TestCase):
    def test_choice(self):
        from custom_models import boundary_ranks
        wide = boundary_ranks(10000, {}, 10, 0)
        self.assertEqual(wide, boundary_ranks(10000, {}, 10, 0))
        self.assertEqual(len(set(wide)), 10)
        self.assertTrue(all(9 < r < 10000 for r in wide))
        # Centre-weighted: more picks in the middle half than in the outer quarters together.
        self.assertGreater(sum(2500 <= r < 7500 for r in wide), sum(r < 2500 or r >= 7500 for r in wide))
        # Labels shrink the bracket; labelled and visible rows are never picked.
        known = {100: 'target', 5: 'target', 300: 'other', 150: 'other', 120: 'target', 9000: 'other'}
        narrow = boundary_ranks(10000, known, 10, 0)
        self.assertTrue(all(120 < r < 150 for r in narrow) and len(set(narrow)) == 10)
        self.assertEqual(boundary_ranks(10000, {**known, 125: 'other'}, 10, 0), [121, 122, 123, 124])
        # Rounds differ while labels stay the same.
        rounds = [tuple(boundary_ranks(10000, known, 10, k)) for k in range(5)]
        self.assertTrue(all(a != b for a, b in zip(rounds, rounds[1:])))
        self.assertEqual(set(boundary_ranks(30, {}, 10, 1)) & set(boundary_ranks(30, {}, 10, 0)), set())
        # Small and empty brackets.
        self.assertEqual(boundary_ranks(15, {}, 10, 0), [10, 11, 12, 13, 14])
        self.assertEqual(boundary_ranks(40, {20: 'target', 21: 'other'}, 10, 0), [15, 16, 17, 18, 19, 22, 23, 24, 25, 26])
        self.assertEqual(boundary_ranks(40, {20: 'target', 21: 'other'}, 10, 1), [10, 11, 12, 13, 14, 27, 28, 29, 30, 31])
        self.assertEqual(boundary_ranks(10, {}, 10, 0), [])


if __name__ == '__main__':
    unittest.main()
