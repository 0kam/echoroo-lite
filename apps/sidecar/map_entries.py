"""Ways into the sound map: which groups to visit, by where the AI likely mixed species up, by a species, or by example sounds.

Each way names target sounds on the current map and flags the groups where they are dense (see `flags`); targets
outside every flagged group are counted as scattered. Nothing is stored: the map's points, the answers given by ear
and the roll-ups are read each time.
"""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from explore import PCA_DIMS, SCORE_THRESHOLD, _current_run
from labels import LATEST
from species import names_for
from storage import CatalogError, connect

NEIGHBOURS = 10  # sounds counted as "near" an example
CLIP_NEAR = 30   # sounds counted as "like" an example clip from outside the site (Xeno-Canto or a file)
# Which groups get a flag. Rules of thumb, to review with users' data.
MIN_TARGETS = 3  # a group with fewer target sounds gets no flag
DENSE = 0.4      # a group is flagged when targets are at least this share of its sounds; else its parts are looked into
MOST = 10        # flags offered at most


def _same(point, row):
    """A sound of the map and an answered interval are the same sound when they overlap by more than half of the shorter."""
    overlap = min(point['end_s'], row['end_s']) - max(point['start_s'], row['start_s'])
    return overlap > 0.5 * min(point['end_s'] - point['start_s'], row['end_s'] - row['start_s']) - 1e-6


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
            if _same(point, row):
                answers.setdefault(point['position'], []).append((row['taxon_id'], row['verdict']))
    return points, nodes, answers


def _count(node, flags):
    return int(flags[node['lo']:node['hi']].sum())


def flags(nodes, targets, listened):
    """The groups worth visiting, one rule for every way in: from the top, a group with fewer than MIN_TARGETS
    targets gets nothing; one where targets are at least DENSE of its sounds is flagged; else its parts are looked
    into. Most targets nobody listened to first (then most targets), MOST at most."""
    def dense(node):
        count = _count(node, targets)
        return count >= MIN_TARGETS and count >= DENSE * (node['hi'] - node['lo'])
    found, stack = [], [nodes[0]]
    while stack:
        node = stack.pop()
        inside = [nodes[c] for c in node['children']]
        # The whole map is where the user already is: where it has dense parts, those are flagged instead.
        if dense(node) and not (node['parent'] is None and any(map(dense, inside))):
            found.append({'node': node['node'], 'size': node['hi'] - node['lo'], 'lo': node['lo'], 'hi': node['hi'],
                          'targets': _count(node, targets), 'left': _count(node, targets & ~listened)})
        elif _count(node, targets) >= MIN_TARGETS:
            stack.extend(inside)
    return sorted(found, key=lambda g: (-g['left'], -g['targets'], g['node']))[:MOST]


def _species(db, species_id):
    row = db.execute('SELECT * FROM species WHERE id=?', (species_id,)).fetchone()
    if row is None:
        raise CatalogError('not_found')
    return row


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


def _found(points, nodes, answers, targets, species_id=None):
    """What every way in gives the page: the flagged groups, how many targets lie outside them, the targets' positions
    (map order) and the marks. A sound counts as listened to when it has a mark."""
    marks = _marks(points, answers, species_id)
    listened = np.zeros(len(points), dtype=bool)
    listened[[at for at, _ in marks]] = True
    groups = flags(nodes, targets, listened)
    return {'groups': groups, 'scattered': int(targets.sum()) - sum(g['targets'] for g in groups),
            'targets': np.flatnonzero(targets).tolist(), 'marks': marks}


def by_species(root, dataset_id, species_id):
    """Entry A: the groups where the AI detected the species."""
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
    detected = np.array([any(score >= SCORE_THRESHOLD for n, score in p['candidates'] if mine(n)) for p in points], dtype=bool)
    answers = {position: [(species_id if t == species_id or mine(names.get(t, '')) else t, verdict) for t, verdict in found]
               for position, found in answers.items()}
    return {'species': names_for(taxon), **_found(points, nodes, answers, detected, species_id)}


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
    return {'species': names_for(taxon), 'examples': len(set(placed)), 'mixed': chosen_examples['mixed'],
            **_found(points, nodes, answers, near, species_id)}


@lru_cache(maxsize=8)
def _clip_order(root, models, run_id, engine, kind, source_id, start_s, end_s):
    """The map's sounds most like a clip, best first: cosine similarity of the map AI's embeddings, a long clip
    counting by its best window. Kept while the app runs: the page asks again for every group opened."""
    from audio import source
    from search import query_embeddings
    queries = np.stack(query_embeddings(models, source(root, kind, source_id), start_s, end_s, engine=engine)[0])
    queries /= np.linalg.norm(queries, axis=1, keepdims=True)
    best = []
    with connect(root) as db:
        rows = db.execute('SELECT v.embedding FROM explore_points p JOIN vectors v ON v.rowid=p.segment_id WHERE p.run_id=? ORDER BY p.position', (run_id,))
        # ponytail: exact scan in 256-vector blocks, as the saved search does; index the vectors if large maps make this slow.
        while batch := rows.fetchmany(256):
            matrix = np.stack([np.frombuffer(r['embedding'], dtype='<f4') for r in batch])
            best.extend((matrix @ queries.T).max(axis=1) / np.maximum(np.linalg.norm(matrix, axis=1), 1e-12))
    return tuple(np.argsort(-np.asarray(best), kind='stable')[:CLIP_NEAR].tolist())


def by_clip(root, models, dataset_id, species_id, kind, source_id, start_s, end_s):
    """Entry B with an example from outside the site's answers (Xeno-Canto or a file on the PC): the groups around
    the map's sounds most like the chosen part of it."""
    _, engine, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    order = _clip_order(root, models, run['id'], engine, kind, source_id, start_s, end_s)
    with connect(root) as db:
        taxon = _species(db, species_id)
        points, nodes, answers = _load(db, run)
    near = np.zeros(len(points), dtype=bool)
    near[list(order)] = True
    return {'species': names_for(taxon), **_found(points, nodes, answers, near, species_id)}


def recorded(root, dataset_id, node):
    """The answers given by ear on the sounds of one group, newest first, shaped as the list of checked sounds."""
    from zoneinfo import ZoneInfo
    from heard import _current, _public, _site, _taxa
    _, _, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    with connect(root) as db:
        by_recording = {}
        for point in db.execute('''SELECT p.position,a.recording_id,s.start_s,s.end_s FROM explore_nodes n
                JOIN explore_points p ON p.run_id=n.run_id AND p.position>=n.lo AND p.position<n.hi
                JOIN segments s ON s.id=p.segment_id JOIN analyses a ON a.id=s.analysis_id WHERE n.run_id=? AND n.node=?''', (run['id'], node)):
            by_recording.setdefault(point['recording_id'], []).append(point)
        zone, taxa = ZoneInfo(_site(db, dataset_id)['timezone']), _taxa(db)
        rows = _current(db, dataset_id)
    found = []
    for row in reversed(rows):
        # "No bird sound" taken back since is no answer, as on the map's marks.
        if taxa[row['taxon_id']].get('rank') == 'none' and row['verdict'] != 'present':
            continue
        at = next((p['position'] for p in by_recording.get(row['recording_id'], ()) if _same(p, row)), None)
        if at is not None:
            found.append({**_public(row, taxa, zone), 'position': at})
    return found


def unsure(root, dataset_id):
    """Entry C: groups with many sounds the AI likely mixed up: its first answer was a bird not recorded in Japan
    and the roll-up reaches rollups.AWAY_SCORE, as on the site page's list. `guess`: for each target, in order,
    which of `guesses` the AI answered."""
    from rollups import AWAY_SCORE, _guess
    _, engine, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    with connect(root) as db:
        points, nodes, answers = _load(db, run)
        at = {(p['analysis_id'], round(p['start_s'], 3)): p['position'] for p in points}
        labels = {}  # position -> the species the AI answered
        for row in db.execute('SELECT analysis_id,start_s,top_label FROM rollups WHERE score>=? AND analysis_id IN (SELECT value FROM json_each(?))',
                              (AWAY_SCORE, json.dumps(sorted({p['analysis_id'] for p in points})))):
            position = at.get((row['analysis_id'], round(row['start_s'], 3)))
            if position is not None:
                labels[position] = row['top_label']
        guesses = {label: _guess(db, {'top_engine': engine, 'top_label': label}) for label in sorted(set(labels.values()))}
    away = np.zeros(len(points), dtype=bool)
    away[list(labels)] = True
    found, names = _found(points, nodes, answers, away), list(guesses)
    return {**found, 'guesses': list(guesses.values()), 'guess': [names.index(labels[at]) for at in found['targets']]}
