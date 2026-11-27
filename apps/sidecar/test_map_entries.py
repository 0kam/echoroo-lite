"""Ways into the sound map: the groups to visit for a species, example sounds, or sounds the AI likely mixed up."""
import unittest
from unittest.mock import patch

import numpy as np

import explore
import labels
import map_entries
from storage import connect
from test_explore import ExploreCheck


def tree(*rows):
    nodes = {node: {'node': node, 'parent': parent, 'lo': lo, 'hi': hi, 'children': []} for node, parent, lo, hi in rows}
    for node in nodes.values():
        if node['parent'] is not None:
            nodes[node['parent']]['children'].append(node['node'])
    return nodes


class FlagRuleTest(unittest.TestCase):
    #   0 [0,100) -> 1 [0,60) -> 3 [0,20), 4 [20,60) -> 5 [20,30), 6 [30,60)
    #             -> 2 [60,100)
    NODES = ((0, None, 0, 100), (1, 0, 0, 60), (2, 0, 60, 100), (3, 1, 0, 20), (4, 1, 20, 60), (5, 4, 20, 30), (6, 4, 30, 60))

    def flagged(self, nodes, positions, listened=()):
        size = nodes[0]['hi']
        targets, heard = np.zeros(size, dtype=bool), np.zeros(size, dtype=bool)
        targets[list(positions)] = True; heard[list(listened)] = True
        return [(g['node'], g['targets'], g['left']) for g in map_entries.flags(nodes, targets, heard)]

    def test_a_dense_group_is_found_at_its_level_and_sparse_targets_get_no_flag(self):
        nodes = tree(*self.NODES)
        # 11 of 100, of 60 and of 40 are too thin; all of node 5 is dense. The target at 31 is left scattered.
        self.assertEqual(self.flagged(nodes, [*range(20, 30), 31], listened=[20, 21]), [(5, 10, 8)])
        # 16 of 40 is dense: the search stops there and does not go on to the denser part inside.
        self.assertEqual(self.flagged(nodes, range(20, 36)), [(4, 16, 16)])
        self.assertEqual(self.flagged(nodes, [1, 35, 70, 90]), [])
        self.assertEqual(self.flagged(nodes, []), [])
        # Half of a group, but fewer than three sounds.
        small = tree((0, None, 0, 10), (1, 0, 0, 4), (2, 0, 4, 10))
        self.assertEqual(self.flagged(small, [0, 1]), [])

    def test_the_whole_map_is_flagged_only_without_dense_parts(self):
        small = tree((0, None, 0, 6), (1, 0, 0, 3), (2, 0, 3, 6))
        self.assertEqual(self.flagged(small, [0, 1, 3, 4]), [(0, 4, 4)])
        self.assertEqual(self.flagged(small, [0, 1, 2, 3]), [(1, 3, 3)])

    def test_at_most_ten_flags_most_unheard_targets_first(self):
        nodes = tree((0, None, 0, 120), *((part + 1, 0, 10 * part, 10 * part + 10) for part in range(12)))
        positions = [10 * part + at for part in range(12) for at in range(5)] + [35]
        # Part 1 was listened to completely, part 2 but for one sound; part 4 has one target more than the others.
        found = self.flagged(nodes, positions, listened=[*range(5), *range(10, 14)])
        self.assertEqual(len(found), map_entries.MOST)
        self.assertEqual((found[0], found[1], found[-1]), ((4, 6, 6), (3, 5, 5), (12, 5, 5)))
        self.assertFalse({1, 2} & {node for node, _, _ in found})


class MapEntryApi(ExploreCheck):
    def test_entries_name_groups_targets_and_what_was_heard(self):
        self.add_analysis(64, 1)
        self.wait_job(self.client.post('/explore', json={'dataset_id': 1}).json()['id'])
        with connect(self.root) as db:
            ids = {r['scientific_name']: r['id'] for r in db.execute('SELECT id,scientific_name FROM species')}
            points = [dict(r) for r in db.execute('''SELECT p.position,s.start_s,p.candidates FROM explore_points p
                JOIN segments s ON s.id=p.segment_id ORDER BY p.position''')]
            # Rows 1, 5, 9, ... score the white-eye highest; one of them and one other sound are heard by ear.
            labels.put(db, ids['Zosterops japonicus'], 1, 5, 10, 'present', 'species_mark')
            labels.put(db, ids['Zosterops japonicus'], 1, 0, 5, 'present', 'sound_map')
            labels.put(db, ids['Zosterops japonicus'], 1, 10, 15, 'absent', 'training')
            labels.put_no_bird(db, 1, 15, 20, 'sound_map')
        at = {p['start_s']: p['position'] for p in points}
        found = self.client.get('/explore/entries/species', params={'dataset': 1, 'species_id': ids['Zosterops japonicus']}).json()
        self.assertEqual(found['species']['scientific_name'], 'Zosterops japonicus')
        self.assertGreater(len(found['targets']), 0)
        # "Not heard" for the species (10-15 s) is not drawn; "no bird sound" (15-20 s) is, as no voice.
        self.assertEqual(sorted(found['marks']), sorted([[at[0], 1], [at[5], 1], [at[15], 2]]))
        for group in found['groups']:
            self.assertGreaterEqual(group['targets'], map_entries.MIN_TARGETS)
            self.assertEqual(group['size'], group['hi'] - group['lo'])
        self.assertEqual(found['scattered'] + sum(g['targets'] for g in found['groups']), len(found['targets']))

        near = self.client.get('/explore/entries/examples', params={'dataset': 1, 'species_id': ids['Zosterops japonicus']}).json()
        self.assertEqual(near['examples'], 2)
        self.assertTrue({at[0], at[5]} <= set(near['targets']))
        self.assertLessEqual(len(near['targets']), 2 * (map_entries.NEIGHBOURS + 1))
        none = self.client.get('/explore/entries/examples', params={'dataset': 1, 'species_id': ids['Horornis diphone']}).json()
        self.assertEqual((none['examples'], none['groups'], none['targets']), (0, [], []))

        # The fixture's AI only answers birds of Japan: no sound is a target, and no group is flagged.
        unsure = self.client.get('/explore/entries/unsure', params={'dataset': 1}).json()
        self.assertEqual((unsure['targets'], unsure['groups'], unsure['scattered']), ([], [], 0))
        self.assertEqual(sorted(unsure['marks']), sorted([[at[0], 1], [at[5], 1], [at[15], 2]]))
        # Sounds whose first answer was a bird not usual here are targets from a roll-up score of 0.5, as on the
        # site page's list: the one at 10 s stays out. Two targets are too few for a flag: both are scattered.
        with connect(self.root) as db:
            analysis = db.execute('SELECT id FROM analyses').fetchone()[0]
            db.executemany("INSERT INTO rollups VALUES(?,?,?,'Strigidae',?,'Strix aluco',?,NULL)",
                           [(analysis, 0, 5, .5, .5), (analysis, 20, 25, .9, .9), (analysis, 10, 15, .49, .49)])
        unsure = self.client.get('/explore/entries/unsure', params={'dataset': 1}).json()
        self.assertEqual((unsure['targets'], unsure['groups'], unsure['scattered']), (sorted([at[20], at[0]]), [], 2))
        self.assertEqual(([g['scientific_name'] for g in unsure['guesses']], unsure['guess']), (['Strix aluco'], [0, 0]))
        # A group's summary counts the same first answers as the flags and the "AI answer" beside a sound, not only
        # the Japan checklist kept with the map: the owl is the answer of its three sounds.
        with connect(self.root) as db:
            answers = explore._first_answers(db, db.execute('SELECT id FROM explore_runs').fetchone()[0], 0, 64)
        self.assertEqual([answers[at[start]] for start in (0, 10, 20)], ['Strix aluco'] * 3)
        whole = self.client.get('/explore', params={'dataset': 1}).json()['node']
        self.assertAlmostEqual(whole['hint_rate'], sum(name is not None for name in answers) / 64)
        # Looking for that species by name finds every sound the AI answered with it: here the 0.5 bar does not apply.
        owl = self.client.get('/species', params={'q': 'Strix aluco'}).json()[0]['id']
        self.assertEqual(self.client.get('/explore/entries/species', params={'dataset': 1, 'species_id': owl}).json()['targets'], sorted([at[0], at[10], at[20]]))
        # The species picker of this way in says how many sounds the AI detected each choice in, by the same rule.
        picked = self.client.get('/datasets/1/heard/species', params={'q': 'Strix aluco', 'detections': True}).json()['species']
        self.assertEqual(next(row['detected'] for row in picked if row['id'] == owl), 3)
        self.assertNotIn('detected', self.client.get('/datasets/1/heard/species', params={'q': 'Strix aluco'}).json()['species'][0])

        # A species AI grown at the site counts as well, in the list of groups and in the picker's numbers.
        with patch('map_entries._species_ai', return_value={'Strix aluco': [at[30]]}):
            self.assertEqual(self.client.get('/explore/entries/species', params={'dataset': 1, 'species_id': owl}).json()['targets'], sorted([at[0], at[10], at[20], at[30]]))
            self.assertEqual(self.client.get('/datasets/1/heard/species', params={'q': 'Strix aluco', 'detections': True}).json()['species'][0]['detected'], 4)
        # A checklist species not usual here is a point's candidate and a roll-up's first answer: its sound is one target.
        with connect(self.root) as db:
            db.execute("INSERT INTO rollups VALUES(?,5,10,'Zosteropidae',.4,'Zosterops japonicus',.9,NULL)", (analysis,))
        self.assertEqual(self.client.get('/explore/entries/species', params={'dataset': 1, 'species_id': ids['Zosterops japonicus']}).json()['targets'], found['targets'])

        # The targets come first in a group's list, and each sound says where it is on the map.
        listed = self.client.post('/explore/nodes/0/segments', params={'dataset': 1}, json={'first': unsure['targets']}).json()['segments']
        self.assertEqual(sorted(s['position'] for s in listed[:2]), unsure['targets'])
        self.assertEqual([s['dist'] for s in listed[2:]], sorted(s['dist'] for s in listed[2:]))
        # A sound outside the group being viewed opens the group beside it that holds the sound.
        one, other = self.client.get('/explore', params={'dataset': 1}).json()['children'][:2]
        self.assertEqual(self.client.get(f"/explore/points/{other['lo']}/group", params={'dataset': 1, 'node': one['node']}).json(), {'node': other['node']})
        self.assertEqual(self.client.get(f"/explore/points/{one['lo']}/group", params={'dataset': 1, 'node': one['node']}).status_code, 400)

        # What was answered in a group, newest first, with where each sound is on the map.
        recorded = self.client.get('/explore/recorded', params={'dataset': 1, 'node': 0}).json()
        self.assertEqual([(r['position'], r['verdict'], r['screen']) for r in recorded],
                         [(at[15], 'present', 'sound_map'), (at[0], 'present', 'sound_map'), (at[5], 'present', 'species_mark')])
        part = one
        inside = self.client.get('/explore/recorded', params={'dataset': 1, 'node': part['node']}).json()
        self.assertEqual({r['position'] for r in inside}, {r['position'] for r in recorded if part['lo'] <= r['position'] < part['hi']})

        # An example from outside the site (Xeno-Canto or a file): the map's sounds most like the clip, best first.
        with connect(self.root) as db:
            vector = np.frombuffer(db.execute('SELECT v.embedding FROM explore_points p JOIN vectors v ON v.rowid=p.segment_id WHERE p.position=7').fetchone()[0], dtype='<f4')
        clip = {'dataset_id': 1, 'species_id': ids['Zosterops japonicus'], 'source_kind': 'recording', 'source_id': 1, 'start_s': 0, 'end_s': 5}
        with patch('search.query_embeddings', return_value=([vector.copy()], {})) as embedded:
            like = self.client.post('/explore/entries/clip', json=clip).json()
            self.assertEqual((7 in like['targets'], len(like['targets'])), (True, map_entries.CLIP_NEAR))
            self.assertEqual(sorted(like['marks']), sorted([[at[0], 1], [at[5], 1], [at[15], 2]]))
            # 30 of the fixture's 64 sounds: dense on the whole map, so it or some of its parts are flagged.
            self.assertTrue(like['groups'])
            # Asked again for the next group opened, the clip is not listened to again.
            self.client.post('/explore/entries/clip', json=clip)
            self.assertEqual(embedded.call_count, 1)
        self.assertEqual(self.client.get('/explore/entries/species', params={'dataset': 1}).status_code, 400)


for _name in dir(ExploreCheck):  # only the fixture is used here, not its tests
    if _name.startswith('test_'):
        setattr(MapEntryApi, _name, None)
del ExploreCheck, _name


if __name__ == '__main__':
    unittest.main()
