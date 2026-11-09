"""Sound-map pick storage, editing, and export checks."""
import csv
import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from models import REGISTRY, main_identity
from picked import XC_FIELDS
from server import create_app
from species import initialize_species
from storage import SCHEMA_VERSION, connect, initialize


class PickedCheck(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        base = Path(self.temporary.name)
        self.root = base / 'data'
        self.models = base / 'models'
        for relative in REGISTRY['perch']['files']:
            path = self.models / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test model' if path.suffix == '.onnx' else b'label')
        self.app = create_app(self.root, self.models, 'test')
        self.client = TestClient(self.app, headers={'Authorization': 'Bearer test'})
        self.client.__enter__()
        self.source = base / 'source.wav'
        sf.write(self.source, np.linspace(-.2, .2, 12 * 8000, dtype=np.float32), 8000, subtype='PCM_24')
        self.other_source = base / 'other.wav'
        sf.write(self.other_source, np.zeros(12 * 8000, dtype=np.float32), 8000)
        with connect(self.root) as db:
            db.execute("INSERT INTO sites VALUES(1,'Fuji','Asia/Tokyo',35.12345,138.98765)")
            db.execute("INSERT INTO sites VALUES(2,'Other','UTC',1,2)")
            db.execute("INSERT INTO datasets VALUES(1,1,'Data','unused')")
            db.execute("INSERT INTO datasets VALUES(2,2,'Other data','unused')")
            for rid, dataset, path, recorded_at in (
                    (1, 1, self.source, '2024-01-01T15:30:00+00:00'),
                    (2, 2, self.other_source, '2024-01-01T15:30:00+00:00')):
                stat = path.stat()
                db.execute('''INSERT INTO recordings VALUES(?,?,?,'2024-01-02T00:30:00',
                    'Asia/Tokyo',?,12,8000,?,?)''',
                    (rid, dataset, str(path), recorded_at, stat.st_size, stat.st_mtime_ns))
            job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'complete',1)").lastrowid
            analysis = db.execute("INSERT INTO analyses VALUES(NULL,?,1,'perch','{}','unused')", (job,)).lastrowid
            segment_ids = []
            for start, end in ((0, 5), (5, 10), (10, 12)):
                segment_ids.append(db.execute('INSERT INTO segments VALUES(NULL,?,?,?)',
                                              (analysis, start, end)).lastrowid)
            engine, model_sha = main_identity(self.root)
            run = db.execute("INSERT INTO explore_runs(site_id,engine,model_sha,params) VALUES(1,?,?,?)",
                (engine, model_sha, json.dumps({'segment_count': 3}))).lastrowid
            db.executemany('INSERT INTO explore_points VALUES(?,?,?,?,?,?)',
                ((run, position, segment, 0, 0, '[]') for position, segment in enumerate(segment_ids)))
            db.execute("INSERT INTO explore_nodes VALUES(?,0,NULL,'',0,0,3,?,0,'[]')", (run, segment_ids[0]))

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def put(self, start=0, end=5, **changes):
        body = {'dataset_id': 1, 'recording_id': 1, 'start_s': start, 'end_s': end,
                'species': None, 'background': [], 'sound_type': '', 'quality': '', 'others': False}
        body.update(changes)
        return self.client.put('/explore/picked', json=body)

    def test_v5_upgrade_keeps_explore_rows_and_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertTrue(initialize(root))
            initialize_species(root)
            old_file = root / 'explore' / 'points-old.bin'
            old_file.parent.mkdir(); old_file.write_bytes(b'old')
            with connect(root) as db:
                db.execute("INSERT INTO sites VALUES(1,'Kept','Asia/Tokyo',35,138)")
                db.execute("INSERT INTO datasets VALUES(1,1,'Kept','unused')")
                db.execute("INSERT INTO recordings VALUES(1,1,'/old.wav','2024-01-01','Asia/Tokyo',"
                           "'2023-12-31T15:00:00+00:00',5,8000,1,1)")
                job = db.execute("INSERT INTO jobs(dataset_id,status,total) VALUES(1,'complete',1)").lastrowid
                analysis = db.execute("INSERT INTO analyses VALUES(NULL,?,1,'perch','{}','unused')", (job,)).lastrowid
                segment = db.execute('INSERT INTO segments VALUES(NULL,?,0,5)', (analysis,)).lastrowid
                run = db.execute("INSERT INTO explore_runs(site_id,engine,model_sha,params) VALUES(1,'perch','old',?)",
                                 (json.dumps({'points_file': old_file.name}),)).lastrowid
                db.execute("INSERT INTO explore_points VALUES(?,0,?,0,0,'[]')", (run, segment))
                db.execute("INSERT INTO explore_nodes VALUES(?,0,NULL,'',0,0,1,?,0,'[]')", (run, segment))
                db.execute('DROP TABLE picked_sounds')
                db.execute('PRAGMA user_version=5')
            self.assertTrue(initialize(root))
            with connect(root) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM explore_runs').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM explore_points').fetchone()[0], 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM explore_nodes').fetchone()[0], 1)
                self.assertTrue(db.execute("SELECT 1 FROM sqlite_master WHERE name='picked_sounds'").fetchone())
            self.assertEqual(old_file.read_bytes(), b'old')

    def test_put_get_delete_validation_and_site_membership(self):
        with connect(self.root) as db:
            known = dict(db.execute("SELECT scientific_name,id FROM species WHERE scientific_name IN "
                                    "('Horornis diphone','Parus cinereus')"))
        response = self.put(species='Horornis diphone', background=['Parus cinereus', 'Unknown bird'],
                            sound_type='song', quality='A', others=True)
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()
        self.assertEqual(item['species'], {'id': known['Horornis diphone'], 'scientific_name': 'Horornis diphone'})
        self.assertEqual(item['background'], [
            {'id': known['Parus cinereus'], 'scientific_name': 'Parus cinereus'},
            {'id': None, 'scientific_name': 'Unknown bird'}])
        self.assertTrue(item['others'])
        self.assertEqual(self.client.get('/explore/picked', params={'dataset': 1}).json()['items'], [item])
        invalid = self.put(start=5, end=5)
        self.assertEqual((invalid.status_code, invalid.json()), (422, {'detail': 'invalid_input'}))
        foreign = {**{'dataset_id': 1, 'recording_id': 2, 'start_s': 0, 'end_s': 5,
                      'species': None, 'background': [], 'sound_type': '', 'quality': '', 'others': False}}
        missing = self.client.put('/explore/picked', json=foreign)
        self.assertEqual((missing.status_code, missing.json()), (404, {'detail': 'not_found'}))
        deleted = self.client.delete('/explore/picked', params={
            'dataset': 1, 'recording_id': 1, 'start_s': 0, 'end_s': 5})
        self.assertEqual(deleted.json(), {'deleted': True})
        self.assertEqual(self.client.delete('/explore/picked', params={
            'dataset': 1, 'recording_id': 1, 'start_s': 0, 'end_s': 5}).json(), {'deleted': False})

    def test_export_zip_metadata_audio_manifest_and_errors(self):
        output = Path(self.temporary.name) / 'output'; output.mkdir()
        empty = self.client.post('/explore/picked/export', json={'dataset_id': 1, 'folder': str(output)})
        self.assertEqual((empty.status_code, empty.json()), (400, {'detail': 'nothing_picked'}))
        absent = self.client.post('/explore/picked/export', json={
            'dataset_id': 1, 'folder': str(output / 'missing')})
        self.assertEqual((absent.status_code, absent.json()), (400, {'detail': 'folder_missing'}))
        self.assertEqual(self.put(species='Horornis diphone', background=['Parus cinereus', 'Zosterops japonicus'],
                                  sound_type='call', quality='B', others=True).status_code, 200)
        response = self.client.post('/explore/picked/export', json={'dataset_id': 1, 'folder': str(output)})
        self.assertEqual(response.status_code, 200, response.text)
        path = Path(response.json()['path'])
        self.assertEqual(response.json()['count'], 1)
        self.assertEqual([item.resolve() for item in output.glob('*.zip')], [path.resolve()])
        with zipfile.ZipFile(path) as archive:
            metadata_bytes = archive.read('metadata.csv')
            self.assertTrue(metadata_bytes.startswith(b'\xef\xbb\xbf'))
            reader = csv.DictReader(io.StringIO(metadata_bytes.decode('utf-8-sig')))
            self.assertEqual(tuple(reader.fieldnames), XC_FIELDS)
            row = next(reader)
            self.assertEqual((row['recording date'], row['time of day']), ('2024-01-02', '00:30'))
            self.assertEqual((row['latitude'], row['longitude'], row['country'], row['location name']),
                             ('35.1234', '138.9877', 'Japan', 'Fuji'))
            self.assertEqual((row['genus'], row['species'], row['subspecies']),
                             ('Horornis', 'diphone', ''))
            self.assertEqual(row['background species'], 'Parus cinereus,Zosterops japonicus')
            self.assertEqual((row['sound type(s)'], row['quality']), ('call', 'B'))
            self.assertEqual(row['remarks'], 'Selected by listening on the Echoroo Lite sound map '
                             '(5 s interval at 0\u20135 s of the source recording).')
            audio_name = 'audio/' + row['filename']
            with sf.SoundFile(io.BytesIO(archive.read(audio_name))) as sound:
                self.assertEqual((sound.samplerate, sound.frames, sound.subtype), (8000, 40000, 'PCM_24'))
            manifest = json.loads(archive.read('manifest.json'))
            self.assertEqual(manifest['schema'], 'echoroo_picked_sounds_v1')
            self.assertEqual(manifest['site'], {'name': 'Fuji', 'timezone': 'Asia/Tokyo',
                                                'latitude': 35.12345, 'longitude': 138.98765})
            self.assertEqual(manifest['items'][0]['filename'], row['filename'])
            self.assertEqual(manifest['items'][0]['background'], ['Parus cinereus', 'Zosterops japonicus'])
            for filename, details in manifest['files'].items():
                content = archive.read(filename)
                self.assertEqual(details, {'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content)})
        with connect(self.root) as db:
            self.assertIsNotNone(db.execute('SELECT exported_at FROM picked_sounds').fetchone()[0])
        # Correcting a label after export keeps the mark: the sound has been handed out once already.
        self.assertIsNotNone(self.put(quality='A').json()['exported_at'])


if __name__ == '__main__':
    unittest.main()
