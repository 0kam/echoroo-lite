"""Exercise the actual practice lifecycle, managed audio, and immutable first answers."""
import io
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from server import create_app
from storage import connect


class QuizTest(unittest.TestCase):
    def test_practice_lifecycle(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            app = create_app(root, root / 'models', 'quiz-test')
            headers = {'Authorization': 'Bearer quiz-test'}
            with TestClient(app, headers=headers) as client:
                library = client.get('/quiz/library').json()
                clips = library['clips']
                self.assertEqual((len(clips), len({c['species_id'] for c in clips})), (26, 26))
                self.assertEqual(client.get('/references').json(), [])
                self.assertEqual(client.get('/quiz/library').json()['sets'], library['sets'])
                kiji = next(c for c in clips if c['species']['scientific_name'] == 'Phasianus versicolor')
                self.assertEqual(kiji['species']['common_names'], {'ja': 'キジ', 'en': 'Green Pheasant'})
                for clip in clips:
                    audio, rate = sf.read(clip['source']['path'])
                    self.assertTrue(len(audio) and np.isfinite(audio).all())
                image = client.get(f"/quiz/clips/{clips[0]['id']}/image")
                self.assertEqual(image.headers['content-type'], 'image/jpeg')
                self.assertTrue(image.content.startswith(b'\xff\xd8'))
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
                client.post(f"/quiz/clips/{added['id']}/enabled", json={'enabled': False})
                self.assertEqual(client.post('/quiz/sessions', json={'clip_ids': [added['id'], clips[1]['id']]}).json()['detail'], 'quiz_selection_missing')
                client.post(f"/quiz/clips/{added['id']}/enabled", json={'enabled': True})
                self.assertEqual(client.get(f'/quiz/sessions/{sid}').json(), complete)
            with TestClient(create_app(root, root / 'models', 'quiz-test'), headers=headers) as restarted:
                self.assertEqual(len(restarted.get('/quiz/library').json()['clips']), 28)
                self.assertEqual(restarted.get(f'/quiz/sessions/{sid}').json(), complete)
                self.assertEqual(restarted.get(f'/quiz/sessions/{sid}/questions/0/audio').status_code, 200)


if __name__ == '__main__':
    unittest.main()
