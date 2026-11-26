"""Ways into the sound map: groups of a suitable size around a species, example sounds, or sounds the AI likely mixed up."""
import unittest
from unittest.mock import patch

import numpy as np

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


class DrillDownTest(unittest.TestCase):
    #   0 [0,100) -> 1 [0,60) -> 3 [0,20), 4 [20,60) -> 5 [20,30) (too small), 6 [30,60)
    #             -> 2 [60,100)
    NODES = ((0, None, 0, 100), (1, 0, 0, 60), (2, 0, 60, 100), (3, 1, 0, 20), (4, 1, 20, 60), (5, 4, 20, 30), (6, 4, 30, 60))

    def flags(self, *positions):
        flags = np.zeros(100, dtype=bool); flags[list(positions)] = True
        return flags

    def test_goes_down_while_one_part_holds_most_targets_and_stops_above_small_groups(self):
        nodes = tree(*self.NODES)
        # All targets in [20,30): node 5 holds them but is smaller than 15, so its parent is the focus.
        self.assertEqual(map_entries.focus(nodes, self.flags(21, 22, 23, 24))['node'], 4)
        # The share is of the group being left: 9 of 10 in node 1, then 7 of those 9 in node 4, all 7 in node 6.
        self.assertEqual(map_entries.focus(nodes, self.flags(31, 32, 33, 34, 35, 36, 37, 1, 2, 70))['node'], 6)
        # 6 of 10 is not most.
        self.assertEqual(map_entries.focus(nodes, self.flags(31, 32, 33, 34, 35, 36, 70, 71, 72, 73))['node'], 0)
        self.assertEqual(map_entries.focus(nodes, self.flags(31, 32, 33, 34, 35, 36, 1, 2, 3, 4))['node'], 1)
        self.assertEqual(map_entries.focus(nodes, self.flags())['node'], 0)

    def test_groups_are_the_parts_with_targets_most_first(self):
        nodes = tree(*self.NODES)
        spread = self.flags(1, 2, 70, 71, 72)
        self.assertEqual([g['node'] for g in map_entries.groups(nodes, spread)], [2, 1])
        self.assertEqual([g['node'] for g in map_entries.groups(nodes, self.flags(21, 22))], [4])
        self.assertEqual(map_entries.groups(nodes, self.flags()), [])

    def test_a_small_map_offers_its_small_groups(self):
        # 70 sounds: a group of 13 holds every target and is offered, where a large map would stop above it.
        nodes = tree((0, None, 0, 70), (1, 0, 0, 33), (2, 0, 33, 57), (3, 0, 57, 70))
        flags = np.zeros(70, dtype=bool); flags[[58, 59, 60]] = True
        self.assertEqual([g['node'] for g in map_entries.groups(nodes, flags)], [3])
        flags[[1]] = True
        self.assertEqual([g['node'] for g in map_entries.groups(nodes, flags)], [3])
        flags[[2, 40]] = True
        self.assertEqual([g['node'] for g in map_entries.groups(nodes, flags)], [3, 1, 2])
        self.assertEqual(sorted(g['node'] for g in map_entries._frontier(nodes, 60)), [1, 2, 3])


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
        at = {p['start_s']: p['position'] for p in points}
        found = self.client.get('/explore/entries/species', params={'dataset': 1, 'species_id': ids['Zosterops japonicus']}).json()
        self.assertEqual(found['species']['scientific_name'], 'Zosterops japonicus')
        self.assertEqual(found['heard'], 2)
        self.assertEqual(found['detected'], len(found['targets']))
        self.assertGreater(found['detected'], 0)
        self.assertEqual(sorted(found['marks']), sorted([[at[0], 1], [at[5], 1], [at[10], 2]]))
        self.assertTrue(found['groups'])
        for group in found['groups']:
            self.assertGreaterEqual(group['detected'] + group['heard'], 1)
            self.assertEqual(group['size'], group['hi'] - group['lo'])
        self.assertEqual(sum(g['detected'] for g in found['groups']) <= found['detected'], True)

        near = self.client.get('/explore/entries/examples', params={'dataset': 1, 'species_id': ids['Zosterops japonicus']}).json()
        self.assertEqual((near['examples'], near['example_positions']), (2, sorted([at[0], at[5]])))
        self.assertTrue(set(near['example_positions']) <= set(near['targets']))
        self.assertLessEqual(len(near['targets']), 2 * (map_entries.NEIGHBOURS + 1))
        self.assertGreaterEqual(sum(g['examples'] for g in near['groups']), 1)
        none = self.client.get('/explore/entries/examples', params={'dataset': 1, 'species_id': ids['Horornis diphone']}).json()
        self.assertEqual((none['examples'], none['groups'], none['targets']), (0, [], []))

        # The fixture's AI only answers birds of Japan: no sound is a target, and no group is listed.
        unsure = self.client.get('/explore/entries/unsure', params={'dataset': 1}).json()
        self.assertEqual((unsure['targets'], unsure['groups'], unsure['unsure']), ([], [], 0))
        self.assertEqual(sorted(unsure['marks']), sorted([[at[0], 1], [at[5], 1], [at[10], 2]]))
        # Two sounds whose first answer was a bird not recorded in Japan (the roll-ups) are the only targets, best score first;
        # sounds where no bird reached the threshold (rows 2, 6, ...) stay out.
        with connect(self.root) as db:
            analysis = db.execute('SELECT id FROM analyses').fetchone()[0]
            db.executemany("INSERT INTO rollups VALUES(?,?,?,'Strigidae',?,'Strix aluco',?,NULL)",
                           [(analysis, 0, 5, .5, .5), (analysis, 20, 25, .9, .9)])
        unsure = self.client.get('/explore/entries/unsure', params={'dataset': 1}).json()
        self.assertEqual((unsure['targets'], unsure['unsure']), ([at[20], at[0]], 2))
        self.assertEqual(([g['scientific_name'] for g in unsure['guesses']], unsure['guess']), (['Strix aluco'], [0, 0]))
        self.assertTrue(unsure['groups'])
        self.assertEqual([g['left'] for g in unsure['groups']], sorted((g['left'] for g in unsure['groups']), reverse=True))
        for group in unsure['groups']:
            self.assertGreaterEqual(group['unsure'], 1)
            self.assertEqual(group['checked'] + group['left'], group['unsure'])
        # The sound at 0 s was answered by ear.
        self.assertEqual((sum(g['unsure'] for g in unsure['groups']), sum(g['checked'] for g in unsure['groups'])), (2, 1))

        # What was answered in a group, newest first, with where each sound is on the map.
        recorded = self.client.get('/explore/recorded', params={'dataset': 1, 'node': 0}).json()
        self.assertEqual([(r['position'], r['verdict'], r['screen']) for r in recorded],
                         [(at[10], 'absent', 'training'), (at[0], 'present', 'sound_map'), (at[5], 'present', 'species_mark')])
        part = unsure['groups'][0]
        inside = self.client.get('/explore/recorded', params={'dataset': 1, 'node': part['node']}).json()
        self.assertEqual({r['position'] for r in inside}, {r['position'] for r in recorded if part['lo'] <= r['position'] < part['hi']})

        # An example from outside the site (Xeno-Canto or a file): the map's sounds most like the clip, best first.
        with connect(self.root) as db:
            vector = np.frombuffer(db.execute('SELECT v.embedding FROM explore_points p JOIN vectors v ON v.rowid=p.segment_id WHERE p.position=7').fetchone()[0], dtype='<f4')
        clip = {'dataset_id': 1, 'species_id': ids['Zosterops japonicus'], 'source_kind': 'recording', 'source_id': 1, 'start_s': 0, 'end_s': 5}
        with patch('search.query_embeddings', return_value=([vector.copy()], {})) as embedded:
            like = self.client.post('/explore/entries/clip', json=clip).json()
            self.assertEqual((like['targets'][0], len(like['targets']), like['near']), (7, map_entries.CLIP_NEAR, map_entries.CLIP_NEAR))
            self.assertEqual(sorted(like['marks']), sorted([[at[0], 1], [at[5], 1], [at[10], 2]]))
            self.assertEqual(sum(g['near'] for g in like['groups']) <= like['near'] and bool(like['groups']), True)
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
