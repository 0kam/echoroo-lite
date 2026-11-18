"""Sounds answered with a bird outside Japan: found once per analysis, listed by family, and answered by the user."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from analysis import digest
from models import REGISTRY
from server import create_app
from storage import connect
from test_models import FakeSession, model_files, wait_job

OUTSIDE = 'Dryobates pubescens'  # a woodpecker (Picidae) that is not on the Japan checklist


class RollupSession(FakeSession):
    """Windows 0 and 3 answer the woodpecker outside Japan, window 1 a Japan species, window 2 a non-bird class."""

    def run(self, outputs, inputs):
        values = dict(zip(outputs, super().run(outputs, inputs)))
        if 'logits' in values:
            values['logits'][:] = -50
            for window, column in enumerate((400, 0, 5000, 400)[:len(values['logits'])]):
                values['logits'][window, column] = 5
        return [values[name] for name in outputs]


class RollupTest(unittest.TestCase):
    def test_outside_japan_answers_are_listed_by_family_and_answered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / 'models'; model_files(directory)
            head = directory / REGISTRY['kinglet-xs']['files'][1]
            with np.load(head) as saved:
                arrays = dict(saved)
            arrays['labels'][400] = OUTSIDE
            np.savez(head, **arrays)
            audio = root / 'audio'; audio.mkdir()
            sf.write(audio / '20240808_060000.wav', np.full(32000 * 20, .25), 32000)
            data = root / 'data'
            session = RollupSession('kinglet-xs', None)
            with TestClient(create_app(data, directory, 'test'), headers={'Authorization': 'Bearer test'}) as client, \
                    patch('analysis.load_onnx', return_value=(session, digest(directory / REGISTRY['kinglet-xs']['files'][0]))):
                client.post('/imports', json=dict(folder=str(audio), site_name='Site', dataset_name='Data',
                                                  timezone='Asia/Tokyo', latitude=35, longitude=138))
                client.put('/models', json={'main': 'kinglet-xs', 'extra': []})
                self.assertEqual(client.post('/jobs', json={'dataset_id': 1}).status_code, 200)
                self.assertEqual(wait_job(client)['status'], 'complete')
                # The analysis job already looked for roll-ups; asking again does not find them twice.
                with connect(data) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM rollup_scans').fetchone()[0], 1)
                summary = client.get('/datasets/1/rollups').json()
                self.assertEqual([(f['family']['scientific_name'], f['family']['common_names']['ja'], f['sounds'], f['left'])
                                  for f in summary['families']], [('Picidae', 'キツツキ科', 2, 2)])
                self.assertEqual(summary['families'][0]['family']['rank'], 'family')
                family = client.get('/datasets/1/rollups/Picidae').json()
                self.assertEqual([(s['start_s'], s['end_s'], s['guess']['scientific_name'], s['answer']) for s in family['sounds']],
                                 [(0, 5, OUTSIDE, None), (15, 20, OUTSIDE, None)])
                names = {c['scientific_name'] for c in family['choices']}
                self.assertIn('Yungipicus kizuki', names)
                self.assertNotIn(OUTSIDE, names)
                # A family is never offered as a species.
                self.assertEqual(client.get('/species?q=Picidae').json(), [])
                self.assertEqual(client.get('/species?q=キツツキ科').json(), [])
                kogera = next(c for c in family['choices'] if c['scientific_name'] == 'Yungipicus kizuki')
                sound = dict(dataset_id=1, recording_id=1, family='Picidae')

                def answer(start, kind, species_id=None):
                    response = client.post('/rollups/answers', json={**sound, 'start_s': start, 'end_s': start + 5,
                                                                      'kind': kind, 'species_id': species_id})
                    self.assertEqual(response.status_code, 200, response.text)
                    return client.get('/datasets/1/rollups/Picidae').json()['sounds']

                sounds = answer(0, 'species', kogera['id'])
                # Answered sounds go to the end; the species heard is evidence like any other "heard".
                self.assertEqual([(s['start_s'], s['answer'] and s['answer']['kind']) for s in sounds], [(15, None), (0, 'species')])
                self.assertEqual(sounds[1]['answer']['species']['id'], kogera['id'])
                overview = client.get('/datasets/1/overview').json()
                row = next(s for s in overview['species'] if s['species']['id'] == kogera['id'])
                self.assertEqual(row['presence']['status'], 'confirmed')
                with connect(data) as db:
                    label = db.execute("SELECT * FROM labels WHERE source='rollup_fix'").fetchone()
                    self.assertEqual((label['verdict'], label['taxon_id']), ('present', kogera['id']))
                    self.assertIn(OUTSIDE, label['source_ref'])
                # An answer that stops at the family is kept on the family row and is evidence of no species.
                for kind in ('family', 'not_bird', 'unknown'):
                    sounds = answer(15, kind)
                    self.assertEqual({s['start_s']: s['answer']['kind'] for s in sounds}, {0: 'species', 15: kind})
                overview = client.get('/datasets/1/overview').json()
                self.assertFalse(any(s['species'].get('rank') == 'family' for s in overview['species']))
                self.assertEqual(client.get('/datasets/1/rollups').json()['families'][0]['left'], 0)
                with connect(data) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM labels WHERE source='rollup_fix'").fetchone()[0], 2)
                # Changing the answer replaces it; taking it back leaves the sound unanswered again.
                sounds = answer(0, 'unknown')
                self.assertFalse(any(s['species']['id'] == kogera['id'] and s['presence']['status'] == 'confirmed'
                                     for s in client.get('/datasets/1/overview').json()['species']))
                sounds = answer(0, None)
                self.assertEqual({s['start_s']: s['answer'] and s['answer']['kind'] for s in sounds}, {0: None, 15: 'unknown'})
                self.assertEqual(client.post('/rollups/answers', json={**sound, 'start_s': 5, 'end_s': 10, 'kind': 'unknown'}).status_code, 400)
                self.assertEqual(client.post('/rollups/answers', json={**sound, 'start_s': 0, 'end_s': 5, 'kind': 'species'}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
