"""Ways into the sound map: groups of a suitable size around a species, example sounds, or sounds the AI is unsure of."""
import unittest

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

        unsure = self.client.get('/explore/entries/unsure', params={'dataset': 1}).json()
        low = [p['position'] for p in points if (c := __import__('json').loads(p['candidates'])) and max(s for _, s in c) < .1]
        self.assertEqual(unsure['targets'], sorted(low))
        self.assertEqual(unsure['unsure'], len(low))
        self.assertEqual([g['checked'] > 0 for g in unsure['groups']], sorted(g['checked'] > 0 for g in unsure['groups']))
        self.assertEqual(sorted(unsure['marks']), sorted([[at[0], 1], [at[5], 1], [at[10], 2]]))
        self.assertEqual(self.client.get('/explore/entries/species', params={'dataset': 1}).status_code, 400)


for _name in dir(ExploreCheck):  # only the fixture is used here, not its tests
    if _name.startswith('test_'):
        setattr(MapEntryApi, _name, None)
del ExploreCheck, _name


if __name__ == '__main__':
    unittest.main()
