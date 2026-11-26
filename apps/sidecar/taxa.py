"""One display flow for every AI: keep all-class scores, show only Japan-checklist species, and roll a window whose
best class is a bird not usual at the site in that season up to that class's family ("〇〇科の一種").

"Usual" (`at_home`) is a Japan-checklist species the range model (BirdNET geo, region.py) predicts there in that
week, or one the model has no class for. Without the range model, every Japan-checklist species counts as usual.

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


def families():
    """Scientific names of every bird family."""
    return sorted(set(_families()[0].values()) | set(_families()[2]))


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


@lru_cache(maxsize=1)
def _geo_names():
    """Every name of a Japan-checklist species the range model has a class for -> its BirdNET names."""
    data = json.loads((DATA / 'japan-birds.json').read_text(encoding='utf-8'))
    found = {}
    for row in data['mappings']:
        if row.get('birdnet_scientific_name'):
            for name in filter(None, (row.get('scientific_name'), row.get('perch_scientific_name'), row['birdnet_scientific_name'])):
                found.setdefault(name, set()).add(row['birdnet_scientific_name'])
    return found


@lru_cache(maxsize=8)
def at_home(labels, usual):
    """Boolean mask of the classes usual at a place and time: Japan-checklist species among `usual` (the BirdNET names
    the range model predicts there) and those the range model has no class for. `labels` is a tuple, `usual` a frozenset."""
    geo = _geo_names()
    names = [str(label).partition('_')[0] for label in labels]
    return in_japan(labels) & np.array([name not in geo or not geo[name].isdisjoint(usual) for name in names], dtype=bool)


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
            window_s=5, seconds=None, home=None):
    """Candidates and family roll-ups of one recording's all-class scores.

    `scores` is (windows, classes) of probabilities: softmax (sums to 1 over all classes) or sigmoid (independent).
    `allowed` masks the classes that may be shown as species (default: the Japan checklist). Returns
    (candidates, rollups); each candidate is (start_s, end_s, label, score) and each roll-up a dict naming the family,
    its score, the class the AI put first and its best Japan-checklist class. A window is rolled up when its best class
    over all classes is a bird outside `home` (a mask as `at_home` gives; default: the Japan checklist) and the family's
    score (summed for softmax, the highest for sigmoid) reaches `rollup_threshold` (default `threshold`, else nothing
    is held back).
    """
    labels = tuple(str(label) for label in labels)
    scores = np.asarray(scores)
    japan = in_japan(labels)
    allowed = japan if allowed is None else np.asarray(allowed, dtype=bool)
    home = japan if home is None else home
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
        # A last piece shorter than half a window is mostly padding: too little to ask a person about.
        if home[best] or not family or end - start < window_s / 2:
            continue
        score = float(row[members[family]].sum() if score_type == 'softmax' else row[members[family]].max())
        if rollup_threshold is None or score >= rollup_threshold:
            # The AI's best answer among the Japan checklist species, offered beside the family's species.
            listed = int(np.flatnonzero(japan)[np.argmax(row[japan])]) if japan.any() else None
            rollups.append({'start_s': start, 'end_s': end, 'family': family_names(family), 'score': score,
                            'top_label': labels[best], 'top_score': float(row[best]),
                            'japan_label': None if listed is None else labels[listed]})
    return candidates, rollups
