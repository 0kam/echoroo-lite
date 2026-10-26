"""Check full-score aggregation, local calendar effort and report exports."""
import csv
import io
import json
import tempfile
import unittest
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from reports import calendar_cells, effort
from server import create_app
from storage import connect


class ReportCheck(unittest.TestCase):
    def test_reports_from_complete_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            (root/'models.json').write_text(json.dumps({'main':'perch','extra':['birdnet'],'confirmed':True}))
            for name, seconds in [('235945', 30), ('120000', 17)]:
                day = '20240808' if name == '235945' else '20240809'
                sf.write(root / f'rec_{day}_{name}.wav', np.ones(24000 * seconds, dtype=np.float32) * .1, 24000)
            with TestClient(create_app(root, root, 'test'), headers={'Authorization': 'Bearer test'}) as client:
                client.post('/imports', json=dict(folder=str(root), site_name='=formula', dataset_name='Test', timezone='Asia/Tokyo', latitude=35, longitude=138))
                taxon = client.get('/species?q=ウグイス').json()[0]
                species_a, species_b = 'Horornis diphone', 'Zosterops japonicus'
                labels = [species_a + '_Japanese Bush Warbler', species_b + '_Japanese White-eye']
                scores = np.full((10, 2), .01)
                scores[1, 0], scores[6, 1] = .8, .9
                bird_path = root / 'birdnet.npz'
                np.savez(bird_path, start_s=np.repeat(np.arange(10) * 3, 2), end_s=np.repeat(np.arange(1, 11) * 3, 2),
                         labels=np.tile(labels, 10), scores=scores.flatten())
                logits = np.zeros((6, 3))
                logits[0, 0], logits[1, 2], logits[4, 1] = 3, 10, 5
                perch_path = root / 'perch.npz'
                np.savez(perch_path, labels=[species_a, species_b, 'Outside region'], logits=logits)
                with connect(root) as db:
                    db.execute("INSERT INTO jobs(id,dataset_id,status,total) VALUES(1,1,'complete',2)")
                    for aid, engine, path in [(1, 'birdnet', bird_path), (2, 'perch', perch_path)]:
                        meta = {'model_sha256': engine, 'model': engine, 'species': labels, 'candidate_filter': {'species': labels}}
                        db.execute('INSERT INTO analyses VALUES(?,1,1,?,?,?)', (aid, engine, json.dumps(meta), str(path)))
                opts = client.get('/reports/options?dataset_id=1').json()
                self.assertEqual((opts['first_date'], opts['last_date']), ('2024-08-08', '2024-08-09'))
                self.assertEqual({m['engine'] for m in opts['models']}, {'birdnet', 'perch'})
                body = dict(dataset_id=1, engine='birdnet', model_sha256='birdnet', threshold=.5,
                            start_date='2024-08-08', end_date='2024-08-09', species_id=taxon['id'])
                response = client.post('/reports/detection', json=body)
                self.assertEqual(response.status_code, 200, response.text)
                detection = response.json()
                summary = next(s for s in detection['summaries'] if s['species']['id'] == taxon['id'])
                self.assertEqual((summary['candidate_windows'], summary['eligible_windows'], summary['candidate_days'], summary['recorded_days']), (1, 10, 1, 2))
                self.assertEqual(detection['evidence'][0]['start_s'], 3)
                self.assertEqual(detection['evidence'][0]['local_time'], '2024-08-08T23:59:48+09:00')
                cells = {(c['date'], c['hour']): c for c in detection['cells']}
                self.assertEqual(cells['2024-08-08', 23]['candidate_rate'], .2)
                self.assertEqual(cells['2024-08-09', 0]['candidate_rate'], 0)
                self.assertEqual(cells['2024-08-09', 12]['status'], 'unanalysed')
                self.assertEqual(cells['2024-08-09', 10]['status'], 'no_recording')
                self.assertEqual(sum(c['recorded_seconds'] for c in cells.values()), 47)
                self.assertEqual(sum(c['effective_seconds'] for c in cells.values()), 30)
                perch = client.post('/reports/detection', json={**body, 'engine': 'perch', 'model_sha256': 'perch'}).json()
                self.assertEqual(len(perch['summaries']), 2)
                self.assertEqual(perch['summaries'][0]['eligible_windows'], 6)
                self.assertTrue(all(s['species']['scientific_name'] != 'Outside region' for s in perch['summaries']))
                filtered = client.get(f"/reports/{detection['id']}?cell_date=2024-08-09&hour=0").json()
                self.assertEqual(filtered['evidence_count'], 0)
                artifact = client.post(f"/reports/{detection['id']}/export", json={'folder': str(root),
                    'audio': [{'recording_id': 1, 'start_s': 3, 'end_s': 6}], 'svg': '<svg xmlns="http://www.w3.org/2000/svg"/>'}).json()
                with zipfile.ZipFile(artifact['path']) as package:
                    self.assertIn('spiral.svg', package.namelist())
                    exported = list(csv.DictReader(io.StringIO(package.read('evidence.csv').decode('utf-8-sig'))))
                    self.assertEqual(len(exported), 1)
                    self.assertEqual(exported[0]['site_name'], "'=formula")
                    audio, rate = sf.read(io.BytesIO(package.read(exported[0]['audio_file'])))
                    self.assertEqual((len(audio), rate), (96000, 32000))
                    quality = list(csv.DictReader(io.StringIO(package.read('quality.csv').decode('utf-8-sig'))))[0]
                    self.assertEqual((quality['precision'], quality['recall'], quality['quality']), ('', '', 'not_evaluated'))
                self.assertEqual(client.post('/reports/detection', json={**body, 'end_date': '2026-08-09'}).json()['detail'], 'report_range')
                self.assertEqual(client.post('/reports/detection', json={**body, 'model_sha256': 'missing'}).json()['detail'], 'no_report_analyses')
                with connect(root) as db:
                    # A newer incompatible model must not silently fall back to old predictions.
                    db.execute("INSERT INTO jobs(id,dataset_id,status,total) VALUES(2,1,'complete',1)")
                    db.execute("INSERT INTO analyses VALUES(3,2,1,'birdnet',?,?)", (json.dumps({'model_sha256': 'new'}), str(bird_path)))
                self.assertEqual(client.post('/reports/detection', json=body).json()['detail'], 'no_report_analyses')
                self.assertEqual(client.get(f"/reports/{detection['id']}").json()['summaries'], detection['summaries'])
                historical = client.post('/reports/detection', json={**body, 'base_report_id': detection['id']}).json()
                self.assertEqual(historical['summaries'], detection['summaries'])
                self.assertEqual(historical['analysis_ids'], [1])
                self.assertEqual(client.get('/reports?dataset_id=1').json()[0]['id'], historical['id'])
            # The clock repeats, but elapsed recording effort is counted exactly once.
            rec = {'recorded_at': '2024-11-03T05:30:00+00:00', 'seconds': 7200}
            values = list(effort(rec, ZoneInfo('America/New_York')))
            self.assertEqual(sum(seconds for (day, hour), seconds in values if hour == 1), 5400)
            self.assertEqual(sum(seconds for (day, hour), seconds in values if hour == 2), 1800)
            leap = calendar_cells([], ZoneInfo('Asia/Tokyo'), date(2024, 1, 1), date(2024, 12, 31))
            self.assertEqual(len(leap), 366 * 24)


if __name__ == '__main__':
    unittest.main()
