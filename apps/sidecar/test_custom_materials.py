"""Label-source regression checks for custom-model training material."""
import json
import unittest

from custom_models import create, readiness
from storage import connect
from test_custom_models import SPECIES, custom_model_fixture


class CustomMaterialCheck(unittest.TestCase):
    def test_only_explicit_current_calibration_labels_become_examples(self):
        with custom_model_fixture() as (root, annotation, segments, run):
            with connect(root) as db:
                def annotation_item(position, recording_id, start, ranges, complete=True):
                    item = db.execute('''INSERT INTO annotation_items(session_id,position,recording_id,start_s,end_s,stratum)
                        VALUES(?,?,?,?,?,'test')''', (annotation, position, recording_id, start, start + 15)).lastrowid
                    answer = db.execute('''INSERT INTO annotation_answers(item_id,ranges,complete,quiz_snapshot,palette_ids)
                        VALUES(?,?,?,'{}','[]')''', (item, json.dumps(ranges), int(complete))).lastrowid
                    return item, answer

                target = [{'species_id': SPECIES, 'start_s': 16., 'end_s': 17.}]
                _, positive_answer = annotation_item(0, 2, 15., target)
                draft_item, _ = annotation_item(1, 1, 15., target)
                db.execute('''INSERT INTO annotation_answers(item_id,ranges,complete,quiz_snapshot,palette_ids)
                    VALUES(?,?,0,'{}','[]')''', (draft_item, json.dumps(target)))
                annotation_item(2, 2, 0., [])  # Unmentioned is not a negative label.

                validation_snapshot = json.dumps({'window_s': 15, 'kind': 'detection'})
                validation = db.execute('''INSERT INTO validation_sessions(dataset_id,species_id,fingerprint,snapshot)
                    VALUES(1,?,'fixture',?)''', (SPECIES, validation_snapshot)).lastrowid
                def validation_answer(phase, position, recording_id, start, verdict):
                    item = db.execute('''INSERT INTO validation_items(session_id,phase,position,recording_id,start_s,stratum,scores)
                        VALUES(?,?,?,?,?,'test','{}')''', (validation, phase, position, recording_id, start)).lastrowid
                    return item, db.execute('''INSERT INTO validation_answers(item_id,verdict,quiz_snapshot,ranges)
                        VALUES(?,?,'{}','[]')''', (item, verdict)).lastrowid

                parent_item, parent_answer = validation_answer('calibration', 0, 3, 15., 'other')
                validation_answer('calibration', 1, 4, 0., 'other')  # Conflicts with an explicit target.
                evaluation_item, evaluation_answer = validation_answer('evaluation', 0, 4, 15., 'other')
                db.execute("INSERT INTO search_judgements(run_id,species_id,segment_id,verdict,note) VALUES(?,?,?,'unknown','')",
                    (run, SPECIES, segments[1, 2]))

            public = readiness(root, annotation, SPECIES)
            self.assertTrue(public['can_train'], public['reasons'])
            self.assertEqual((public['examples']['positive_5s'], public['examples']['negative_5s']), (4, 10))
            self.assertEqual((public['examples']['unknown_excluded'], public['examples']['evaluation_answers_excluded']), (1, 1))
            self.assertEqual(public['examples']['label_conflicts'], 1)
            self.assertEqual(public['examples']['by_source']['annotation_range'], 1)

            model = create(root, annotation, SPECIES)
            self.assertEqual((model['training']['positive_5s'], model['training']['negative_5s']), (4, 10))
            with connect(root) as db:
                snapshot = json.loads(db.execute('SELECT snapshot FROM custom_models WHERE id=?', (model['id'],)).fetchone()[0])
            examples = snapshot['training']['examples']
            self.assertNotIn(segments[1, 3], {row['segment_id'] for row in examples})  # Latest annotation is a draft.
            self.assertNotIn(segments[1, 2], {row['segment_id'] for row in examples})  # Latest judgement is unknown.
            self.assertNotIn(segments[4, 0], {row['segment_id'] for row in examples})  # Conflicting labels are excluded.
            self.assertIn(positive_answer, {source['source_answer_id'] for row in examples for source in row['sources']})
            self.assertFalse(any(source['source_kind'] == 'validation_calibration'
                                 and source['source_id'] == evaluation_item and source['source_answer_id'] == evaluation_answer
                                 for row in examples for source in row['sources']))
            def from_parent(source):
                return (source['source_kind'] == 'validation_calibration'
                        and source['source_id'] == parent_item
                        and source['source_answer_id'] == parent_answer)
            expanded = [row for row in examples if any(from_parent(source) for source in row['sources'])]
            self.assertEqual([row['start_s'] for row in expanded], [15., 20., 25.])
            self.assertTrue(all(any(from_parent(source) for source in row['sources']) for row in expanded))


if __name__ == '__main__':
    unittest.main()
