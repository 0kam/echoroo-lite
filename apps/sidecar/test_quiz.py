"""Exercise the actual practice lifecycle, managed audio, and immutable first answers."""
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from audio import add_reference
from quiz import clip_view
from server import create_app
from storage import connect


class QuizTest(unittest.TestCase):
    def test_practice_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = create_app(root, root / 'models', 'quiz-test')
            headers = {'Authorization': 'Bearer quiz-test'}
            with TestClient(app, headers=headers) as client:
                self.assertEqual(client.get('/quiz/library').json(), {'clips': [], 'sets': [], 'sessions': []})
                self.assertFalse((root / 'quiz').exists())
                original = root / 'practice.wav'
                sf.write(original, np.linspace(-.1, .1, 32000), 32000)
                reference = client.post('/references', json={'path': str(original)}).json()
                for name in ('Horornis diphone', 'Parus minor', 'Phasianus versicolor', 'Picus awokera'):
                    species = client.get('/species', params={'q': name}).json()[0]
                    added = client.post('/quiz/clips', json={
                        'source_kind': 'reference', 'source_id': reference['id'], 'species_id': species['id'],
                        'start_s': 0, 'end_s': 1, 'sound_type': 'song', 'note': 'A section was cut.',
                        'credit': {'recordist': 'Fixture', 'license': 'https://creativecommons.org/licenses/by/4.0/'}})
                    self.assertEqual(added.status_code, 200, added.text)
                library = client.get('/quiz/library').json()
                clips = library['clips']
                self.assertEqual((len(clips), len({c['species_id'] for c in clips})), (4, 4))
                self.assertTrue(all(c['bundled_key'] is None for c in clips))
                self.assertEqual(library['sets'], [])
                self.assertEqual([r['id'] for r in client.get('/references').json()], [reference['id']])
                self.assertEqual(client.get('/quiz/library').json()['sets'], library['sets'])
                kiji = next(c for c in clips if c['species']['scientific_name'] == 'Phasianus versicolor')
                self.assertEqual(kiji['species']['common_names'], {'ja': 'キジ', 'en': 'Green Pheasant'})
                for clip in clips:
                    audio, rate = sf.read(clip['source']['path'])
                    self.assertTrue(len(audio) and np.isfinite(audio).all())
                ids = [c['id'] for c in clips]
                self.assertEqual(client.post('/quiz/sessions', json={'clip_ids': ids[:1]}).json()['detail'], 'quiz_min_species')
                session = client.post('/quiz/sessions', json={'clip_ids': ids, 'count': 3}).json()
                sid = session['id']
                self.assertEqual(session['purpose'], 'practice')
                self.assertNotIn('clip', session['question'])
                self.assertEqual(session['review'], [])
                self.assertEqual(len(session['question']['choices']), 4)
                with connect(root) as db:
                    snapshot = json.loads(db.execute('SELECT snapshot FROM quiz_sessions WHERE id=?', (sid,)).fetchone()[0])
                correct = snapshot['questions'][0]['clip']['species_id']
                self.assertEqual(client.get(f'/quiz/sessions/{sid}/questions/1/audio').status_code, 400)
                self.assertEqual(client.get(f'/quiz/sessions/{sid}/questions/0/audio').status_code, 200)
                interval='?start_s=0&end_s=1'
                self.assertEqual(client.get(f'/quiz/sessions/{sid}/questions/1/spectrogram{interval}').status_code,400)
                plot=client.get(f'/quiz/sessions/{sid}/questions/0/spectrogram{interval}')
                self.assertEqual(plot.status_code,200,plot.text)
                ref=snapshot['questions'][0]['clip']['source']['id']
                self.assertEqual(plot.json(),client.get(f'/sources/reference/{ref}/spectrogram{interval}').json())
                self.assertEqual(plot.json()['frequency_scale'],'mel')
                self.assertEqual(client.get(f'/quiz/sessions/{sid}/questions/0/spectrogram{interval}&scale=linear').json()['frequency_scale'],'linear')
                self.assertEqual(client.get(f'/quiz/sessions/{sid}/questions/0/spectrogram{interval}&scale=invalid').status_code,422)
                self.assertTrue(plot.json()['pixels'])
                self.assertFalse({'path','species','clip','source'} & plot.json().keys())
                result = client.post(f'/quiz/sessions/{sid}/answers', json={'position': 0, 'species_id': correct}).json()
                self.assertEqual(result['question']['feedback']['status'], 'correct')
                self.assertEqual(result['question']['feedback']['clip']['metadata']['credit']['license'],
                                 'https://creativecommons.org/licenses/by/4.0/')
                self.assertIn('section was cut', result['question']['feedback']['clip']['metadata']['note'])
                self.assertEqual(client.post(f'/quiz/sessions/{sid}/answers', json={'position': 0, 'species_id': correct}).json()['answered'], 1)
                self.assertEqual(client.post(f'/quiz/sessions/{sid}/answers', json={'position': 0}).json()['detail'], 'quiz_answer_saved')
                self.assertEqual(client.get(f'/quiz/sessions/{sid}').json()['question']['position'], 1)
                client.post(f'/quiz/sessions/{sid}/answers', json={'position': 1})
                q = client.get(f'/quiz/sessions/{sid}').json()['question']
                wrong = next(c['id'] for c in q['choices'] if c['id'] != snapshot['questions'][2]['clip']['species_id'])
                client.post(f'/quiz/sessions/{sid}/answers', json={'position': 2, 'species_id': wrong})
                complete = client.get(f'/quiz/sessions/{sid}').json()
                self.assertTrue(complete['complete'])
                self.assertEqual((complete['answered'], complete['correct'], complete['unknown']), (3, 1, 1))
                self.assertEqual(sum(s['incorrect'] for s in complete['per_species']), 1)
                # Add an exact selected interval; deleting the original cannot remove the practice clip.
                original = root / 'original.wav'
                sf.write(original, np.column_stack([np.linspace(-.2, .2, 48000), np.zeros(48000)]), 48000, subtype='FLOAT')
                ref = client.post('/references', json={'path': str(original)}).json()
                body = dict(source_kind='reference', source_id=ref['id'], species_id=clips[0]['species_id'],
                            start_s=.2, end_s=.8, note='A user-selected call', credit={'recordist': 'Test'})
                added = client.post('/quiz/clips', json=body).json()
                self.assertEqual(added['metadata']['label_source'], 'user_selection')
                self.assertEqual(added['metadata']['credit']['recordist'], 'Test')
                original.unlink()
                response = client.get(f"/sources/reference/{added['reference_id']}/audio")
                waveform, rate = sf.read(io.BytesIO(response.content))
                self.assertEqual((len(waveform), rate), (19200, 32000))
                self.assertAlmostEqual(added['seconds'], .6)
                # Selecting another interval from a practice copy preserves its source credit.
                retrim = client.post('/quiz/clips', json={**body, 'source_id': added['reference_id'],
                    'start_s': .1, 'end_s': .4, 'credit': {}}).json()
                self.assertEqual(retrim['metadata']['credit']['recordist'], 'Test')
                self.assertEqual(retrim['metadata']['original_quiz_clip_id'], added['id'])
                saved_set = client.post('/quiz/sets', json={'name': 'My practice', 'clip_ids': [added['id'], clips[1]['id']]}).json()
                self.assertIsInstance(saved_set['id'], int)
                saved = client.get('/quiz/library').json()['sets'][0]
                self.assertNotIn('set', saved)
                self.assertIsNone(saved['bundled_key'])
                self.assertEqual(saved['clip_ids'], [added['id'], clips[1]['id']])
                client.post(f"/quiz/clips/{added['id']}/enabled", json={'enabled': False})
                self.assertEqual(client.post('/quiz/sessions', json={'clip_ids': [added['id'], clips[1]['id']]}).json()['detail'], 'quiz_selection_missing')
                client.post(f"/quiz/clips/{added['id']}/enabled", json={'enabled': True})
                self.assertEqual(client.get(f'/quiz/sessions/{sid}').json(), complete)
            with TestClient(create_app(root, root / 'models', 'quiz-test'), headers=headers) as restarted:
                self.assertEqual(len(restarted.get('/quiz/library').json()['clips']), 6)
                self.assertEqual(restarted.get(f'/quiz/sessions/{sid}').json(), complete)
                self.assertEqual(restarted.get(f'/quiz/sessions/{sid}/questions/0/audio').status_code, 200)

    def test_retires_old_bundle_without_touching_history_or_audio(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            headers = {'Authorization': 'Bearer quiz-test'}
            app = create_app(root, root / 'models', 'quiz-test')
            old_audio = root / 'old.mp3'
            sf.write(old_audio, np.linspace(-.1, .1, 32000), 32000, format='WAV')
            reference = add_reference(root, old_audio)
            with connect(root) as db:
                species = db.execute("SELECT * FROM species WHERE scientific_name='Horornis diphone'").fetchone()
                old_clips = {}
                for bundled_key, image in (('koe-e-lab-v1:uguisu', 'img/uguisu.jpg'),
                                           ('xeno-canto-v1:uguisu', 'img/xc.jpg')):
                    metadata = {'key': bundled_key, 'sound_type': 'song', 'image_file': image,
                                'credit': {}, 'note': ''}
                    clip_id = db.execute(
                        'INSERT INTO quiz_clips(species_id,reference_id,bundled_key,metadata) VALUES(?,?,?,?)',
                        (species['id'], reference['id'], bundled_key, json.dumps(metadata))).lastrowid
                    old_clips[bundled_key] = clip_view(
                        db, db.execute('SELECT * FROM quiz_clips WHERE id=?', (clip_id,)).fetchone())
                clip = old_clips['koe-e-lab-v1:uguisu']
                clip_id = clip['id']
                snapshot = {'purpose': 'practice', 'clip_ids': [clip_id], 'requested_count': 1,
                            'method': 'uniform_clips_without_replacement_v1',
                            'questions': [{'clip': clip, 'choices': [clip['species']]}]}
                answers = [{'species_id': species['id'], 'at': '2026-01-01T00:00:00+00:00'}]
                session_id = db.execute('INSERT INTO quiz_sessions(snapshot,answers) VALUES(?,?)',
                                        (json.dumps(snapshot), json.dumps(answers))).lastrowid
                db.execute("INSERT INTO quiz_sets(name,clip_ids,bundled_key) VALUES('old',?,'koe-e-lab-v1')",
                           (json.dumps([clip_id]),))
                old_xc = old_clips['xeno-canto-v1:uguisu']
                xc_snapshot = {**snapshot, 'clip_ids': [old_xc['id']],
                               'questions': [{'clip': old_xc, 'choices': [old_xc['species']]}]}
                xc_session_id = db.execute('INSERT INTO quiz_sessions(snapshot,answers) VALUES(?,?)',
                                           (json.dumps(xc_snapshot), json.dumps(answers))).lastrowid
                db.execute("INSERT INTO quiz_sets(name,clip_ids,bundled_key) VALUES('old xc',?,'xeno-canto-v1')",
                           (json.dumps([old_xc['id']]),))
            original_audio = old_audio.read_bytes()
            with TestClient(app, headers=headers) as client:
                library = client.get('/quiz/library').json()
                retired = [row for row in library['clips'] if row['id'] in {clip['id'] for clip in old_clips.values()}]
                self.assertEqual([row['enabled'] for row in retired], [0, 0])
                self.assertEqual(len(library['sets']), 2)
                self.assertEqual({row['bundled_key'] for row in library['sets']}, {None})
                self.assertEqual(client.get('/quiz/library').json(), library)
                self.assertEqual(old_audio.read_bytes(), original_audio)
                self.assertEqual(client.post('/quiz/sessions', json={'clip_ids': [c['id'] for c in retired]}).json()['detail'],
                                 'quiz_selection_missing')
                history = client.get(f'/quiz/sessions/{session_id}')
                self.assertEqual((history.status_code, history.json()['answered'], history.json()['correct']), (200, 1, 1))
                xc_history = client.get(f'/quiz/sessions/{xc_session_id}').json()
                self.assertEqual(xc_history['correct'], 1)
                for retired_session in (session_id, xc_session_id):
                    with patch('quiz.checked_source', side_effect=AssertionError('retired audio was read')):
                        unavailable = client.get(f'/quiz/sessions/{retired_session}/questions/0/audio')
                    self.assertEqual((unavailable.status_code, unavailable.json()['detail']), (400, 'quiz_audio_unavailable'))
                    plot = client.get(f'/quiz/sessions/{retired_session}/questions/0/spectrogram?start_s=0&end_s=1')
                    self.assertEqual(plot.json()['detail'], 'quiz_audio_unavailable')
            with connect(root) as db:
                saved = db.execute('SELECT snapshot,answers FROM quiz_sessions WHERE id=?', (session_id,)).fetchone()
                self.assertEqual(tuple(saved), (json.dumps(snapshot), json.dumps(answers)))
                xc_saved = db.execute('SELECT snapshot,answers FROM quiz_sessions WHERE id=?', (xc_session_id,)).fetchone()
                self.assertEqual(tuple(xc_saved), (json.dumps(xc_snapshot), json.dumps(answers)))


if __name__ == '__main__':
    unittest.main()
