"""One label store: the 0.2 tables move into it, other names of a species merge, and every screen's answer is used alike."""
import json
import unittest

from labels import on_pieces
from storage import SCHEMA_VERSION, columns, connect, initialize
from test_species_flow import site


class LabelStoreCheck(unittest.TestCase):
    def test_schema_7_tables_move_into_labels(self):
        with site() as (root, client, species):
            sid = species['id']
            with connect(root) as db:
                # The 0.2.2 (schema 7) label tables, one row each, plus an unknown typed name on the sound map.
                db.executescript('''DROP TABLE picked_sounds;
                    CREATE TABLE picked_sounds (
                        recording_id INTEGER NOT NULL REFERENCES recordings ON DELETE CASCADE,
                        start_s REAL NOT NULL, end_s REAL NOT NULL, species TEXT,
                        sound_type TEXT NOT NULL DEFAULT '' CHECK(sound_type IN ('','song','call')),
                        quality TEXT NOT NULL DEFAULT '' CHECK(quality IN ('','A','B','C','D','E')),
                        others INTEGER NOT NULL DEFAULT 0, background TEXT NOT NULL DEFAULT '[]', exported_at TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                        PRIMARY KEY(recording_id,start_s,end_s));
                    CREATE TABLE window_answers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, species_id TEXT NOT NULL REFERENCES species,
                        recording_id INTEGER NOT NULL REFERENCES recordings, start_s REAL NOT NULL, end_s REAL NOT NULL,
                        verdict TEXT NOT NULL CHECK(verdict IN ('target','other','unknown')),
                        session_id INTEGER REFERENCES validation_sessions, quiz_snapshot TEXT, examples_snapshot TEXT,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
                    CREATE TABLE presence_marks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, dataset_id INTEGER NOT NULL REFERENCES datasets,
                        species_id TEXT NOT NULL REFERENCES species, recording_id INTEGER NOT NULL REFERENCES recordings,
                        start_s REAL NOT NULL, end_s REAL NOT NULL, run_id INTEGER REFERENCES search_runs,
                        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, UNIQUE(species_id,recording_id,start_s,end_s));
                    CREATE TABLE model_labels (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, species_id TEXT NOT NULL REFERENCES species,
                        segment_id INTEGER NOT NULL REFERENCES segments,
                        label TEXT NOT NULL CHECK(label IN ('target','other')), UNIQUE(species_id,segment_id));''')
                db.execute("INSERT INTO window_answers(species_id,recording_id,start_s,end_s,verdict,quiz_snapshot) VALUES(?,1,10,15,'other','{\"a\":1}')", (sid,))
                db.execute('INSERT INTO presence_marks(dataset_id,species_id,recording_id,start_s,end_s) VALUES(1,?,2,0,3)', (sid,))
                db.execute("INSERT INTO model_labels(species_id,segment_id,label) VALUES(?,1,'target')", (sid,))
                db.execute('''INSERT INTO picked_sounds(recording_id,start_s,end_s,species,background,quality)
                    VALUES(3,0,5,'Horornis diphone','["Parus minor","Nonexistent bird"]','A')''')
                db.execute('DROP TABLE labels')
                db.execute('PRAGMA user_version=7')
            self.assertTrue(initialize(root))
            from species import initialize_species
            initialize_species(root)
            with connect(root) as db:
                self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0], SCHEMA_VERSION)
                for table in ('window_answers', 'presence_marks', 'model_labels', 'picked_sounds_v7'):
                    self.assertEqual(columns(db, table), {})
                self.assertNotIn('species', columns(db, 'picked_sounds'))
                self.assertEqual(db.execute('SELECT quality FROM picked_sounds').fetchone()[0], 'A')
                tit, unknown = (db.execute('SELECT id FROM species WHERE scientific_name=?', (name,)).fetchone()[0]
                                for name in ('Parus cinereus', 'Nonexistent bird'))
                rows = [tuple(r) for r in db.execute('SELECT taxon_id,recording_id,start_s,end_s,verdict,source FROM labels ORDER BY id')]
                quiz = db.execute("SELECT json_extract(source_ref,'$.quiz_snapshot.a') FROM labels WHERE source='validation'").fetchone()[0]
                self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
            # Order sets the current answer: checks, marks, the sound map, then the species-AI page. "Parus minor" is
            # another name of Parus cinereus, so its label moved there.
            self.assertEqual(rows, [(sid, 1, 10, 15, 'absent', 'validation'), (sid, 2, 0, 3, 'present', 'species_mark'),
                                    (sid, 3, 0, 5, 'present', 'sound_map'), (tit, 3, 0, 5, 'present', 'sound_map'),
                                    (unknown, 3, 0, 5, 'present', 'sound_map'), (sid, 1, 0, 5, 'present', 'training')])
            self.assertEqual(quiz, 1)
            item = client.get('/explore/picked', params={'dataset': 1}).json()['items'][0]
            self.assertEqual((item['species']['scientific_name'], [b['scientific_name'] for b in item['background']]),
                             ('Horornis diphone', ['Parus cinereus', 'Nonexistent bird']))
            self.assertTrue(initialize(root))  # opening again changes nothing
            with connect(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM labels').fetchone()[0], 6)

    def test_other_names_of_a_species_are_listed_once(self):
        with site() as (root, client, species):
            for query in ('Parus minor', 'シジュウカラ'):
                found = [s['scientific_name'] for s in client.get('/species', params={'q': query}).json()]
                self.assertIn('Parus cinereus', found)
                self.assertNotIn('Parus minor', found)
            # Naming the other name names the species.
            client.post('/species', json={'scientific_name': 'Parus minor', 'ja': '試験の名前'})
            tit = client.get('/species', params={'q': 'Parus cinereus'}).json()[0]
            self.assertEqual(tit['common_names']['ja'], '試験の名前')

    def test_a_sound_map_answer_is_evidence_and_material_and_a_pick_records_nothing(self):
        with site() as (root, client, species):
            sid = species['id']
            dates = {'start_date': '2024-08-01', 'end_date': '2024-08-06'}
            response = client.put('/explore/picked', json={'dataset_id': 1, 'recording_id': 1, 'start_s': 0, 'end_s': 5,
                                                   'sound_type': '', 'quality': '', 'others': False})
            self.assertEqual((response.status_code, response.json()['species']), (200, None), response.text)
            with connect(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM labels').fetchone()[0], 0)
            client.post('/heard/map-answer', json={'dataset_id': 1, 'recording_id': 1, 'start_s': 0, 'end_s': 5, 'species_id': sid, 'verdict': 'present'})
            presence = client.get(f'/datasets/1/species/{sid}', params=dates).json()['presence']
            self.assertEqual((presence['status'], [e['origin'] for e in presence['evidence']]), ('confirmed', ['map']))
            ready = client.get(f'/datasets/1/species/{sid}/custom-model', params=dates).json()
            self.assertEqual((ready['examples']['positive_5s'], ready['examples']['other_answers']), (1, 1))
            client.delete('/explore/picked', params={'dataset': 1, 'recording_id': 1, 'start_s': 0, 'end_s': 5})
            with connect(root) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM labels').fetchone()[0], 1)

    def test_answers_on_five_second_pieces(self):
        pieces = {1: [(0, 5, 'a'), (5, 10, 'b'), (10, 15, 'c')]}
        row = lambda i, start, end, verdict, source='validation': {'id': i, 'recording_id': 1, 'start_s': start, 'end_s': end,
                                                                     'verdict': verdict, 'source': source}
        # "Absent" over 15 s holds for every piece; "present" over 15 s says nothing about one piece; 3 s inside one does.
        self.assertEqual(set(on_pieces([row(1, 0, 15, 'absent')], pieces)), {'a', 'b', 'c'})
        self.assertEqual(on_pieces([row(1, 0, 15, 'present')], pieces), {})
        self.assertEqual(on_pieces([row(1, 6, 9, 'present')], pieces)['b']['id'], 1)
        self.assertEqual(on_pieces([row(1, 0, 5, 'unknown')], pieces), {})
        # A later answer wins, except the species-AI page's.
        later = on_pieces([row(1, 0, 5, 'absent', 'training'), row(2, 0, 5, 'present')], pieces, first='training')
        self.assertEqual(later['a']['id'], 1)
        self.assertEqual(on_pieces([row(1, 0, 5, 'absent'), row(2, 0, 5, 'present')], pieces)['a']['id'], 2)


if __name__ == '__main__':
    unittest.main()
