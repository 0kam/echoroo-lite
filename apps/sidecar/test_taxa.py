"""Every AI goes through one display flow: Japan species are shown, a best guess outside Japan rolls up to its family."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from analysis import birdnet_dense
from predictions import perch_display
from taxa import display, family_names, family_of, in_japan


class TaxaCheck(unittest.TestCase):
    def test_families(self):
        self.assertEqual(family_of('Parus cinereus'), 'Paridae')
        self.assertEqual(family_of('Parus minor_Japanese Tit'), 'Paridae')  # BirdNET label, other name
        self.assertEqual(family_of('Anarhynchus alexandrinus'), 'Charadriidae')  # renamed genus, by hand
        self.assertEqual(family_of('Dryocopus javensis'), 'Picidae')
        self.assertIsNone(family_of('Acheta domesticus'))  # a cricket is never rolled up
        self.assertEqual(family_names('Picidae'), {'scientific_name': 'Picidae', 'ja': 'キツツキ科'})
        self.assertEqual(family_names('Furnariidae'), {'scientific_name': 'Furnariidae', 'ja': 'カマドドリ科'})  # not in Japan
        self.assertEqual(family_names('Pellorneidae'), {'scientific_name': 'Pellorneidae'})  # no Japanese name yet
        self.assertEqual(list(in_japan(['Parus minor_Japanese Tit', 'Phoebastria albatrus', 'Picus canus',
                                        'Dryocopus pileatus'])), [True, True, True, False])

    def test_rollup_softmax_sums_and_sigmoid_takes_the_highest(self):
        labels = ['Dendrocopos major', 'Dryocopus pileatus', 'Melanerpes formicivorus', 'Acheta domesticus']
        # Window 0: an American woodpecker first -> woodpecker family. Window 1: a Japanese bird first -> none.
        # Window 2: a cricket first -> none. Window 3: too weak for the threshold.
        scores = np.array([[.15, .3, .25, .3], [.6, .2, .1, .1], [.1, .1, .1, .7], [.02, .04, .03, .91]])
        scores[3] = [.02, .05, .01, .02]
        candidates, rollups = display(scores, labels, score_type='softmax', rollup_threshold=.1, seconds=18)
        self.assertEqual([(r['start_s'], r['family']['ja'], r['top_label']) for r in rollups],
                         [(0, 'キツツキ科', 'Dryocopus pileatus')])
        self.assertAlmostEqual(rollups[0]['score'], .7)  # the whole family, Japanese member included
        self.assertEqual({label for _, _, label, _ in candidates}, {'Dendrocopos major'})
        self.assertEqual(candidates[-1][:2], (15, 18))
        _, sigmoid = display(scores, labels, score_type='sigmoid', rollup_threshold=.1)
        self.assertAlmostEqual(sigmoid[0]['score'], .3)

    def test_perch_display_keeps_candidates_and_adds_rollups(self):
        labels = np.array(['Horornis diphone', 'Dryocopus pileatus', 'Picus canus', 'Some frog'])
        logits = np.array([[0, 3, 1, 0], [3, 0, 0, 0]], dtype=np.float32)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'scores.npz'
            np.savez_compressed(path, logits=logits, labels=labels)
            candidates, rollups, meta = perch_display(path, 10, 7, {'species': ['Horornis diphone', 'Picus canus']})
        self.assertEqual(meta['display_species_count'], 2)
        self.assertEqual(len(candidates), 4)
        self.assertEqual([(r['start_s'], r['family']['scientific_name']) for r in rollups], [(0, 'Picidae')])

    def test_birdnet_result_becomes_one_row_per_window(self):
        result = SimpleNamespace(species_list=np.array(['A a_A', 'B b_B', 'C c_C']),
                                 species_ids=np.array([[[2, 0, 1], [1, 2, 0], [0, 1, 2]]], dtype=np.uint16),
                                 species_probs=np.array([[[.3, .1, .2], [.5, .6, .4], [0, 0, 0]]], dtype=np.float32))
        scores, names = birdnet_dense(result, 5.5)  # two 3-s windows; the padded third is dropped
        self.assertEqual(list(names), ['A a_A', 'B b_B', 'C c_C'])
        np.testing.assert_allclose(scores, [[.1, .2, .3], [.4, .5, .6]])

    def test_reports_read_all_class_birdnet(self):
        from reports import read_classification_windows
        labels = np.array(['Parus minor_Japanese Tit', 'Dryocopus pileatus_Pileated Woodpecker'])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'b.npz'
            np.savez_compressed(path, scores=np.array([[.7, .1], [.2, .9]], dtype=np.float32), labels=labels)
            result = read_classification_windows({'scores_path': str(path), 'engine': 'birdnet'}, 6,
                                                 ['Parus minor_Japanese Tit'])
        self.assertEqual(list(result[0]), ['Parus cinereus'])  # filed under the app's name for the species
        self.assertEqual(list(result[1]), [0, 3])
        np.testing.assert_allclose(result[2].ravel(), [.7, .2])


if __name__ == '__main__':
    unittest.main()
