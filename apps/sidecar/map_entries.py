"""Ways into the sound map: where to look first, by a species, by example sounds, or by where the AI is unsure.

Each entry names target sounds on the current map and picks groups of a suitable size around them: going down
the map's tree while one child still holds most of the targets, never below a small group. Nothing is stored:
the map's points, the answers given by ear and the roll-ups are read each time.
"""
import json
from pathlib import Path

import numpy as np

from explore import PCA_DIMS, SCORE_THRESHOLD, _current_run
from labels import LATEST
from species import names_for
from storage import CatalogError, connect

SHARE = 0.7      # go into a child while it holds this share of the targets
SMALLEST = 15    # never offer a group smaller than this (see _smallest for maps of a few dozen sounds)
GROUPS = 3
NEIGHBOURS = 10  # sounds counted as "near" an example
UNSURE_GROUP = 60  # groups ranked for "the AI is unsure" hold at most about this many sounds


def _load(db, run):
    """The map's points in position order, its tree, and for each point what was answered by ear in it."""
    points = [dict(r) for r in db.execute('''SELECT p.position,p.segment_id,p.candidates,a.recording_id,s.analysis_id,s.start_s,s.end_s
        FROM explore_points p JOIN segments s ON s.id=p.segment_id JOIN analyses a ON a.id=s.analysis_id
        WHERE p.run_id=? ORDER BY p.position''', (run['id'],))]
    for point in points:
        point['candidates'] = json.loads(point['candidates'])
    nodes = {r['node']: dict(r) for r in db.execute('SELECT node,parent,lo,hi FROM explore_nodes WHERE run_id=?', (run['id'],))}
    for node in nodes.values():
        node['children'] = []
    for node in nodes.values():
        if node['parent'] is not None:
            nodes[node['parent']]['children'].append(node['node'])
    by_recording = {}
    for point in points:
        by_recording.setdefault(point['recording_id'], []).append(point)
    answers = {}  # position -> [(taxon, verdict)]
    # "No bird sound" is a sound listened to with no species heard; one taken back since is no answer.
    for row in db.execute(f'''SELECT l.taxon_id,CASE WHEN s.rank='none' THEN 'absent' ELSE l.verdict END AS verdict,l.recording_id,l.start_s,l.end_s
            FROM labels l JOIN species s ON s.id=l.taxon_id WHERE l.recording_id IN (SELECT value FROM json_each(?)) AND {LATEST}
            AND (s.rank<>'none' OR l.verdict='present')''', (json.dumps(list(by_recording)),)):
        for point in by_recording[row['recording_id']]:
            overlap = min(point['end_s'], row['end_s']) - max(point['start_s'], row['start_s'])
            if overlap > 0.5 * min(point['end_s'] - point['start_s'], row['end_s'] - row['start_s']) - 1e-6:
                answers.setdefault(point['position'], []).append((row['taxon_id'], row['verdict']))
    return points, nodes, answers


def _count(node, flags):
    return int(flags[node['lo']:node['hi']].sum())


def _smallest(nodes):
    """SMALLEST, or less on a map so small that a sixth of it is under that: its few groups are all worth offering."""
    return min(SMALLEST, max(5, (nodes[0]['hi'] - nodes[0]['lo']) // 6))


def focus(nodes, targets):
    """The deepest group that still holds most of the targets: from the top, into the child with at least SHARE
    of the current group's targets, as long as that child is not smaller than SMALLEST."""
    node, smallest = nodes[0], _smallest(nodes)
    while True:
        here = _count(node, targets)
        inside = [nodes[c] for c in node['children'] if nodes[c]['hi'] - nodes[c]['lo'] >= smallest]
        best = max(inside, key=lambda child: _count(child, targets), default=None)
        if not here or best is None or _count(best, targets) < SHARE * here:
            return node
        node = best


def groups(nodes, targets):
    """Up to GROUPS groups to look at, most targets first: the parts of the focus group that hold targets, or the
    focus group itself when it has no parts of a useful size."""
    node = focus(nodes, targets)
    parts = [nodes[c] for c in node['children'] if nodes[c]['hi'] - nodes[c]['lo'] >= _smallest(nodes) and _count(nodes[c], targets)]
    chosen = sorted(parts, key=lambda part: (-_count(part, targets), part['node']))[:GROUPS] or [node]
    return [part for part in chosen if _count(part, targets)]


def _frontier(nodes, limit):
    """The map cut into groups of at most `limit` sounds where the tree allows, none smaller than SMALLEST
    (except the whole map)."""
    found, stack = [], [nodes[0]]
    while stack:
        node = stack.pop()
        inside = [nodes[c] for c in node['children'] if nodes[c]['hi'] - nodes[c]['lo'] >= _smallest(nodes)]
        if node['hi'] - node['lo'] <= limit or not inside:
            found.append(node)
        else:
            # Parts smaller than SMALLEST are left out: too few sounds to rank as a group.
            stack.extend(inside)
    return found


def _species(db, species_id):
    row = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
    if row is None:
        raise CatalogError('not_found')
    return row


def _shape(node, **more):
    return {'node': node['node'], 'size': node['hi'] - node['lo'], 'lo': node['lo'], 'hi': node['hi'], **more}


def _marks(points, answers, species_id=None):
    """What was answered by ear, to draw on the map: [position, 1 heard | 2 not heard]. With a species its answers;
    without, any species heard (1) or only "not heard" answers (2)."""
    marks = []
    for position, found in answers.items():
        verdicts = {verdict for taxon, verdict in found if species_id is None or taxon == species_id}
        if 'present' in verdicts:
            marks.append([position, 1])
        elif 'absent' in verdicts:
            marks.append([position, 2])
    return sorted(marks)


def by_species(root, dataset_id, species_id):
    """Entry A: the groups where the AI detected the species or it was heard."""
    _, _, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    with connect(root) as db:
        taxon = _species(db, species_id)
        points, nodes, answers = _load(db, run)
        names = {r['id']: r['scientific_name'] for r in db.execute('SELECT id,scientific_name FROM species WHERE id IN (SELECT value FROM json_each(?))',
                 (json.dumps(sorted({t for found in answers.values() for t, _ in found})),))} if taxon['rank'] == 'family' else {}
    name = taxon['scientific_name']
    if taxon['rank'] == 'family':
        # "A bird of family X": any species of the family the AI detected or that was heard counts.
        from taxa import family_of
        mine = lambda n: family_of(n) == name
    else:
        mine = lambda n: n == name
    detected = np.array([any(mine(n) and score >= SCORE_THRESHOLD for n, score in p['candidates']) for p in points], dtype=bool)
    answers = {position: [(species_id if t == species_id or mine(names.get(t, '')) else t, verdict) for t, verdict in found]
               for position, found in answers.items()}
    heard = np.zeros(len(points), dtype=bool)
    for position, found in answers.items():
        heard[position] = (species_id, 'present') in found
    chosen = groups(nodes, detected | heard)
    return {'species': names_for(taxon), 'detected': int(detected.sum()), 'heard': int(heard.sum()),
            'groups': [_shape(g, detected=_count(g, detected), heard=_count(g, heard)) for g in chosen],
            'targets': np.flatnonzero(detected).tolist(), 'marks': _marks(points, answers, species_id)}


def by_examples(root, dataset_id, species_id):
    """Entry B: the groups around example sounds of the species (heard sounds the app chose, as for a search)."""
    from heard import search_examples
    _, _, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    chosen_examples = search_examples(root, dataset_id, species_id)
    examples = chosen_examples['examples']
    with connect(root) as db:
        taxon = _species(db, species_id)
        points, nodes, answers = _load(db, run)
    params = json.loads(run['params'])
    placed = []  # the map point each example lies in
    for example in examples:
        inside = [p['position'] for p in points if p['recording_id'] == example['recording_id']
                  and p['start_s'] < example['end_s'] and example['start_s'] < p['end_s']]
        if inside:
            placed.append(max(inside, key=lambda at: min(points[at]['end_s'], example['end_s']) - max(points[at]['start_s'], example['start_s'])))
    near = np.zeros(len(points), dtype=bool)
    name = params.get('pca_file')
    path = Path(root) / 'explore' / name if isinstance(name, str) and Path(name).name == name else None
    if placed and path is not None and path.is_file() and path.stat().st_size == len(points) * PCA_DIMS * 4:
        pca = np.asarray(np.memmap(path, mode='r', dtype='<f4', shape=(len(points), PCA_DIMS)))
        for at in placed:
            distance = np.linalg.norm(pca - pca[at], axis=1)
            near[np.argsort(distance, kind='stable')[:NEIGHBOURS + 1]] = True
    else:
        near[placed] = True
    spots = np.zeros(len(points), dtype=bool)
    spots[placed] = True
    chosen = groups(nodes, near) if placed else []
    return {'species': names_for(taxon), 'examples': len(placed), 'example_positions': sorted(set(placed)),
            'heard': chosen_examples['heard'], 'mixed': chosen_examples['mixed'],
            'groups': [_shape(g, examples=_count(g, spots), near=_count(g, near)) for g in chosen],
            'targets': np.flatnonzero(near).tolist(), 'marks': _marks(points, answers, species_id)}


def unsure(root, dataset_id):
    """Entry C: groups with many sounds the AI could not settle: its first answer was a bird outside Japan (the
    roll-ups), or its best Japan answer stayed under the display threshold. Groups with the most such sounds nobody
    listened to come first."""
    _, _, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    with connect(root) as db:
        points, nodes, answers = _load(db, run)
        outside = {}  # (analysis, start) -> family
        for row in db.execute('SELECT analysis_id,start_s,family FROM rollups WHERE analysis_id IN (SELECT value FROM json_each(?))',
                              (json.dumps(sorted({p['analysis_id'] for p in points})),)):
            outside[(row['analysis_id'], round(row['start_s'], 3))] = row['family']
        families = {r['scientific_name']: names_for(r) for r in db.execute(
            "SELECT * FROM species WHERE rank='family' AND scientific_name IN (SELECT value FROM json_each(?))",
            (json.dumps(sorted(set(outside.values()))),))}
    from taxa import japan_names
    japan = japan_names()
    # No Japan-checklist bird reaches the display threshold: either a bird from elsewhere does ("away"), or nothing does ("low").
    weak = np.array([bool(p['candidates']) and all(score < SCORE_THRESHOLD for n, score in p['candidates'] if n in japan) for p in points], dtype=bool)
    away = np.array([(p['analysis_id'], round(p['start_s'], 3)) in outside for p in points], dtype=bool) | \
        weak & np.array([any(score >= SCORE_THRESHOLD for _, score in p['candidates']) for p in points], dtype=bool)
    low = weak & ~away
    listened = np.zeros(len(points), dtype=bool)
    listened[list(answers)] = True
    targets = away | low
    found = []
    for node in _frontier(nodes, max(UNSURE_GROUP, len(points) // 20)):
        count = _count(node, targets)
        if not count:
            continue
        inside = [outside[(p['analysis_id'], round(p['start_s'], 3))] for p in points[node['lo']:node['hi']]
                  if (p['analysis_id'], round(p['start_s'], 3)) in outside]
        family = max(set(inside), key=inside.count) if inside else None
        elsewhere = _count(node, away)
        kind = 'family' if family and inside.count(family) * 2 > elsewhere and elsewhere >= _count(node, low) else \
            'outside' if elsewhere >= _count(node, low) and elsewhere else 'low'
        found.append(_shape(node, unsure=count, outside=elsewhere, low=_count(node, low), checked=_count(node, targets & listened),
                            left=_count(node, targets & ~listened),
                            kind=kind, family=families.get(family) if kind == 'family' else None))
    found.sort(key=lambda g: (-g['left'], g['node']))
    return {'groups': found[:10], 'unsure': int(targets.sum()), 'outside': int(away.sum()), 'low': int(low.sum()),
            'targets': np.flatnonzero(targets).tolist(),
            'marks': _marks(points, answers)}
