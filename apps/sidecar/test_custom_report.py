"""End-to-end custom-model creation, evaluation, and portable report checks."""
import csv
import hashlib
import io
import json
import unittest
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from results_workflow import fingerprint
from storage import connect
from species import names_for
from test_custom_models import custom_model_fixture
from server import create_app


def make_report_audio(root):
    """Replace the fitting fixture's byte stubs with tiny valid source audio."""
    for recording_id in range(1, 7):
        path = root / f'recording-{recording_id}.wav'
        sf.write(path, np.zeros(30_000, dtype=np.float32), 1000)
    with connect(root) as db:
        for recording_id in range(1, 7):
            path = root / f'recording-{recording_id}.wav'
            stat = path.stat()
            db.execute('UPDATE recordings SET sample_rate=?,size=?,mtime_ns=? WHERE id=?',
                       (1000, stat.st_size, stat.st_mtime_ns, recording_id))
    current = fingerprint(root, 1)
    with connect(root) as db:
        db.execute('UPDATE annotation_sessions SET fingerprint=? WHERE id=1', (current,))


def prepare_report_search(root, run, segment_ids):
    """Make the fixture's label-source search run a real, exportable search."""
    with connect(root) as db:
        dataset = dict(db.execute('SELECT * FROM datasets WHERE id=1').fetchone())
        site = dict(db.execute('SELECT * FROM sites WHERE id=1').fetchone())
        species = names_for(db.execute('SELECT * FROM species WHERE id=?', ('custom-model-species',)).fetchone())
        analysis_ids = [row['id'] for row in db.execute(
            'SELECT id FROM analyses ORDER BY id')]
        for analysis_id in analysis_ids:
            path = root / f'perch-{analysis_id}.npz'
            np.savez(path, labels=['Test bird', 'Other'], logits=np.zeros((6, 2), dtype=np.float32))
            db.execute('UPDATE analyses SET scores_path=? WHERE id=?', (str(path), analysis_id))
        snapshot = {
            'species': species, 'dataset': dataset, 'site': site,
            'start_date': '2024-08-08', 'end_date': '2024-08-08',
            'date_scope': 'recording_start_local', 'model_sha256': 'perch-sha',
            'references': [], 'analysis_ids': analysis_ids,
            'total_recordings': 6, 'searched_recordings': 6,
            'unsearched_recordings': 0, 'searched_segments': 36,
        }
        db.execute('UPDATE search_runs SET snapshot=? WHERE id=?',
                   (json.dumps(snapshot), run))
        db.execute('INSERT INTO search_hits(run_id,segment_id,similarity,reference_index,query_window) '
                   'VALUES(?,?,?,?,?)', (run, segment_ids[5, 0], .8, 0, 0))


class CustomReportCheck(unittest.TestCase):
    def test_custom_creation_species_result_and_immutable_export(self):
        with custom_model_fixture() as (root, annotation, _segments, run):
            make_report_audio(root)
            prepare_report_search(root, run, _segments)
            headers = {'Authorization': 'Bearer test'}
            with TestClient(create_app(root, root, 'test'), headers=headers) as client:
                created = client.post('/custom-models', json={
                    'annotation_session_id': annotation, 'species_id': 'custom-model-species',
                })
                self.assertEqual(created.status_code, 200, created.text)
                model = created.json()
                model_id = model['id']
                self.assertEqual(model['status'], 'ready')
                self.assertGreaterEqual(model['cv']['reference_precision']['value'], .9)
                detail = client.get('/datasets/1/species/custom-model-species', params={
                    'start_date': '2024-08-08', 'end_date': '2024-08-08',
                    'engine': 'custom', 'model_id': model_id,
                })
                self.assertEqual(detail.status_code, 200, detail.text)
                report = detail.json()['report']
                self.assertEqual((report['engine'], report['model_id'], report['window_s']),
                                 ('custom', model_id, 15))
                self.assertNotEqual(report['engine'], 'perch')
                self.assertEqual(report['threshold'], model['cv']['threshold'])

                validation = client.post('/validations', json={'model_id': model_id})
                self.assertEqual(validation.status_code, 200, validation.text)
                validation = validation.json()
                while validation['question']:
                    question = validation['question']
                    validation = client.post(
                        f"/validations/{validation['id']}/answers",
                        json={'item_id': question['id'], 'verdict': 'target'},
                    ).json()
                self.assertEqual(validation['stage'], 'complete')
                overview = client.get('/datasets/1/overview').json()
                taxon = next(row for row in overview['species'] if row['species']['id'] == 'custom-model-species')
                self.assertEqual(taxon['custom_models'][0]['validation']['id'], validation['id'])
                self.assertEqual(taxon['custom_models'][0]['validation']['stage'], 'complete')

                detail = client.get('/datasets/1/species/custom-model-species', params={
                    'start_date': '2024-08-08', 'end_date': '2024-08-08',
                    'engine': 'custom', 'model_id': model_id,
                }).json()
                body = {
                    'dataset_id': 1, 'start_date': '2024-08-08', 'end_date': '2024-08-08',
                    'revision': detail['revision'], 'species_ids': ['custom-model-species'],
                    'folder': str(root), 'locale': 'en',
                }
                exported = client.post('/workflow-exports', json=body)
                self.assertEqual(exported.status_code, 200, exported.text)
                package = Path(exported.json()['path'])
                original = package.read_bytes()
                with zipfile.ZipFile(package) as archive:
                    names = set(archive.namelist())
                    prefix = f'models/{model_id}/'
                    self.assertTrue({prefix + name for name in ('model.npz', 'scores.npz', 'provenance.json')} <= names)
                    provenance = json.loads(archive.read(prefix + 'provenance.json'))
                    for key, filename in (('artifact_sha256', 'model.npz'), ('scores_sha256', 'scores.npz')):
                        self.assertEqual(provenance[key], hashlib.sha256(archive.read(prefix + filename)).hexdigest())
                    manifest = json.loads(archive.read('manifest.json'))
                    custom_result = next(result for result in manifest['results'] if result['engine'] == 'custom')
                    figure = custom_result['figure_id']
                    self.assertEqual(custom_result['engine'], 'custom')
                    self.assertNotEqual(custom_result['engine'], 'perch')
                    self.assertEqual((custom_result['threshold'], custom_result['window_s']),
                                     (model['cv']['threshold'], 15))
                    self.assertIn(f'figures/{figure}.png', names)
                    self.assertIn(f'figures/{figure}.svg', names)
                    scores = list(csv.DictReader(io.TextIOWrapper(
                        archive.open(f'scores/{figure}.csv'), encoding='utf-8-sig')))
                    self.assertTrue(scores)
                    self.assertTrue(all(float(row['end_s']) - float(row['start_s']) == 15 for row in scores))
                    species_rows = list(csv.DictReader(io.TextIOWrapper(archive.open('species.csv'), encoding='utf-8-sig')))
                    custom_rows = [row for row in species_rows if row.get('engine') == 'custom'
                                   and row.get('figure_id') == figure]
                    self.assertEqual(len(custom_rows), 1)
                    self.assertEqual(int(custom_rows[0]['candidate_windows']), sum(
                        float(row['score']) >= model['cv']['threshold'] for row in scores))
                    html = archive.read('index.html').decode()
                    self.assertIn('Cross-validation separates selected 5-second training examples', html)
                    self.assertIn('evaluated separately on 15-second intervals from recordings excluded from training', html)
                    self.assertIn('4 present / 8 absent', html)
                    self.assertIn('4 recordings used to create the model were excluded from quality evaluation', html)
                    self.assertIn('Plots and aggregates also include training recordings', html)
                    quality = list(csv.DictReader(io.TextIOWrapper(archive.open('quality.csv'), encoding='utf-8-sig')))
                    custom_quality = [row for row in quality if row.get('engine') == 'custom'
                                      and row.get('figure_id') == figure]
                    self.assertEqual(len(custom_quality), 1)
                    self.assertEqual(int(custom_quality[0]['evaluation_reviewed']), validation['evaluated_count'])
                    self.assertNotIn('reference_precision', quality[0])
                    validation_export = json.loads(archive.read(f"validation/{validation['id']}.json"))
                    self.assertTrue({item['recording_id'] for item in validation_export['items']} <= {5, 6})
                    for filename, entry in manifest['files'].items():
                        self.assertEqual(hashlib.sha256(archive.read(filename)).hexdigest(), entry['sha256'])
                # Correcting a later evaluation answer cannot mutate the published ZIP.
                last = validation['history'][-1]
                client.post(f"/validations/{validation['id']}/answers", json={
                    'item_id': last['id'], 'verdict': 'other',
                })
                self.assertEqual(package.read_bytes(), original)
                self.assertEqual(client.get('/datasets/1/exports').status_code, 200)


if __name__ == '__main__':
    unittest.main()
