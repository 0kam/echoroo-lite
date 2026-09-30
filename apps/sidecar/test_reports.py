"""Check full-score aggregation, local calendar effort, frozen judgements and report exports."""
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
                    for i in range(6):
                        db.execute('INSERT INTO segments VALUES(?,2,?,?)', (i + 1, i * 5, (i + 1) * 5))
                    snap = {'species': taxon, 'dataset': dict(db.execute('SELECT * FROM datasets').fetchone()),
                            'site': dict(db.execute('SELECT * FROM sites').fetchone()), 'analysis_ids': [2], 'model_sha256': 'perch', 'references': []}
                    db.execute('INSERT INTO search_runs VALUES(1,1,?,?,CURRENT_TIMESTAMP)', (taxon['id'], json.dumps(snap)))
                    db.executemany('INSERT INTO search_hits VALUES(1,?,?,0,0)', enumerate([-.5, .2, .4, .6, .8, .9], 1))
                    for sid, verdict in [(1, 'target'), (2, 'other'), (3, 'unknown')]:
                        db.execute("INSERT INTO search_judgements(run_id,species_id,segment_id,verdict,note) VALUES(1,?,?,?,'')", (taxon['id'], sid, verdict))
                opts = client.get('/reports/options?dataset_id=1').json()
                self.assertEqual((opts['first_date'], opts['last_date']), ('2024-08-08', '2024-08-09'))
                self.assertEqual({m['engine'] for m in opts['models']}, {'birdnet', 'perch'})
                body = dict(dataset_id=1, engine='birdnet', model_sha256='birdnet', threshold=.5,
                            start_date='2024-08-08', end_date='2024-08-09', species_id=taxon['id'])
                response = client.post('/reports/detection', json=body)
                self.assertEqual(response.status_code, 200, response.text)
                detection = response.json()
                summary = next(s for s in detection['summaries'] if s['species']['id'] == taxon['id'])
                self.assertEqual((summary['candidate_windows'], summary['eligible_windows'], summary['candidate_days'], summary['recorded_days']), (1, 2, 1, 2))
                self.assertEqual(detection['evidence'][0]['start_s'], 3)
                self.assertEqual(detection['evidence'][0]['local_time'], '2024-08-08T23:59:48+09:00')
                cells = {(c['date'], c['hour']): c for c in detection['cells']}
                self.assertEqual(cells['2024-08-08', 23]['candidate_rate'], 1)
                self.assertEqual(cells['2024-08-09', 0]['candidate_rate'], 0)
                self.assertEqual(cells['2024-08-09', 12]['status'], 'unanalysed')
                self.assertEqual(cells['2024-08-09', 10]['status'], 'no_recording')
                self.assertEqual(sum(c['recorded_seconds'] for c in cells.values()), 47)
                self.assertEqual(sum(c['effective_seconds'] for c in cells.values()), 30)
                perch = client.post('/reports/detection', json={**body, 'engine': 'perch', 'model_sha256': 'perch'}).json()
                self.assertEqual(len(perch['summaries']), 2)
                self.assertEqual(perch['summaries'][0]['eligible_windows'], 2)
                self.assertTrue(all(s['species']['scientific_name'] != 'Outside region' for s in perch['summaries']))
                report_body = dict(run_id=1, start_date='2024-08-08', end_date='2024-08-09', threshold=.7)
                search = client.post('/reports/search', json=report_body).json()
                self.assertEqual((search['summaries'][0]['candidate_windows'], search['summaries'][0]['eligible_windows']), (2, 6))
                self.assertEqual(search['judgements'], dict(target=1, other=1, unknown=1, unreviewed=3))
                self.assertEqual(sum(b['count'] for b in search['distribution']), 6)
                self.assertEqual(search['evidence'][0]['score'], -.5)  # Confirmed evidence survives the threshold.
                before = next(c for c in search['cells'] if c['date'] == '2024-08-08' and c['hour'] == 23)
                self.assertEqual(before['confirmed_windows'], 1)
                self.assertAlmostEqual(before['mean_similarity'], (.2 + .4 - .5) / 3)
                self.assertEqual(before['max_similarity'], .4)
                client.post('/search-runs/1/judgements', json=dict(segment_id=1, verdict='other'))
                self.assertEqual(client.get(f"/reports/{search['id']}").json()['judgements']['target'], 1)
                self.assertEqual(client.post('/reports/search', json=report_body).json()['judgements']['target'], 0)
                filtered = client.get(f"/reports/{search['id']}?cell_date=2024-08-09&hour=0").json()
                self.assertEqual(filtered['evidence_count'], 2)
                no_threshold = client.post('/reports/search', json={**report_body, 'threshold': None}).json()
                self.assertIsNone(no_threshold['summaries'][0]['candidate_rate'])
                self.assertEqual(no_threshold['total_evidence'], 6)
                for sid in range(1, 7):
                    client.post('/search-runs/1/judgements', json={'segment_id': sid, 'verdict': 'target' if sid >= 4 else 'other'})
                suggestion = client.post('/reports/search', json=report_body).json()['threshold_suggestion']
                self.assertAlmostEqual(suggestion['threshold'], .5)
                self.assertEqual(suggestion['purpose'], 'exploration_only')
                artifact = client.post(f"/reports/{search['id']}/export", json={'folder': str(root),
                    'audio': [{'recording_id': 1, 'start_s': 0, 'end_s': 5}], 'svg': '<svg xmlns="http://www.w3.org/2000/svg"/>'}).json()
                with zipfile.ZipFile(artifact['path']) as package:
                    self.assertIn('spiral.svg', package.namelist())
                    exported = list(csv.DictReader(io.StringIO(package.read('evidence.csv').decode('utf-8-sig'))))
                    self.assertEqual(len(exported), 5)
                    self.assertEqual(exported[0]['site_name'], "'=formula")
                    self.assertEqual(exported[0]['status'], 'target')
                    clip = exported[0]['audio_file']
                    audio, rate = sf.read(io.BytesIO(package.read(clip)))
                    self.assertEqual((len(audio), rate), (160000, 32000))
                    quality = list(csv.DictReader(io.StringIO(package.read('quality.csv').decode('utf-8-sig'))))[0]
                    self.assertEqual((quality['precision'], quality['recall'], quality['quality']), ('', '', 'not_evaluated'))
                    self.assertEqual(quality['species_id'], search['species']['id'])
                    saved = json.loads(package.read('report.json'))
                    self.assertEqual(saved['judgements']['target'], 1)
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
