import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

from custom_models import TARGET_PRECISION, _prior_evaluation_recordings, readiness
from storage import connect, initialize
from test_custom_models import SPECIES, custom_model_fixture


class CustomModelHoldoutTest(unittest.TestCase):
    def test_readiness_reserves_only_unseen_evaluation_recordings_for_a_new_model(self):
        with custom_model_fixture() as (root,annotation,_,_):
            with connect(root) as db:
                session=db.execute("INSERT INTO validation_sessions(dataset_id,species_id,fingerprint,snapshot) VALUES(1,?,'old','{}')",(SPECIES,)).lastrowid
                item=db.execute("INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,stratum,scores) VALUES(?,'evaluation',0,5,0,'x','{}')",(session,)).lastrowid
                db.execute("INSERT INTO validation_answers(item_id,verdict) VALUES(?,'target')",(item,))
            state=readiness(root,annotation,SPECIES)
            self.assertEqual(state['split']['evaluation_recording_ids'],[6])
            self.assertEqual(state['split']['prior_evaluation_recording_ids'],[5])
            self.assertEqual(state['split']['excluded_evaluation_group_recording_ids'],[5])

    def test_prior_same_species_evaluation_excludes_the_current_whole_day_group(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);initialize(root)
            with connect(root) as db:
                db.execute("CREATE TABLE species(id TEXT PRIMARY KEY, scientific_name TEXT NOT NULL UNIQUE, names TEXT NOT NULL DEFAULT '{}')")
                db.executemany("INSERT INTO species VALUES(?,?, '{}')",[('target','Target bird'),('other','Other bird')])
                db.execute("INSERT INTO sites VALUES(1,'Site','Asia/Tokyo',35,138)")
                db.executemany("INSERT INTO datasets VALUES(?,1,?,?)",[(1,'Current',temporary),(2,'Alias',temporary)])
                times=['2026-01-01T00:00:00+00:00','2026-01-01T08:00:00+00:00','2026-01-02T00:00:00+00:00','2026-01-03T00:00:00+00:00']
                for rid,recorded_at in enumerate(times,1):
                    db.execute("INSERT INTO recordings VALUES(?,?,?,'2026-01-01T00:00:00','Asia/Tokyo',?,30,24000,1,1)",
                        (rid,1,str(root/f'{rid}.wav'),recorded_at))
                def answer(species,phase,recording,dataset=2):
                    session=db.execute("INSERT INTO validation_sessions(dataset_id,species_id,fingerprint,snapshot) VALUES(?,?,?,'{}')",
                        (dataset,species,'old')).lastrowid
                    item=db.execute("INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,stratum,scores) VALUES(?,?,0,?,0,'x','{}')",
                        (session,phase,recording)).lastrowid
                    db.execute("INSERT INTO validation_answers(item_id,verdict) VALUES(?,'target')",(item,))
                answer('target','evaluation',1)
                answer('target','calibration',3)
                answer('other','evaluation',4)
                records={row['id']:dict(row) for row in db.execute('SELECT * FROM recordings')}
                prior,excluded=_prior_evaluation_recordings(db,'target',records,'local_day',ZoneInfo('Asia/Tokyo'))
                self.assertEqual(prior,[1])
                self.assertEqual(excluded,[1,2])
                self.assertEqual(_prior_evaluation_recordings(db,'target',records,'recording',ZoneInfo('Asia/Tokyo')),([1],[1]))

    def test_custom_cv_target_remains_separate_at_ninety_percent(self):
        self.assertEqual(TARGET_PRECISION,.9)


if __name__=='__main__':unittest.main()
