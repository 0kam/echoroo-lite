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
NO_JAPAN = ('Thamnophilus doliatus', 'Furnarius rufus')  # their families have no species on the Japan checklist


class RollupSession(FakeSession):
    """Windows 0 and 3 answer the woodpecker outside Japan (3 with the higher score), window 1 a Japan species,
    window 2 a non-bird class, windows 4 and 5 birds of families that Japan does not have (5 with the higher score)."""

    def run(self, outputs, inputs):
        values = dict(zip(outputs, super().run(outputs, inputs)))
        if 'logits' in values:
            values['logits'][:] = -50
            for window, (column, sure) in enumerate(((400, False), (0, True), (5000, True), (400, True), (401, False), (402, True))[:len(values['logits'])]):
                values['logits'][window, column] = 5
                if not sure:  # a non-bird class takes a share of the score
                    values['logits'][window, 5001] = 4
        return [values[name] for name in outputs]


class RollupTest(unittest.TestCase):
    def test_outside_japan_answers_are_listed_by_family_and_answered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); directory = root / 'models'; model_files(directory)
            head = directory / REGISTRY['kinglet-xs']['files'][1]
            with np.load(head) as saved:
                arrays = dict(saved)
            arrays['labels'][400:403] = (OUTSIDE,) + NO_JAPAN
            np.savez(head, **arrays)
            audio = root / 'audio'; audio.mkdir()
            sf.write(audio / '20240808_060000.wav', np.full(32000 * 30, .25), 32000)
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
                picidae, merged = summary['families']
                self.assertEqual((picidae['family']['scientific_name'], picidae['family']['common_names']['ja'], picidae['sounds'], picidae['left']),
                                 ('Picidae', 'キツツキ科', 2, 2))
                # Families without a Japan species share one last row, opened with the reserved key.
                self.assertEqual((merged['family'], merged['sounds'], merged['left'], merged['families']), (None, 2, 2, 2))
                self.assertEqual([f['scientific_name'] for f in merged['examples']], ['Furnariidae', 'Thamnophilidae'])
                self.assertEqual({g['scientific_name'] for g in merged['guesses']}, set(NO_JAPAN))
                none = client.get('/datasets/1/rollups/_none').json()
                self.assertEqual((none['family'], none['choices']), (None, []))
                self.assertEqual([(s['start_s'], s['family']) for s in none['sounds']], [(25, 'Furnariidae'), (20, 'Thamnophilidae')])
                response = client.post('/rollups/answers', json=dict(dataset_id=1, recording_id=1, family='Furnariidae', start_s=25, end_s=30, kind='not_bird'))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([s['answer'] and s['answer']['kind'] for s in client.get('/datasets/1/rollups/_none').json()['sounds']], ['not_bird', None])
                self.assertEqual(client.get('/datasets/1/rollups').json()['families'][1]['left'], 1)
                self.assertEqual(summary['families'][0]['family']['rank'], 'family')
                family = client.get('/datasets/1/rollups/Picidae').json()
                self.assertEqual([(s['start_s'], s['end_s'], s['guess']['scientific_name'], s['answer']) for s in family['sounds']],
                                 [(15, 20, OUTSIDE, None), (0, 5, OUTSIDE, None)])
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
                # Sounds stay in the AI's score order whatever was answered; the species heard is evidence like any other "heard".
                self.assertEqual([(s['start_s'], s['answer'] and s['answer']['kind']) for s in sounds], [(15, None), (0, 'species')])
                self.assertEqual(sounds[1]['answer']['species']['id'], kogera['id'])
                self.assertEqual([s['scientific_name'] for s in client.get('/species?q=こげら').json()][:1], ['Yungipicus kizuki'])
                overview = client.get('/datasets/1/overview').json()
                row = next(s for s in overview['species'] if s['species']['id'] == kogera['id'])
                self.assertEqual(row['presence']['status'], 'confirmed')
                with connect(data) as db:
                    label = db.execute("SELECT * FROM labels WHERE source='rollup_fix' ORDER BY id DESC").fetchone()
                    self.assertEqual((label['verdict'], label['taxon_id']), ('present', kogera['id']))
                    self.assertIn(OUTSIDE, label['source_ref'])
                # An answer that stops at the family is kept on the family row and is evidence of no species.
                for kind in ('family', 'not_bird', 'unknown'):
                    sounds = answer(15, kind)
                    self.assertEqual({s['start_s']: s['answer']['kind'] for s in sounds}, {0: 'species', 15: kind})
                    if kind == 'not_bird':
                        # "No bird sound" is its own record, not "family: not heard".
                        with connect(data) as db:
                            label = db.execute("SELECT l.verdict,s.rank FROM labels l JOIN species s ON s.id=l.taxon_id WHERE l.source='rollup_fix' AND l.start_s=15").fetchone()
                            self.assertEqual(tuple(label), ('present', 'none'))
                        self.assertEqual(client.get('/datasets/1/heard').json()['no_bird']['site'], 2)  # with the one answered above
                # As it was stored before ("family: not heard"), it still reads as answered.
                with connect(data) as db:
                    db.execute("UPDATE labels SET verdict='absent' WHERE source='rollup_fix' AND start_s=15")
                self.assertEqual(client.get('/datasets/1/rollups/Picidae').json()['sounds'][0]['answer'], {'kind': 'not_bird', 'species': None})
                with connect(data) as db:
                    db.execute("UPDATE labels SET verdict='unknown' WHERE source='rollup_fix' AND start_s=15")
                overview = client.get('/datasets/1/overview').json()
                self.assertFalse(any(s['species'].get('rank') for s in overview['species']))
                self.assertEqual(client.get('/datasets/1/rollups').json()['families'][0]['left'], 0)
                with connect(data) as db:
                    self.assertEqual(db.execute("SELECT COUNT(*) FROM labels WHERE source='rollup_fix'").fetchone()[0], 3)
                # Changing the answer replaces it; taking it back leaves the sound unanswered again.
                sounds = answer(0, 'unknown')
                self.assertFalse(any(s['species']['id'] == kogera['id'] and s['presence']['status'] == 'confirmed'
                                     for s in client.get('/datasets/1/overview').json()['species']))
                sounds = answer(0, None)
                self.assertEqual({s['start_s']: s['answer'] and s['answer']['kind'] for s in sounds}, {0: None, 15: 'unknown'})
                self.assertEqual(client.post('/rollups/answers', json={**sound, 'start_s': 5, 'end_s': 10, 'kind': 'unknown'}).status_code, 400)
                # Only roll-ups whose family score reaches 0.5 are shown: on the site's list, a family's page and the
                # page of families Japan does not have. The scores stay stored, and so do answers given below the line.
                with connect(data) as db:
                    kept = db.execute('SELECT COUNT(*) FROM labels').fetchone()[0]
                    db.execute('UPDATE rollups SET score=.49 WHERE start_s IN (15,25)')
                picidae, merged = client.get('/datasets/1/rollups').json()['families']
                self.assertEqual((picidae['sounds'], picidae['left'], merged['sounds'], merged['left'], merged['families']), (1, 1, 1, 1, 1))
                self.assertEqual([s['start_s'] for s in client.get('/datasets/1/rollups/Picidae').json()['sounds']], [0])
                self.assertEqual([s['start_s'] for s in client.get('/datasets/1/rollups/_none').json()['sounds']], [20])
                with connect(data) as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM labels').fetchone()[0], kept)
                    db.execute('UPDATE rollups SET score=.5 WHERE start_s=15')
                self.assertEqual([s['start_s'] for s in client.get('/datasets/1/rollups/Picidae').json()['sounds']], [0, 15])
                self.assertEqual(client.post('/rollups/answers', json={**sound, 'start_s': 0, 'end_s': 5, 'kind': 'species'}).status_code, 422)

                # With the range model the line is "usual at the site in the recording's week", and the roll-ups are
                # found again: the score changed by hand above is back, and the answers given stay.
                warbler = lambda: [s['start_s'] for s in client.get('/datasets/1/rollups/Scotocercidae').json()['sounds']]
                picidae = lambda: {s['start_s']: s['answer'] and s['answer']['kind'] for s in client.get('/datasets/1/rollups/Picidae').json()['sounds']}
                self.assertEqual((warbler(), picidae()), ([], {0: None, 15: 'unknown'}))
                geo = directory / 'birdnet/geo-models/v2.4/tf/model-fp32.tflite'
                geo.parent.mkdir(parents=True); geo.touch()
                here = lambda key, latitude, longitude, week: frozenset({'Horornis diphone', 'Dendrocopos major'} if (latitude, week) == (35, 30) else ())
                with patch('region.predict_species', side_effect=here), patch('results_workflow.predict_species', side_effect=here):
                    # The bush warbler of window 1 is usual here in the second week of August: not rolled up.
                    self.assertEqual((warbler(), picidae()), ([], {0: None, 15: 'unknown'}))
                    self.assertEqual([s['start_s'] for s in client.get('/datasets/1/rollups/_none').json()['sounds']], [25, 20])
                    # The same basis: nothing is found again.
                    with connect(data) as db:
                        db.execute('UPDATE rollups SET score=.49 WHERE start_s=25')
                    self.assertEqual([s['start_s'] for s in client.get('/datasets/1/rollups/_none').json()['sounds']], [20])
                    # The family's species to choose from are still the Japan checklist's, those usual here first.
                    choices = client.get('/datasets/1/rollups/Picidae').json()['choices']
                    self.assertEqual([(c['scientific_name'], c['likely']) for c in choices[:2]][0], ('Dendrocopos major', True))
                    self.assertFalse(choices[1]['likely'])
                    self.assertIn('Yungipicus kizuki', {c['scientific_name'] for c in choices})
                    # The site moves to where the range model does not predict the warbler: a Japan checklist
                    # species is rolled up there, and the other sounds and their answers stay.
                    with connect(data) as db:
                        db.execute('UPDATE sites SET latitude=36')
                    self.assertEqual((warbler(), picidae()), ([5], {0: None, 15: 'unknown'}))
                    self.assertEqual([s['start_s'] for s in client.get('/datasets/1/rollups/_none').json()['sounds']], [25, 20])
                    with connect(data) as db:
                        self.assertEqual(db.execute('SELECT COUNT(*) FROM labels').fetchone()[0], kept)
                        self.assertEqual(db.execute('SELECT COUNT(*),COUNT(basis) FROM rollup_scans').fetchone()[:], (1, 1))


if __name__ == '__main__':
    unittest.main()
