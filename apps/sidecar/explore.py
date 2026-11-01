"""Precomputed PCA sound maps over the current main-engine embeddings."""
import hashlib
import json
import multiprocessing
import os
import struct
import tempfile
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from uuid import uuid4

import numpy as np
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
SUBSAMPLE_MAX = 20_000
SILHOUETTE_MAX = 4_000
K_GRID = (10, 15, 20, 25, 30, 40, 50, 60)
LARGE_K_GRID = (80, 100, 150, 200)
SEED = 20261101
CACHE_VERSION = 1
SCORE_THRESHOLD = 0.1
_PROCESS_SAMPLE = None
_PROCESS_CANCEL = None


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


def mean_silhouette(matrix, labels, max_points=SILHOUETTE_MAX, seed=SEED):
    """Port of the benchmark's deterministic mean-Euclidean silhouette."""
    rng = np.random.default_rng(seed)
    indices = (np.arange(len(matrix)) if len(matrix) <= max_points
               else np.sort(rng.choice(len(matrix), max_points, replace=False)))
    sample = np.asarray(matrix[indices], dtype=np.float64)
    sample_labels = np.asarray(labels[indices])
    unique, counts = np.unique(sample_labels, return_counts=True)
    if len(unique) < 2:
        return -1.0, len(sample)
    norms = np.einsum('ij,ij->i', sample, sample)
    total = 0.0
    for start in range(0, len(sample), 256):
        block = sample[start:start + 256]
        distances = np.sqrt(np.maximum(
            np.einsum('ij,ij->i', block, block)[:, None] + norms[None, :] - 2 * block @ sample.T, 0))
        for offset, row_distances in enumerate(distances):
            own = sample_labels[start + offset]
            own_count = int(counts[np.flatnonzero(unique == own)[0]])
            if own_count == 1:
                continue
            own_mask = sample_labels == own
            a = float(row_distances[own_mask].sum() / (own_count - 1))
            b = min(float(row_distances[sample_labels == label].mean()) for label in unique if label != own)
            total += (b - a) / max(a, b) if max(a, b) else 0.0
    return total / len(sample), len(sample)


def _candidate(sample, k, cancel=None):
    centers, labels, squared, iterations = lloyd(sample, k, cancel)
    silhouette, count = mean_silhouette(sample, labels)
    return {'k': k, 'silhouette': silhouette, 'inertia': float(squared.sum(dtype=np.float64)),
            'silhouette_sample_n': count, 'iterations': iterations, 'centers': centers}


class _SharedCancel:
    def is_set(self):
        return bool(_PROCESS_CANCEL.value)


def _process_init(sample, cancel_flag):
    global _PROCESS_SAMPLE, _PROCESS_CANCEL
    _PROCESS_SAMPLE = sample
    _PROCESS_CANCEL = cancel_flag


def _process_candidate(k):
    return _candidate(_PROCESS_SAMPLE, k, _SharedCancel())


def k_grid(n):
    values = K_GRID
    if n > SUBSAMPLE_MAX:
        values += tuple(k for k in LARGE_K_GRID if k < n / 50)
    return tuple(k for k in values if k < min(n, SUBSAMPLE_MAX))


def select_k(matrix, cancel=None, executor_kind='processes'):
    rng = np.random.default_rng(SEED)
    indices = (np.arange(len(matrix)) if len(matrix) <= SUBSAMPLE_MAX
               else np.sort(rng.choice(len(matrix), SUBSAMPLE_MAX, replace=False)))
    sample = np.asarray(matrix[indices], dtype=np.float32)
    grid = k_grid(len(matrix))
    if not grid:
        raise CatalogError('insufficient_explore_embeddings')
    # ponytail: four candidates at once keeps BLAS fast without multiplying its working memory twelvefold.
    workers = min(4, len(grid), os.cpu_count() or 1)
    results = []
    if executor_kind == 'threads':
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_candidate, sample, k, cancel) for k in grid]
            for future in as_completed(futures):
                _check_cancel(cancel)
                results.append(future.result())
    else:
        context = multiprocessing.get_context('spawn')
        cancel_flag = context.Value('b', 0)
        with ProcessPoolExecutor(max_workers=workers, mp_context=context, initializer=_process_init,
                                 initargs=(sample, cancel_flag)) as pool:
            pending = {pool.submit(_process_candidate, k) for k in grid}
            while pending:
                completed, pending = wait(pending, timeout=.05, return_when=FIRST_COMPLETED)
                if cancel is not None and cancel.is_set():
                    cancel_flag.value = 1
                    for future in pending:
                        future.cancel()
                    raise CatalogError('cancelled')
                results.extend(future.result() for future in completed)
    results.sort(key=lambda item: item['k'])
    selected = max(results, key=lambda item: (item['silhouette'], -item['k']))
    public = [{key: value for key, value in item.items() if key != 'centers'} for item in results]
    return selected['centers'], {'selected_k': selected['k'], 'grid': public,
                                 'subsample_n': len(sample), 'parallel': executor_kind}


def _score_rows(path, engine, count):
    try:
        with np.load(path, allow_pickle=False) as saved:
            labels = np.asarray(saved['labels']).astype(str)
            key = 'logits' if 'logits' in saved else 'scores'
            values = np.asarray(saved[key])
    except (OSError, ValueError, KeyError):
        raise CatalogError('invalid_scores') from None
    if values.ndim != 2 or values.shape != (count, len(labels)) or not len(labels) or not np.isfinite(values).all():
        raise CatalogError('invalid_scores')
    winners = values.argmax(axis=1)
    if key == 'logits':
        maximum = np.empty(count, dtype=np.float32)
        for start in range(0, count, 256):
            block = values[start:start + 256].astype(np.float64)
            peak = block.max(axis=1)
            maximum[start:start + len(block)] = 1 / np.exp(block - peak[:, None]).sum(axis=1)
    else:
        maximum = values[np.arange(count), winners].astype(np.float32)
    names = [canonical_scientific_name(engine, labels[index]) for index in winners]
    return maximum, names


def cluster_summaries(root, analyses, index, segment_ids, labels, distances, cancel=None):
    n = len(labels)
    maximum = np.empty(n, dtype=np.float32)
    species_codes = np.empty(n, dtype=np.int32)
    codes = {}
    names = []
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
            scores, winners = _score_rows(analysis['scores_path'], analysis['engine'], len(rows))
            maximum[start:end] = scores
            for offset, name in enumerate(winners, start):
                if name not in codes:
                    codes[name] = len(names)
                    names.append(name)
                species_codes[offset] = codes[name]
        records = {row['scientific_name']: {'id': row['id'], 'scientific_name': row['scientific_name']}
                   for row in db.execute('SELECT id,scientific_name FROM species WHERE scientific_name IN '
                    '(SELECT value FROM json_each(?))', (json.dumps(names),))}
    return aggregate_summaries(segment_ids, labels, distances, maximum, species_codes, names, records, cancel)


def aggregate_summaries(segment_ids, labels, distances, maximum, species_codes, names, records, cancel=None):
    summaries = []
    for cluster in np.unique(labels):
        _check_cancel(cancel)
        members = np.flatnonzero(labels == cluster)
        qualified = members[maximum[members] >= SCORE_THRESHOLD]
        top = None
        if len(qualified) * 5 >= len(members):
            counts = np.bincount(species_codes[qualified], minlength=len(names))
            name = names[int(counts.argmax())]
            top = records.get(name, {'id': None, 'scientific_name': name})
        representative = members[int(np.argmin(distances[members]))]
        summaries.append({'cluster': int(cluster), 'size': len(members),
            'rep_segment_id': int(segment_ids[representative]),
            'score_mean': float(maximum[members].mean(dtype=np.float64)), 'top_species': top})
    return summaries


def write_scatter(path, projected, labels, segment_ids):
    n = len(labels)
    if n > np.iinfo(np.uint32).max:
        raise CatalogError('invalid_vectors')
    with path.open('wb') as output:
        output.write(struct.pack('<I', n))
        output.write(np.asarray(projected[:, 0], dtype='<f4').tobytes())
        output.write(np.asarray(projected[:, 1], dtype='<f4').tobytes())
        output.write(np.asarray(labels, dtype='<i4').tobytes())
        output.write(np.asarray(segment_ids, dtype='<i8').tobytes())


def _remove_points_file(root, params):
    name = params.get('points_file') if isinstance(params, dict) else None
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
        _remove_points_file(root, json.loads(row['params']))


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
    final_file = None
    staged_id = None
    published = False
    try:
        dataset, engine, model_sha, analyses = _context(root, dataset_id)
        for analysis in analyses:
            checked_source(analysis)
        _cleanup_staging(root, dataset['site_id'], engine)
        _progress(root, job_id, 'explore_cache', 0)
        matrix, segment_ids, index, index_path = embedding_cache(
            root, dataset['site_id'], engine, model_sha, analyses, cancel)
        _check_cancel(cancel)
        folder = Path(root) / 'explore'
        with tempfile.TemporaryDirectory(prefix='.stage-', dir=folder) as temporary:
            temporary = Path(temporary)
            _progress(root, job_id, 'explore_pca', 1)
            projected, pca = exact_pca(matrix, temporary / 'pca50.f32', cancel)
            _progress(root, job_id, 'explore_selecting_clusters', 2)
            initial, selection = select_k(projected, cancel)
            _progress(root, job_id, 'explore_clustering', 3)
            _, labels, squared, iterations = lloyd(projected, initial, cancel)
            distances = np.sqrt(squared, dtype=np.float32)
            unique, labels = np.unique(labels, return_inverse=True)
            labels = labels.astype(np.int32)
            _progress(root, job_id, 'explore_summarizing', 4)
            summaries = cluster_summaries(root, analyses, index, segment_ids, labels, distances, cancel)
            params = {'staging': True}
            with connect(root) as db:
                staged_sha = f'{model_sha}.staging.{uuid4().hex}'
                staged_id = db.execute('INSERT INTO explore_runs(site_id,engine,model_sha,params) VALUES(?,?,?,?)',
                    (dataset['site_id'], engine, staged_sha, json.dumps(params))).lastrowid
            filename = f'points-{staged_id}.bin'
            params = {'analysis_ids': [row['analysis_id'] for row in analyses], 'segment_count': len(labels),
                      'points_file': filename, 'cache_index': index_path.name, 'cache_version': CACHE_VERSION,
                      'pca': pca, 'k_selection': selection, 'final_iterations': iterations,
                      'cluster_count': len(unique), 'score_threshold': SCORE_THRESHOLD}
            staged_file = temporary / filename
            write_scatter(staged_file, projected, labels, segment_ids)
            _check_cancel(cancel)
            final_file = folder / filename
            staged_file.replace(final_file)
            with connect(root) as db:
                for start in range(0, len(labels), VECTOR_BATCH):
                    _check_cancel(cancel)
                    end = min(start + VECTOR_BATCH, len(labels))
                    db.executemany('INSERT INTO explore_points VALUES(?,?,?,?,?,?)',
                        ((staged_id, int(segment_ids[i]), float(projected[i, 0]), float(projected[i, 1]),
                          int(labels[i]), float(distances[i])) for i in range(start, end)))
                db.executemany('INSERT INTO explore_clusters VALUES(?,?,?,?,?,?)',
                    ((staged_id, item['cluster'], item['size'], item['rep_segment_id'], item['score_mean'],
                      json.dumps(item['top_species'], ensure_ascii=False) if item['top_species'] else None)
                     for item in summaries))
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
                _remove_points_file(root, json.loads(old['params']))
        _progress(root, job_id, 'finished', 5, status='complete')
        return {'id': staged_id}
    except Exception as error:
        if staged_id is not None and not published:
            with connect(root) as db:
                db.execute('DELETE FROM explore_runs WHERE id=?', (staged_id,))
        if final_file is not None and not published:
            final_file.unlink(missing_ok=True)
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


def status(root, dataset_id):
    site_id, engine, model_sha, run = _current_run(root, dataset_id)
    with connect(root) as db:
        building = db.execute("""SELECT 1 FROM jobs j JOIN datasets d ON d.id=j.dataset_id
            WHERE d.site_id=? AND j.status IN ('queued','running') AND j.stage LIKE 'explore_%' LIMIT 1""",
                              (site_id,)).fetchone() is not None
        if run is None:
            return {'status': 'building' if building else 'none', 'created_at': None, 'engine': engine,
                    'segment_count': 0, 'clusters': []}
        params = json.loads(run['params'])
        current = sorted(row['analysis_id'] for row in compatible_analyses(db, dataset_id, model_sha, engine))
        state = 'building' if building else 'stale' if current != params.get('analysis_ids') else 'ready'
        rows = db.execute('''SELECT c.*,s.id AS segment_id,a.recording_id,r.path,s.start_s,s.end_s,
            r.recorded_at,r.timezone FROM explore_clusters c JOIN segments s ON s.id=c.rep_segment_id
            JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
            WHERE c.run_id=? ORDER BY c.size DESC,c.cluster''', (run['id'],)).fetchall()
    clusters = []
    for row in rows:
        clusters.append({'cluster': row['cluster'], 'size': row['size'],
            'rep': {key: row[key] for key in ('segment_id', 'recording_id', 'path', 'start_s', 'end_s', 'recorded_at', 'timezone')},
            'score_mean': row['score_mean'],
            'top_species': json.loads(row['top_species']) if row['top_species'] else None})
    return {'status': state, 'created_at': run['created_at'], 'engine': run['engine'],
            'segment_count': params['segment_count'], 'clusters': clusters}


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


def cluster_segments(root, dataset_id, cluster, offset=0):
    _, _, _, run = _current_run(root, dataset_id)
    if run is None:
        raise CatalogError('not_found')
    with connect(root) as db:
        total = db.execute('SELECT COUNT(*) FROM explore_points WHERE run_id=? AND cluster=?',
                           (run['id'], cluster)).fetchone()[0]
        if not total:
            raise CatalogError('not_found')
        rows = db.execute('''SELECT p.segment_id,a.recording_id,r.path,s.start_s,s.end_s,
            r.recorded_at,r.timezone,p.dist FROM explore_points p JOIN segments s ON s.id=p.segment_id
            JOIN analyses a ON a.id=s.analysis_id JOIN recordings r ON r.id=a.recording_id
            WHERE p.run_id=? AND p.cluster=? ORDER BY p.dist,p.segment_id LIMIT 20 OFFSET ?''',
            (run['id'], cluster, offset)).fetchall()
    return {'total': total, 'segments': [dict(row) for row in rows]}
