"""One display flow for every AI: keep all-class scores, show only Japan-checklist species, and roll a window whose
best class lies outside Japan up to that class's family ("〇〇科の一種").

Families come from data/families.json (scripts/build_families.py). A class without a family is not a bird (insects,
frogs, mammals, noise) and is never rolled up.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

DATA = Path(__file__).parent / 'data'


@lru_cache(maxsize=1)
def _families():
    data = json.loads((DATA / 'families.json').read_text(encoding='utf-8'))
    genus = {}
    for name, family in data['species'].items():
        genus.setdefault(name.split()[0], set()).add(family)
    return data['species'], {g: next(iter(f)) for g, f in genus.items() if len(f) == 1}, data['families']


def family_of(name):
    """Scientific family name of a class, or None for a non-bird class. "Genus species_Common name" labels work."""
    species, genus, _ = _families()
    name = str(name).partition('_')[0].strip()
    return species.get(name) or (genus.get(name.split()[0]) if ' ' in name else None)


def family_names(family):
    return {'scientific_name': family, **_families()[2].get(family, {})}


@lru_cache(maxsize=1)
def japan_names():
    """Every name a Japan-checklist species has in any model: the Perch name, the checklist name and BirdNET's."""
    data = json.loads((DATA / 'japan-birds.json').read_text(encoding='utf-8'))
    names = set(data['species']) | {row['scientific_name'] for row in data.get('unmatched', ())}
    for row in data['mappings']:
        names.update(filter(None, (row.get('scientific_name'), row.get('perch_scientific_name'),
                                   row.get('birdnet_scientific_name'))))
    return frozenset(names)


def in_japan(labels):
    """Boolean mask of the classes that are Japan-checklist species."""
    japan = japan_names()
    return np.array([str(label).partition('_')[0] in japan for label in labels], dtype=bool)


@lru_cache(maxsize=8)
def _family_index(labels):
    """Family of each class (None for non-birds) and, per family, the indices of its classes."""
    families = [family_of(label) for label in labels]
    members = {}
    for index, family in enumerate(families):
        if family:
            members.setdefault(family, []).append(index)
    return families, {family: np.array(index) for family, index in members.items()}


def display(scores, labels, *, allowed=None, score_type='softmax', top_k=5, threshold=None, rollup_threshold=None,
            window_s=5, seconds=None):
    """Candidates and family roll-ups of one recording's all-class scores.

    `scores` is (windows, classes) of probabilities: softmax (sums to 1 over all classes) or sigmoid (independent).
    `allowed` masks the classes that may be shown as species (default: the Japan checklist). Returns
    (candidates, rollups); each candidate is (start_s, end_s, label, score) and each roll-up a dict naming the family,
    its score and the out-of-Japan class the AI put first. A window is rolled up when its best class over all classes
    is a bird outside Japan and the family's score (summed for softmax, the highest for sigmoid) reaches
    `rollup_threshold` (default `threshold`, else nothing is held back).
    """
    labels = tuple(str(label) for label in labels)
    scores = np.asarray(scores)
    japan = in_japan(labels)
    allowed = japan if allowed is None else np.asarray(allowed, dtype=bool)
    shown = np.flatnonzero(allowed)
    families, members = _family_index(labels)
    rollup_threshold = threshold if rollup_threshold is None else rollup_threshold
    candidates, rollups = [], []
    for window, row in enumerate(scores):
        start = window * window_s
        end = start + window_s if seconds is None else min(start + window_s, seconds)
        for index in shown[np.argsort(-row[shown], kind='stable')[:top_k]]:
            if threshold is None or row[index] >= threshold:
                candidates.append((start, end, labels[index], float(row[index])))
        best = int(np.argmax(row))
        family = families[best]
        if japan[best] or not family:
            continue
        score = float(row[members[family]].sum() if score_type == 'softmax' else row[members[family]].max())
        if rollup_threshold is None or score >= rollup_threshold:
            rollups.append({'start_s': start, 'end_s': end, 'family': family_names(family), 'score': score,
                            'top_label': labels[best], 'top_score': float(row[best])})
    return candidates, rollups
