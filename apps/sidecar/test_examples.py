"""Species examples: named first, confirmed newest first, never a current question."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import soundfile as sf

import examples  # noqa: F401  Imported before patching species.ebird_codes.
import test_validation_groups as base
from storage import connect


class ExamplesCheck(unittest.TestCase):
    setUp = base.ValidationGroupCheck.setUp
    post = base.ValidationGroupCheck.post

    def examples(self, species, **query):
        response = self.client.get(f"/species/{species['id']}/examples", params={'dataset_id': 1, **query})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_order_exclusion_and_snapshots(self):
        warbler = self.species[0]  # Horornis diphone is in the bundled quiz.
        xc = self.root / 'XC1.wav'
        sf.write(xc, np.zeros(20000), 1000)
        reference = self.post('/references', {'path': str(xc)})
        with connect(self.root) as db:
            db.execute('INSERT INTO xc_recordings(id,metadata,reference_id) VALUES(1,?,?)', (json.dumps(
                {'id': 1, 'scientific_name': 'Horornis diphone', 'recordist': 'A. Recordist',
                 'license': '//creativecommons.org/licenses/by-nc-sa/4.0/', 'url': 'https://xeno-canto.org/1'}), reference['id']))
            used = {r[0] for r in db.execute('SELECT start_s FROM annotation_items WHERE recording_id=1')}
            start = next(s for s in range(0, 345, 15) if s not in used)  # Analysis 1 is recording 1.
            segment = db.execute('INSERT INTO segments(analysis_id,start_s,end_s) VALUES(1,?,?)', (start, start + 5)).lastrowid
            run = db.execute('INSERT INTO search_runs(dataset_id,species_id,snapshot) VALUES(1,?,?)',
                             (warbler['id'], json.dumps({'references': []}))).lastrowid
            db.execute('INSERT INTO search_hits VALUES(?,?,.9,0,0)', (run, segment))
        refs = [{'kind': 'named', 'origin': 'quiz', 'id': 1}, {'kind': 'confirmed', 'origin': 'annotation', 'id': 'R1-1'}]
        judged = self.post(f'/search-runs/{run}/judgements', {'segment_id': segment, 'verdict': 'target', 'examples_snapshot': refs})
        self.assertEqual(json.loads(judged['examples_snapshot']), refs)
        with connect(self.root) as db:  # Newer than the annotation answers, even within the same second.
            db.execute("UPDATE search_judgements SET created_at='2999-01-01 00:00:00'")

        free = self.examples(warbler)
        self.assertTrue(free['in_quiz'])
        self.assertEqual(free['ebird_code'], 'jabwar')  # From the shipped eBird taxonomy mapping.
        origins = [e['label']['origin'] for e in free['examples']]
        self.assertEqual(origins, ['quiz', 'xeno-canto', 'search-judgement', 'annotation'])
        self.assertEqual(free['examples'][1]['label']['recordist'], 'A. Recordist')
        self.assertEqual((free['examples'][1]['start_s'], free['examples'][1]['end_s']), (0, 15))
        annotation = free['examples'][3]
        self.assertEqual(annotation['end_s'] - annotation['start_s'], 5)
        self.assertIn('T', annotation['label']['local_time'])

        # The current sample's hits are never shown as examples.
        self.assertNotIn('search-judgement', [e['label']['origin'] for e in self.examples(warbler, run_id=run)['examples']])
        # Neither is a practice clip cut from that same window.
        clip = self.post('/quiz/clips', {'species_id': warbler['id'], 'source_kind': 'recording', 'source_id': 1,
                                         'start_s': start, 'end_s': start + 5})
        self.assertNotIn(clip['id'], [e['label']['id'] for e in self.examples(warbler, run_id=run)['examples'] if e['label']['origin'] == 'quiz'])
        with connect(self.root) as db:
            db.execute('UPDATE quiz_clips SET enabled=0 WHERE id=?', (clip['id'],))

        # All items of the validation (here the shared calibration windows) are excluded.
        group = self.post('/validations', {**self.scope, 'species_ids': [s['id'] for s in self.species[:2]],
                                           'annotation_session_id': self.annotation_id})
        guarded = self.examples(warbler, validation_id=group['id'])
        self.assertEqual([e['label']['origin'] for e in guarded['examples']], ['quiz', 'xeno-canto', 'search-judgement'])
        question = group['question']
        reviews = [{**r, 'verdict': 'unknown', 'examples_snapshot': refs[:1]} for r in question['reviews']]
        self.post(f"/validations/{group['id']}/answers", {'item_id': question['id'], 'verdict': 'unknown',
                                                          'version': question['version'], 'reviews': reviews})
        with connect(self.root) as db:
            saved = db.execute('SELECT examples_snapshot FROM validation_answers WHERE examples_snapshot IS NOT NULL').fetchall()
        self.assertEqual([json.loads(r[0]) for r in saved], [refs[:1], refs[:1]])

        # Without a Xeno-Canto key the one-button fetch reports the existing error code.
        response = self.client.post(f"/species/{warbler['id']}/examples/xeno-canto", json={'dataset_id': 1})
        self.assertEqual(response.json()['detail'], 'xc_key_required')

    def test_ebird_codes(self):
        from species import ebird_codes
        self.assertEqual(ebird_codes()['Tarsiger cyanurus'], 'refblu1')  # Shipped mapping built by scripts/build_ebird_codes.py.
        self.assertEqual(self.client.get(f"/species/{self.species[0]['id']}").json()['ebird_code'], 'jabwar')
        with patch('species.ebird_codes', return_value={}), patch('examples.ebird_codes', return_value={}):
            self.assertIsNone(self.client.get(f"/species/{self.species[0]['id']}").json()['ebird_code'])
            self.assertIsNone(self.examples(self.species[0])['ebird_code'])
        spec = importlib.util.spec_from_file_location('build_ebird_codes', Path(__file__).parents[2] / 'scripts/build_ebird_codes.py')
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        with tempfile.TemporaryDirectory() as folder:
            source, output = Path(folder) / 'taxonomy.csv', Path(folder) / 'codes.json'
            source.write_text('\ufeffCATEGORY,Species_Code,SCIENTIFIC_NAME\nspecies,jabwar,Horornis diphone\nissf,x,Horornis diphone x\n'
                              'species,azutit,Cyanistes cyanus\n', encoding='utf-8')
            with patch.object(script, 'OUTPUT', output):
                script.main(source)
            self.assertEqual(list(json.loads(output.read_text(encoding='utf-8')).items()),
                             [('Cyanistes cyanus', 'azutit'), ('Horornis diphone', 'jabwar')])


if __name__ == '__main__':
    unittest.main()
