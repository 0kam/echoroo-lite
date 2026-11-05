"""Precomputed PCA sound maps over the current main-engine embeddings."""
import hashlib
import heapq
import json
import struct
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import numpy as np
from scipy.cluster.hierarchy import linkage
from scipy.cluster.vq import ClusterError, kmeans2, vq

from models import main_identity, window_s
from search import compatible_analyses
from species import canonical_scientific_name
from storage import CatalogError, checked_source, connect


EMBEDDING_DIMS = 1536
PCA_DIMS = 50
VECTOR_BATCH = 4096
PCA_BATCH = 4096
KMEANS_BATCH = 8192
EXACT_MAX = 10_000
LEAF_COUNT = 2_000
FIT_MAX = 20_000
TERMINAL_MAX = 20
TAU = 0.6
MAX_CHILDREN = 8
LANDMARK_MAX = 10_000
PACMAP_NEIGHBORS = 10
PACMAP_MID_NEAR = 5
PACMAP_FURTHER = 20
PACMAP_ITERATIONS = 450
PACMAP_LR = 1.0
LOCALMAP_THRESHOLD = 10
LOCALMAP_TRIES = 1000
SEED = 20261101
CACHE_VERSION = 1
MAP_VERSION = 2  # 2 = groups cut from the 2-D LocalMAP layout; runs without it are version 1
SCORE_THRESHOLD = 0.1
CANDIDATE_MIN = 0.05


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise CatalogError('cancelled')


def _progress(root, job_id, stage, done, total=5, status='running', error=None):
    if job_id is None:
        return
    with connect(root) as db:
        db.execute('UPDATE jobs SET status=?,stage=?,done=?,total=?,error=? WHERE id=?',
                   (status, stage, done, total, error, job_id))


def _write_json(path, value):
    temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _cache_paths(folder, site_id, engine, model_sha):
    key = hashlib.sha256(f'{site_id}\0{engine}\0{model_sha}'.encode()).hexdigest()[:20]
    base = folder / f'embeddings-{key}'
    return base.with_suffix('.f32'), base.with_suffix('.i64'), base.with_suffix('.json')


def _valid_cache(index, matrix_path, ids_path, site_id, engine, model_sha, current_ids):
    if not isinstance(index, dict) or index.get('version') != CACHE_VERSION:
        return False
    if (index.get('site_id'), index.get('engine'), index.get('model_sha')) != (site_id, engine, model_sha):
        return False
    analyses = index.get('analyses')
    if not isinstance(analyses, dict) or not set(map(int, analyses)).issubset(current_ids):
        return False
    rows = 0
    for _, item in sorted(analyses.items(), key=lambda pair: pair[1].get('start', -1)):
        if (not isinstance(item, dict) or item.get('start') != rows or item.get('end', -1) < rows
                or item.get('engine') != engine or item.get('model_sha') != model_sha):
            return False
        rows = item['end']
    if index.get('rows') != rows:
        return False
    expected_matrix = rows * EMBEDDING_DIMS * np.dtype('<f4').itemsize
    expected_ids = rows * np.dtype('<i8').itemsize
    return (matrix_path.is_file() and ids_path.is_file()
            and matrix_path.stat().st_size >= expected_matrix and ids_path.stat().st_size >= expected_ids)


def _load_cache_index(index_path):
    try:
        return json.loads(index_path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None


def _analysis_counts(root, analysis_ids):
    encoded = json.dumps(analysis_ids)
    with connect(root) as db:
        segments = dict(db.execute('''SELECT analysis_id,COUNT(*) FROM segments
            WHERE analysis_id IN (SELECT value FROM json_each(?)) GROUP BY analysis_id''', (encoded,)))
        vectors = dict(db.execute('''SELECT s.analysis_id,COUNT(*) FROM segments s JOIN vectors v ON v.rowid=s.id
            WHERE s.analysis_id IN (SELECT value FROM json_each(?)) GROUP BY s.analysis_id''', (encoded,)))
    if any(segments.get(aid, 0) == 0 or segments.get(aid) != vectors.get(aid) for aid in analysis_ids):
        raise CatalogError('invalid_vectors')
    return {aid: segments[aid] for aid in analysis_ids}


def _stream_vectors(root, analyses, matrix, segment_ids, start, cancel):
    """Read every requested analysis' vector blob once, in contiguous row order."""
    analysis_ids = [row['analysis_id'] for row in analyses]
    encoded = json.dumps(analysis_ids)
    position = start
    with connect(root) as db:
        cursor = db.execute('''SELECT s.id,s.analysis_id,v.embedding FROM segments s
            JOIN vectors v ON v.rowid=s.id
            WHERE s.analysis_id IN (SELECT value FROM json_each(?))
            ORDER BY s.analysis_id,s.start_s,s.id''', (encoded,))
        while rows := cursor.fetchmany(VECTOR_BATCH):
            _check_cancel(cancel)
            batch = np.empty((len(rows), EMBEDDING_DIMS), dtype='<f4')
            for offset, row in enumerate(rows):
                vector = np.frombuffer(row['embedding'], dtype='<f4')
                if len(vector) != EMBEDDING_DIMS or not np.isfinite(vector).all():
                    raise CatalogError('invalid_vectors')
                batch[offset] = vector
                segment_ids[position + offset] = row['id']
            matrix[position:position + len(rows)] = batch
            position += len(rows)
    return position


def _cache_index(site_id, engine, model_sha, analyses, counts, start=0, previous=None):
    entries = {} if previous is None else dict(previous['analyses'])
    position = start
    for row in analyses:
        aid = row['analysis_id']
        end = position + counts[aid]
        entries[str(aid)] = {'start': position, 'end': end, 'engine': engine, 'model_sha': model_sha}
        position = end
    return {'version': CACHE_VERSION, 'site_id': site_id, 'engine': engine, 'model_sha': model_sha,
            'rows': position, 'analyses': entries}


def embedding_cache(root, site_id, engine, model_sha, analyses, cancel=None):
    """Return the append-only vector cache and its segment IDs."""
    folder = Path(root) / 'explore'
    folder.mkdir(parents=True, exist_ok=True)
    matrix_path, ids_path, index_path = _cache_paths(folder, site_id, engine, model_sha)
    analyses = sorted(analyses, key=lambda row: row['analysis_id'])
    current_ids = {row['analysis_id'] for row in analyses}
    index = _load_cache_index(index_path)
    reuse = _valid_cache(index, matrix_path, ids_path, site_id, engine, model_sha, current_ids)
    cached_ids = set(map(int, index['analyses'])) if reuse else set()
    pending = [row for row in analyses if row['analysis_id'] not in cached_ids]
    counts = _analysis_counts(root, [row['analysis_id'] for row in pending]) if pending else {}

    if reuse:
        old_rows = index['rows']
        new_index = _cache_index(site_id, engine, model_sha, pending, counts, old_rows, index)
        total = new_index['rows']
        matrix_bytes = total * EMBEDDING_DIMS * np.dtype('<f4').itemsize
        ids_bytes = total * np.dtype('<i8').itemsize
        with matrix_path.open('r+b') as stream:
            stream.truncate(matrix_bytes)
        with ids_path.open('r+b') as stream:
            stream.truncate(ids_bytes)
        matrix = np.memmap(matrix_path, mode='r+', dtype='<f4', shape=(total, EMBEDDING_DIMS))
        segment_ids = np.memmap(ids_path, mode='r+', dtype='<i8', shape=(total,))
        try:
            if pending and _stream_vectors(root, pending, matrix, segment_ids, old_rows, cancel) != total:
                raise CatalogError('invalid_vectors')
            matrix.flush()
            segment_ids.flush()
            _write_json(index_path, new_index)
        except Exception:
            del matrix, segment_ids
            with matrix_path.open('r+b') as stream:
                stream.truncate(old_rows * EMBEDDING_DIMS * np.dtype('<f4').itemsize)
            with ids_path.open('r+b') as stream:
                stream.truncate(old_rows * np.dtype('<i8').itemsize)
            raise
        del matrix, segment_ids
        index = new_index
    else:
        counts = _analysis_counts(root, [row['analysis_id'] for row in analyses])
        index = _cache_index(site_id, engine, model_sha, analyses, counts)
        token = uuid4().hex
        temporary_matrix = matrix_path.with_name(matrix_path.name + '.' + token + '.tmp')
        temporary_ids = ids_path.with_name(ids_path.name + '.' + token + '.tmp')
        matrix = np.memmap(temporary_matrix, mode='w+', dtype='<f4', shape=(index['rows'], EMBEDDING_DIMS))
        segment_ids = np.memmap(temporary_ids, mode='w+', dtype='<i8', shape=(index['rows'],))
        try:
            if _stream_vectors(root, analyses, matrix, segment_ids, 0, cancel) != index['rows']:
                raise CatalogError('invalid_vectors')
            matrix.flush()
            segment_ids.flush()
            del matrix, segment_ids
            temporary_matrix.replace(matrix_path)
            temporary_ids.replace(ids_path)
            _write_json(index_path, index)
        finally:
            temporary_matrix.unlink(missing_ok=True)
            temporary_ids.unlink(missing_ok=True)
    matrix = np.memmap(matrix_path, mode='r', dtype='<f4', shape=(index['rows'], EMBEDDING_DIMS))
    segment_ids = np.memmap(ids_path, mode='r', dtype='<i8', shape=(index['rows'],))
    return matrix, segment_ids, index, index_path


def exact_pca(matrix, output_path, cancel=None, dimensions=PCA_DIMS):
    """Exact covariance PCA from one raw-moment pass, followed by batched projection."""
    n, width = matrix.shape
    if n < 2 or width != EMBEDDING_DIMS:
        raise CatalogError('invalid_vectors')
    total = np.zeros(width, dtype=np.float64)
    second = np.zeros((width, width), dtype=np.float64)
    for start in range(0, n, PCA_BATCH):
        _check_cancel(cancel)
        batch = np.asarray(matrix[start:start + PCA_BATCH], dtype=np.float64)
        total += batch.sum(axis=0)
        second += batch.T @ batch
    mean = total / n
    covariance = (second - np.outer(total, total) / n) / (n - 1)
    covariance = (covariance + covariance.T) * 0.5
    _check_cancel(cancel)
    values, vectors = np.linalg.eigh(covariance)
    count = min(dimensions, width)
    order = np.argsort(values)[-count:][::-1]
    components = vectors[:, order]
    projected = np.memmap(output_path, mode='w+', dtype='<f4', shape=(n, count))
    for start in range(0, n, PCA_BATCH):
        _check_cancel(cancel)
        batch = np.asarray(matrix[start:start + PCA_BATCH], dtype=np.float64)
        projected[start:start + len(batch)] = (batch - mean) @ components
    projected.flush()
    return projected, {'dimensions': count, 'explained_variance': values[order].tolist()}


def _nearest(matrix, centers, cancel=None, batch_size=KMEANS_BATCH):
    labels = np.empty(len(matrix), dtype=np.int32)
    squared = np.empty(len(matrix), dtype=np.float32)
    center_norms = np.einsum('ij,ij->i', centers, centers)
    for start in range(0, len(matrix), batch_size):
        _check_cancel(cancel)
        block = np.asarray(matrix[start:start + batch_size], dtype=np.float32)
        distances = (np.einsum('ij,ij->i', block, block)[:, None] + center_norms[None, :]
                     - 2 * block @ centers.T)
        np.maximum(distances, 0, out=distances)
        chosen = distances.argmin(axis=1)
        labels[start:start + len(block)] = chosen
        squared[start:start + len(block)] = distances[np.arange(len(block)), chosen]
    return labels, squared


def _vq_nearest(matrix, centers, cancel=None):
    _check_cancel(cancel)
    labels, distances = vq(np.asarray(matrix), centers, check_finite=False)
    _check_cancel(cancel)
    return labels.astype(np.int32, copy=False), np.square(distances, dtype=np.float32)


def _initial_centers(matrix, k, seed, cancel=None):
    rng = np.random.default_rng(seed)
    centers = np.empty((k, matrix.shape[1]), dtype=np.float32)
    chosen = np.zeros(len(matrix), dtype=bool)
    first = int(rng.integers(len(matrix)))
    centers[0] = matrix[first]
    chosen[first] = True
    _, closest = _nearest(matrix, centers[:1], cancel)
    for index in range(1, k):
        _check_cancel(cancel)
        total = float(closest.sum(dtype=np.float64))
        if total:
            point = int(rng.choice(len(matrix), p=closest.astype(np.float64) / total))
        else:
            point = int(np.flatnonzero(~chosen)[0])
        if chosen[point]:
            point = int(np.flatnonzero(~chosen)[0])
        chosen[point] = True
        centers[index] = matrix[point]
        _, distance = _nearest(matrix, centers[index:index + 1], cancel)
        np.minimum(closest, distance, out=closest)
    return centers


def _means(matrix, labels, squared, k):
    counts = np.bincount(labels, minlength=k)
    centers = np.empty((k, matrix.shape[1]), dtype=np.float32)
    for column in range(matrix.shape[1]):
        centers[:, column] = np.bincount(labels, weights=np.asarray(matrix[:, column]), minlength=k) / np.maximum(counts, 1)
    if np.any(counts == 0):
        available = np.argsort(-squared, kind='stable')
        used = set()
        for cluster in np.flatnonzero(counts == 0):
            point = next(int(value) for value in available if int(value) not in used)
            used.add(point)
            centers[cluster] = matrix[point]
    return centers


def lloyd(matrix, initial, cancel=None, max_iter=20):
    """One scipy Lloyd iteration at a time so cancellation remains responsive."""
    centers = (_initial_centers(matrix, int(initial), SEED + int(initial), cancel)
               if np.isscalar(initial) else np.asarray(initial, dtype=np.float32).copy())
    previous = None
    iterations = 0
    for iterations in range(1, max_iter + 1):
        _check_cancel(cancel)
        try:
            updated, labels = kmeans2(matrix, centers, iter=1, minit='matrix', missing='raise',
                                      check_finite=False, seed=SEED)
        except ClusterError:
            labels, squared = _vq_nearest(matrix, centers, cancel)
            updated = _means(matrix, labels, squared, len(centers))
        _check_cancel(cancel)
        if previous is not None and np.array_equal(labels, previous):
            centers = updated
            break
        previous = labels
        centers = updated
    labels, squared = _vq_nearest(matrix, centers, cancel)
    return centers, labels, squared, iterations


def _score_rows(path, engine, count, allowed_labels=None):
    try:
        with np.load(path, allow_pickle=False) as saved:
            labels = np.asarray(saved['labels']).astype(str)
            key = 'logits' if 'logits' in saved else 'scores'
            values = np.asarray(saved[key])
    except (OSError, ValueError, KeyError):
        raise CatalogError('invalid_scores') from None
    if values.ndim != 2 or values.shape != (count, len(labels)) or not len(labels) or not np.isfinite(values).all():
        raise CatalogError('invalid_scores')
    indices = np.arange(len(labels))
    if allowed_labels is not None:
        indices = indices[np.isin(labels, list(map(str, allowed_labels)))]
    maximum = np.zeros(count, dtype=np.float32)
    winners = [None] * count
    candidates = [[] for _ in range(count)]
    if not len(indices):
        return maximum, winners, candidates
    names = [canonical_scientific_name(engine, labels[index]) for index in indices]
    for start in range(0, count, 256):
        block = values[start:start + 256]
        if key == 'logits':
            block = block.astype(np.float64)
            peak = block.max(axis=1)
            denominator = np.exp(block - peak[:, None]).sum(axis=1)
            selected = np.exp(block[:, indices] - peak[:, None]) / denominator[:, None]
        else:
            selected = block[:, indices]
        top = selected.argmax(axis=1)
        maximum[start:start + len(block)] = selected[np.arange(len(block)), top]
        for offset, index in enumerate(top, start):
            winners[offset] = names[index]
        limit = min(3, len(indices))
        chosen = np.argpartition(-selected, limit - 1, axis=1)[:, :limit]
        scores = np.take_along_axis(selected, chosen, axis=1)
        ordering = np.argsort(-scores, axis=1, kind='stable')
        chosen = np.take_along_axis(chosen, ordering, axis=1)
        scores = np.take_along_axis(scores, ordering, axis=1)
        for row in range(len(block)):
            candidates[start + row] = [[names[index], round(float(score), 4)]
                for index, score in zip(chosen[row], scores[row]) if score >= CANDIDATE_MIN]
    return maximum, winners, candidates


def _append_linkage(tree, matrix, leaf_nodes, scale=1.0):
    left, right, height, size = tree
    mapped = list(map(int, leaf_nodes))
    for row in matrix:
        a, b = mapped[int(row[0])], mapped[int(row[1])]
        node = len(left)
        left.append(a); right.append(b); height.append(float(row[2]) * scale)
        size.append(size[a] + size[b]); mapped.append(node)
    return mapped[-1]


def binary_tree(projected, cancel=None):
    """Build one binary Ward tree whose leaves are projected row indices."""
    n = len(projected)
    tree = ([-1] * n, [-1] * n, [0.0] * n, [1] * n)
    if n <= EXACT_MAX:
        root = _append_linkage(tree, linkage(np.asarray(projected), 'ward'), range(n))
        return tuple(np.asarray(values) for values in tree), root, {'method': 'exact'}

    rng = np.random.default_rng(SEED)
    fit_indices = (np.arange(n) if n <= FIT_MAX else np.sort(rng.choice(n, FIT_MAX, replace=False)))
    fit = np.asarray(projected[fit_indices], dtype=np.float32)
    requested = min(LEAF_COUNT, len(fit))
    centers, _, _, iterations = lloyd(fit, requested, cancel)
    labels, _ = _nearest(projected, centers, cancel)
    used, labels = np.unique(labels, return_inverse=True)
    labels = labels.astype(np.int32, copy=False)
    counts = np.bincount(labels)
    centers = np.empty((len(used), projected.shape[1]), dtype=np.float32)
    for column in range(projected.shape[1]):
        centers[:, column] = np.bincount(labels, weights=np.asarray(projected[:, column]),
                                          minlength=len(used)) / counts
    top = linkage(centers, 'ward')
    joining = np.empty(len(centers), dtype=np.float64)
    for row in top:
        for child in (int(row[0]), int(row[1])):
            if child < len(centers):
                joining[child] = row[2]
    sorted_rows = np.argsort(labels, kind='stable')
    offsets = np.concatenate(([0], np.cumsum(counts)))
    leaf_roots = []
    for leaf in range(len(centers)):
        _check_cancel(cancel)
        members = sorted_rows[offsets[leaf]:offsets[leaf + 1]]
        if len(members) == 1:
            leaf_roots.append(int(members[0]))
            continue
        within = linkage(np.asarray(projected[members]), 'ward')
        scale = min(1.0, 0.9 * joining[leaf] / within[-1, 2]) if within[-1, 2] else 1.0
        leaf_roots.append(_append_linkage(tree, within, members, scale))
    root = _append_linkage(tree, top, leaf_roots)
    details = {'method': 'leaves', 'requested_leaves': requested, 'leaf_count': len(centers),
               'fit_count': len(fit), 'iterations': iterations}
    return tuple(np.asarray(values) for values in tree), root, details


def _flat_children(tree, node):
    left, right, height, _ = tree
    heap = [(-height[left[node]], 0, int(left[node])), (-height[right[node]], 1, int(right[node]))]
    heapq.heapify(heap)
    counter = 2
    while len(heap) < MAX_CHILDREN:
        _, _, top = heap[0]
        if left[top] < 0 or height[top] < TAU * height[node]:
            break
        heapq.heappop(heap)
        for child in (left[top], right[top]):
            heapq.heappush(heap, (-height[child], counter, int(child)))
            counter += 1
    return [item[2] for item in heap]


def flatten_tree(tree, root, projected, cancel=None):
    """Flatten the binary tree and assign one contiguous position range to every node."""
    size = tree[3]
    nodes = [{'node': 0, 'binary': int(root), 'parent': None, 'name': '', 'depth': 0,
              'children': [], 'lo': None, 'hi': None}]
    pending = [0]
    while pending:
        current = pending.pop()
        record = nodes[current]
        if size[record['binary']] <= TERMINAL_MAX:
            continue
        children = sorted(_flat_children(tree, record['binary']), key=lambda child: (-size[child], child))
        for index, child in enumerate(children, 1):
            name = f"{record['name']}-{index}" if record['name'] else str(index)
            child_id = len(nodes)
            nodes.append({'node': child_id, 'binary': child, 'parent': current, 'name': name,
                          'depth': record['depth'] + 1, 'children': [], 'lo': None, 'hi': None})
            record['children'].append(child_id)
        pending.extend(reversed(record['children']))

    order = []
    pending = [0]
    while pending:
        current = pending.pop()
        record = nodes[current]
        if record['children']:
            pending.extend(reversed(record['children']))
            continue
        members = []
        binary = [record['binary']]
        while binary:
            item = binary.pop()
            if tree[0][item] < 0:
                members.append(item)
            else:
                binary.extend((int(tree[1][item]), int(tree[0][item])))
        members = np.asarray(members, dtype=np.int64)
        values = np.asarray(projected[members], dtype=np.float64)
        mean = values.mean(axis=0)
        distance = np.einsum('ij,ij->i', values - mean, values - mean)
        members = members[np.argsort(distance, kind='stable')]
        record['lo'] = len(order); order.extend(map(int, members)); record['hi'] = len(order)
    for record in reversed(nodes):
        if record['children']:
            record['lo'] = nodes[record['children'][0]]['lo']
            record['hi'] = nodes[record['children'][-1]]['hi']
    _check_cancel(cancel)
    return nodes, np.asarray(order, dtype=np.int64)


def _score_data(root, analyses, index, segment_ids, cancel=None):
    maximum = np.empty(len(segment_ids), dtype=np.float32)
    species_codes = np.full(len(segment_ids), -1, dtype=np.int32)
    candidates = [[] for _ in range(len(segment_ids))]
    filters = {}
    codes, names = {}, []
    with connect(root) as db:
        for analysis in analyses:
            _check_cancel(cancel)
            entry = index['analyses'][str(analysis['analysis_id'])]
            start, end = entry['start'], entry['end']
            rows = db.execute('SELECT id,start_s FROM segments WHERE analysis_id=? ORDER BY start_s,id',
                              (analysis['analysis_id'],)).fetchall()
            metadata = json.loads(analysis['metadata'])
            width = metadata.get('window_s', window_s(analysis['engine']))
            if (len(rows) != end - start or any(row['id'] != int(segment_ids[start + offset])
                    or abs(row['start_s'] - offset * width) > 1e-6 for offset, row in enumerate(rows))):
                raise CatalogError('invalid_scores')
            candidate_filter = metadata.get('candidate_filter')
            allowed = (candidate_filter.get('species') if isinstance(candidate_filter, dict)
                       and 'species' in candidate_filter else None)
            filters.setdefault(analysis['engine'], 'none')
            if allowed is not None:
                filters[analysis['engine']] = 'applied'
            scores, winners, row_candidates = _score_rows(
                analysis['scores_path'], analysis['engine'], len(rows), allowed)
            maximum[start:end] = scores
            candidates[start:end] = row_candidates
            for offset, name in enumerate(winners, start):
                if name is None:
                    continue
                if name not in codes:
                    codes[name] = len(names); names.append(name)
                species_codes[offset] = codes[name]
        records = {row['scientific_name']: {'id': row['id'], 'scientific_name': row['scientific_name']}
                   for row in db.execute('SELECT id,scientific_name FROM species WHERE scientific_name IN '
                    '(SELECT value FROM json_each(?))', (json.dumps(names),))}
    return maximum, species_codes, names, records, candidates, filters


def node_summaries(root, analyses, index, segment_ids, projected, nodes, order, cancel=None):
    maximum, species_codes, names, records, candidates, filters = _score_data(
        root, analyses, index, segment_ids, cancel)
    ordered_maximum = maximum[order]
    ordered_species = species_codes[order]
    sums = np.zeros((len(nodes), projected.shape[1]), dtype=np.float64)
    for record in reversed(nodes):
        if record['children']:
            sums[record['node']] = sums[record['children']].sum(axis=0)
        else:
            sums[record['node']] = np.asarray(projected[order[record['lo']:record['hi']]],
                                               dtype=np.float64).sum(axis=0)
    summaries = []
    for record in nodes:
        if record['node'] % 256 == 0:
            _check_cancel(cancel)
        lo, hi = record['lo'], record['hi']
        members = order[lo:hi]
        mean = sums[record['node']] / (hi - lo)
        values = np.asarray(projected[members], dtype=np.float64)
        distance = np.einsum('ij,ij->i', values - mean, values - mean)
        representative = int(members[int(distance.argmin())])
        qualified = ordered_maximum[lo:hi] >= SCORE_THRESHOLD
        qualified_count = int(qualified.sum())
        top = []
        if qualified_count:
            counts = np.bincount(ordered_species[lo:hi][qualified], minlength=len(names))
            for code in np.argsort(-counts, kind='stable')[:3]:
                if not counts[code]:
                    break
                name = names[code]
                top.append({**records.get(name, {'id': None, 'scientific_name': name}),
                            'share': float(counts[code] / qualified_count)})
        summaries.append({**record, 'rep_segment_id': int(segment_ids[representative]),
                          'hint_rate': float(qualified_count / (hi - lo)),
                          'top_species': top})
    return summaries, candidates, filters


def knn(matrix, k, cancel=None, block=1024):
    matrix = np.asarray(matrix, dtype=np.float32)
    norms = np.einsum('ij,ij->i', matrix, matrix)
    indices = np.empty((len(matrix), k), dtype=np.int32)
    distances = np.empty((len(matrix), k), dtype=np.float32)
    for start in range(0, len(matrix), block):
        _check_cancel(cancel)
        end = min(start + block, len(matrix))
        squared = norms[start:end, None] + norms[None, :] - 2 * (matrix[start:end] @ matrix.T)
        squared[np.arange(end - start), np.arange(start, end)] = np.inf
        part = np.argpartition(squared, k, axis=1)[:, :k]
        values = np.take_along_axis(squared, part, axis=1)
        ordering = np.argsort(values, axis=1)
        indices[start:end] = np.take_along_axis(part, ordering, axis=1)
        distances[start:end] = np.sqrt(np.maximum(np.take_along_axis(values, ordering, axis=1), 0))
    return indices, distances


def _nearby(layout, nearest, old, rng, cancel=None, block=2048):
    n = len(layout)
    partners = old.reshape(n, PACMAP_FURTHER).copy()
    for start in range(0, n, block):
        _check_cancel(cancel)
        end = min(start + block, n)
        own = np.arange(start, end)
        candidates = rng.integers(0, n, size=(end - start, LOCALMAP_TRIES))
        close = np.linalg.norm(layout[candidates] - layout[own, None, :], axis=-1) <= LOCALMAP_THRESHOLD
        accepted = (close & (candidates != own[:, None])
                    & ~(candidates[:, :, None] == nearest[own, None, :]).any(-1))
        rank = np.cumsum(accepted, axis=1)
        rows, columns = np.nonzero(accepted & (rank <= PACMAP_FURTHER))
        partners[start + rows, rank[rows, columns] - 1] = candidates[rows, columns]
    return partners.ravel()


def pacmap(matrix, cancel=None):
    """Build PaCMAP coordinates with LocalMAP's last phase."""
    matrix = np.asarray(matrix, dtype=np.float32)
    n = len(matrix)
    rng = np.random.default_rng(SEED)
    neighbors = min(PACMAP_NEIGHBORS, n - 1)
    candidates = min(neighbors + 50, n - 1)
    indices, distances = knn(matrix, candidates, cancel)
    sigma_start = min(3, candidates - 1)
    sigma_end = min(6, candidates)
    sigma = distances[:, sigma_start:sigma_end].mean(axis=1) + 1e-10
    scaled = distances ** 2 / (sigma[:, None] * sigma[indices])
    nearest = np.take_along_axis(indices, np.argsort(scaled, axis=1)[:, :neighbors], axis=1)
    choices = rng.integers(0, n, size=(n, PACMAP_MID_NEAR, 6))
    mid = np.empty((n, PACMAP_MID_NEAR), dtype=np.int64)
    for start in range(0, n, 4096):
        _check_cancel(cancel)
        end = min(start + 4096, n)
        squared = ((matrix[start:end, None, None, :] - matrix[choices[start:end]]) ** 2).sum(axis=-1)
        second = np.argsort(squared, axis=2)[:, :, 1]
        mid[start:end] = np.take_along_axis(choices[start:end], second[..., None], axis=2)[..., 0]
    further = rng.integers(0, n, size=(n, PACMAP_FURTHER))
    pairs = [(np.repeat(np.arange(n), neighbors), nearest.ravel()),
             (np.repeat(np.arange(n), PACMAP_MID_NEAR), mid.ravel()),
             (np.repeat(np.arange(n), PACMAP_FURTHER), further.ravel())]
    layout = np.asarray(matrix[:, :2], dtype=np.float64) * 0.01
    moment = np.zeros_like(layout); variance = np.zeros_like(layout)
    beta1, beta2, epsilon = 0.9, 0.999, 1e-7
    for iteration in range(PACMAP_ITERATIONS):
        if iteration % 10 == 0:
            _check_cancel(cancel)
        if iteration < 100:
            weights = (2.0, 1000 * (1 - iteration / 100) + 3 * iteration / 100, 1.0)
        elif iteration < 200:
            weights = (3.0, 3.0, 1.0)
        else:
            weights = (1.0, 0.0, 1.0)
        gradient = np.zeros_like(layout)
        for (source, target), weight, kind in zip(pairs, weights, ('near', 'mid', 'far')):
            if not weight:
                continue
            difference = layout[source] - layout[target]
            distance = 1 + np.einsum('ij,ij->i', difference, difference)
            if kind == 'near':
                coefficient = weight * 20 / (10 + distance) ** 2
                if iteration > 200:
                    coefficient *= (LOCALMAP_THRESHOLD / 2) / np.sqrt(distance)
            elif kind == 'mid':
                coefficient = weight * 20000 / (10000 + distance) ** 2
            else:
                coefficient = -weight * 2 / (1 + distance) ** 2
            contribution = coefficient[:, None] * difference
            for column in (0, 1):
                gradient[:, column] += (np.bincount(source, contribution[:, column], n)
                                        - np.bincount(target, contribution[:, column], n))
        moment = beta1 * moment + (1 - beta1) * gradient
        variance = beta2 * variance + (1 - beta2) * gradient ** 2
        layout -= PACMAP_LR * (moment / (1 - beta1 ** (iteration + 1))) / (
            np.sqrt(variance / (1 - beta2 ** (iteration + 1))) + epsilon)
        if iteration > 200 and iteration % 10 == 0:
            pairs[2] = (pairs[2][0], _nearby(layout, nearest, pairs[2][1], rng, cancel))
    _check_cancel(cancel)
    return layout.astype(np.float32)


def nearest_landmarks(matrix, landmarks, cancel=None, k=5, block=2048):
    matrix = np.asarray(matrix, dtype=np.float32); landmarks = np.asarray(landmarks, dtype=np.float32)
    norms = np.einsum('ij,ij->i', landmarks, landmarks)
    indices = np.empty((len(matrix), k), dtype=np.int32)
    distances = np.empty((len(matrix), k), dtype=np.float32)
    for start in range(0, len(matrix), block):
        _check_cancel(cancel)
        end = min(start + block, len(matrix))
        squared = (np.einsum('ij,ij->i', matrix[start:end], matrix[start:end])[:, None]
                   + norms[None, :] - 2 * matrix[start:end] @ landmarks.T)
        part = np.argpartition(squared, k, axis=1)[:, :k]
        values = np.take_along_axis(squared, part, axis=1)
        ordering = np.argsort(values, axis=1)
        indices[start:end] = np.take_along_axis(part, ordering, axis=1)
        distances[start:end] = np.sqrt(np.maximum(np.take_along_axis(values, ordering, axis=1), 0))
    return indices, distances


def layout_points(projected, cancel=None):
    if len(projected) <= LANDMARK_MAX:
        return pacmap(projected, cancel), 'full'
    rng = np.random.default_rng(SEED)
    landmarks = np.sort(rng.choice(len(projected), LANDMARK_MAX, replace=False))
    landmark_layout = pacmap(projected[landmarks], cancel)
    indices, distances = nearest_landmarks(projected, projected[landmarks], cancel)
    weights = 1 / (distances + 1e-3)
    weights /= weights.sum(axis=1, keepdims=True)
    layout = np.einsum('nk,nkd->nd', weights, landmark_layout[indices]).astype(np.float32)
    layout[landmarks] = landmark_layout
    return layout, 'landmarks'


def write_scatter(path, layout, segment_ids):
    n = len(layout)
    if n > np.iinfo(np.uint32).max:
        raise CatalogError('invalid_vectors')
    with path.open('wb') as output:
        output.write(struct.pack('<I', n))
        output.write(np.asarray(layout[:, 0], dtype='<f4').tobytes())
        output.write(np.asarray(layout[:, 1], dtype='<f4').tobytes())
        output.write(np.asarray(segment_ids, dtype='<i8').tobytes())


def _remove_run_files(root, params):
    if not isinstance(params, dict):
        return
    for key in ('points_file', 'pca_file'):
        name = params.get(key)
        if isinstance(name, str) and Path(name).name == name:
            try:
                (Path(root) / 'explore' / name).unlink(missing_ok=True)
            except OSError:
                pass


def _cleanup_staging(root, site_id, engine):
    with connect(root) as db:
        rows = db.execute("SELECT id,params FROM explore_runs WHERE site_id=? AND engine=? AND json_extract(params,'$.staging')=1",
                          (site_id, engine)).fetchall()
        db.executemany('DELETE FROM explore_runs WHERE id=?', ((row['id'],) for row in rows))
    for row in rows:
        try:
            _remove_run_files(root, json.loads(row['params']))
        except (TypeError, ValueError):
            pass


def _context(root, dataset_id):
    engine, model_sha = main_identity(root)
    if model_sha is None:
        raise CatalogError('no_search_embeddings')
    with connect(root) as db:
        dataset = db.execute('SELECT * FROM datasets WHERE id=?', (dataset_id,)).fetchone()
        if dataset is None:
            raise CatalogError('not_found')
        compatible = [dict(row) for row in compatible_analyses(db, dataset_id, model_sha, engine)]
        details = {row['id']: row for row in db.execute(
            'SELECT id,engine,metadata,scores_path FROM analyses WHERE id IN (SELECT value FROM json_each(?))',
            (json.dumps([row['analysis_id'] for row in compatible]),))}
        analyses = [{**row, **{key: details[row['analysis_id']][key]
                     for key in ('engine', 'metadata', 'scores_path')}} for row in compatible]
    analyses.sort(key=lambda row: row['analysis_id'])
    if not analyses:
        raise CatalogError('no_search_embeddings')
    return dict(dataset), engine, model_sha, analyses


def build(root, dataset_id, job_id=None, cancel=None):
    """Build a staged map, then replace the current map in one short transaction."""
    final_files = []
    staged_id = None
    published = False
    try:
        dataset, engine, model_sha, analyses = _context(root, dataset_id)
        for analysis in analyses:
            checked_source(analysis)
        _cleanup_staging(root, dataset['site_id'], engine)
        stage_seconds = {}
        _progress(root, job_id, 'explore_cache', 0)
        started = time.perf_counter()
        matrix, segment_ids, index, index_path = embedding_cache(
            root, dataset['site_id'], engine, model_sha, analyses, cancel)
        stage_seconds['cache'] = time.perf_counter() - started
        _check_cancel(cancel)
        folder = Path(root) / 'explore'
        with tempfile.TemporaryDirectory(prefix='.stage-', dir=folder) as temporary:
            temporary = Path(temporary)
            _progress(root, job_id, 'explore_pca', 1)
            started = time.perf_counter()
            projected, pca = exact_pca(matrix, temporary / 'pca50-source.f32', cancel)
            stage_seconds['pca'] = time.perf_counter() - started
            _check_cancel(cancel)
            _progress(root, job_id, 'explore_layout', 2)
            started = time.perf_counter()
            layout, layout_mode = layout_points(projected, cancel)
            stage_seconds['layout'] = time.perf_counter() - started
            _check_cancel(cancel)
            _progress(root, job_id, 'explore_tree', 3)
            started = time.perf_counter()
            tree, root_node, tree_details = binary_tree(layout, cancel)
            nodes, order = flatten_tree(tree, root_node, projected, cancel)
            stage_seconds['tree'] = time.perf_counter() - started
            _check_cancel(cancel)
            _progress(root, job_id, 'explore_summarizing', 4)
            started = time.perf_counter()
            summaries, candidates, filters = node_summaries(
                root, analyses, index, segment_ids, projected, nodes, order, cancel)
            params = {'staging': True}
            with connect(root) as db:
                staged_sha = f'{model_sha}.staging.{uuid4().hex}'
                staged_id = db.execute('INSERT INTO explore_runs(site_id,engine,model_sha,params) VALUES(?,?,?,?)',
                    (dataset['site_id'], engine, staged_sha, json.dumps(params))).lastrowid
            points_name = f'points-{staged_id}.bin'
            pca_name = f'pca50-{staged_id}.f32'
            staged_points = temporary / points_name
            staged_pca = temporary / pca_name
            write_scatter(staged_points, layout[order], segment_ids[order])
            ordered_pca = np.memmap(staged_pca, mode='w+', dtype='<f4', shape=(len(order), PCA_DIMS))
            for start in range(0, len(order), PCA_BATCH):
                end = min(start + PCA_BATCH, len(order))
                ordered_pca[start:end] = projected[order[start:end]]
            ordered_pca.flush(); del ordered_pca
            _check_cancel(cancel)
            final_files = [folder / points_name, folder / pca_name]
            staged_points.replace(final_files[0]); staged_pca.replace(final_files[1])
            with connect(root) as db:
                for start in range(0, len(order), VECTOR_BATCH):
                    _check_cancel(cancel)
                    end = min(start + VECTOR_BATCH, len(order))
                    db.executemany('INSERT INTO explore_points VALUES(?,?,?,?,?,?)',
                        ((staged_id, position, int(segment_ids[order[position]]),
                          float(layout[order[position], 0]), float(layout[order[position], 1]),
                          json.dumps(candidates[order[position]], ensure_ascii=False))
                         for position in range(start, end)))
                db.executemany('INSERT INTO explore_nodes VALUES(?,?,?,?,?,?,?,?,?,?)',
                    ((staged_id, item['node'], item['parent'], item['name'], item['depth'], item['lo'], item['hi'],
                      item['rep_segment_id'], item['hint_rate'],
                      json.dumps(item['top_species'], ensure_ascii=False))
                     for item in summaries))
                stage_seconds['summarizing'] = time.perf_counter() - started
                params = {'analysis_ids': [row['analysis_id'] for row in analyses],
                          'segment_count': len(order), 'points_file': points_name, 'pca_file': pca_name,
                          'cache_index': index_path.name, 'cache_version': CACHE_VERSION, 'pca': pca,
                          'method': tree_details['method'], 'tree': tree_details,
                          'node_count': len(nodes), 'depth': max(item['depth'] for item in nodes),
                          'layout': layout_mode, 'map_version': MAP_VERSION, 'stage_seconds': stage_seconds,
                          'candidate_filter': filters,
                          'score_threshold': SCORE_THRESHOLD, 'candidate_min': CANDIDATE_MIN}
                db.execute('UPDATE explore_runs SET params=? WHERE id=?', (json.dumps(params, ensure_ascii=False), staged_id))
            _check_cancel(cancel)
            with connect(root) as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT id,params FROM explore_runs WHERE site_id=? AND engine=? AND model_sha=?',
                                 (dataset['site_id'], engine, model_sha)).fetchone()
                if old:
                    db.execute('DELETE FROM explore_runs WHERE id=?', (old['id'],))
                db.execute('UPDATE explore_runs SET model_sha=? WHERE id=?', (model_sha, staged_id))
            published = True
            if old:
                _remove_run_files(root, json.loads(old['params']))
        _progress(root, job_id, 'finished', 5, status='complete')
        return {'id': staged_id}
    except Exception as error:
        if staged_id is not None and not published:
            with connect(root) as db:
                db.execute('DELETE FROM explore_runs WHERE id=?', (staged_id,))
        if not published:
            for path in final_files:
                path.unlink(missing_ok=True)
        if isinstance(error, CatalogError) and str(error) == 'cancelled':
            _progress(root, job_id, 'finished', 0, status='cancelled')
        else:
            code = str(error) if isinstance(error, CatalogError) else 'explore_failed'
            _progress(root, job_id, 'finished', 0, status='failed', error=code)
        raise


def _dataset_site(db, dataset_id):
    row = db.execute('SELECT site_id FROM datasets WHERE id=?', (dataset_id,)).fetchone()
    if row is None:
        raise CatalogError('not_found')
    return row['site_id']


def _current_run(root, dataset_id):
    engine, model_sha = main_identity(root)
    with connect(root) as db:
        site_id = _dataset_site(db, dataset_id)
        run = (db.execute('SELECT * FROM explore_runs WHERE site_id=? AND engine=? AND model_sha=?',
                          (site_id, engine, model_sha)).fetchone() if model_sha else None)
    return site_id, engine, model_sha, run


def _node_row(db, run_id, node):
    return db.execute('''SELECT n.*,s.id AS segment_id,a.recording_id,r.path,s.start_s,s.end_s,
        r.recorded_at,r.timezone FROM explore_nodes n JOIN segments s ON s.id=n.rep_segment_id
        JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
        WHERE n.run_id=? AND n.node=?''', (run_id, node)).fetchone()


def _species_ids(db, names):
    return {row['scientific_name']: row['id'] for row in db.execute(
        'SELECT id,scientific_name FROM species WHERE scientific_name IN '
        '(SELECT value FROM json_each(?))', (json.dumps(sorted(set(names))),))}


def _node_shape(row, species_ids, child_count=0):
    top_species = json.loads(row['top_species'])
    return {'node': row['node'], 'name': row['name'], 'parent': row['parent'], 'depth': row['depth'],
            'size': row['hi'] - row['lo'], 'lo': row['lo'], 'hi': row['hi'], 'child_count': child_count,
            'rep': {key: row[key] for key in ('segment_id', 'recording_id', 'path', 'start_s', 'end_s',
                                               'recorded_at', 'timezone')},
            'hint_rate': row['hint_rate'],
            'top_species': [{'id': species_ids.get(item['scientific_name']),
                             'scientific_name': item['scientific_name'], 'share': item['share']}
                            for item in top_species]}


def status(root, dataset_id, node=0):
    site_id, engine, model_sha, run = _current_run(root, dataset_id)
    with connect(root) as db:
        building = db.execute("""SELECT 1 FROM jobs j JOIN datasets d ON d.id=j.dataset_id
            WHERE d.site_id=? AND j.status IN ('queued','running') AND j.stage LIKE 'explore_%' LIMIT 1""",
                              (site_id,)).fetchone() is not None
        if run is None:
            return {'status': 'building' if building else 'none', 'created_at': None, 'engine': engine,
                    'segment_count': 0}
        params = json.loads(run['params'])
        current = sorted(row['analysis_id'] for row in compatible_analyses(db, dataset_id, model_sha, engine))
        stale_reason = ('data' if current != params.get('analysis_ids') else
                        'method' if params.get('map_version') != MAP_VERSION else None)
        state = 'building' if building else 'stale' if stale_reason else 'ready'
        result = {'status': state, 'created_at': run['created_at'], 'engine': run['engine'],
                  'segment_count': params['segment_count']}
        if state == 'stale':
            result['stale_reason'] = stale_reason
        # A stale map, or the one being replaced, stays browsable until the new one is published.
        selected = _node_row(db, run['id'], node)
        if selected is None:
            raise CatalogError('not_found')
        children = db.execute('''SELECT n.*,s.id AS segment_id,a.recording_id,r.path,s.start_s,s.end_s,
            r.recorded_at,r.timezone FROM explore_nodes n JOIN segments s ON s.id=n.rep_segment_id
            JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
            WHERE n.run_id=? AND n.parent=? ORDER BY n.hi-n.lo DESC,n.node''', (run['id'], node)).fetchall()
        counts = dict(db.execute('''SELECT parent,COUNT(*) FROM explore_nodes WHERE run_id=? AND parent IN
            (SELECT value FROM json_each(?)) GROUP BY parent''', (run['id'], json.dumps([node] + [row['node'] for row in children]))))
        node_rows = [selected, *children]
        species_ids = _species_ids(db, [item['scientific_name'] for row in node_rows
            for item in json.loads(row['top_species'])])
        path = []
        current = selected
        while current is not None:
            path.append({'node': current['node'], 'name': current['name'],
                         'size': current['hi'] - current['lo']})
            current = _node_row(db, run['id'], current['parent']) if current['parent'] is not None else None
    result.update({'node': _node_shape(selected, species_ids, counts.get(node, 0)),
                   'path': list(reversed(path)),
                   'children': [_node_shape(row, species_ids, counts.get(row['node'], 0)) for row in children]})
    return result


def points_file(root, dataset_id):
    _, _, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    params = json.loads(run['params'])
    name = params.get('points_file')
    if not isinstance(name, str) or Path(name).name != name:
        raise CatalogError('not_found')
    path = Path(root) / 'explore' / name
    if not path.is_file():
        raise CatalogError('not_found')
    return path


def _segment_shape(row, species_ids):
    return {**{key: row[key] for key in ('segment_id', 'recording_id', 'path', 'start_s', 'end_s',
                                          'recorded_at', 'timezone')},
            'candidates': [{'id': species_ids.get(name), 'scientific_name': name, 'score': score}
                           for name, score in row['candidates']]}


def point_segment(root, dataset_id, position):
    _, _, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    with connect(root) as db:
        row = db.execute('''SELECT p.segment_id,p.candidates,a.recording_id,r.path,s.start_s,s.end_s,
            r.recorded_at,r.timezone FROM explore_points p JOIN segments s ON s.id=p.segment_id
            JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
            WHERE p.run_id=? AND p.position=?''', (run['id'], position)).fetchone()
        if row is None:
            raise CatalogError('not_found')
        row = dict(row); row['candidates'] = json.loads(row['candidates'])
        species_ids = _species_ids(db, [name for name, _ in row['candidates']])
    return _segment_shape(row, species_ids)


def node_segments(root, dataset_id, node, offset=0, near=None):
    _, _, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    params = json.loads(run['params'])
    with connect(root) as db:
        selected = db.execute('SELECT * FROM explore_nodes WHERE run_id=? AND node=?',
                              (run['id'], node)).fetchone()
        if selected is None:
            raise CatalogError('not_found')
        terminal = db.execute('SELECT 1 FROM explore_nodes WHERE run_id=? AND parent=? LIMIT 1',
                              (run['id'], node)).fetchone() is None
    lo, hi = selected['lo'], selected['hi']
    if near is not None and near >= params['segment_count']:
        raise CatalogError('not_found')
    name = params.get('pca_file')
    path = Path(root) / 'explore' / name if isinstance(name, str) and Path(name).name == name else None
    expected = params['segment_count'] * PCA_DIMS * np.dtype('<f4').itemsize
    if path is None or not path.is_file() or path.stat().st_size != expected:
        raise CatalogError('not_found')
    pca = np.memmap(path, mode='r', dtype='<f4', shape=(params['segment_count'], PCA_DIMS))
    values = np.asarray(pca[lo:hi], dtype=np.float64)
    centered = values - (values.mean(axis=0) if near is None else pca[near])
    distance = np.sqrt(np.einsum('ij,ij->i', centered, centered))
    ordering = np.arange(hi - lo) if terminal and near is None else np.argsort(distance, kind='stable')
    page = ordering[offset:offset + 20]
    positions = (page + lo).tolist()
    with connect(root) as db:
        rows = db.execute('''SELECT p.position,p.segment_id,p.candidates,a.recording_id,r.path,s.start_s,s.end_s,
            r.recorded_at,r.timezone FROM explore_points p JOIN segments s ON s.id=p.segment_id
            JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
            WHERE p.run_id=? AND p.position IN (SELECT value FROM json_each(?))''',
            (run['id'], json.dumps(positions))).fetchall()
        rows = [dict(row) for row in rows]
        for row in rows:
            row['candidates'] = json.loads(row['candidates'])
        species_ids = _species_ids(db, [name for row in rows for name, _ in row['candidates']])
    by_position = {row['position']: row for row in rows}
    segments = []
    for position, index in zip(positions, page):
        row = by_position[position]
        shaped = _segment_shape(row, species_ids)
        shaped['dist'] = float(distance[index]); segments.append(shaped)
    return {'total': hi - lo, 'segments': segments}
