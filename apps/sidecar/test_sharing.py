"""Passing things around: other sites' answers as material, a species AI as one file, answers as a CSV table."""
import csv
import json
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

import labels
from storage import connect
from test_species_flow import E0, E1, no_tests, site

SCOPE = {'start_date': '2024-08-01', 'end_date': '2024-08-06'}


def answer(root, sid, recordings):
    """Per recording: heard at 0, 30 and 60 s (the separable voice pieces), not heard at 5-25 s."""
    with connect(root) as db:
        for recording in recordings:
            for start in (0, 30, 60):
                labels.put(db, sid, recording, start, start + 5, 'present', 'training')
            for start in (5, 10, 15, 20, 25):
                labels.put(db, sid, recording, start, start + 5, 'absent', 'training')


def second_site(root, client):
    """Another site with two analysed recordings (ids 7 and 8) of the same main AI."""
    audio = root / 'audio2'
    audio.mkdir()
    for day in (1, 2):
        sf.write(audio / f'2024090{day}_060000.wav', np.full(90 * 1000, .1, dtype=np.float32), 1000)
    made = client.post('/imports', json=dict(folder=str(audio), site_name='Other', dataset_name='Data 2', timezone='Asia/Tokyo', latitude=36, longitude=139))
    assert made.status_code == 200, made.text
    with connect(root) as db:
        dataset = db.execute('SELECT MAX(id) FROM datasets').fetchone()[0]
        job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(?,'done',2)", (dataset,)).lastrowid
        recordings = [r[0] for r in db.execute('SELECT id FROM recordings WHERE dataset_id=? ORDER BY id', (dataset,))]
        for rid in recordings:
            probs = np.tile([.3, .2, .2], 6)
            path = root / f'perch-{rid}.npz'
            np.savez(path, labels=['Horornis diphone', 'Other'], logits=np.log(np.column_stack([probs, 1 - probs])))
            analysis = db.execute("INSERT INTO analyses(job_id,recording_id,engine,metadata,scores_path) VALUES(?,?,'perch',?,?)",
                (job, rid, json.dumps({'model_sha256': 'perch-sha', 'candidate_filter': {'species': ['Horornis diphone_Japanese Bush Warbler']}}), str(path))).lastrowid
            for index in range(18):
                segment = db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(?,?,?)', (analysis, index * 5, index * 5 + 5)).lastrowid
                vector = (E0 if index % 6 == 0 else E1) + np.eye(1, 1536, 30 + rid, dtype='<f4')[0] * .05
                db.execute('INSERT INTO vectors(rowid,embedding) VALUES(?,?)', (segment, vector.tobytes()))
    return dataset, recordings


class SharingCheck(unittest.TestCase):
    def test_other_sites_answers_become_material_only_when_chosen(self):
        with site() as (root, client, species):
            sid = species['id']
            no_tests(root, sid)
            dataset, recordings = second_site(root, client)
            answer(root, sid, [1])            # 8 answers here: too few alone
            answer(root, sid, recordings)     # 16 at the other site
            url = f'/datasets/1/species/{sid}/custom-model'
            state = client.get(url, params=SCOPE).json()
            self.assertEqual((state['can_train'], state['needed'], state['sharing']),
                             (False, 12, {'all_sites': False, 'positive_5s': 6, 'negative_5s': 10, 'sites': 1}))
            self.assertEqual(client.post('/custom-models/sharing', json={'dataset_id': 1, 'species_id': sid, 'all_sites': True}).json(), {'all_sites': True})
            state = client.get(url, params=SCOPE).json()
            self.assertEqual((state['can_train'], state['needed'], state['examples']['positive_5s'], state['sharing']['all_sites']), (True, 0, 3, True))
            model = client.post('/custom-models', json={'dataset_id': 1, 'species_id': sid, **SCOPE}).json()
            self.assertEqual((model['training']['positive_5s'], model['training']['negative_5s'], model['training']['other_sites']),
                             (9, 15, {'positive_5s': 6, 'negative_5s': 10, 'sites': 1}))
            # Only this site's sounds are scored; nothing new to make it from afterwards.
            self.assertEqual(model['inference']['scored_5s'], 108)
            self.assertIn('custom_no_new_answers', client.get(url, params=SCOPE).json()['reasons'])
            # The other site is untouched and does not use this site's answers unless it is set to.
            other = client.get(f'/datasets/{dataset}/species/{sid}/custom-model').json()
            self.assertEqual((other['sharing']['all_sites'], other['examples']['positive_5s'], other['sharing']['positive_5s']), (False, 6, 3))

    def test_a_species_ai_goes_out_as_one_file_and_comes_in_as_a_new_ai(self):
        with site() as (root, client, species):
            sid = species['id']
            no_tests(root, sid)
            answer(root, sid, [1, 2, 3])
            made = client.post('/custom-models', json={'dataset_id': 1, 'species_id': sid, **SCOPE}).json()
            out = root / 'out'; out.mkdir()
            path = client.post(f"/custom-models/{made['id']}/export", json={'folder': str(out), 'hide_site': True}).json()['path']
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            self.assertEqual((data['format'], data['version'], data['species']['scientific_name'], data['site'], data['training']['positive_5s']),
                             ('echoroo-species-ai', 2, 'Horornis diphone', None, 9))
            # The material goes as sound descriptions (no audio, no file names): 24 sounds stay small.
            self.assertEqual((len(data['material']['voice']), sum(data['material']['voice']), set(data['material']['sites'])), (24, 9, {None}))
            self.assertNotIn('20240801', json.dumps(data['material']))
            self.assertLess(Path(path).stat().st_size, 200000)
            seen = client.post('/custom-models/import', json={'dataset_id': 1, 'path': path, 'inspect': True}).json()
            self.assertEqual((seen['species']['id'], seen['warning'], seen['training'], seen['site']), (sid, None, {'positive_5s': 9, 'negative_5s': 15, 'other_sites': None}, None))
            taken = client.post('/custom-models/import', json={'dataset_id': 1, 'path': path, 'species_id': sid, **SCOPE}).json()
            self.assertNotEqual(taken['id'], made['id'])
            self.assertEqual((taken['applicable'], taken['imported']['warning'], taken['inference']['scored_5s']), (True, None, 108))
            # Same weights, same scores; the imported AI is the current one and the one made here comes back when it is deleted.
            from custom_models import score_windows
            self.assertEqual([round(w['score'], 6) for w in score_windows(root, taken['id'])], [round(w['score'], 6) for w in score_windows(root, made['id'])])
            url = f'/datasets/1/species/{sid}/custom-model'
            self.assertEqual(client.get(url, params=SCOPE).json()['model']['id'], taken['id'])
            # Making the AI again starts a new one beside the imported file's.
            answer(root, sid, [4])
            again = client.post('/custom-models', json={'dataset_id': 1, 'species_id': sid, **SCOPE}).json()
            self.assertNotIn(again['id'], (taken['id'],))
            self.assertIsNone(again['imported'])
            self.assertEqual(client.post(f"/custom-models/{taken['id']}/delete", json={}).status_code, 200)

            # A file from another AI of the family is taken with a warning; a newer or broken file is refused.
            other = {**data, 'feature': {**data['feature'], 'engine': 'kinglet-s', 'model_sha256': 'x', 'spec': 'kinglet-s_embedding_1536_l2_v1'}}
            (out / 'other.json').write_text(json.dumps(other), encoding='utf-8')
            self.assertEqual(client.post('/custom-models/import', json={'dataset_id': 1, 'path': str(out / 'other.json'), 'inspect': True}).json()['warning'], 'ai_file_other_engine')
            for name, changed, error in (('newer', {**data, 'version': 3}, 'ai_file_newer'), ('engine', {**data, 'feature': {**data['feature'], 'engine': 'birdnet', 'spec': 'birdnet_embedding_1536_l2_v1'}}, 'ai_file_invalid'), ('short', {**data, 'weights': 'AAAA'}, 'ai_file_invalid'),
                                         ('species', {**data, 'species': {'scientific_name': 'Nemo nemo'}}, 'ai_file_species_unknown')):
                (out / f'{name}.json').write_text(json.dumps(changed), encoding='utf-8')
                refused = client.post('/custom-models/import', json={'dataset_id': 1, 'path': str(out / f'{name}.json'), 'inspect': True})
                self.assertEqual((refused.status_code, refused.json()['detail']), (400, error))
            (out / 'broken.json').write_text('{', encoding='utf-8')
            self.assertEqual(client.post('/custom-models/import', json={'dataset_id': 1, 'path': str(out / 'broken.json')}).json()['detail'], 'ai_file_invalid')

    def test_an_ai_grows_at_another_site_and_comes_back_as_a_version(self):
        with site() as (root, client, species):
            sid = species['id']
            # Site A: test sounds in recording 5, material in recordings 1-3, an AI made from them.
            answer(root, sid, [5])
            with connect(root) as db:
                db.execute('INSERT INTO test_recordings(site_id,species_id,recording_id) VALUES(1,?,5)', (sid,))
                db.execute("INSERT INTO test_phases(site_id,species_id,state) VALUES(1,?,'fixed')", (sid,))
            answer(root, sid, [1, 2, 3])
            a = client.post('/custom-models', json={'dataset_id': 1, 'species_id': sid, **SCOPE}).json()
            self.assertEqual((a['training']['positive_5s'], a['training']['negative_5s']), (9, 15))
            dataset, recordings = second_site(root, client)
            other = client.get(f'/datasets/{dataset}/species/{sid}/ai-versions').json()
            self.assertEqual([(o['id'], o['site'], o['round'], o['score']) for o in other['others']],
                             [(a['id'], 'Site', 1, {'heard': 3, 'not_heard': 5, 'found': 3, 'false': 0})])
            # Site B tries it: a copy, and the test sounds there are still to be made, drawn by the copy's score.
            tried = client.post(f"/custom-models/{a['id']}/use", json={'dataset_id': dataset}).json()
            self.assertEqual((tried['imported']['from_model'], tried['imported']['site'], tried['imported']['round'], tried['imported']['file']), (a['id'], 'Site', 1, None))
            url = f'/datasets/{dataset}/species/{sid}/custom-model'
            state = client.get(url, params={'batch': True}).json()
            self.assertEqual((state['phase'], state['model']['id'], state['source'], state['inherited']['positive_5s'], state['can_train']), ('collecting', tried['id'], 'custom', 9, False))
            self.assertEqual(client.post(f"/custom-models/{a['id']}/use", json={'dataset_id': dataset}).json()['id'], tried['id'])  # the same copy again
            answer(root, sid, [recordings[0]])
            with connect(root) as db:
                db.execute('INSERT INTO test_recordings(site_id,species_id,recording_id) SELECT site_id,?,? FROM datasets WHERE id=?', (sid, recordings[0], dataset))
            self.assertEqual(client.post(f'/datasets/{dataset}/species/{sid}/test-phase').json(), {'state': 'fixed'})
            state = client.get(url).json()
            # Growing from the copy takes A's material; nothing of B's yet, so nothing new to make it from.
            self.assertEqual((state['inherited'], state['can_train'], state['reasons']),
                             ({'positive_5s': 9, 'negative_5s': 15, 'sites': ['Site']}, False, ['custom_no_new_answers']))
            answer(root, sid, [recordings[1]])
            b = client.post('/custom-models', json={'dataset_id': dataset, 'species_id': sid, 'start_date': state['start_date'], 'end_date': state['end_date']}).json()
            self.assertNotEqual(b['id'], tried['id'])
            self.assertEqual((b['round'], b['training']['positive_5s'], b['training']['inherited']['positive_5s']), (1, 12, 9))
            # Back at A: B's AI grew from A's, tried on A's test sounds beside A's own; set aside, it is not offered again.
            here = client.get(f'/datasets/1/species/{sid}/ai-versions', params=SCOPE).json()
            self.assertEqual((here['current_id'], here['newer']['id'], here['newer']['site'], here['newer']['parent']['model_id']), (a['id'], b['id'], 'Other', a['id']))
            self.assertEqual(here['newer']['tests'], {'heard': 3, 'not_heard': 5, 'now': {'found': 3, 'false': 0}, 'new': {'found': 3, 'false': 0}})
            self.assertEqual(sorted((v['id'], v['here'], [u['site'] for u in v['used']]) for v in here['versions']), [(a['id'], True, ['Site']), (b['id'], False, ['Other'])])
            client.post(f'/datasets/1/species/{sid}/ai-versions/set-aside', json={'model_id': b['id']})
            self.assertIsNone(client.get(f'/datasets/1/species/{sid}/ai-versions', params=SCOPE).json()['newer'])
            # A uses B's AI (a copy here), then goes back to its own.
            copy = client.post(f"/custom-models/{b['id']}/use", json={'dataset_id': 1, **SCOPE}).json()
            self.assertEqual(client.get(f'/datasets/1/species/{sid}/custom-model', params=SCOPE).json()['model']['id'], copy['id'])
            here = client.get(f'/datasets/1/species/{sid}/ai-versions', params=SCOPE).json()
            self.assertEqual(sorted((v['id'], v['here']) for v in here['versions']), [(a['id'], False), (b['id'], True)])
            client.post(f"/custom-models/{a['id']}/use", json={'dataset_id': 1, **SCOPE})
            self.assertEqual(client.get(f'/datasets/1/species/{sid}/custom-model', params=SCOPE).json()['model']['id'], a['id'])
            # B's AI as a file keeps all its material; grown from it at A, A's own sounds in it count as A's answers are now.
            out = root / 'out'; out.mkdir()
            path = client.post(f"/custom-models/{b['id']}/export", json={'folder': str(out)}).json()['path']
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            self.assertEqual((len(data['material']['voice']), data['parent'], sorted(set(data['material']['sites']))), (32, {'round': 1, 'site': 'Site'}, ['Other', 'Site']))
            seen = client.post('/custom-models/import', json={'dataset_id': 1, 'path': path, 'inspect': True, **SCOPE}).json()
            self.assertEqual((seen['material'], seen['here']), (32, {'heard': 3, 'not_heard': 5, 'found': 3, 'false': 0}))
            client.post('/custom-models/import', json={'dataset_id': 1, 'path': path, 'species_id': sid, **SCOPE})
            self.assertEqual(client.get(f'/datasets/1/species/{sid}/custom-model', params=SCOPE).json()['inherited'], {'positive_5s': 3, 'negative_5s': 5, 'sites': ['Other']})

    def test_answers_go_out_and_come_in_as_a_table(self):
        with site() as (root, client, species):
            sid = species['id']
            answer(root, sid, [1])
            out = root / 'out'; out.mkdir()
            made = client.post('/datasets/1/heard/export', json={'folder': str(out)}).json()
            self.assertEqual(made['rows'], 8)
            with open(made['path'], encoding='utf-8-sig', newline='') as stream:
                table = list(csv.DictReader(stream))
            self.assertEqual((table[0]['file'], table[0]['recording_start'], table[0]['scientific_name'], table[0]['verdict'], table[0]['name']),
                             ('20240801_060000.wav', '2024-08-01T06:00:00+09:00', 'Horornis diphone', 'present', 'ウグイス'))
            # Taken in where the answers already are: nothing changes.
            self.assertEqual(client.post('/datasets/1/heard/import', json={'dataset_id': 1, 'path': made['path']}).json(),
                             {'added': 0, 'different': 0, 'same': 8, 'no_recording': 0, 'no_species': 0, 'invalid': 0})
            # After the answers are gone (another PC with the same recordings), they come back; odd rows are counted.
            with connect(root) as db:
                db.execute('DELETE FROM labels')
            table[1]['file'] = table[1]['file'].upper()  # file names are compared without case
            rows = table + [{**table[0], 'file': 'nowhere.wav'}, {**table[0], 'recording_start': '2023-01-01T06:00:00+09:00'}, {**table[0], 'scientific_name': 'Nemo nemo'}, {**table[0], 'verdict': 'maybe'}]
            with open(out / 'in.csv', 'w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(table[0])); writer.writeheader(); writer.writerows(rows)
            self.assertEqual(client.post('/datasets/1/heard/import', json={'dataset_id': 1, 'path': str(out / 'in.csv')}).json(),
                             {'added': 8, 'different': 0, 'same': 0, 'no_recording': 2, 'no_species': 1, 'invalid': 1})
            heard = client.get('/datasets/1/heard').json()
            self.assertEqual((heard['rows'][0]['site'], heard['total']['site']), ({'present': 3, 'absent': 5, 'unknown': 0}, 8))
            # An answer that differs from the one here is left out and counted.
            with open(out / 'flip.csv', 'w', encoding='utf-8', newline='') as stream:
                writer = csv.DictWriter(stream, fieldnames=list(table[0])); writer.writeheader(); writer.writerow({**table[0], 'verdict': 'absent'})
            self.assertEqual(client.post('/datasets/1/heard/import', json={'dataset_id': 1, 'path': str(out / 'flip.csv')}).json(),
                             {'added': 0, 'different': 1, 'same': 0, 'no_recording': 0, 'no_species': 0, 'invalid': 0})
            self.assertEqual(client.get('/datasets/1/heard').json()['rows'][0]['site'], {'present': 3, 'absent': 5, 'unknown': 0})
            self.assertEqual(client.post('/datasets/1/heard/import', json={'dataset_id': 1, 'path': str(out / 'none.csv')}).json()['detail'], 'file_missing')
            self.assertEqual(client.post('/custom-models/import', json={'dataset_id': 1, 'path': str(out / 'none.json'), 'inspect': True}).json()['detail'], 'file_missing')
            (out / 'bad.csv').write_text('a,b\n1,2\n', encoding='utf-8')
            self.assertEqual(client.post('/datasets/1/heard/import', json={'dataset_id': 1, 'path': str(out / 'bad.csv')}).json()['detail'], 'heard_file_invalid')

    def test_no_bird_sound_goes_out_and_comes_in_with_the_table(self):
        with site() as (root, client, species):
            sound = dict(dataset_id=1, recording_id=1, start_s=1, end_s=3)
            client.post('/heard/map-answer', json={**sound, 'verdict': 'no_bird'})
            out = root / 'out'; out.mkdir()
            made = client.post('/datasets/1/heard/export', json={'folder': str(out)}).json()
            with open(made['path'], encoding='utf-8-sig', newline='') as stream:
                table = list(csv.DictReader(stream))
            self.assertEqual([(row['scientific_name'], row['name'], row['verdict']) for row in table], [('(no bird)', '鳥の声がしない', 'present')])
            empty = {'added': 0, 'different': 0, 'same': 0, 'no_recording': 0, 'no_species': 0, 'invalid': 0}
            take = lambda: client.post('/datasets/1/heard/import', json={'dataset_id': 1, 'path': made['path']}).json()
            self.assertEqual(take(), {**empty, 'same': 1})
            with connect(root) as db:
                db.execute('DELETE FROM labels')
            self.assertEqual(take(), {**empty, 'added': 1})
            self.assertEqual(client.get('/datasets/1/heard').json()['no_bird'], {'site': 1, 'all': 1})
            # Where a species is recorded as heard, the table's "no bird sound" is left out; taken back, it is not written.
            client.post('/heard/map-answer', json={**sound, 'verdict': 'present', 'species_id': species['id']})
            self.assertEqual(take(), {**empty, 'different': 1})
            again = client.post('/datasets/1/heard/export', json={'folder': str(out / '..')}).json()
            self.assertEqual(again['rows'], 1)


if __name__ == '__main__':
    unittest.main()
