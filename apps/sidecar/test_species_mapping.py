"""BirdNET labels resolve to the checklist species represented by Perch names."""
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from reports import read_classification_windows
from species import decorate_candidates, initialize_species
from storage import connect


ROOT = Path(__file__).resolve().parents[2]


class SpeciesMappingCheck(unittest.TestCase):
    def test_bundled_mapping_is_valid_and_unique(self):
        data = json.loads((ROOT / 'apps/sidecar/data/japan-birds.json').read_text(encoding='utf-8'))
        labels = {line.partition('_')[0] for line in
                  (ROOT / 'poc/models/birdnet/acoustic-models/v2.4/tf/labels/en_us.txt').read_text(encoding='utf-8').splitlines()}
        mapped = {}
        for row in data['mappings']:
            name = row['birdnet_scientific_name']
            self.assertTrue(not name or name in labels)
            if name:
                previous = mapped.get(row['perch_scientific_name'])
                self.assertTrue(previous is None or previous == name)
                mapped[row['perch_scientific_name']] = name
        self.assertEqual(len(mapped), len(set(mapped.values())))
        expected = {'Emberiza personata':'Emberiza spodocephala', 'Phasianus versicolor':'Phasianus colchicus',
                    'Larus vegae':'Larus argentatus', 'Ardea ibis':'Bubulcus ibis', 'Parus cinereus':'Parus minor'}
        self.assertEqual({name:mapped[name] for name in expected}, expected)

    def test_birdnet_and_perch_share_the_app_species(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize_species(root)
            birdnet = [{'label':'Parus minor_Japanese Tit'}]
            perch = [{'label':'Parus cinereus'}]
            with connect(root) as db:
                decorate_candidates(db, 'birdnet', birdnet)
                decorate_candidates(db, 'perch', perch)
            self.assertEqual(birdnet[0]['species_id'], perch[0]['species_id'])
            self.assertEqual(birdnet[0]['scientific_name'], 'Parus cinereus')

            path = root / 'birdnet.npz'
            label = 'Parus minor_Japanese Tit'
            np.savez(path, labels=np.repeat(label, 2), scores=[.2, .8], start_s=[0, 3], end_s=[3, 6])
            labels, starts, scores, _ = read_classification_windows(
                {'scores_path':str(path),'engine':'birdnet'}, 6, [label])
            self.assertEqual((labels.tolist(), starts.tolist(), scores.ravel().tolist()),
                             (['Parus cinereus'], [0, 3], [.2, .8]))


if __name__ == '__main__':
    unittest.main()
